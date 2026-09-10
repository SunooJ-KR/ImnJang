# ============================================================================
# 15.assign_dong_all.py
# ============================================================================
# Author:      yjkim
# Purpose:     서울 전역 아파트 동을 실거래 단지(aptSeq)에 1:1 배정한다
# Description: 09의 "동 -> 단지" 방향은 유지하되, 배정 근거를 두 가지로 바꾼다.
#
#              (1) 단지 경계 폴리곤 (12.2, OSM landuse 4,285개)
#                  같은 경계 안의 동은 그 경계 안의 앵커에게만 갈 수 있다.
#                  최근접 원형 반경은 3ha+ 단지에서 앵커가 단지 중심을 중앙값
#                  112m 벗어나 구조적으로 틀렸다.
#
#              (2) 한국부동산원 등록 동수를 배정 상한으로 (17에서 확보)
#                  경계만으로는 부족했다. 17의 대조 결과 동수 정확 일치율이
#                  60.8%였고 과다 배정이 1,760건이었다(한영해시안 실제 1동에
#                  257동 배정). 공식 등록 동수를 capacity로 걸면 과다가
#                  구조적으로 차단된다.
#
#              배정은 용량 제약 greedy다. 경계 근거 쌍을 거리순으로 먼저 소진하고,
#              남은 동을 최근접 쌍으로 채우되 앵커별 잔여 용량을 넘지 않는다.
#
#              준공년도는 배정에 쓰지 않는다. 초판은 년도가 맞는 앵커를 고른 뒤
#              년도로 검증해 일치율이 정의상 100%였다. 검증 전용으로 격리한다.
#
#              통과 기준:
#                (1) 동수 정확 일치율 >= 90% (등록 정보 대조, 17이 최종 판정)
#                (2) 동을 1개 이상 받은 단지 비율 >= 80%
#                (3) 과다 배정 단지 0건 (상한을 걸었으므로 구조적으로 보장)
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path
from scipy.spatial import cKDTree
from shapely import wkt

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

BUILDING_PATH = output_dir / "12.1.osm_buildings.txt"
BOUNDARY_PATH = output_dir / "12.2.osm_complex_boundary.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"
REGISTRY_PATH = output_dir / "raw" / "reb" / "apt_registry.csv"
COMPLEX_RESULT = output_dir / "15.1.complex_final.txt"
BUILDING_RESULT = output_dir / "15.2.building_assigned.txt"

METRIC_CRS = "EPSG:5179"
MAX_DIST_M = 300         # 경계로 못 푸는 동에만 적용하는 최근접 상한
N_NEIGHBORS = 8          # 용량이 차면 차순위 앵커로 넘어가므로 초판보다 넉넉히 본다
YEAR_TOLERANCE = 2       # 검증 전용. 배정에는 쓰지 않는다
YEAR_RANGE = (1930, 2027)  # OSM start_date에 '19997', '202' 같은 오타 태그가 있다
MIN_HEIGHT_M = 5.0       # levels 15인데 height 4m 같은 태그 오류를 걸러낸다
APARTMENT_CODE = "1"     # 등록 정보 단지종류 1=아파트

UNASSIGNED = -1


# ============================================================================
# 1. 단지 앵커 + 등록 동수(배정 상한)
# ============================================================================

print("===== 1. 단지 앵커 =====")
master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})
master = master.dropna(subset=["lat", "lon"]).reset_index(drop=True)


def normalize_address(text):
    """등록 정보 '서울특별시 강서구 마곡동 744' <-> 실거래 gu/umd/jibun 를 같은 키로"""
    text = text.fillna("").astype(str).str.strip()
    text = text.str.replace(r"^서울(특별시)?\s*", "", regex=True)
    text = text.str.replace(r"\s+", " ", regex=True)
    return text.str.replace(r"산\s+(?=\d)", "산", regex=True)


registry = pd.read_csv(REGISTRY_PATH, encoding="utf-8-sig", dtype=str)
registry = registry[registry["주소"].str.startswith("서울", na=False)
                    & (registry["단지종류"] == APARTMENT_CODE)].copy()
registry["reg_dong"] = pd.to_numeric(registry["동수"], errors="coerce")
registry["reg_units"] = pd.to_numeric(registry["세대수"], errors="coerce")
registry["reg_year"] = pd.to_numeric(registry["사용승인일"].str[:4], errors="coerce")
registry["join_key"] = normalize_address(registry["주소"])
registry = registry.drop_duplicates("join_key")

master["join_key"] = normalize_address(
    master["gu"].fillna("") + " " + master["umd_name"].fillna("")
    + " " + master["jibun"].fillna(""))
master = master.merge(registry[["join_key", "reg_dong", "reg_units", "reg_year"]],
                      on="join_key", how="left")

anchors = gpd.GeoDataFrame(
    master[["aptSeq", "apt_name", "gu", "build_year", "n_deals",
            "reg_dong", "reg_units", "reg_year"]],
    geometry=gpd.points_from_xy(master["lon"], master["lat"]),
    crs="EPSG:4326",
).to_crs(METRIC_CRS)

# 같은 지번에 여러 aptSeq가 등록된 경우 등록 정보 1건을 나눠 갖는다.
# 상한을 그대로 주면 합쳐서 초과하므로 지번당 균등 분할한다.
share = master.groupby("join_key")["aptSeq"].transform("size")
capacity = (anchors["reg_dong"] / share.to_numpy()).to_numpy()
capacity = np.where(np.isnan(capacity), np.inf, np.ceil(capacity))

n_capped = int(np.isfinite(capacity).sum())
print(f"  단지 앵커 {len(anchors)}개 / 등록 동수 확보 {n_capped} "
      f"({100 * n_capped / len(anchors):.1f}%)")
print(f"  상한 없는 단지 {len(anchors) - n_capped}개 (등록 정보 미매칭 — 과다 배정 가능)")


# ============================================================================
# 2. 아파트 동 · 단지 경계 로드
# ============================================================================

print("\n===== 2. 아파트 동 · 단지 경계 =====")


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

year_valid = buildings["build_year"].between(*YEAR_RANGE)
n_bad_year = int(buildings["build_year"].notna().sum() - year_valid.sum())
buildings.loc[~year_valid, "build_year"] = np.nan

too_short = buildings["height_m"] < MIN_HEIGHT_M
buildings.loc[too_short, "height_m"] = np.nan
buildings.loc[too_short, "height_source"] = "none"

print(f"  아파트 동 {len(buildings)}개 (준공년도 이상치 {n_bad_year}건, "
      f"height {MIN_HEIGHT_M}m 미만 {int(too_short.sum())}건 무효화)")
print(f"  단지 경계 폴리곤 {len(boundaries)}개")


# ============================================================================
# 3. 용량 제약 배정
# ============================================================================
# 후보 쌍 (동, 앵커, 거리)을 만들고 우선순위대로 소진한다.
#   1순위: 같은 경계 폴리곤 안 (거리 오름차순)
#   2순위: MAX_DIST_M 안의 최근접 (거리 오름차순)
# 앵커의 잔여 용량이 0이면 건너뛴다. 동은 한 번만 배정된다.

print("\n===== 3. 용량 제약 배정 =====")

building_points = gpd.GeoDataFrame(
    buildings[["osm_id"]], geometry=buildings.geometry.centroid, crs=METRIC_CRS)


def containing_boundary(points):
    """겹치는 폴리곤이 있으면 한 점이 여러 행으로 늘어난다. 첫 번째만 쓴다"""
    joined = gpd.sjoin(points[["geometry"]], boundaries[["boundary_id", "geometry"]],
                       how="left", predicate="within")
    return joined["boundary_id"].groupby(level=0).first().reindex(points.index)


anchor_boundary = containing_boundary(anchors)
building_boundary = containing_boundary(building_points)
print(f"  경계 안 앵커 {int(anchor_boundary.notna().sum())}/{len(anchors)}, "
      f"경계 안 동 {int(building_boundary.notna().sum())}/{len(buildings)}")

anchor_xy = np.column_stack([anchors.geometry.x, anchors.geometry.y])
building_xy = np.column_stack([building_points.geometry.x, building_points.geometry.y])

# --- 후보 쌍 생성 ---
pairs = []   # (우선순위, 거리, 동 index, 앵커 index)

anchors_by_boundary = (anchor_boundary.dropna().astype(int)
                       .reset_index().groupby("boundary_id")["index"].apply(list))
for boundary_id, building_idx in (building_boundary.dropna().astype(int)
                                  .reset_index().groupby("boundary_id")["index"]):
    candidate_idx = anchors_by_boundary.get(boundary_id)
    if not candidate_idx:
        continue
    building_idx = np.asarray(building_idx)
    candidate_idx = np.asarray(candidate_idx)
    dist = np.linalg.norm(
        building_xy[building_idx][:, None, :] - anchor_xy[candidate_idx][None, :, :], axis=2)
    for row, b in enumerate(building_idx):
        for col, a in enumerate(candidate_idx):
            pairs.append((0, dist[row, col], b, a))

near_dist, near_idx = cKDTree(anchor_xy).query(
    building_xy, k=N_NEIGHBORS, distance_upper_bound=MAX_DIST_M)
for b in range(len(buildings)):
    for d, a in zip(near_dist[b], near_idx[b]):
        if a >= len(anchors):
            break
        pairs.append((1, d, b, a))

pairs.sort(key=lambda p: (p[0], p[1]))
print(f"  후보 쌍 {len(pairs)}개 (경계 {sum(1 for p in pairs if p[0] == 0)} / "
      f"최근접 {sum(1 for p in pairs if p[0] == 1)})")

# --- greedy 소진 ---
owner = np.full(len(buildings), UNASSIGNED)
method = np.full(len(buildings), "none", dtype=object)
remaining = capacity.copy()
PRIORITY_NAME = {0: "boundary", 1: "nearest"}

for priority, dist, b, a in pairs:
    if owner[b] != UNASSIGNED or remaining[a] <= 0:
        continue
    owner[b], method[b] = a, PRIORITY_NAME[priority]
    remaining[a] -= 1

assigned = owner != UNASSIGNED
buildings["aptSeq"] = np.where(assigned, anchors["aptSeq"].to_numpy()[owner], None)
buildings["boundary_id"] = building_boundary.to_numpy()
buildings["assign_method"] = method
buildings["assign_confidence"] = np.select(
    [method == "boundary", method == "nearest"], ["HIGH", "LOW"], default=None)
buildings["dist_m"] = np.where(
    assigned, np.linalg.norm(building_xy - anchor_xy[owner], axis=1), np.nan)

n_assigned = int(assigned.sum())
print(f"  배정 성공: {n_assigned}/{len(buildings)} ({100 * n_assigned / len(buildings):.1f}%)")
print(f"  방식별: {pd.Series(method).value_counts().to_dict()}")
print(f"  배정 거리 중앙값: {buildings['dist_m'].median():.0f}m")
assert buildings["osm_id"].is_unique, "동 중복 배정 발생"


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
result["dong_diff"] = result["n_dong"] - result["reg_dong"]

covered = result["n_dong"].notna()
coverage_rate = 100 * covered.mean()
weighted_coverage = 100 * result[covered]["n_deals"].sum() / result["n_deals"].sum()
print(f"  동을 받은 단지: {int(covered.sum())}/{len(result)} ({coverage_rate:.1f}%)")
print(f"  거래량 가중: {weighted_coverage:.1f}%")

# 등록 동수 대조 — 이것이 실제 정확도다 (17이 같은 계산을 독립 실행한다)
comparable = result.dropna(subset=["n_dong", "reg_dong"])
exact = (comparable["dong_diff"] == 0)
dong_accuracy = 100 * exact.mean() if len(comparable) else 0.0
n_over = int((comparable["dong_diff"] > 0).sum())
print(f"\n  등록 동수 대조 {len(comparable)}건: 정확 {int(exact.sum())} ({dong_accuracy:.1f}%)")
print(f"    과다 {n_over}건 / 과소 {int((comparable['dong_diff'] < 0).sum())}건")
for label in ["HIGH", "MIXED", "LOW"]:
    sub = comparable[comparable["confidence"] == label]
    if len(sub):
        print(f"    {label:5s} n={len(sub):5d}  정확 {100 * (sub['dong_diff'] == 0).mean():5.1f}%  "
              f"과다 {100 * (sub['dong_diff'] > 0).mean():5.1f}%")

horizon_ready = int(result["n_with_height"].sum())
print(f"\n  horizon 투입 가능 동: {horizon_ready}/{n_assigned} "
      f"({100 * horizon_ready / max(n_assigned, 1):.1f}%)")


# ============================================================================
# 6. 판정 및 저장
# ============================================================================

print("\n===== 6. 판정 =====")
checks = [
    ("등록 동수 정확 일치 >= 90%", dong_accuracy, 90.0),
    ("동을 받은 단지 >= 80%", coverage_rate, 80.0),
    ("과다 배정 0건", -n_over, 0.0),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    shown = f"{n_over}건" if label.startswith("과다") else f"실측 {value:.1f}%"
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:26s} {shown}")

result.to_csv(COMPLEX_RESULT, sep="\t", index=False, lineterminator="\n")
buildings.drop(columns="geometry").to_csv(
    BUILDING_RESULT, sep="\t", index=False, lineterminator="\n")
print(f"\n단지 최종: {COMPLEX_RESULT}")
print(f"동 배정:   {BUILDING_RESULT}")
print(f"\n===== 동 배정 {'통과' if all_passed else '미통과'} =====")
