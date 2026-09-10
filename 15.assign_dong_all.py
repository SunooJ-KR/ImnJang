# ============================================================================
# 15.assign_dong_all.py
# ============================================================================
# Author:      yjkim
# Purpose:     서울 전역 아파트 동을 실거래 단지(aptSeq)에 1:1 배정한다
# Description: 09의 "동 -> 단지" 방향은 유지하되, 배정 근거를 거리에서 단지 경계로
#              바꾼다. 09/초판 15의 최근접 원형 반경은 세 가지로 무너졌다:
#                - 3ha 이상 단지에서 앵커(카카오 대표점)가 단지 중심에서 중앙값
#                  112m 벗어나고 34.7%는 단지 반경 밖이다. 300m 원이 단지
#                  반대편은 놓치고 옆 단지는 과잉 포섭한다
#                - 밀집 지역에서 한 앵커가 주변 동을 독식한다(초판 최대 256동)
#                - 이를 재배분으로 덮으려 했으나 붙은 동의 63%가 준공년도 불일치,
#                  거리는 3.4배로 오히려 악화됐다. 재배분은 폐기한다
#
#              그래서 12.2의 OSM 단지 경계 폴리곤 4,285개를 1차 근거로 쓴다.
#              같은 경계 안의 동은 그 경계 안의 앵커에게만 갈 수 있다.
#
#              준공년도를 배정에서 뺐다. 초판은 년도가 맞는 앵커를 고른 뒤 년도로
#              검증해 rank>=0 동의 일치율이 정의상 100%였다. 순환을 끊어야
#              게이트가 실제 정확도를 측정한다. 년도는 검증 전용이다.
#
#              통과 기준:
#                (1) 단지당 동 수 <= 100 (서울 최대 단지도 84동 수준)
#                (2) 동을 1개 이상 받은 단지 비율 >= 80%
#                (3) 준공년도 ±2년 일치율 >= 90%  (이제 독립 검증)
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import sys

import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path
from scipy.spatial import cKDTree
from shapely import wkt

work_dir = Path(__file__).parent
output_dir = work_dir / "output"

BUILDING_PATH = output_dir / "12.1.osm_buildings.txt"
BOUNDARY_PATH = output_dir / "12.2.osm_complex_boundary.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"
COMPLEX_RESULT = output_dir / "15.1.complex_final.txt"
BUILDING_RESULT = output_dir / "15.2.building_assigned.txt"

METRIC_CRS = "EPSG:5179"
MAX_DIST_M = 300         # 경계로 못 푸는 동에만 적용하는 최근접 상한
YEAR_TOLERANCE = 2       # 검증 전용. 배정에는 쓰지 않는다
YEAR_RANGE = (1930, 2027)  # OSM start_date에 '19997', '202' 같은 오타 태그가 있다
MIN_HEIGHT_M = 5.0       # levels 15인데 height 4m 같은 태그 오류를 걸러낸다
MAX_DONG_PER_COMPLEX = 100

# 앵커를 경계 밖 이 거리까지 끌어당긴다. 0/25/50/100/150m 스윕 결과 경계 기반
# 일치율이 95.2 -> 90.4 -> 85.2 -> 82.2 -> 81.3%로 단조 감소하고 커버리지는
# 2.3%p만 늘었다. 앵커가 경계 밖이라는 것 자체가 다른 단지라는 신호다. 0 유지.
ANCHOR_SNAP_M = float(sys.argv[1]) if len(sys.argv) > 1 else 0.0

UNASSIGNED = -1


# ============================================================================
# 1. 단지 앵커 (14 지오코딩 결과)
# ============================================================================

print("===== 1. 단지 앵커 로드 =====")
master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})
master = master.dropna(subset=["lat", "lon"]).reset_index(drop=True)
anchors = gpd.GeoDataFrame(
    master[["aptSeq", "apt_name", "gu", "build_year", "n_deals"]],
    geometry=gpd.points_from_xy(master["lon"], master["lat"]),
    crs="EPSG:4326",
).to_crs(METRIC_CRS)

# 같은 주소로 등록된 별개 aptSeq가 있다(DMC파크뷰자이 1~5단지 + 임대 2개가 모두
# '가재울미래로 2'). 주소가 같으면 지오코딩으로는 분리할 수 없다.
n_shared_coord = int(anchors.geometry.duplicated(keep=False).sum())
print(f"  단지 앵커 {len(anchors)}개 (좌표를 공유하는 앵커 {n_shared_coord}개)")


# ============================================================================
# 2. 아파트 동 · 단지 경계 로드
# ============================================================================

print("\n===== 2. 아파트 동 · 단지 경계 로드 =====")


def read_wkt_layer(path, query=None):
    df = pd.read_csv(path, sep="\t")
    if query is not None:
        df = df[query(df)]
    df = df.reset_index(drop=True)
    return gpd.GeoDataFrame(
        df.drop(columns="geometry"),
        geometry=df["geometry"].apply(wkt.loads), crs="EPSG:4326",
    ).to_crs(METRIC_CRS)


buildings = read_wkt_layer(BUILDING_PATH, lambda d: d["building"] == "apartments")
boundaries = read_wkt_layer(BOUNDARY_PATH)
boundaries["boundary_id"] = np.arange(len(boundaries))

# 오타 태그가 무조건 불일치로 계산되는 것을 막는다
year_valid = buildings["build_year"].between(*YEAR_RANGE)
n_bad_year = int(buildings["build_year"].notna().sum() - year_valid.sum())
buildings.loc[~year_valid, "build_year"] = np.nan

# levels와 어긋나는 height 태그. horizon 엔진에 3~4m 아파트가 들어가면 안 된다
too_short = buildings["height_m"] < MIN_HEIGHT_M
buildings.loc[too_short, "height_m"] = np.nan
buildings.loc[too_short, "height_source"] = "none"

print(f"  아파트 동 {len(buildings)}개 (준공년도 이상치 {n_bad_year}건, "
      f"height {MIN_HEIGHT_M}m 미만 {int(too_short.sum())}건 무효화)")
print(f"  단지 경계 폴리곤 {len(boundaries)}개")


# ============================================================================
# 3. 경계 기준 배정
# ============================================================================
# 1차: 같은 경계 폴리곤 안의 동은 그 경계 안의 앵커에게만 간다.
#      앵커가 이미 단지를 특정하므로 거리 상한이 필요 없다.
# 2차: 경계가 없거나 경계 안에 앵커가 없는 동만 최근접 앵커로 배정한다.

print("\n===== 3. 경계 기준 배정 =====")

building_points = gpd.GeoDataFrame(
    buildings[["osm_id"]], geometry=buildings.geometry.centroid, crs=METRIC_CRS)

def containing_boundary(points, snap_m=None):
    """겹치는 폴리곤이 있으면 한 점이 여러 행으로 늘어난다. 첫 번째만 쓴다.
    snap_m을 주면 경계 밖 그 거리까지도 같은 단지로 본다."""
    layer = boundaries[["boundary_id", "geometry"]]
    if snap_m:
        joined = gpd.sjoin_nearest(points[["geometry"]], layer,
                                   how="left", max_distance=snap_m)
    else:
        joined = gpd.sjoin(points[["geometry"]], layer, how="left", predicate="within")
    return joined["boundary_id"].groupby(level=0).first().reindex(points.index)


# 동은 경계 안에 있어야 그 단지 동이다. 스냅은 앵커에만 적용한다
anchor_boundary = containing_boundary(anchors, ANCHOR_SNAP_M)
building_boundary = containing_boundary(building_points)

print(f"  경계에 붙은 앵커 {int(anchor_boundary.notna().sum())}/{len(anchors)} "
      f"(스냅 {ANCHOR_SNAP_M:.0f}m), 경계 안 동 {int(building_boundary.notna().sum())}/{len(buildings)}")

anchor_xy = np.column_stack([anchors.geometry.x, anchors.geometry.y])
building_xy = np.column_stack([building_points.geometry.x, building_points.geometry.y])

owner = np.full(len(buildings), UNASSIGNED)
method = np.full(len(buildings), "none", dtype=object)

# --- 1차: 경계 내부 ---
anchors_by_boundary = (anchor_boundary.dropna().astype(int)
                       .reset_index().groupby("boundary_id")["index"].apply(list))

for boundary_id, building_idx in (building_boundary.dropna().astype(int)
                                  .reset_index().groupby("boundary_id")["index"]):
    candidate_idx = anchors_by_boundary.get(boundary_id)
    if not candidate_idx:
        continue
    building_idx = np.asarray(building_idx)
    candidate_idx = np.asarray(candidate_idx)
    # ponytail: 한 경계 안 여러 앵커는 최근접으로 가른다. 같은 좌표를 공유하는
    # 앵커들(1단지/2단지/임대)은 이 방법으로 분리 불가 — boundary_id를 남겨
    # downstream이 한 덩어리로 볼 수 있게 한다
    dist = np.linalg.norm(
        building_xy[building_idx][:, None, :] - anchor_xy[candidate_idx][None, :, :], axis=2)
    owner[building_idx] = candidate_idx[dist.argmin(axis=1)]
    method[building_idx] = "boundary"

print(f"  1차 경계 배정: {int((owner != UNASSIGNED).sum())}동")

# --- 2차: 남은 동은 최근접 앵커 (준공년도는 쓰지 않는다) ---
rest = np.where(owner == UNASSIGNED)[0]
dist, nearest = cKDTree(anchor_xy).query(
    building_xy[rest], k=1, distance_upper_bound=MAX_DIST_M)
hit = nearest < len(anchors)
owner[rest[hit]] = nearest[hit]
method[rest[hit]] = "nearest"

assigned = owner != UNASSIGNED
buildings["aptSeq"] = np.where(assigned, anchors["aptSeq"].to_numpy()[owner], None)
buildings["boundary_id"] = building_boundary.to_numpy()
buildings["assign_method"] = method
# 경계 근거는 준공년도 일치율 95.2%, 최근접 근거는 73.1%다. 같은 값으로 쓰면 안 된다
buildings["assign_confidence"] = np.select(
    [method == "boundary", method == "nearest"], ["HIGH", "LOW"], default=None)
buildings["dist_m"] = np.where(
    assigned, np.linalg.norm(building_xy - anchor_xy[owner], axis=1), np.nan)

n_assigned = int(assigned.sum())
print(f"  2차 최근접 배정: {int(hit.sum())}동")
print(f"  배정 성공: {n_assigned}/{len(buildings)} ({100 * n_assigned / len(buildings):.1f}%)")
print(f"  방식별: {pd.Series(method).value_counts().to_dict()}")
print(f"  배정 거리 중앙값: {buildings['dist_m'].median():.0f}m "
      f"(경계 {buildings[buildings['assign_method']=='boundary']['dist_m'].median():.0f}m / "
      f"최근접 {buildings[buildings['assign_method']=='nearest']['dist_m'].median():.0f}m)")


# ============================================================================
# 4. height 결측 대체 (같은 단지 다른 동의 중앙값)
# ============================================================================
# HANDOFF 4절 결정: 대체 플래그를 남겨 리포트에 공개한다.

print("\n===== 4. height 결측 대체 =====")

complex_median = buildings.groupby("aptSeq")["height_m"].transform("median")
needs_fill = buildings["height_m"].isna() & complex_median.notna()
buildings.loc[needs_fill, "height_m"] = complex_median[needs_fill]
buildings.loc[needs_fill, "height_source"] = "complex_median"

print(f"  단지 중앙값으로 대체: {int(needs_fill.sum())}동")
print(f"  대체 불가: {int(buildings['height_m'].isna().sum())}동")
print(f"  height_source: {buildings['height_source'].value_counts(dropna=False).to_dict()}")


# ============================================================================
# 5. 단지별 집계 및 교차검증
# ============================================================================

print("\n===== 5. 단지별 집계 =====")

complex_agg = (buildings[buildings["aptSeq"].notna()]
    .groupby("aptSeq")
    .agg(
        n_dong=("osm_id", "count"),
        n_with_height=("height_m", lambda s: int(s.notna().sum())),
        n_height_imputed=("height_source", lambda s: int((s == "complex_median").sum())),
        n_by_boundary=("assign_method", lambda s: int((s == "boundary").sum())),
        confidence=("assign_confidence", lambda s: "HIGH" if (s == "HIGH").all() else
                    ("LOW" if (s == "LOW").all() else "MIXED")),
        max_height_m=("height_m", "max"),
        max_levels=("levels", "max"),
        osm_year_median=("build_year", "median"),
        mean_dist=("dist_m", "mean"),
    )
    .reset_index()
)
result = anchors.drop(columns="geometry").merge(complex_agg, on="aptSeq", how="left")
result["boundary_id"] = anchor_boundary.to_numpy()

covered = result["n_dong"].notna()
coverage_rate = 100 * covered.mean()
weighted_coverage = 100 * result[covered]["n_deals"].sum() / result["n_deals"].sum()
print(f"  동을 받은 단지: {int(covered.sum())}/{len(result)} ({coverage_rate:.1f}%)")
print(f"  거래량 가중: {weighted_coverage:.1f}%")
print(f"  단지당 동 수 중앙값 {result['n_dong'].median():.0f}, "
      f"최대 {result['n_dong'].max():.0f}, 100동 초과 {int((result['n_dong'] > 100).sum())}단지")

verified = result.dropna(subset=["osm_year_median", "build_year"]).copy()
verified["year_diff"] = (verified["build_year"] - verified["osm_year_median"]).abs()
within_tolerance = verified["year_diff"] <= YEAR_TOLERANCE
consistency = 100 * within_tolerance.mean() if len(verified) else 0.0
print(f"\n  준공년도 대조 {len(verified)}건 / ±{YEAR_TOLERANCE}년 일치 "
      f"{int(within_tolerance.sum())} ({consistency:.1f}%)")

# 신뢰도 등급별로 나눠 봐야 경계 근거가 실제로 나은지 알 수 있다
rate_by_confidence = {}
for label in ["HIGH", "MIXED", "LOW"]:
    sub = verified[verified["confidence"] == label]
    if len(sub):
        rate_by_confidence[label] = 100 * (sub["year_diff"] <= YEAR_TOLERANCE).mean()
        print(f"    {label:5s} n={len(sub):5d}  일치율 {rate_by_confidence[label]:.1f}%")

horizon_ready = int(result["n_with_height"].sum())
print(f"\n  horizon 투입 가능 동: {horizon_ready}/{n_assigned} "
      f"({100 * horizon_ready / max(n_assigned, 1):.1f}%)")
print(f"\n  동을 못 받은 단지 상위 5개 구:\n{result[~covered]['gu'].value_counts().head(5)}")


# ============================================================================
# 6. 판정 및 저장
# ============================================================================

print("\n===== 6. 판정 =====")
max_dong = float(result["n_dong"].max())
high = result[result["confidence"] == "HIGH"]
high_coverage = 100 * len(high) / len(result)
checks = [
    (f"단지당 동 수 <= {MAX_DONG_PER_COMPLEX}", MAX_DONG_PER_COMPLEX - max_dong, 0.0),
    ("동을 받은 단지 >= 80%", coverage_rate, 80.0),
    ("준공년도 ±2년 일치율 >= 90%", consistency, 90.0),
    ("  └ HIGH 신뢰도만 >= 90%", rate_by_confidence.get("HIGH", 0.0), 90.0),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    shown = f"최대 {max_dong:.0f}동" if label.startswith("단지당") else f"실측 {value:.1f}%"
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:28s} {shown}")

result.to_csv(COMPLEX_RESULT, sep="\t", index=False, lineterminator="\n")
buildings.drop(columns="geometry").to_csv(
    BUILDING_RESULT, sep="\t", index=False, lineterminator="\n")
print(f"\n  신뢰도 분포: {result['confidence'].value_counts(dropna=False).to_dict()}")
print(f"  HIGH 단지 {len(high)} ({high_coverage:.1f}%), "
      f"거래량 점유 {100 * high['n_deals'].sum() / result['n_deals'].sum():.1f}%")

print(f"\n단지 최종: {COMPLEX_RESULT}")
print(f"동 배정:   {BUILDING_RESULT}")
print(f"\n===== 동 배정 {'통과' if all_passed else '미통과'} =====")
