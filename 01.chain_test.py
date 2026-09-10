# ============================================================================
# 01.chain_test.py
# ============================================================================
# Author:      yjkim
# Purpose:     실거래 <-> 단지 <-> 동 폴리곤 연결 사슬의 OSM 구간을 실측 검증
# Description: D1 검증. 계획서(plan.md) §10 D3에서 최대 난관으로 지목된
#              "단지 <-> 건물 폴리곤 매칭"을 OSM 데이터만으로 성립시킬 수
#              있는지 확인한다.
#              - landuse=residential(단지) 폴리곤과 building=apartments(동)를 수집
#              - 공간 포함 판정으로 동을 단지에 배정 (이름 퍼지 매칭 없이)
#              - 실거래 aptNm과 붙일 조인 키 후보(단지명/법정동/세대수/준공년도) 확보율 측정
#              - horizon 엔진 입력(동 폴리곤 + 층수)의 결측률 측정
#
#              통과 기준:
#                (1) 동 -> 단지 배정률 >= 80%
#                (2) 단지 중 세대수·준공년도 교차검증 키를 가진 비율 >= 50%
#                (3) 동 중 층수 확보(직접 or 추정) 비율 >= 90%
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import re
import sys
import time
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import geopandas as gpd
from shapely.geometry import Polygon

work_dir = Path(__file__).parent
output_dir = work_dir / "output"
output_dir.mkdir(exist_ok=True)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "imnjang-prototype/0.1 (contest research)"

# 강남구. 서울 전역 중 아파트 밀도가 높고 단지 형태가 다양해 1차 검증에 적합
TARGET_NAME = "강남구"
TARGET_BBOX = (37.4600, 127.0100, 37.5400, 127.1200)  # south, west, north, east

FLOOR_HEIGHT_M = 2.8  # plan.md §6.1 가정


# ============================================================================
# 1. OSM 데이터 수집
# ============================================================================

def fetch_overpass(query):
    """Overpass API 호출. User-Agent 없으면 406을 반환하므로 반드시 지정한다."""
    response = requests.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=180,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    return response.json()["elements"]


def build_query(bbox):
    south, west, north, east = bbox
    box = f"{south},{west},{north},{east}"
    return f"""
    [out:json][timeout:180];
    (
      way["building"~"^(apartments|residential)$"]({box});
      way["landuse"="residential"]["name"]({box});
    );
    out tags geom;
    """


def elements_to_gdf(elements, kind):
    """Overpass way 요소를 GeoDataFrame으로 변환. 3점 미만 도형은 폴리곤이 아니므로 제외."""
    records = []
    for element in elements:
        geometry = element.get("geometry")
        if not geometry or len(geometry) < 4:
            continue
        coords = [(point["lon"], point["lat"]) for point in geometry]
        tags = element.get("tags", {})
        records.append({
            "osm_id": element["id"],
            "name": tags.get("name"),
            "levels_raw": tags.get("building:levels"),
            "height_raw": tags.get("height"),
            "flats_raw": tags.get("building:flats"),
            "start_date": tags.get("start_date"),
            "district": tags.get("addr:district"),
            "subdistrict": tags.get("addr:subdistrict"),
            "geometry": Polygon(coords),
        })
    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
    print(f"  {kind}: {len(gdf)}개")
    return gdf


print(f"===== 1. OSM 수집 ({TARGET_NAME}) =====")
start_time = time.time()
elements = fetch_overpass(build_query(TARGET_BBOX))
print(f"  Overpass 응답 {len(elements)}건, {time.time() - start_time:.1f}초")

building_elements = [e for e in elements if e.get("tags", {}).get("building")]
complex_elements = [e for e in elements if e.get("tags", {}).get("landuse") == "residential"]

buildings = elements_to_gdf(building_elements, "동 폴리곤(building)")
complexes = elements_to_gdf(complex_elements, "단지 폴리곤(landuse)")

if len(buildings) == 0 or len(complexes) == 0:
    sys.exit("수집 실패: 폴리곤이 비어 있음")


# ============================================================================
# 2. 수치 필드 정규화
# ============================================================================

def parse_numeric(series):
    """'19', '19.0', '15;16' 같은 OSM 값에서 첫 숫자만 취한다."""
    extracted = series.astype("string").str.extract(r"(\d+(?:\.\d+)?)", expand=False)
    return pd.to_numeric(extracted, errors="coerce")


buildings["levels"] = parse_numeric(buildings["levels_raw"])
buildings["height"] = parse_numeric(buildings["height_raw"])
buildings["flats"] = parse_numeric(buildings["flats_raw"])
buildings["built_year"] = parse_numeric(buildings["start_date"])

# 층수 결측 시 height로 역산. horizon 엔진은 층수가 아니라 높이만 필요하므로
# 둘 중 하나만 있으면 관측점을 만들 수 있다.
buildings["levels_filled"] = buildings["levels"].fillna(
    (buildings["height"] / FLOOR_HEIGHT_M).round()
)
buildings["height_filled"] = buildings["height"].fillna(
    buildings["levels"] * FLOOR_HEIGHT_M
)

print("\n===== 2. 동 폴리곤 필드 확보율 =====")
n_buildings = len(buildings)
field_coverage = pd.DataFrame([
    {"field": "building:levels", "n": int(buildings["levels"].notna().sum())},
    {"field": "height", "n": int(buildings["height"].notna().sum())},
    {"field": "levels or height (horizon 입력)", "n": int(buildings["height_filled"].notna().sum())},
    {"field": "building:flats (세대수)", "n": int(buildings["flats"].notna().sum())},
    {"field": "start_date (준공년도)", "n": int(buildings["built_year"].notna().sum())},
    {"field": "name (동 번호)", "n": int(buildings["name"].notna().sum())},
]).assign(pct=lambda x: (100 * x["n"] / n_buildings).round(1))
print(field_coverage.to_string(index=False))


# ============================================================================
# 3. 공간 포함 판정으로 동 -> 단지 배정
# ============================================================================
# 이름 퍼지 매칭 없이 기하로만 붙인다. 단지 폴리곤이 중첩되는 경우가 있어
# 동 중심점이 들어가는 단지 중 면적이 가장 작은 것을 택한다(가장 구체적인 경계).

print("\n===== 3. 동 -> 단지 배정 (공간 포함) =====")

METRIC_CRS = "EPSG:5179"  # UTM-K. 거리·면적 연산은 반드시 투영좌표계에서 한다
buildings_m = buildings.to_crs(METRIC_CRS)
complexes_m = complexes.to_crs(METRIC_CRS)

complexes_m["complex_area_m2"] = complexes_m.geometry.area
buildings_m["building_area_m2"] = buildings_m.geometry.area
buildings_m["centroid"] = buildings_m.geometry.centroid

centroids = buildings_m.set_geometry("centroid")[["osm_id", "centroid"]]
joined = gpd.sjoin(
    centroids,
    complexes_m[["osm_id", "name", "complex_area_m2", "geometry"]].rename(
        columns={"osm_id": "complex_osm_id", "name": "complex_name"}
    ),
    how="left",
    predicate="within",
)

# 중첩 단지: 면적이 가장 작은 단지에 배정
joined = (joined
    .sort_values("complex_area_m2")
    .drop_duplicates(subset="osm_id", keep="first")
    [["osm_id", "complex_osm_id", "complex_name"]]
)

buildings_m = buildings_m.merge(joined, on="osm_id", how="left")

n_assigned = int(buildings_m["complex_osm_id"].notna().sum())
assign_rate = 100 * n_assigned / n_buildings
print(f"  배정 성공: {n_assigned}/{n_buildings} ({assign_rate:.1f}%)")

unassigned = buildings_m[buildings_m["complex_osm_id"].isna()]
print(f"  미배정 동 평균 면적: {unassigned['building_area_m2'].mean():.0f}m2 "
      f"(배정된 동: {buildings_m[buildings_m['complex_osm_id'].notna()]['building_area_m2'].mean():.0f}m2)")


# ============================================================================
# 4. 단지 레벨 집계 및 실거래 조인 키 확보율
# ============================================================================
# 실거래 데이터와 붙일 때 쓸 교차검증 키가 실제로 얼마나 확보되는지 본다.
# 단지명만으로 매칭하면 위험하므로 세대수·준공년도·법정동이 함께 있어야 한다.

print("\n===== 4. 단지 집계 및 조인 키 확보율 =====")

assigned = buildings_m[buildings_m["complex_osm_id"].notna()].copy()
complex_summary = (assigned
    .groupby(["complex_osm_id", "complex_name"], dropna=False)
    .agg(
        n_buildings=("osm_id", "count"),
        n_with_height=("height_filled", lambda s: int(s.notna().sum())),
        total_flats=("flats", "sum"),
        n_with_flats=("flats", lambda s: int(s.notna().sum())),
        built_year=("built_year", "median"),
        max_levels=("levels_filled", "max"),
        subdistrict=("subdistrict", lambda s: s.dropna().iloc[0] if s.notna().any() else None),
    )
    .reset_index()
)

# 세대수는 모든 동에 flats가 있어야 합계가 신뢰 가능하다
complex_summary["flats_complete"] = (
    complex_summary["n_with_flats"] == complex_summary["n_buildings"]
)
complex_summary["height_complete"] = (
    complex_summary["n_with_height"] == complex_summary["n_buildings"]
)

n_complexes = len(complex_summary)
key_coverage = pd.DataFrame([
    {"join_key": "단지명", "n": int(complex_summary["complex_name"].notna().sum())},
    {"join_key": "법정동(subdistrict)", "n": int(complex_summary["subdistrict"].notna().sum())},
    {"join_key": "세대수 합계(전 동 완비)", "n": int(complex_summary["flats_complete"].sum())},
    {"join_key": "준공년도", "n": int(complex_summary["built_year"].notna().sum())},
    {"join_key": "전 동 높이 확보(horizon 가능)", "n": int(complex_summary["height_complete"].sum())},
]).assign(pct=lambda x: (100 * x["n"] / n_complexes).round(1))
print(f"  단지 수: {n_complexes}")
print(key_coverage.to_string(index=False))

# 교차검증 가능 단지 = 단지명 + 법정동 + (세대수 또는 준공년도)
verifiable = complex_summary[
    complex_summary["complex_name"].notna()
    & complex_summary["subdistrict"].notna()
    & (complex_summary["flats_complete"] | complex_summary["built_year"].notna())
]
verify_rate = 100 * len(verifiable) / n_complexes
print(f"\n  교차검증 가능 단지: {len(verifiable)}/{n_complexes} ({verify_rate:.1f}%)")


# ============================================================================
# 5. 통과 기준 판정
# ============================================================================

print("\n===== 5. 통과 기준 판정 =====")

horizon_ready_rate = 100 * buildings_m["height_filled"].notna().sum() / n_buildings

checks = [
    ("동 -> 단지 배정률 >= 80%", assign_rate, 80.0),
    ("교차검증 가능 단지 >= 50%", verify_rate, 50.0),
    ("동 높이 확보율 >= 90%", horizon_ready_rate, 90.0),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:32s} 실측 {value:.1f}%")


# ============================================================================
# 6. 결과 저장
# ============================================================================

complex_path = output_dir / "01.1.complex_summary.txt"
building_path = output_dir / "01.2.building_assigned.txt"
sample_path = output_dir / "01.3.complex_name_sample.txt"

complex_summary.to_csv(complex_path, sep="\t", index=False, lineterminator="\n")
(buildings_m
    .drop(columns=["geometry", "centroid"])
    .to_csv(building_path, sep="\t", index=False, lineterminator="\n")
)
# 실거래 aptNm과 대조할 단지명 표본. 사람이 눈으로 확인하는 용도
(complex_summary
    .sort_values("n_buildings", ascending=False)
    [["complex_name", "subdistrict", "n_buildings", "total_flats", "built_year", "max_levels"]]
    .head(50)
    .to_csv(sample_path, sep="\t", index=False, lineterminator="\n")
)

print(f"\n단지 집계: {complex_path}")
print(f"동 배정 결과: {building_path}")
print(f"단지명 표본: {sample_path}")

print(f"\n===== 사슬 검증 {'통과' if all_passed else '미통과'} =====")
