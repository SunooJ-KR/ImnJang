# ============================================================================
# 28.collect_view_targets.py
# ============================================================================
# Author:      yjkim
# Purpose:     조망 판정에 사용할 한강·주요 하천, 산 봉우리, 정비사업 구역을 수집한다
# Description: Overpass 서울 bbox 타일 수집과 원본 캐시를 사용한다. 수계는
#              EPSG:5179 면적 10,000 m² 이상만 남기며, 공원은 재수집하지 않는다.
#              서울 열린데이터광장 정비사업 API는 후보를 확인한 뒤 적절한 구역
#              데이터만 저장하고, 없으면 28.3을 미확보로 남긴다.
# ============================================================================

import json
import re
import time
from pathlib import Path
from urllib.parse import quote, quote_plus

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely import make_valid, wkt
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"
raw_dir = output_dir / "raw"
WATER_PATH = output_dir / "28.1.water.txt"
PEAK_PATH = output_dir / "28.2.mountains.txt"
REDEVELOP_PATH = output_dir / "28.3.redevelop.txt"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
SEOUL_OPENAPI_URL = "https://openapi.seoul.go.kr:8088"
USER_AGENT = "imnjang-prototype/0.1 (contest research)"
SEOUL_BBOX = (37.40, 126.76, 37.71, 127.19)  # south, west, north, east
TILE_DEG = 0.10
SLEEP_SEC = 1
RETRY_MAX = 4
RETRY_BASE_SEC = 15
RETRY_STATUS = {429, 500, 502, 503, 504}
WATER_MIN_AREA_M2 = 10_000
HAN_RIVER_MIN_AREA_M2 = 5_000_000
HAN_RIVER_MIN_LON_WIDTH_DEG = 0.15
WATER_QUERY_VERSION = "v3_relation_body_geom"

# 협력업체 정보는 구역 데이터가 아니므로 후보에서 제외한다.
REDEVELOP_SERVICE_CANDIDATES = (
    "CleanupBusiInfo", "CleanupBusinessInfo", "CleanupBizInfo",
    "CleanupBsnsInfo", "CleanupZoneInfo", "CleanupAreaInfo",
    "CleanupProjectInfo",
)


def load_env(key):
    """값은 읽기만 하며 어느 로그에도 출력하지 않는다."""
    env_path = work_dir / ".env"
    if not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


SEOUL_OPENAPI_KEY = load_env("SEOUL_OPENAPI_KEY")


def mask_key(text):
    """예외 URL의 원문·URL 인코딩 API key 모두를 가린다."""
    masked = str(text)
    if not SEOUL_OPENAPI_KEY:
        return masked
    for variant in sorted({
        SEOUL_OPENAPI_KEY, quote(SEOUL_OPENAPI_KEY, safe=""),
        quote_plus(SEOUL_OPENAPI_KEY),
    }, key=len, reverse=True):
        masked = masked.replace(variant, "<SEOUL_OPENAPI_KEY>")
    return masked


# ============================================================================
# 1. Overpass 타일 수집
# ============================================================================

def tiles():
    south, west, north, east = SEOUL_BBOX
    return [
        (round(lat, 3), round(lon, 3), round(min(lat + TILE_DEG, north), 3),
         round(min(lon + TILE_DEG, east), 3))
        for lat in np.arange(south, north, TILE_DEG)
        for lon in np.arange(west, east, TILE_DEG)
    ]


def water_query(tile):
    box = f"{tile[0]},{tile[1]},{tile[2]},{tile[3]}"
    return f"""
    [out:json][timeout:180];
    (
      way["natural"="water"]({box});
      way["waterway"="riverbank"]({box});
      way["water"="river"]({box});
      relation["natural"="water"]({box});
      relation["waterway"="riverbank"]({box});
      relation["water"="river"]({box});
    );
    // relation의 member way 좌표를 받아 outer/inner ring을 조립한다.
    out body geom;
    """


def peak_query(tile):
    box = f"{tile[0]},{tile[1]},{tile[2]},{tile[3]}"
    return f"""[out:json][timeout:180];node["natural"="peak"]({box});out body;"""


def fetch_overpass(query):
    response = requests.post(
        OVERPASS_URL, data={"data": query}, timeout=240,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json()["elements"]


def collect_overpass(kind, query_builder, cache_version=None):
    """타일별 캐시와 429/504 계열 지수 backoff를 적용한다."""
    cache_name = f"{kind}_{cache_version}" if cache_version else kind
    cache_dir = raw_dir / "view_targets" / cache_name
    cache_dir.mkdir(parents=True, exist_ok=True)
    failures, calls = [], 0
    tile_list = tiles()
    for index, tile in enumerate(tile_list, start=1):
        cache_path = cache_dir / f"{tile[0]}_{tile[1]}.json"
        if cache_path.exists():
            continue
        print(f"  수집 중 [{index}/{len(tile_list)}] {kind} {tile[0]},{tile[1]}")
        elements = None
        for attempt in range(RETRY_MAX):
            try:
                calls += 1
                elements = fetch_overpass(query_builder(tile))
                break
            except requests.RequestException as error:
                status = getattr(getattr(error, "response", None), "status_code", None)
                if attempt == RETRY_MAX - 1 or status not in RETRY_STATUS:
                    failures.append({"tile": tile, "status": status, "error": str(error)[:160]})
                    print(f"    [실패] tile={tile}, status={status}, error={error}")
                    break
                wait = RETRY_BASE_SEC * (2 ** attempt)
                print(f"    [재시도 {attempt + 1}/{RETRY_MAX - 1}] status={status}, {wait}초 대기")
                time.sleep(wait)
        if elements is not None:
            cache_path.write_text(json.dumps(elements, ensure_ascii=False), encoding="utf-8")
            time.sleep(SLEEP_SEC)
    elements = []
    for cache_path in sorted(cache_dir.glob("*.json")):
        elements.extend(json.loads(cache_path.read_text(encoding="utf-8")))
    return elements, failures, calls, len(tile_list)


# ============================================================================
# 2. 수계·산 정규화
# ============================================================================

def normalize_polygon(coords):
    if not coords or len(coords) < 3:
        return None
    ring = [(point["lon"], point["lat"]) for point in coords]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    geometry = Polygon(ring)
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.is_empty or geometry.geom_type not in {"Polygon", "MultiPolygon"}:
        return None
    return geometry


def polygonal_geometry(geometry):
    """유효한 Polygon/MultiPolygon만 남기고 GeometryCollection은 평탄화한다."""
    if geometry is None or geometry.is_empty:
        return None
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.geom_type in {"Polygon", "MultiPolygon"}:
        return geometry
    polygons = [part for part in geometry.geoms if part.geom_type in {"Polygon", "MultiPolygon"}]
    if not polygons:
        return None
    merged = unary_union(polygons)
    return merged if merged.geom_type in {"Polygon", "MultiPolygon"} else None


def relation_polygons(elements):
    """타일별 relation member 선분을 합쳐 hole을 포함한 polygon으로 조립한다."""
    outer_lines, inner_lines = [], []
    for element in elements:
        for member in element.get("members", []):
            role = member.get("role")
            if role not in {"", "outer", "inner"}:
                continue
            coords = member.get("geometry") or []
            points = [(point["lon"], point["lat"]) for point in coords
                      if "lon" in point and "lat" in point]
            if len(points) >= 2:
                (inner_lines if role == "inner" else outer_lines).append(LineString(points))
    if not outer_lines:
        return None
    # 같은 relation이 여러 타일에서 받은 선분을 모두 합친 뒤 outer/inner ring을 따로 조립한다.
    outer_polygons = list(polygonize(unary_union(outer_lines)))
    if not outer_polygons:
        return None
    outer_union = unary_union(outer_polygons)
    if not inner_lines:
        return polygonal_geometry(outer_union)
    inner_polygons = list(polygonize(unary_union(inner_lines)))
    if not inner_polygons:
        return polygonal_geometry(outer_union)
    inner_union = unary_union(inner_polygons)
    return polygonal_geometry(outer_union.difference(inner_union))


def element_name(elements):
    """타일 조각 중 이름 tag가 있는 것을 우선 사용한다."""
    for element in elements:
        tags = element.get("tags", {})
        name = tags.get("name:ko") or tags.get("name")
        if name:
            return name
    return None


def interior_ring_count(geometry):
    """Polygon 또는 MultiPolygon이 보유한 interior ring 수를 반환한다."""
    if geometry is None or geometry.is_empty:
        return 0
    if geometry.geom_type == "Polygon":
        return len(geometry.interiors)
    if geometry.geom_type == "MultiPolygon":
        return sum(len(polygon.interiors) for polygon in geometry.geoms)
    return 0


def water_frame(elements):
    grouped = {}
    for element in elements:
        element_type = element.get("type")
        element_id = element.get("id")
        if element_type not in {"way", "relation"} or element_id is None:
            continue
        grouped.setdefault((element_type, element_id), []).append(element)

    records = []
    for (element_type, element_id), fragments in grouped.items():
        if element_type == "relation":
            geometry = relation_polygons(fragments)
        else:
            polygons = [normalize_polygon(element.get("geometry")) for element in fragments]
            polygons = [geometry for geometry in polygons if geometry is not None]
            geometry = polygonal_geometry(unary_union(polygons)) if polygons else None
        if geometry is None:
            continue
        records.append({
            "osm_id": f"{element_type}/{element_id}",
            "name": element_name(fragments),
            "geometry": geometry,
        })
    # 네트워크 실패 등으로 원본이 비었을 때도 아래 자체 검증까지 진행해 원인을
    # 명확히 출력한다. 빈 GeoDataFrame에 geometry column을 지정하면 예외가 난다.
    if not records:
        return pd.DataFrame(columns=["osm_id", "name", "geometry", "water_area_m2"])
    water = gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:4326")
    water["water_area_m2"] = water.to_crs("EPSG:5179").area
    water = water.loc[water["water_area_m2"] >= WATER_MIN_AREA_M2].copy()
    return water.sort_values("water_area_m2", ascending=False).reset_index(drop=True)


def peak_frame(elements):
    records = []
    for element in elements:
        tags = element.get("tags", {})
        records.append({
            "osm_id": element["id"], "name": tags.get("name:ko") or tags.get("name"),
            "lat": element.get("lat"), "lon": element.get("lon"), "ele_m": tags.get("ele"),
        })
    peaks = pd.DataFrame(records, columns=["osm_id", "name", "lat", "lon", "ele_m"])
    if peaks.empty:
        return peaks
    peaks = peaks.drop_duplicates(subset="osm_id").copy()
    for column in ["lat", "lon", "ele_m"]:
        peaks[column] = pd.to_numeric(peaks[column], errors="coerce")
    return peaks.sort_values(["ele_m", "name"], ascending=[False, True],
                             na_position="last").reset_index(drop=True)


# ============================================================================
# 3. 서울 열린데이터광장 정비사업 수집
# ============================================================================

def seoul_api_url(service, start, end):
    """키는 URL에 필요하지만 호출 로그에는 URL을 절대 출력하지 않는다."""
    key = quote(SEOUL_OPENAPI_KEY, safe="")
    return f"{SEOUL_OPENAPI_URL}/{key}/json/{service}/{start}/{end}"


def response_rows(payload, service):
    body = payload.get(service, {})
    if not isinstance(body, dict) or body.get("RESULT", {}).get("CODE") != "INFO-000":
        return None, None
    rows = body.get("row")
    return (rows, int(body.get("list_total_count", len(rows)))) if isinstance(rows, list) else (None, None)


def is_redevelop_schema(rows):
    """사업·구역 column을 요구해 주변/협력업체 데이터를 배제한다."""
    if not rows:
        return False
    keys = " ".join(str(key).lower() for key in rows[0])
    return any(marker in keys for marker in (
        "사업", "구역", "정비", "bsns", "biz", "zone", "area", "project",
    ))


def collect_redevelop():
    if not SEOUL_OPENAPI_KEY:
        print("  [미확보] .env에 SEOUL_OPENAPI_KEY 없음")
        return None, "SEOUL_OPENAPI_KEY 없음", 0
    cache_dir = raw_dir / "redevelop"
    cache_dir.mkdir(parents=True, exist_ok=True)
    calls, reasons = 0, []
    for service in REDEVELOP_SERVICE_CANDIDATES:
        cache_path = cache_dir / f"{service}_000001_001000.json"
        try:
            if cache_path.exists():
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
            else:
                calls += 1
                response = requests.get(seoul_api_url(service, 1, 1000), timeout=45,
                                        headers={"User-Agent": USER_AGENT})
                response.raise_for_status()
                payload = response.json()
                rows, _ = response_rows(payload, service)
                if rows is not None:
                    cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except (requests.RequestException, ValueError) as error:
            reasons.append(f"{service}: {mask_key(error)[:100]}")
            continue
        rows, total_count = response_rows(payload, service)
        if rows is None or not is_redevelop_schema(rows):
            reasons.append(f"{service}: 정비사업 구역 스키마 아님 또는 API 오류")
            continue
        all_rows = rows
        for start in range(1001, total_count + 1, 1000):
            end = min(start + 999, total_count)
            page_cache = cache_dir / f"{service}_{start:06d}_{end:06d}.json"
            try:
                if page_cache.exists():
                    page = json.loads(page_cache.read_text(encoding="utf-8"))
                else:
                    calls += 1
                    response = requests.get(seoul_api_url(service, start, end), timeout=45,
                                            headers={"User-Agent": USER_AGENT})
                    response.raise_for_status()
                    page = response.json()
                    page_cache.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
                page_rows, _ = response_rows(page, service)
                if page_rows is None:
                    raise ValueError("정상 row 응답이 아님")
                all_rows.extend(page_rows)
            except (requests.RequestException, ValueError) as error:
                reasons.append(f"{service}: 다음 페이지 실패 {mask_key(error)[:100]}")
                all_rows = None
                break
        if all_rows is not None:
            print(f"  채택 서비스: {service} / {len(all_rows):,}건")
            return all_rows, service, calls
    print("  [미확보] 적절한 서울 열린데이터광장 정비사업 구역 API를 찾지 못함")
    for reason in reasons:
        print(f"    - {reason}")
    return None, "적절한 API 서비스 미발견", calls


def first_value(row, aliases):
    normalized = {str(key).upper(): value for key, value in row.items()}
    for alias in aliases:
        value = normalized.get(alias.upper())
        if value not in (None, ""):
            return value
    return None


def parse_wkt_4326(value):
    """CRS 불명 projected WKT를 EPSG:4326으로 오인하지 않도록 범위를 확인한다."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        geometry = wkt.loads(value)
    except Exception:
        return None
    minx, miny, maxx, maxy = geometry.bounds
    if 126 <= minx <= 128 and 126 <= maxx <= 128 and 36 <= miny <= 38 and 36 <= maxy <= 38:
        return geometry.wkt
    return None


def redevelop_frame(rows):
    records = []
    for row in rows:
        lon = pd.to_numeric(first_value(row, ("LON", "LNG", "LONGITUDE", "X", "XCOORD", "X_COORD")),
                            errors="coerce")
        lat = pd.to_numeric(first_value(row, ("LAT", "LATITUDE", "Y", "YCOORD", "Y_COORD")),
                            errors="coerce")
        if not (126 <= lon <= 128 and 36 <= lat <= 38):
            lon, lat = np.nan, np.nan
        records.append({
            "사업구분": first_value(row, ("사업구분", "사업유형", "사업종류", "BSNS_SE", "BSNS_TYPE", "BIZ_TYPE")),
            "구역명": first_value(row, ("구역명", "사업명", "정비구역명", "BSNS_NM", "BIZ_NM", "ZONE_NM", "AREA_NM", "PROJECT_NM")),
            "자치구": first_value(row, ("자치구", "자치구명", "GU_NM", "SGG_NM", "SIGNGU_NM")),
            "진행단계": first_value(row, ("진행단계", "추진단계", "단계", "PRGRS_STEP", "STEP_NM", "PROGRESS")),
            "geometry": parse_wkt_4326(first_value(row, ("GEOMETRY", "GEOM", "WKT", "SPATIAL_DATA", "SHAPE"))),
            "lat": lat, "lon": lon,
            "주소": first_value(row, ("주소", "소재지", "위치", "ADDR", "ADDRESS", "LOC")),
        })
    return pd.DataFrame(records)


# ============================================================================
# 4. 실행·저장·자체 검증
# ============================================================================

print("===== 1. 수계 수집 (Overpass) =====")
water_elements, water_failures, water_calls, water_tiles = collect_overpass(
    "water", water_query, cache_version=WATER_QUERY_VERSION,
)
water = water_frame(water_elements)
print(f"  원본 {len(water_elements):,}건 / 면적 필터 후 {len(water):,}건 / 신규 호출 {water_calls}건")

print("\n===== 2. 산 봉우리 수집 (Overpass) =====")
peak_elements, peak_failures, peak_calls, peak_tiles = collect_overpass("peaks", peak_query)
peaks = peak_frame(peak_elements)
missing_ele_rate = peaks["ele_m"].isna().mean() * 100 if len(peaks) else 100.0
print(f"  봉우리 {len(peaks):,}건 / ele 결측률 {missing_ele_rate:.1f}% / 신규 호출 {peak_calls}건")

print("\n===== 3. 정비사업 구역 수집 (서울 열린데이터광장 API) =====")
redevelop_rows, redevelop_source, redevelop_calls = collect_redevelop()
redevelop = redevelop_frame(redevelop_rows) if redevelop_rows is not None else None
if redevelop is not None:
    print(f"  정규화 {len(redevelop):,}건 / polygon {int(redevelop['geometry'].notna().sum()):,}건 / "
          f"좌표 {int(redevelop[['lat', 'lon']].notna().all(axis=1).sum()):,}건")

print("\n===== 4. 저장 =====")
water_out = water[["osm_id", "name", "geometry", "water_area_m2"]].copy()
water_out["geometry"] = gpd.GeoSeries(water_out["geometry"], crs="EPSG:4326").to_wkt()
water_out.to_csv(WATER_PATH, sep="\t", index=False, lineterminator="\n", encoding="utf-8")
peaks.to_csv(PEAK_PATH, sep="\t", index=False, lineterminator="\n", encoding="utf-8")
print(f"  저장 {WATER_PATH} ({len(water_out):,}행)")
print(f"  저장 {PEAK_PATH} ({len(peaks):,}행)")
if redevelop is not None:
    redevelop.to_csv(REDEVELOP_PATH, sep="\t", index=False, lineterminator="\n", encoding="utf-8")
    print(f"  저장 {REDEVELOP_PATH} ({len(redevelop):,}행)")
else:
    print("  28.3.redevelop.txt 미생성 (정비사업 구역 데이터 미확보)")

print("\n===== 5. 자체 검증 =====")
water_names = water["name"].fillna("").astype(str)
han_river = water.loc[water_names.str.contains("한강")].sort_values(
    "water_area_m2", ascending=False,
)
han_river_main = han_river.iloc[0] if len(han_river) else None
han_river_area = han_river_main["water_area_m2"] if han_river_main is not None else np.nan
han_river_lon_width = (
    han_river_main.geometry.bounds[2] - han_river_main.geometry.bounds[0]
    if han_river_main is not None else np.nan
)
han_river_interior_ring_count = (
    interior_ring_count(han_river_main.geometry) if han_river_main is not None else 0
)
relation_water_count = int(water["osm_id"].str.startswith("relation/").sum())
major_mountains = {name: peaks["name"].fillna("").str.contains(name).any()
                   for name in ("남산", "북한산", "관악산")}
located_peaks = peaks.dropna(subset=["lat", "lon"])
checks = [
    ("수계 수집 타일 무실패", not water_failures, f"실패 {len(water_failures)} / {water_tiles}타일"),
    ("수계가 1건 이상", len(water) > 0, f"{len(water):,}건"),
    ("수계 geometry 전부 유효", bool(water.geometry.is_valid.all()) if len(water) else False,
     f"무효 {int((~water.geometry.is_valid).sum()) if len(water) else 0}건"),
    ("수계 면적 10,000m² 이상", bool((water["water_area_m2"] >= WATER_MIN_AREA_M2).all()) if len(water) else False,
     f"최소 {water['water_area_m2'].min() if len(water) else float('nan'):.1f}m²"),
    ("한강 명칭 수계가 1건 이상", len(han_river) >= 1, f"{len(han_river):,}건"),
    ("한강 폴리곤 면적 5km² 이상", bool(han_river_area >= HAN_RIVER_MIN_AREA_M2),
     f"{han_river_area:,.1f}m²"),
    ("한강 폴리곤 bbox 경도 폭 0.15도 이상",
     bool(han_river_lon_width >= HAN_RIVER_MIN_LON_WIDTH_DEG),
     f"{han_river_lon_width:.4f}도"),
    ("relation 유래 수계가 1건 이상", relation_water_count >= 1,
     f"{relation_water_count:,}건"),
    ("한강 폴리곤 interior ring이 1개 이상", han_river_interior_ring_count >= 1,
     f"{han_river_interior_ring_count:,}개"),
    ("봉우리 수집 타일 무실패", not peak_failures, f"실패 {len(peak_failures)} / {peak_tiles}타일"),
    ("봉우리가 1건 이상", len(peaks) > 0, f"{len(peaks):,}건"),
    ("봉우리 좌표가 서울 bbox 안",
     bool(located_peaks["lat"].between(SEOUL_BBOX[0], SEOUL_BBOX[2]).all()
          and located_peaks["lon"].between(SEOUL_BBOX[1], SEOUL_BBOX[3]).all()),
     f"좌표 보유 {len(located_peaks):,}건"),
    ("남산·북한산·관악산 확인", all(major_mountains.values()),
     ", ".join(f"{name}={found}" for name, found in major_mountains.items())),
    ("정비사업 산출 정책 일관", redevelop is None or len(redevelop) > 0,
     "미확보" if redevelop is None else f"{len(redevelop):,}건 ({redevelop_source})"),
]
all_passed = True
for label, passed, detail in checks:
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:<36} {detail}")
print(f"\n  ele 결측률: {missing_ele_rate:.1f}%")
print(f"  Overpass 신규 호출: 수계 {water_calls}건 / 봉우리 {peak_calls}건")
print(f"  서울 API 신규 호출: {redevelop_calls}건")
print(f"\n===== 조망 대상 수집 {'통과' if all_passed else '미통과'} =====")
assert all_passed, "자체 검증 실패"
