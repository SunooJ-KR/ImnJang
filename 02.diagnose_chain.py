# ============================================================================
# 02.diagnose_chain.py
# ============================================================================
# Author:      yjkim
# Purpose:     01.chain_test.py에서 실패한 두 기준의 원인을 분해한다
# Description: 01에서 배정률 44.4%, 높이 확보율 73.7%로 기준 미달이 나왔다.
#              두 지표 모두 분모가 잘못됐을 가능성이 있어 원인을 나눠 본다.
#              - 가설 A: 미배정 동 대부분은 building=residential(빌라/다세대)이며
#                        애초에 아파트 단지 소속이 아니다 -> 분모에서 빼야 한다
#              - 가설 B: 미배정 아파트는 단지 폴리곤 경계 밖에 살짝 걸쳐 있다
#                        -> 근접 거리로 구제 가능한지 본다
#              - 가설 C: 높이 결측은 단지 내 다른 동의 층수로 대체 가능하다
#                        (같은 단지 동들은 층수가 비슷하다)
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
import time
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
TARGET_BBOX = (37.4600, 127.0100, 37.5400, 127.1200)
METRIC_CRS = "EPSG:5179"
FLOOR_HEIGHT_M = 2.8


# ============================================================================
# 1. 수집 (01과 동일하되 building 태그 값을 보존)
# ============================================================================

def fetch_overpass(query, cache_path):
    """Overpass는 반복 호출 시 504로 막히므로 응답을 디스크에 캐시한다."""
    if cache_path.exists():
        print(f"  캐시 사용: {cache_path.name}")
        return json.loads(cache_path.read_text(encoding="utf-8"))["elements"]

    last_error = None
    for attempt in range(3):
        try:
            response = requests.post(
                OVERPASS_URL, data={"data": query}, timeout=300,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            cache_path.write_text(response.text, encoding="utf-8")
            return response.json()["elements"]
        except requests.exceptions.RequestException as error:
            last_error = error
            print(f"  실패 {attempt + 1}/3 ({error}). 30초 후 재시도")
            time.sleep(30)
    raise RuntimeError(f"Overpass 호출 실패: {last_error}")


south, west, north, east = TARGET_BBOX
box = f"{south},{west},{north},{east}"
query = f"""
[out:json][timeout:180];
(
  way["building"~"^(apartments|residential)$"]({box});
  way["landuse"="residential"]["name"]({box});
);
out tags geom;
"""

print("===== 1. 수집 =====")
start_time = time.time()
elements = fetch_overpass(query, output_dir / "cache_overpass_gangnam.json")
print(f"  {len(elements)}건, {time.time() - start_time:.1f}초")


def to_records(elements, want_building):
    records = []
    for element in elements:
        tags = element.get("tags", {})
        is_building = "building" in tags
        if is_building != want_building:
            continue
        geometry = element.get("geometry")
        if not geometry or len(geometry) < 4:
            continue
        records.append({
            "osm_id": element["id"],
            "building_type": tags.get("building"),
            "name": tags.get("name"),
            "levels": tags.get("building:levels"),
            "height": tags.get("height"),
            "subdistrict": tags.get("addr:subdistrict"),
            "geometry": Polygon([(p["lon"], p["lat"]) for p in geometry]),
        })
    return gpd.GeoDataFrame(records, crs="EPSG:4326").to_crs(METRIC_CRS)


buildings = to_records(elements, want_building=True)
complexes = to_records(elements, want_building=False)


def parse_numeric(series):
    extracted = series.astype("string").str.extract(r"(\d+(?:\.\d+)?)", expand=False)
    return pd.to_numeric(extracted, errors="coerce")


buildings["levels_num"] = parse_numeric(buildings["levels"])
buildings["height_num"] = parse_numeric(buildings["height"])
buildings["height_m"] = buildings["height_num"].fillna(buildings["levels_num"] * FLOOR_HEIGHT_M)
buildings["area_m2"] = buildings.geometry.area


# ============================================================================
# 2. 가설 A - building 태그별로 나눠 본다
# ============================================================================

print("\n===== 2. 가설 A: building 태그별 분해 =====")

complexes["complex_area_m2"] = complexes.geometry.area
centroids = buildings[["osm_id", "geometry"]].copy()
centroids["geometry"] = buildings.geometry.centroid

joined = gpd.sjoin(
    centroids,
    complexes[["osm_id", "name", "complex_area_m2", "geometry"]].rename(
        columns={"osm_id": "complex_osm_id", "name": "complex_name"}
    ),
    how="left", predicate="within",
)
joined = (joined
    .sort_values("complex_area_m2")
    .drop_duplicates(subset="osm_id", keep="first")
    [["osm_id", "complex_osm_id", "complex_name"]]
)
buildings = buildings.merge(joined, on="osm_id", how="left")
buildings["assigned"] = buildings["complex_osm_id"].notna()

by_type = (buildings
    .groupby("building_type")
    .agg(
        n=("osm_id", "count"),
        assigned=("assigned", "sum"),
        median_area=("area_m2", "median"),
        median_levels=("levels_num", "median"),
    )
    .assign(assign_pct=lambda x: (100 * x["assigned"] / x["n"]).round(1))
    .reset_index()
)
print(by_type.to_string(index=False))

# 아파트 단지의 '동'이라면 최소한의 규모가 있다. 소형 residential은 빌라로 본다.
apartments = buildings[buildings["building_type"] == "apartments"].copy()
apt_assign_rate = 100 * apartments["assigned"].mean()
print(f"\n  building=apartments 배정률: {apt_assign_rate:.1f}% (n={len(apartments)})")


# ============================================================================
# 3. 가설 B - 미배정 아파트는 단지 경계 밖에 걸쳐 있는가
# ============================================================================

print("\n===== 3. 가설 B: 미배정 아파트의 단지 근접 거리 =====")

unassigned_apt = apartments[~apartments["assigned"]].copy()
if len(unassigned_apt) > 0 and len(complexes) > 0:
    unassigned_centroids = unassigned_apt.copy()
    unassigned_centroids["geometry"] = unassigned_apt.geometry.centroid
    nearest = gpd.sjoin_nearest(
        unassigned_centroids[["osm_id", "geometry"]],
        complexes[["osm_id", "name", "geometry"]].rename(
            columns={"osm_id": "near_complex_id", "name": "near_complex_name"}
        ),
        how="left", distance_col="dist_m",
    ).drop_duplicates(subset="osm_id")

    for threshold in [0, 10, 30, 50, 100]:
        n_within = int((nearest["dist_m"] <= threshold).sum())
        rescued_rate = 100 * (apartments["assigned"].sum() + n_within) / len(apartments)
        print(f"  단지 경계 {threshold:3d}m 이내 구제 시: +{n_within:4d}동 -> 배정률 {rescued_rate:.1f}%")
    print(f"\n  미배정 아파트 최근접 단지 거리 중앙값: {nearest['dist_m'].median():.0f}m")
else:
    print("  미배정 아파트 없음")


# ============================================================================
# 4. 가설 C - 단지 내 다른 동의 층수로 높이 결측을 메울 수 있는가
# ============================================================================

print("\n===== 4. 가설 C: 단지 내 층수 보간 =====")

assigned_apt = apartments[apartments["assigned"]].copy()
print(f"  단지에 배정된 아파트 동: {len(assigned_apt)}")

direct_rate = 100 * assigned_apt["height_m"].notna().mean()
print(f"  직접 높이 확보율: {direct_rate:.1f}%")

# 같은 단지 동들의 층수 중앙값으로 대체
complex_median_levels = (assigned_apt
    .groupby("complex_osm_id")["levels_num"]
    .median()
    .rename("complex_median_levels")
)
assigned_apt = assigned_apt.join(complex_median_levels, on="complex_osm_id")
assigned_apt["height_filled"] = assigned_apt["height_m"].fillna(
    assigned_apt["complex_median_levels"] * FLOOR_HEIGHT_M
)
filled_rate = 100 * assigned_apt["height_filled"].notna().mean()
print(f"  단지 중앙값 보간 후:  {filled_rate:.1f}%")

# 보간이 타당한지: 단지 내 층수 분산이 실제로 작은가
spread = (assigned_apt
    .dropna(subset=["levels_num"])
    .groupby("complex_osm_id")["levels_num"]
    .agg(["count", "std", "median"])
    .query("count >= 3")
)
print(f"\n  동 3개 이상 단지 {len(spread)}곳의 단지 내 층수 표준편차:")
print(f"    중앙값 {spread['std'].median():.1f}층 / 75분위 {spread['std'].quantile(0.75):.1f}층")
print(f"    표준편차 2층 이하 단지 비율: {100 * (spread['std'] <= 2).mean():.0f}%")


# ============================================================================
# 5. 재판정
# ============================================================================

print("\n===== 5. 아파트 단지 기준 재판정 =====")

rescue_50m = 0
if len(unassigned_apt) > 0:
    rescue_50m = int((nearest["dist_m"] <= 50).sum())
final_assign_rate = 100 * (apartments["assigned"].sum() + rescue_50m) / len(apartments)

checks = [
    ("아파트 동 -> 단지 배정률(50m 구제 포함) >= 80%", final_assign_rate, 80.0),
    ("배정 동 높이 확보율(단지 보간 후) >= 90%", filled_rate, 90.0),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:44s} 실측 {value:.1f}%")

diag_path = output_dir / "02.1.building_type_breakdown.txt"
by_type.to_csv(diag_path, sep="\t", index=False, lineterminator="\n")
print(f"\n태그별 분해: {diag_path}")
print(f"\n===== 재판정 {'통과' if all_passed else '미통과'} =====")
