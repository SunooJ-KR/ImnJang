# ============================================================================
# 12.collect_osm.py
# ============================================================================
# Author:      yjkim
# Purpose:     서울 전역 OSM 건물 폴리곤과 생활 인프라 POI를 수집한다
# Description: D1에서 OSM(ODbL)만으로 아파트 동 높이 95.0%가 확보됨을 확인했다.
#              그 범위를 강남구에서 서울 전역으로 넓힌다.
#              - 건물: apartments/residential 전역 + landuse=residential(단지 경계)
#              - 초고층: 09시 태양고도 11.6도에서 150m 건물의 그림자는 약 720m다.
#                근거리 반경(206m) 밖이라도 차폐하므로 height>=150 건물은 전역 수집
#              - POI: plan 우선순위 1~3만. 4~6(야간상권·교통량·생활인구)은 §8.2 절단 대상
#              - 서울 bbox를 0.1도 타일로 쪼개 타일별 JSON 캐시를 남긴다.
#                Overpass 타임아웃·429가 나도 재실행하면 남은 타일만 받는다
#
#              통과 기준:
#                (1) 미수집 타일 0개
#                (2) 아파트 동 25,000개 이상 (D1 강남구 4,465동 기준 하한)
#                (3) 높이(height 태그 또는 building:levels) 확보율 >= 90%
#                (4) POI 카테고리 전부 1건 이상
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
from shapely.geometry import Polygon

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"
cache_dir = output_dir / "raw"

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "imnjang-prototype/0.1 (contest research)"

SEOUL_BBOX = (37.42, 126.76, 37.70, 127.19)   # south, west, north, east
TILE_DEG = 0.1
SLEEP_SEC = 5          # Overpass 공용 서버 예의. 줄이면 429가 돌아온다
FLOOR_HEIGHT_M = 2.8   # plan.md §6.1 가정. height 태그가 없을 때만 쓴다
TALL_BUILDING_M = 150

POI_FILTERS = {
    "subway": ['node["railway"="station"]["station"="subway"]',
               'node["railway"="subway_entrance"]'],
    "school": ['way["amenity"="school"]', 'node["amenity"="school"]'],
    "hospital": ['way["amenity"="hospital"]', 'node["amenity"="hospital"]'],
    "mart": ['node["shop"~"^(supermarket|department_store|mall)$"]',
             'way["shop"~"^(supermarket|department_store|mall)$"]'],
    "convenience": ['node["shop"="convenience"]'],
    "restaurant": ['node["amenity"~"^(restaurant|cafe)$"]'],
    "park": ['way["leisure"="park"]'],
}


# ============================================================================
# 1. Overpass 호출
# ============================================================================

def fetch_overpass(query):
    """User-Agent 없으면 406을 반환하므로 반드시 지정한다."""
    response = requests.post(
        OVERPASS_URL, data={"data": query}, timeout=300,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json()["elements"]


def tiles():
    south, west, north, east = SEOUL_BBOX
    lats = np.arange(south, north, TILE_DEG)
    lons = np.arange(west, east, TILE_DEG)
    return [(round(lat, 2), round(lon, 2),
             round(min(lat + TILE_DEG, north), 2), round(min(lon + TILE_DEG, east), 2))
            for lat in lats for lon in lons]


def building_query(tile):
    box = f"{tile[0]},{tile[1]},{tile[2]},{tile[3]}"
    return f"""
    [out:json][timeout:300];
    (
      way["building"~"^(apartments|residential)$"]({box});
      way["landuse"="residential"]["name"]({box});
      way["building"]["height"]({box});
    );
    out tags geom;
    """


def poi_query(tile):
    box = f"{tile[0]},{tile[1]},{tile[2]},{tile[3]}"
    clauses = "\n      ".join(
        f"{selector}({box});" for filters in POI_FILTERS.values() for selector in filters
    )
    return f"""
    [out:json][timeout:300];
    (
      {clauses}
    );
    out tags center;
    """


def collect(kind, query_builder):
    """타일별로 받아 캐시에 남긴다. 이미 있으면 건너뛴다."""
    (cache_dir / kind).mkdir(parents=True, exist_ok=True)
    tile_list = tiles()
    failures = []
    for index, tile in enumerate(tile_list, start=1):
        cache_path = cache_dir / kind / f"{tile[0]}_{tile[1]}.json"
        if cache_path.exists():
            continue
        print(f"수집 중 [{index}/{len(tile_list)}] {kind} {tile[0]},{tile[1]}")
        try:
            elements = fetch_overpass(query_builder(tile))
        except requests.exceptions.RequestException as error:
            failures.append({"kind": kind, "tile": str(tile), "error": str(error)[:150]})
            print(f"  [실패] {error}")
            continue
        cache_path.write_text(json.dumps(elements, ensure_ascii=False), encoding="utf-8")
        time.sleep(SLEEP_SEC)
    return failures, len(tile_list)


print("===== 1. OSM 수집 =====")
build_failures, n_tiles = collect("osm_building", building_query)
poi_failures, _ = collect("osm_poi", poi_query)
print(f"  타일 {n_tiles}개 / 실패 건물 {len(build_failures)} POI {len(poi_failures)}")


# ============================================================================
# 2. 건물 정규화
# ============================================================================

def load_cached(kind):
    elements = []
    for cache_path in sorted((cache_dir / kind).glob("*.json")):
        elements.extend(json.loads(cache_path.read_text(encoding="utf-8")))
    return elements


def parse_numeric(series):
    """'12', '12.5', '35 m' 같은 표기가 섞여 있다. 숫자만 남긴다."""
    return pd.to_numeric(
        series.astype("string").str.extract(r"(\d+\.?\d*)", expand=False), errors="coerce"
    )


print("\n===== 2. 건물 정규화 =====")
records = []
for element in load_cached("osm_building"):
    geometry = element.get("geometry")
    if not geometry or len(geometry) < 4:   # 3점 미만은 폴리곤이 아니다
        continue
    tags = element.get("tags", {})
    records.append({
        "osm_id": element["id"],
        "name": tags.get("name"),
        "building": tags.get("building"),
        "landuse": tags.get("landuse"),
        "levels_raw": tags.get("building:levels"),
        "height_raw": tags.get("height"),
        "flats_raw": tags.get("building:flats"),
        "start_date": tags.get("start_date"),
        "geometry": Polygon([(point["lon"], point["lat"]) for point in geometry]),
    })

osm = gpd.GeoDataFrame(records, crs="EPSG:4326").drop_duplicates(subset="osm_id")
osm["levels"] = parse_numeric(osm["levels_raw"])
osm["height_tag"] = parse_numeric(osm["height_raw"])
osm["flats"] = parse_numeric(osm["flats_raw"])
osm["build_year"] = parse_numeric(osm["start_date"])

# height 태그를 우선하고, 없을 때만 층수 x 2.8m로 추정한다 (D1 계획 수정사항)
osm["height_m"] = osm["height_tag"].fillna(osm["levels"] * FLOOR_HEIGHT_M)
osm["height_source"] = np.where(osm["height_tag"].notna(), "tag",
                                np.where(osm["levels"].notna(), "levels", "none"))

complexes = osm[osm["landuse"] == "residential"].copy()
buildings = osm[osm["landuse"] != "residential"].copy()
apartments = buildings[buildings["building"] == "apartments"].copy()
tall = buildings[buildings["height_m"] >= TALL_BUILDING_M].copy()

print(f"  건물 {len(buildings):,} (아파트 {len(apartments):,} / 150m 이상 {len(tall):,})")
print(f"  단지 경계(landuse) {len(complexes):,}")


# ============================================================================
# 3. POI 정규화
# ============================================================================

print("\n===== 3. POI 정규화 =====")

def classify(tags):
    """Overpass는 여러 필터의 합집합을 돌려주므로 태그로 카테고리를 되짚는다."""
    if tags.get("railway") in ("station", "subway_entrance"):
        return "subway"
    if tags.get("amenity") == "school":
        return "school"
    if tags.get("amenity") == "hospital":
        return "hospital"
    if tags.get("shop") in ("supermarket", "department_store", "mall"):
        return "mart"
    if tags.get("shop") == "convenience":
        return "convenience"
    if tags.get("amenity") in ("restaurant", "cafe"):
        return "restaurant"
    if tags.get("leisure") == "park":
        return "park"
    return None


poi_records = []
for element in load_cached("osm_poi"):
    center = element if "lat" in element else element.get("center")
    if not center:
        continue
    tags = element.get("tags", {})
    category = classify(tags)
    if category is None:
        continue
    poi_records.append({
        "osm_id": element["id"],
        "category": category,
        "name": tags.get("name"),
        "amenity": tags.get("amenity"),
        "lon": center["lon"],
        "lat": center["lat"],
        # 초품아 판정은 '초등학교' 여부에 달렸다. school 태그만으로는 중·고교와 섞인다
        "is_elementary": category == "school" and "초등학교" in (tags.get("name") or ""),
    })

if not poi_records:
    raise SystemExit("POI가 하나도 없다. Overpass 캐시를 확인할 것")
poi = pd.DataFrame(poi_records).drop_duplicates(subset=["osm_id", "category"])
print(poi.groupby("category").size().rename("n").reset_index().to_string(index=False))


# ============================================================================
# 4. 저장
# ============================================================================

# 폴리곤은 WKT로 저장한다. downstream은 shapely.wkt.loads로 되읽는다
building_out = buildings.drop(columns=["levels_raw", "height_raw", "flats_raw"]).copy()
building_out["geometry"] = building_out.geometry.to_wkt()
complex_out = complexes[["osm_id", "name", "geometry"]].copy()
complex_out["geometry"] = complex_out.geometry.to_wkt()

building_path = output_dir / "12.1.osm_buildings.txt"
complex_path = output_dir / "12.2.osm_complex_boundary.txt"
poi_path = output_dir / "12.3.osm_poi.txt"
pd.DataFrame(building_out).to_csv(building_path, sep="\t", index=False, lineterminator="\n")
pd.DataFrame(complex_out).to_csv(complex_path, sep="\t", index=False, lineterminator="\n")
poi.to_csv(poi_path, sep="\t", index=False, lineterminator="\n")


# ============================================================================
# 5. 자체 검증
# ============================================================================

print("\n===== 5. 판정 =====")

n_missing_tiles = sum(
    n_tiles - len(list((cache_dir / kind).glob("*.json"))) for kind in ("osm_building", "osm_poi")
)
height_rate = apartments["height_m"].notna().mean() * 100
empty_categories = [name for name in POI_FILTERS if name not in set(poi["category"])]

checks = [
    ("미수집 타일 0개", n_missing_tiles == 0, f"{n_missing_tiles}개"),
    ("아파트 동 25,000개 이상", len(apartments) >= 25_000, f"{len(apartments):,}동"),
    ("아파트 높이 확보 >= 90%", height_rate >= 90, f"{height_rate:.1f}%"),
    ("POI 카테고리 전부 존재", not empty_categories, f"빈 카테고리 {empty_categories or '없음'}"),
]
for label, passed, observed in checks:
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:<26} 실측 {observed}")

print(f"\n  높이 출처: {apartments['height_source'].value_counts().to_dict()}")
print(f"\n건물: {building_path}")
print(f"단지 경계: {complex_path}")
print(f"POI: {poi_path}")
