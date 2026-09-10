# ============================================================================
# 09.assign_dong.py
# ============================================================================
# Author:      yjkim
# Purpose:     각 아파트 동을 실거래 단지(aptSeq)에 1:1 배정한다
# Description: 08에서 매칭률 96.9%가 나왔지만 연결된 동이 12,024개로 실제 동 수
#              4,465개를 크게 초과했다. 원인은 한 buffer 클러스터에 최대 27개 단지가
#              붙는 과병합이며, 건축년도 일치율도 53.6%로 무너졌다.
#
#              방향을 뒤집는다. 08에서 단지 좌표를 100% 확보했으므로
#              "단지를 클러스터에 붙이기"가 아니라 "동을 단지에 배정하기"로 푼다.
#              각 동은 정확히 하나의 단지에만 속하므로 중복이 원천 차단된다.
#
#              배정 규칙:
#                - 동 중심에서 가장 가까운 단지 앵커에 배정
#                - MAX_DIST_M 초과는 미배정 (다른 구의 단지일 수 있음)
#                - 준공년도가 ±2년 밖이면 차순위 앵커를 검토
#
#              통과 기준:
#                (1) 배정된 동 수 <= 전체 동 수 (중복 없음)
#                (2) 동을 1개 이상 받은 단지 비율 >= 80%
#                (3) 준공년도 ±2년 일치율 >= 90%
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon

work_dir = Path(__file__).parent
output_dir = work_dir / "output"

OSM_CACHE = output_dir / "cache_overpass_gangnam.json"
MASTER_PATH = output_dir / "08.1.geocoded_master.txt"

METRIC_CRS = "EPSG:5179"
MAX_DIST_M = 300         # 단지 앵커에서 이보다 먼 동은 같은 단지로 보지 않는다
YEAR_TOLERANCE = 2
N_NEIGHBORS = 5          # 준공년도 불일치 시 검토할 차순위 앵커 수


# ============================================================================
# 1. 단지 앵커 (08 지오코딩 결과)
# ============================================================================

print("===== 1. 단지 앵커 로드 =====")
master = pd.read_csv(MASTER_PATH, sep="\t")
master = master.dropna(subset=["lat", "lon"]).reset_index(drop=True)
anchors = gpd.GeoDataFrame(
    master[["aptSeq", "apt_name", "build_year", "n_trades"]],
    geometry=gpd.points_from_xy(master["lon"], master["lat"]),
    crs="EPSG:4326",
).to_crs(METRIC_CRS)
print(f"  단지 앵커 {len(anchors)}개 (좌표 100%)")


# ============================================================================
# 2. 아파트 동 로드
# ============================================================================

print("\n===== 2. 아파트 동 로드 =====")
elements = json.loads(OSM_CACHE.read_text(encoding="utf-8"))["elements"]

records = []
for element in elements:
    tags = element.get("tags", {})
    if tags.get("building") != "apartments":
        continue
    geometry = element.get("geometry")
    if not geometry or len(geometry) < 4:
        continue
    records.append({
        "osm_id": element["id"],
        "dong_name": tags.get("name"),
        "start_date": tags.get("start_date"),
        "levels": tags.get("building:levels"),
        "height": tags.get("height"),
        "flats": tags.get("building:flats"),
        "geometry": Polygon([(p["lon"], p["lat"]) for p in geometry]),
    })
buildings = gpd.GeoDataFrame(records, crs="EPSG:4326").to_crs(METRIC_CRS)


def parse_numeric(series):
    return pd.to_numeric(
        series.astype("string").str.extract(r"(\d+(?:\.\d+)?)", expand=False), errors="coerce")


buildings["osm_year"] = parse_numeric(buildings["start_date"])
buildings["levels_num"] = parse_numeric(buildings["levels"])
buildings["height_num"] = parse_numeric(buildings["height"])
buildings["flats_num"] = parse_numeric(buildings["flats"])
buildings["building_height_m"] = buildings["height_num"].fillna(buildings["levels_num"] * 2.8)
print(f"  아파트 동 {len(buildings)}개")


# ============================================================================
# 3. 최근접 앵커 배정 (준공년도로 차순위 검토)
# ============================================================================

print("\n===== 3. 동 -> 단지 배정 =====")

building_points = buildings.geometry.centroid
anchor_xy = np.array([[p.x, p.y] for p in anchors.geometry])
building_xy = np.array([[p.x, p.y] for p in building_points])

# 동 x 앵커 거리 행렬. 강남구 규모(4465 x 391)에서는 전체 계산이 더 단순하고 빠르다.
distances = np.linalg.norm(building_xy[:, None, :] - anchor_xy[None, :, :], axis=2)
order = np.argsort(distances, axis=1)[:, :N_NEIGHBORS]

# pandas nullable(Float64)의 NA는 bool 평가가 모호하므로 numpy float으로 내린다
anchor_years = anchors["build_year"].astype(float).to_numpy()
osm_years = buildings["osm_year"].astype(float).to_numpy()

assignments = []
for i in range(len(buildings)):
    osm_year = osm_years[i]
    chosen, chosen_rank = None, None
    for rank, anchor_index in enumerate(order[i]):
        if distances[i, anchor_index] > MAX_DIST_M:
            break
        # 준공년도를 아는 경우에만 일치를 요구한다
        if not np.isnan(osm_year) and not np.isnan(anchor_years[anchor_index]):
            if abs(osm_year - anchor_years[anchor_index]) > YEAR_TOLERANCE:
                continue
        chosen, chosen_rank = anchor_index, rank
        break
    # 년도 일치 후보가 없으면 거리 최우선으로 되돌린다
    if chosen is None:
        nearest = order[i, 0]
        if distances[i, nearest] <= MAX_DIST_M:
            chosen, chosen_rank = nearest, -1

    assignments.append({
        "osm_id": buildings["osm_id"].iat[i],
        "aptSeq": anchors["aptSeq"].iat[chosen] if chosen is not None else None,
        "dist_m": distances[i, chosen] if chosen is not None else None,
        "anchor_rank": chosen_rank,
    })

assign_df = pd.DataFrame(assignments)
buildings = buildings.merge(assign_df, on="osm_id", how="left")

n_assigned = int(buildings["aptSeq"].notna().sum())
print(f"  배정 성공: {n_assigned}/{len(buildings)} ({100 * n_assigned / len(buildings):.1f}%)")
print(f"  배정 거리 중앙값: {buildings['dist_m'].median():.0f}m")
rank_counts = buildings["anchor_rank"].value_counts().sort_index()
print(f"  앵커 순위별: 최근접 {int(rank_counts.get(0, 0))}, "
      f"차순위 이하 {int(sum(rank_counts.get(r, 0) for r in [1, 2, 3, 4]))}, "
      f"년도 불일치 강제배정 {int(rank_counts.get(-1, 0))}")

# 중복 검증: 동은 정확히 하나의 단지에만 속해야 한다
assert buildings["osm_id"].is_unique, "동 중복 배정 발생"
print(f"  [OK] 동 중복 없음 (배정 동 {n_assigned} <= 전체 {len(buildings)})")


# ============================================================================
# 4. 단지별 집계 및 교차검증
# ============================================================================

print("\n===== 4. 단지별 집계 =====")

complex_agg = (buildings[buildings["aptSeq"].notna()]
    .groupby("aptSeq")
    .agg(
        n_dong=("osm_id", "count"),
        n_with_height=("building_height_m", lambda s: int(s.notna().sum())),
        total_flats=("flats_num", "sum"),
        osm_year_median=("osm_year", "median"),
        max_levels=("levels_num", "max"),
        mean_dist=("dist_m", "mean"),
    )
    .reset_index()
)
result = anchors.drop(columns="geometry").merge(complex_agg, on="aptSeq", how="left")

covered = result["n_dong"].notna()
coverage_rate = 100 * covered.mean()
weighted_coverage = 100 * result[covered]["n_trades"].sum() / result["n_trades"].sum()
print(f"  동을 받은 단지: {int(covered.sum())}/{len(result)} ({coverage_rate:.1f}%)")
print(f"  거래량 가중: {weighted_coverage:.1f}%")
print(f"  단지당 동 수 중앙값: {result['n_dong'].median():.0f}, 최대 {result['n_dong'].max():.0f}")

verified = result.dropna(subset=["osm_year_median", "build_year"]).copy()
verified["year_diff"] = (verified["build_year"] - verified["osm_year_median"]).abs()
within_tolerance = verified["year_diff"] <= YEAR_TOLERANCE
consistency = 100 * within_tolerance.mean() if len(verified) else 0.0
print(f"\n  건축년도 대조 {len(verified)}건 / ±{YEAR_TOLERANCE}년 일치 "
      f"{int(within_tolerance.sum())} ({consistency:.1f}%)")

# horizon 엔진에 실제로 투입 가능한 동
horizon_ready = int(result["n_with_height"].sum())
print(f"\n  horizon 투입 가능 동: {horizon_ready}/{n_assigned} "
      f"({100 * horizon_ready / max(n_assigned, 1):.1f}%)")


# ============================================================================
# 5. 판정 및 저장
# ============================================================================

print("\n===== 5. 판정 =====")
checks = [
    ("동 중복 없음", 100.0 if buildings["osm_id"].is_unique else 0.0, 100.0),
    ("동을 받은 단지 >= 80%", coverage_rate, 80.0),
    ("건축년도 ±2년 일치율 >= 90%", consistency, 90.0),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:28s} 실측 {value:.1f}%")

print(f"\n  [경과] 07 문자열 32.5% -> 08 클러스터 96.9%(중복) -> 09 직접배정 {coverage_rate:.1f}%")

complex_path = output_dir / "09.1.complex_final.txt"
building_path = output_dir / "09.2.building_assigned.txt"
result.to_csv(complex_path, sep="\t", index=False, lineterminator="\n")
buildings.drop(columns=["geometry"]).to_csv(building_path, sep="\t", index=False, lineterminator="\n")
print(f"\n단지 최종: {complex_path}")
print(f"동 배정:   {building_path}")
print(f"\n===== 동 배정 {'통과' if all_passed else '미통과'} =====")
