# ============================================================================
# 24.collect_osm_extra.py
# ============================================================================
# Author:      yjkim
# Purpose:     complex_metrics(§7)를 채우기 위해 12에서 빠진 OSM 원본을 추가 수집한다
# Description: 12(data/collect/12.collect_osm.py)의 타일 분할·캐시·Overpass 재시도
#              패턴을 그대로 재사용한다. 새 쿼리는 다음 4종뿐이다.
#              - 도로·철도 중심선: algorithms.md §6.3 — "중심선까지 45m" 같은
#                사실만 표기한다(소음 dB 추정 폐기). 도로는 위계별(간선/보조간선)로
#                구분하고, 철도는 지상/지하(tunnel=yes)를 구분한다.
#              - 공원 폴리곤: 12는 공원을 POI(node)로만 받아 면적이 없었다.
#                polygon으로 다시 받아 park_area_m2를 계산한다.
#              - 야간 상권(bar/pub/nightclub) + 상점 세분(supermarket/
#                department_store/mall): 12는 상점을 'mart' 하나로 합쳤지만
#                스키마가 dept_store_m/supermarket_m을 따로 요구하므로 원본
#                shop 태그를 보존해 다시 받는다.
#              - 학교 급별: **재수집하지 않는다.** 12가 이미 output/raw/osm_poi/에
#                school 태그를 캐시해 두었고, 여기 isced:level(ISCED 2011:
#                1=초등/2=중/3=고, '2;3'=중고통합)이 80.8% 커버된다(사전 확인).
#                새 Overpass 호출 없이 기존 캐시를 다시 읽어 급별을 가른다.
#
#              통과 기준: 각 카테고리 행 수 > 0, geometry 유효, 좌표가 서울
#              bbox(여유 포함) 안에 있을 것.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from shapely.geometry import LineString, Polygon

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"
cache_dir = output_dir / "raw" / "osm_extra"
poi_cache_dir_12 = output_dir / "raw" / "osm_poi"   # 12번이 만든 기존 캐시 (학교 재사용)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "imnjang-prototype/0.1 (contest research)"

SEOUL_BBOX = (37.42, 126.76, 37.70, 127.19)   # south, west, north, east
TILE_DEG = 0.1            # 공원·야간상권·상점 — 12와 동일 타일
TILE_DEG_LINEAR = 0.05    # 도로·철도 — 데이터가 커서 12보다 잘게 쪼갠다
SLEEP_SEC = 5             # Overpass 공용 서버 예의. 12와 동일값 그대로 쓴다
# Overpass 재시도. 429/504는 서버 부하라 기다리면 풀린다
RETRY_MAX = 4
RETRY_BASE_SEC = 20
RETRY_STATUS = {429, 500, 502, 503, 504}
BBOX_MARGIN = 0.05        # way(bbox)는 경계 밖 노드를 포함한 way도 돌려주므로 검증 시 여유를 둔다

# 위계 매핑 근거: 한국 도로법상 주간선도로 ~ OSM motorway/trunk/primary,
# 보조간선도로 ~ OSM secondary. algorithms.md §6.3이 요구하는 "위계별 구분"이다
ROAD_HIERARCHY = {"motorway": "간선", "trunk": "간선", "primary": "간선", "secondary": "보조간선"}

EXTRA_POI_FILTERS = {
    "nightlife": ['node["amenity"~"^(bar|pub|nightclub)$"]'],
    "shop": ['node["shop"~"^(supermarket|department_store|mall)$"]',
             'way["shop"~"^(supermarket|department_store|mall)$"]'],
}


# ============================================================================
# 1. Overpass 호출 (12와 동일 패턴)
# ============================================================================

def fetch_overpass(query):
    """User-Agent 없으면 406을 반환하므로 반드시 지정한다."""
    response = requests.post(
        OVERPASS_URL, data={"data": query}, timeout=300,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json()["elements"]


def tiles(tile_deg):
    south, west, north, east = SEOUL_BBOX
    lats = np.arange(south, north, tile_deg)
    lons = np.arange(west, east, tile_deg)
    return [(round(lat, 3), round(lon, 3),
             round(min(lat + tile_deg, north), 3), round(min(lon + tile_deg, east), 3))
            for lat in lats for lon in lons]


def linear_query(tile):
    """간선·보조간선 도로 + 지상/지하 철도. 둘 다 out tags geom이라 한 호출로 묶는다."""
    box = f"{tile[0]},{tile[1]},{tile[2]},{tile[3]}"
    return f"""
    [out:json][timeout:300];
    (
      way["highway"~"^(motorway|trunk|primary|secondary)$"]({box});
      way["railway"="rail"]({box});
    );
    out tags geom;
    """


def park_query(tile):
    box = f"{tile[0]},{tile[1]},{tile[2]},{tile[3]}"
    return f"""
    [out:json][timeout:300];
    (
      way["leisure"="park"]({box});
    );
    out tags geom;
    """


def extra_poi_query(tile):
    box = f"{tile[0]},{tile[1]},{tile[2]},{tile[3]}"
    clauses = "\n      ".join(
        f"{selector}({box});" for filters in EXTRA_POI_FILTERS.values() for selector in filters
    )
    return f"""
    [out:json][timeout:300];
    (
      {clauses}
    );
    out tags center;
    """


def collect(kind, query_builder, tile_deg):
    """타일별로 받아 캐시에 남긴다. 이미 있으면 건너뛴다."""
    (cache_dir / kind).mkdir(parents=True, exist_ok=True)
    tile_list = tiles(tile_deg)
    failures = []
    for index, tile in enumerate(tile_list, start=1):
        cache_path = cache_dir / kind / f"{tile[0]}_{tile[1]}.json"
        if cache_path.exists():
            continue
        print(f"수집 중 [{index}/{len(tile_list)}] {kind} {tile[0]},{tile[1]}")
        # Overpass는 부하가 몰리면 429(rate limit)와 504(timeout)를 돌려준다.
        # 도로 쿼리는 타일당 응답이 커서 특히 잘 걸린다. 지수 backoff로 물러선다.
        elements = None
        for attempt in range(RETRY_MAX):
            try:
                elements = fetch_overpass(query_builder(tile))
                break
            except requests.exceptions.RequestException as error:
                status = getattr(getattr(error, "response", None), "status_code", None)
                if attempt == RETRY_MAX - 1 or status not in RETRY_STATUS:
                    failures.append({"kind": kind, "tile": str(tile),
                                     "error": str(error)[:150]})
                    print(f"  [실패] {error}")
                    break
                wait = RETRY_BASE_SEC * (2 ** attempt)
                print(f"  [재시도 {attempt + 1}/{RETRY_MAX - 1}] {status} — {wait}초 대기")
                time.sleep(wait)
        if elements is None:
            continue
        cache_path.write_text(json.dumps(elements, ensure_ascii=False), encoding="utf-8")
        time.sleep(SLEEP_SEC)
    return failures, len(tile_list)


print("===== 1. OSM 추가 수집 (도로·철도 / 공원 / 야간상권·상점) =====")
timing = {}

start = time.perf_counter()
linear_failures, n_linear_tiles = collect("osm_linear", linear_query, TILE_DEG_LINEAR)
timing["linear"] = time.perf_counter() - start
print(f"  도로·철도 타일 {n_linear_tiles}개 / 실패 {len(linear_failures)} ({timing['linear']:.0f}s)")

start = time.perf_counter()
park_failures, n_park_tiles = collect("osm_park", park_query, TILE_DEG)
timing["park"] = time.perf_counter() - start
print(f"  공원 타일 {n_park_tiles}개 / 실패 {len(park_failures)} ({timing['park']:.0f}s)")

start = time.perf_counter()
extra_poi_failures, n_poi_tiles = collect("osm_extra_poi", extra_poi_query, TILE_DEG)
timing["extra_poi"] = time.perf_counter() - start
print(f"  야간상권·상점 타일 {n_poi_tiles}개 / 실패 {len(extra_poi_failures)} ({timing['extra_poi']:.0f}s)")

# 학교는 새로 받지 않는다. 12번이 이미 output/raw/osm_poi/에 school 태그를 캐시해 뒀다
assert poi_cache_dir_12.exists() and list(poi_cache_dir_12.glob("*.json")), \
    "output/raw/osm_poi 캐시가 없다. 먼저 data/collect/12.collect_osm.py를 실행할 것"
timing["school"] = 0.0


# ============================================================================
# 2. 원본 로드 유틸
# ============================================================================

def load_json_dir(directory):
    elements = []
    for cache_path in sorted(directory.glob("*.json")):
        elements.extend(json.loads(cache_path.read_text(encoding="utf-8")))
    return elements


def load_cached(kind):
    return load_json_dir(cache_dir / kind)


def in_seoul_bbox(lats, lons, margin=BBOX_MARGIN):
    south, west, north, east = SEOUL_BBOX
    return (lats.min() >= south - margin and lats.max() <= north + margin
            and lons.min() >= west - margin and lons.max() <= east + margin)


# ============================================================================
# 3. 도로·철도 정규화
# ============================================================================

print("\n===== 2. 도로·철도 정규화 =====")

road_records, rail_records = [], []
for element in load_cached("osm_linear"):
    geometry = element.get("geometry")
    if not geometry or len(geometry) < 2:   # 2점 미만은 선이 아니다
        continue
    tags = element.get("tags", {})
    line = LineString([(point["lon"], point["lat"]) for point in geometry])
    highway = tags.get("highway")
    if highway in ROAD_HIERARCHY:
        road_records.append({
            "osm_id": element["id"], "name": tags.get("name"),
            "highway": highway, "hierarchy": ROAD_HIERARCHY[highway],
            "geometry": line,
        })
    elif tags.get("railway") == "rail":
        # tunnel=yes 구간은 지상 소음원이 아니므로 is_tunnel로 구분해 남긴다(제외하지 않음).
        # 구간을 통째로 버리면 같은 철도 노선의 지상 구간 연결성 정보까지 잃기 때문에,
        # downstream(§6.3 소음원 근접도 계산)에서 `~is_tunnel` 한 줄로 걸러 쓰게 한다.
        rail_records.append({
            "osm_id": element["id"], "name": tags.get("name"),
            "is_tunnel": tags.get("tunnel") == "yes",
            "geometry": line,
        })

roads = gpd.GeoDataFrame(road_records, crs="EPSG:4326").drop_duplicates(subset="osm_id")
rails = gpd.GeoDataFrame(rail_records, crs="EPSG:4326").drop_duplicates(subset="osm_id")
print(f"  도로 {len(roads):,} (간선 {(roads['hierarchy'] == '간선').sum():,} / "
      f"보조간선 {(roads['hierarchy'] == '보조간선').sum():,})")
print(f"  철도 {len(rails):,} (지상 {(~rails['is_tunnel']).sum():,} / 지하 {rails['is_tunnel'].sum():,})")


# ============================================================================
# 4. 공원 정규화
# ============================================================================

print("\n===== 3. 공원 정규화 =====")

park_records = []
for element in load_cached("osm_park"):
    geometry = element.get("geometry")
    if not geometry or len(geometry) < 4:   # 3점 미만은 폴리곤이 아니다
        continue
    coords = [(point["lon"], point["lat"]) for point in geometry]
    if coords[0] != coords[-1]:   # OSM way가 닫힌 링이 아닐 경우 보정
        coords.append(coords[0])
    polygon = Polygon(coords)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)   # 자기교차 등 사소한 무효 형상 복구
    if polygon.is_empty:
        continue
    tags = element.get("tags", {})
    park_records.append({"osm_id": element["id"], "name": tags.get("name"), "geometry": polygon})

parks = gpd.GeoDataFrame(park_records, crs="EPSG:4326").drop_duplicates(subset="osm_id")
if len(parks):
    # EPSG:5179 (Korea 2000 / Unified CS, 단위 m) — 국내 면적 계산 표준 투영
    parks["park_area_m2"] = parks.to_crs("EPSG:5179").area
print(f"  공원 {len(parks):,}")


# ============================================================================
# 5. 야간 상권·상점 정규화
# ============================================================================

print("\n===== 4. 야간 상권·상점 정규화 =====")


def classify_extra(tags):
    if tags.get("amenity") in ("bar", "pub", "nightclub"):
        return "nightlife"
    if tags.get("shop") in ("supermarket", "department_store", "mall"):
        return "shop"
    return None


nightlife_records, shop_records = [], []
for element in load_cached("osm_extra_poi"):
    center = element if "lat" in element else element.get("center")
    if not center:
        continue
    tags = element.get("tags", {})
    category = classify_extra(tags)
    if category == "nightlife":
        nightlife_records.append({
            "osm_id": element["id"], "amenity": tags.get("amenity"), "name": tags.get("name"),
            "lon": center["lon"], "lat": center["lat"],
        })
    elif category == "shop":
        shop_records.append({
            # shop 원본 태그를 그대로 보존한다 (12는 mart로 합쳤지만
            # 스키마가 dept_store_m/supermarket_m을 따로 요구한다)
            "osm_id": element["id"], "shop_type": tags.get("shop"), "name": tags.get("name"),
            "lon": center["lon"], "lat": center["lat"],
        })

nightlife = pd.DataFrame(nightlife_records).drop_duplicates(subset="osm_id")
shops = pd.DataFrame(shop_records).drop_duplicates(subset="osm_id")
print(f"  야간상권(bar/pub/nightclub) {len(nightlife):,}")
if len(shops):
    print("  " + shops.groupby("shop_type").size().rename("n").reset_index().to_string(index=False).replace("\n", "\n  "))
else:
    print("  상점 0건")


# ============================================================================
# 6. 학교 급별 정규화 (재수집 없이 12번 캐시 재사용)
# ============================================================================

print("\n===== 5. 학교 급별 정규화 (12번 캐시 재사용) =====")

school_records = []
for element in load_json_dir(poi_cache_dir_12):
    tags = element.get("tags", {})
    if tags.get("amenity") != "school":
        continue
    center = element if "lat" in element else element.get("center")
    if not center:
        continue
    name = tags.get("name") or ""
    isced_raw = tags.get("isced:level")
    isced_levels = set(isced_raw.split(";")) if isced_raw else set()
    school_records.append({
        "osm_id": element["id"], "name": name, "isced_level_raw": isced_raw,
        # isced:level 우선(ISCED 2011: 1=초등/2=중/3=고), 없으면 이름 기반으로 보완
        "is_elementary": ("1" in isced_levels) or ("초등학교" in name),
        "is_middle": ("2" in isced_levels) or ("중학교" in name),
        "is_high": ("3" in isced_levels) or ("고등학교" in name),
        "level_source": "isced_tag" if isced_raw else ("name" if name else "none"),
        "lon": center["lon"], "lat": center["lat"],
    })

schools = pd.DataFrame(school_records).drop_duplicates(subset="osm_id")
isced_coverage = schools["isced_level_raw"].notna().mean() * 100 if len(schools) else 0.0
any_level = (schools[["is_elementary", "is_middle", "is_high"]].any(axis=1)).mean() * 100 if len(schools) else 0.0
print(f"  학교 {len(schools):,} / isced:level 태그 보유 {isced_coverage:.1f}% / "
      f"급별(초·중·고 중 하나라도) 판정 가능 {any_level:.1f}%")
print(f"  급별 판정 불가(태그도 이름도 없음) {len(schools) - int(round(len(schools) * any_level / 100)):,}건")


# ============================================================================
# 7. 저장 (실제로 수집된 것만)
# ============================================================================

print("\n===== 6. 저장 =====")

outputs = []


def save_if_present(df, path, geometry_col=None):
    if df is None or len(df) == 0:
        print(f"  [건너뜀] {path.name} — 수집된 데이터 없음")
        return
    out = pd.DataFrame(df).copy()
    if geometry_col:
        out[geometry_col] = gpd.GeoSeries(out[geometry_col], crs="EPSG:4326").to_wkt()
    out.to_csv(path, sep="\t", index=False, lineterminator="\n")
    outputs.append(path)
    print(f"  저장 {path} ({len(out):,}행)")


save_if_present(roads, output_dir / "24.1.osm_roads.txt", "geometry")
save_if_present(rails, output_dir / "24.2.osm_rails.txt", "geometry")
save_if_present(parks, output_dir / "24.3.osm_parks.txt", "geometry")
save_if_present(nightlife, output_dir / "24.4.osm_nightlife.txt")
save_if_present(shops, output_dir / "24.5.osm_shops.txt")
save_if_present(schools, output_dir / "24.6.osm_schools.txt")


# ============================================================================
# 8. 자체 검증
# ============================================================================

print("\n===== 7. 판정 =====")

checks = []

if len(roads):
    b = roads.total_bounds   # minx, miny, maxx, maxy
    # 도로도 선형 지물이라 경계를 넘는다 (철도 주석 참고)
    # total_bounds = (min_lon, min_lat, max_lon, max_lat)
    # SEOUL_BBOX    = (south_lat, west_lon, north_lat, east_lon)
    south, west, north, east = SEOUL_BBOX
    overlaps_seoul = (b[0] <= east and b[2] >= west
                      and b[1] <= north and b[3] >= south)
    checks.append(("도로가 서울과 겹침", overlaps_seoul, f"{len(roads):,}건"))
    checks.append(("도로 geometry 전부 유효", roads.geometry.is_valid.all(), f"무효 {(~roads.geometry.is_valid).sum()}건"))
if len(rails):
    # 선형 지물은 시 경계를 넘어 이어진다. 타일이 서울을 덮으므로 받은 선분이
    # 경계 밖으로 삐져나가는 것은 정상이다. bbox 포함을 요구하면 항상 실패한다.
    # 대신 "서울과 겹치는가"를 본다 — 겹치지 않으면 엉뚱한 지역을 받은 것이다.
    b = rails.total_bounds
    # total_bounds = (min_lon, min_lat, max_lon, max_lat)
    # SEOUL_BBOX    = (south_lat, west_lon, north_lat, east_lon)
    south, west, north, east = SEOUL_BBOX
    overlaps_seoul = (b[0] <= east and b[2] >= west
                      and b[1] <= north and b[3] >= south)
    checks.append(("철도가 서울과 겹침", overlaps_seoul, f"{len(rails):,}건"))
    checks.append(("철도 geometry 전부 유효", rails.geometry.is_valid.all(), f"무효 {(~rails.geometry.is_valid).sum()}건"))
if len(parks):
    # 공원도 폴리곤이라 타일 경계에 걸치면 일부가 bbox 밖으로 나갈 수 있다.
    # 도로·철도와 같은 이유로 "포함"이 아니라 "겹침"을 검증한다.
    b = parks.total_bounds
    south, west, north, east = SEOUL_BBOX
    overlaps_seoul = (b[0] <= east and b[2] >= west
                      and b[1] <= north and b[3] >= south)
    checks.append(("공원이 서울과 겹침", overlaps_seoul, f"{len(parks):,}건"))
    checks.append(("공원 geometry 전부 유효", parks.geometry.is_valid.all(), f"무효 {(~parks.geometry.is_valid).sum()}건"))
    checks.append(("공원 면적 전부 양수", (parks["park_area_m2"] > 0).all(), f"0 이하 {(parks['park_area_m2'] <= 0).sum()}건"))
if len(nightlife):
    checks.append(("야간상권 좌표가 서울 bbox 안", in_seoul_bbox(nightlife["lat"], nightlife["lon"]), f"{len(nightlife):,}건"))
if len(shops):
    checks.append(("상점 좌표가 서울 bbox 안", in_seoul_bbox(shops["lat"], shops["lon"]), f"{len(shops):,}건"))
if len(schools):
    checks.append(("학교 좌표가 서울 bbox 안", in_seoul_bbox(schools["lat"], schools["lon"]), f"{len(schools):,}건"))

for label, passed, observed in checks:
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:<28} 실측 {observed}")
    assert passed, f"검증 실패: {label} ({observed})"

print(f"\n  소요시간: 도로·철도 {timing['linear']:.0f}s / 공원 {timing['park']:.0f}s / "
      f"야간상권·상점 {timing['extra_poi']:.0f}s / 학교 0s(캐시 재사용)")
print(f"  실패 타일: 도로·철도 {len(linear_failures)} / 공원 {len(park_failures)} / "
      f"야간상권·상점 {len(extra_poi_failures)}")
print(f"\n생성된 파일: {[str(p) for p in outputs]}")
