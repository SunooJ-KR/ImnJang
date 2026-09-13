# ============================================================================
# 29.fill_view_metrics.py
# ============================================================================
# Author:      yjkim
# Purpose:     skyline profile에 수계·공원·산 및 초등학교 안전경로 지표를 채운다
# Description: 21.3.skyline.npy의 관측점별 72방위 profile로 대상별 조망을
#              판정한다. 모든 거리·방위각 연산은 EPSG:5179에서 수행하며, 원본
#              WKT/좌표는 EPSG:4326으로 저장된 입력을 읽기만 한다.
#
#              river_view는 EPSG:5179 면적 5km² 이상 수계(현 입력에서는 한강 본류
#              2건)만 대상으로 한다. 산 target_angle은
#              degrees(atan2(ele_m - 30 - obs_z, d))를 쓴다.
#              DEM이 없어서 서울 평균 지반고를 30m로 근사한 것이며, 산의 실제
#              지반고·관측지 지반고 차이는 반영하지 못한다. ele_m이 없는 봉우리가
#              5km 후보에 있고 알려진 봉우리가 보이지 않으면 mountain_view는
#              False로 단정하지 않고 결측으로 둔다.
#
#              elem_safe_route는 단지 중심에서 OSM 최근접 초등학교까지의 직선이
#              OSM 간선도로와 교차하는지만 보는 근사다. 실제 보행경로·횡단보도·
#              신호는 반영하지 않는다. 22.1에는 학교 좌표가 없어, 같은 OSM
#              수집 계열인 24.6의 is_elementary=True 좌표를 사용한다.
#
#              29.1의 단지×층대 Boolean은 그 조합의 관측점 중 하나라도 해당
#              대상을 조망하면 True로 집계한다. 입력 부족으로 관측점 판정이
#              불가하고 True도 없으면 결측을 유지한다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import multiprocessing as mp
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from scipy.stats import mannwhitneyu
from shapely import wkt
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

work_dir = Path(__file__).resolve().parents[2]
output_dir = work_dir / "output"

OBS_PATH = output_dir / "21.2.observation_points.txt"
SKYLINE_PATH = output_dir / "21.3.skyline.npy"
WATER_PATH = output_dir / "28.1.water.txt"
PARKS_PATH = output_dir / "24.3.osm_parks.txt"
MOUNTAINS_PATH = output_dir / "28.2.mountains.txt"
ROADS_PATH = output_dir / "24.1.osm_roads.txt"
ACCESS_PATH = output_dir / "22.1.access_metrics.txt"
COMPLEX_PATH = output_dir / "15.1.complex_final.txt"
SCHOOLS_PATH = output_dir / "24.6.osm_schools.txt"

OUT_OBS = output_dir / "29.1.view_metrics.txt"
OUT_COMPLEX = output_dir / "29.2.complex_view.txt"

METRIC_CRS = "EPSG:5179"
N_BINS = 72
BIN_WIDTH_DEG = 5.0
VIEW_BLOCK_THRESHOLD_DEG = 10.0       # 05/21 view_metrics()와 동일
WATER_RADIUS_M = 2000.0
PARK_RADIUS_M = 1000.0
MOUNTAIN_RADIUS_M = 5000.0
HAN_RIVER_MIN_AREA_M2 = 5_000_000.0
MOUNTAIN_MIN_ELE_M = 150.0
MOUNTAIN_MIN_ANGLE_DEG = 2.0
GROUND_APPROX_M = 30.0
ARTERIAL_HIGHWAY = {"motorway", "trunk", "primary"}
N_WORKERS = max(1, mp.cpu_count() - 2)
MP_CONTEXT = mp.get_context("fork")


def load_polygons(path):
    """EPSG:4326 WKT polygon을 5179 geometry 배열과 외곽 sample 배열로 바꾼다."""
    raw = pd.read_csv(path, sep="\t")
    geoms = gpd.GeoSeries(raw["geometry"].map(wkt.loads), crs="EPSG:4326").to_crs(METRIC_CRS)
    assert geoms.is_valid.all(), f"{path.name}에 invalid polygon이 있다"

    samples = []
    for geom in geoms:
        polygons = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        coords = []
        for polygon in polygons:
            ring = np.asarray(polygon.exterior.coords)
            # 꼭짓점이 매우 조밀한 외곽선도 최대 720개를 균등 sample한다.
            take = min(len(ring), 720)
            coords.append(ring[np.linspace(0, len(ring) - 1, take, dtype=int), :2])
        samples.append(np.vstack(coords))
    return raw, geoms.to_numpy(), samples


def polygon_bins(obs_xy, geom, samples):
    """대상 polygon이 차지하는 최소 원형 방위각 arc의 5° bin을 반환한다."""
    point = Point(obs_xy)
    if geom.distance(point) <= 1e-6:
        return np.arange(N_BINS)

    delta = samples - obs_xy
    angles = np.sort(np.degrees(np.arctan2(delta[:, 0], delta[:, 1])) % 360.0)
    if len(angles) == 1:
        return np.array([int(angles[0] // BIN_WIDTH_DEG) % N_BINS])
    wrapped = np.r_[angles, angles[0] + 360.0]
    gap_i = int(np.argmax(np.diff(wrapped)))
    start = angles[(gap_i + 1) % len(angles)]
    end = angles[gap_i] + (360.0 if gap_i < len(angles) - 1 else 0.0)
    if end - start >= 355.0:
        return np.arange(N_BINS)

    centers = np.arange(N_BINS) * BIN_WIDTH_DEG + BIN_WIDTH_DEG / 2
    centers_unwrapped = np.where(centers < start, centers + 360.0, centers)
    bins = np.flatnonzero((centers_unwrapped >= start) & (centers_unwrapped <= end))
    # 아주 좁은 대상도 시작·끝 bin을 빠뜨리지 않는다.
    edge_bins = np.array([int(start // BIN_WIDTH_DEG) % N_BINS,
                          int((end % 360.0) // BIN_WIDTH_DEG) % N_BINS])
    return np.unique(np.r_[bins, edge_bins])


def visible_polygon(obs_xy, obs_z, profile, tree, geoms, samples, radius):
    """STRtree로 반경 후보만 찾아 polygon 지면 조망을 판정한다."""
    point = Point(obs_xy)
    candidate_idx = np.asarray(tree.query(point.buffer(radius)), dtype=int)
    for idx in candidate_idx:
        distance = point.distance(geoms[idx])
        if distance > radius:
            continue
        target_angle = np.degrees(np.arctan2(-obs_z, distance))
        bins = polygon_bins(obs_xy, geoms[idx], samples[idx])
        if np.any(profile[bins] < target_angle):
            return True
    return False


# ============================================================================
# 1. 입력 로드 및 좌표계 변환
# ============================================================================

print("===== 1. 입력 로드 · EPSG:5179 변환 =====")
for required in [OBS_PATH, SKYLINE_PATH, WATER_PATH, PARKS_PATH, MOUNTAINS_PATH,
                 ROADS_PATH, ACCESS_PATH, COMPLEX_PATH, SCHOOLS_PATH]:
    assert required.exists(), f"입력 없음: {required}"

obs = pd.read_csv(OBS_PATH, sep="\t", dtype={"aptSeq": str})
skyline = np.load(SKYLINE_PATH, mmap_mode="r")
assert skyline.shape == (len(obs), N_BINS), (
    f"21.3 shape {skyline.shape}가 21.2 행수/72와 일치하지 않음")
assert skyline.dtype == np.float32, f"21.3 dtype이 float32가 아님: {skyline.dtype}"
assert {"aptSeq", "floor_band", "repr_floor", "obs_height"} <= set(obs.columns), \
    "21.2에 관측점 키 또는 높이 컬럼이 없다"

water_raw, water_geoms, water_samples = load_polygons(WATER_PATH)
parks_raw, park_geoms, park_samples = load_polygons(PARKS_PATH)
water_area = pd.to_numeric(water_raw["water_area_m2"], errors="coerce")
han_river_mask = water_area >= HAN_RIVER_MIN_AREA_M2
han_river_raw = water_raw.loc[han_river_mask].reset_index(drop=True)
han_river_geoms = water_geoms[han_river_mask.to_numpy()]
han_river_samples = [water_samples[i] for i in np.flatnonzero(han_river_mask.to_numpy())]
assert len(han_river_geoms) > 0, (
    f"면적 {HAN_RIVER_MIN_AREA_M2:,.0f}m² 이상 수계가 없다")
han_river_tree = STRtree(han_river_geoms)
park_tree = STRtree(park_geoms)

peaks = pd.read_csv(MOUNTAINS_PATH, sep="\t")
peak_geom = gpd.GeoSeries(gpd.points_from_xy(peaks["lon"], peaks["lat"]), crs="EPSG:4326").to_crs(METRIC_CRS)
peak_xy = np.column_stack([peak_geom.x, peak_geom.y])
peak_ele = pd.to_numeric(peaks["ele_m"], errors="coerce").to_numpy()
eligible_peak_mask = np.isfinite(peak_ele) & (peak_ele >= MOUNTAIN_MIN_ELE_M)
unknown_peak_mask = ~np.isfinite(peak_ele)
assert eligible_peak_mask.any(), (
    f"ele_m ≥ {MOUNTAIN_MIN_ELE_M:.0f}m 봉우리가 없다")
eligible_peak_tree = cKDTree(peak_xy[eligible_peak_mask])
unknown_peak_tree = cKDTree(peak_xy[unknown_peak_mask]) if unknown_peak_mask.any() else None
eligible_peak_ele = peak_ele[eligible_peak_mask]

# 21 관측점은 동 polygon centroid이지만 21.2에는 좌표를 저장하지 않는다. 15.2의
# osm_id polygon centroid를 다시 가져와 profile 행 순서와 동일하게 복원한다.
buildings = pd.read_csv(output_dir / "12.1.osm_buildings.txt", sep="\t")
obs_geom_by_osm = buildings.set_index("osm_id")["geometry"].map(wkt.loads)
obs_wgs84 = gpd.GeoSeries(obs["osm_id"].map(obs_geom_by_osm), crs="EPSG:4326")
assert obs_wgs84.notna().all(), "21.2 osm_id의 building geometry를 복원하지 못함"
obs_metric = obs_wgs84.to_crs(METRIC_CRS).centroid
obs_xy = np.column_stack([obs_metric.x, obs_metric.y])
obs_z = pd.to_numeric(obs["obs_height"], errors="coerce").to_numpy()
assert np.isfinite(obs_z).all(), "obs_height 결측"
print(f"  관측점 {len(obs):,} / skyline {skyline.shape} {skyline.dtype}")
print(f"  수계 {len(water_geoms):,}개 중 river_view 대상 {len(han_river_geoms):,}개 "
      f"(면적 합계 {water_area[han_river_mask].sum() / 1e6:.2f}km², "
      f"기준 ≥ {HAN_RIVER_MIN_AREA_M2 / 1e6:.1f}km²) / 공원 {len(park_geoms):,} / "
      f"봉우리 {len(peaks):,}개 중 대상 {int(eligible_peak_mask.sum()):,}개 "
      f"(ele_m ≥ {MOUNTAIN_MIN_ELE_M:.0f}m, 결측 {int(unknown_peak_mask.sum()):,})")


# ============================================================================
# 2. 관측점별 조망 판정
# ============================================================================

def process_observation(idx):
    profile = skyline[idx]
    xy = obs_xy[idx]
    river = visible_polygon(xy, obs_z[idx], profile, han_river_tree, han_river_geoms,
                            han_river_samples, WATER_RADIUS_M)
    park = visible_polygon(xy, obs_z[idx], profile, park_tree, park_geoms,
                           park_samples, PARK_RADIUS_M)

    mountain = False
    unknown_candidate = False
    for peak_idx in eligible_peak_tree.query_ball_point(xy, MOUNTAIN_RADIUS_M):
        delta = eligible_peak_tree.data[peak_idx] - xy
        distance = float(np.hypot(delta[0], delta[1]))
        target_angle = np.degrees(np.arctan2(
            eligible_peak_ele[peak_idx] - GROUND_APPROX_M - obs_z[idx], distance))
        if target_angle < MOUNTAIN_MIN_ANGLE_DEG:
            continue
        bearing = np.degrees(np.arctan2(delta[0], delta[1])) % 360.0
        bin_idx = int(bearing // BIN_WIDTH_DEG) % N_BINS
        if profile[bin_idx] < target_angle:
            mountain = True
            break
    if not mountain and unknown_peak_tree is not None:
        unknown_candidate = bool(unknown_peak_tree.query_ball_point(xy, MOUNTAIN_RADIUS_M))
    return river, park, (np.nan if unknown_candidate and not mountain else mountain)


print(f"\n===== 2. 관측점 조망 판정 ({N_WORKERS}개 프로세스) =====")
with MP_CONTEXT.Pool(N_WORKERS) as pool:
    view_result = list(pool.imap(process_observation, range(len(obs)), chunksize=128))
view = pd.DataFrame(view_result, columns=["river_view", "park_view", "mountain_view"])
view["river_view"] = view["river_view"].astype("boolean")
view["park_view"] = view["park_view"].astype("boolean")
view["mountain_view"] = view["mountain_view"].astype("boolean")
obs_view = pd.concat([obs[["aptSeq", "floor_band", "repr_floor", "obs_height", "open_span_max"]], view], axis=1)
print("  관측점 조망률:", (view.mean() * 100).round(2).to_dict())


# ============================================================================
# 3. 단지·층대 집계 및 elem_safe_route
# ============================================================================

print("\n===== 3. 단지·층대 집계 · elem_safe_route =====")

def any_or_na(series):
    """True가 하나면 True, 판정된 값이 모두 False면 False, 나머지는 NA."""
    if series.eq(True).any():
        return True
    if series.notna().any():
        return False
    return pd.NA


band = (obs_view.groupby(["aptSeq", "floor_band"], sort=False)
        .agg(river_view=("river_view", any_or_na),
             park_view=("park_view", any_or_na),
             mountain_view=("mountain_view", any_or_na),
             open_span_max=("open_span_max", "median"))
        .reset_index())
for col in ["river_view", "park_view", "mountain_view"]:
    band[col] = band[col].astype("boolean")
assert not band.duplicated(["aptSeq", "floor_band"]).any(), "29.1 키 중복"

complex_df = pd.read_csv(COMPLEX_PATH, sep="\t", dtype={"aptSeq": str})
anchor = complex_df[["aptSeq"]].copy()
master = pd.read_csv(output_dir / "14.1.geocoded_master.txt", sep="\t", dtype={"aptSeq": str})
anchor = anchor.merge(master[["aptSeq", "lat", "lon"]], on="aptSeq", how="left")
anchor_geom = gpd.GeoSeries(gpd.points_from_xy(anchor["lon"], anchor["lat"]), crs="EPSG:4326").to_crs(METRIC_CRS)

schools = pd.read_csv(SCHOOLS_PATH, sep="\t")
elementary = schools[schools["is_elementary"].astype(bool)]
school_geom = gpd.GeoSeries(gpd.points_from_xy(elementary["lon"], elementary["lat"]), crs="EPSG:4326").to_crs(METRIC_CRS)
school_xy = np.column_stack([school_geom.x, school_geom.y])
assert len(school_xy) > 0, "24.6에 초등학교 좌표가 없다"
school_tree = cKDTree(school_xy)

roads = pd.read_csv(ROADS_PATH, sep="\t")
arterial = roads[roads["highway"].isin(ARTERIAL_HIGHWAY)]
road_geoms = gpd.GeoSeries(arterial["geometry"].map(wkt.loads), crs="EPSG:4326").to_crs(METRIC_CRS).to_numpy()
assert len(road_geoms) > 0, "24.1에 간선도로가 없다"
road_tree = STRtree(road_geoms)

safe_route = np.full(len(anchor), np.nan, dtype=object)
valid_anchor = anchor[["lat", "lon"]].notna().all(axis=1).to_numpy()
for i in np.flatnonzero(valid_anchor):
    start = anchor_geom.iloc[i]
    _, school_idx = school_tree.query([start.x, start.y], k=1)
    route = LineString([start, school_geom.iloc[school_idx]])
    candidates = np.asarray(road_tree.query(route), dtype=int)
    safe_route[i] = not any(route.intersects(road_geoms[j]) for j in candidates)

river_ratio = obs_view.groupby("aptSeq")["river_view"].mean()
complex_view = anchor[["aptSeq"]].copy()
complex_view["river_view_ratio"] = complex_view["aptSeq"].map(river_ratio)
complex_view["elem_safe_route"] = pd.array(safe_route, dtype="boolean")


# ============================================================================
# 4. 저장
# ============================================================================

print("\n===== 4. 저장 =====")
band.to_csv(OUT_OBS, sep="\t", index=False, lineterminator="\n")
complex_view.to_csv(OUT_COMPLEX, sep="\t", index=False, lineterminator="\n")
print(f"  저장: {OUT_OBS} ({len(band):,}행)")
print(f"  저장: {OUT_COMPLEX} ({len(complex_view):,}행)")
print("  산출물 한계: mountain_view는 DEM 부재로 ele_m-30m 지반고 근사를 사용함")
print("  산출물 한계: elem_safe_route는 실제 보행경로가 아닌 직선 교차 근사임")


# ============================================================================
# 5. 자체 검증
# ============================================================================

print("\n===== 5. 자체 검증 =====")
# river_view의 대상과 한강 거리 검증의 대상은 같은 면적 필터 수계여야 한다.
han_geoms = han_river_geoms
han_tree = han_river_tree
anchor_points = np.array(anchor_geom.to_numpy(), dtype=object)
han_nearest = han_tree.nearest(anchor_points)
han_dist = np.array([anchor_points[i].distance(han_geoms[han_nearest[i]]) for i in range(len(anchor))])
river_any = obs_view.groupby("aptSeq")["river_view"].apply(lambda s: s.eq(True).any())
river_flag = anchor["aptSeq"].map(river_any).fillna(False).to_numpy(dtype=bool)
true_dist, false_dist = han_dist[river_flag], han_dist[~river_flag]
if len(true_dist) and len(false_dist):
    mw_p = mannwhitneyu(true_dist, false_dist, alternative="less").pvalue
    river_cluster_ok = np.median(true_dist) < np.median(false_dist) and mw_p < 0.05
else:
    mw_p, river_cluster_ok = np.nan, False
river_median_ok = bool(len(true_dist) and np.median(true_dist) < 1500.0)
print(f"  한강 최근접거리 중앙값: river_view=True {np.median(true_dist):.1f}m (n={len(true_dist)}), "
      f"False {np.median(false_dist):.1f}m (n={len(false_dist)}), Mann–Whitney p={mw_p:.3g}")

floor_order = ["LOW", "MID", "HIGH"]
view_rates_by_floor = (obs_view.groupby("floor_band")[["river_view", "park_view", "mountain_view"]]
                       .mean().reindex(floor_order))
view_rates_by_floor.loc["ALL"] = obs_view[["river_view", "park_view", "mountain_view"]].mean()
print("  참고 — 관측점 조망률(%, 층대/전체):")
print((view_rates_by_floor * 100).round(2).to_string())

# 한강변 고층 관측점에는 한강 조망이 대부분 남아 있어야 한다. HIGH 층대가 없는
# 단지는 분모에 넣지 않으며, 분모가 0이면 검증할 근거가 없으므로 FAIL이다.
high_river_by_apt = band.loc[band["floor_band"] == "HIGH"].set_index("aptSeq")["river_view"]
high_river = anchor["aptSeq"].map(high_river_by_apt)
han_300m = han_dist <= 300.0
han_300m_high = han_300m & high_river.notna().to_numpy()
if han_300m_high.any():
    han_300m_high_rate = float(high_river[han_300m_high].eq(True).mean())
    han_300m_high_ok = han_300m_high_rate >= 0.70
else:
    han_300m_high_rate, han_300m_high_ok = np.nan, False
print(f"  한강 300m 이내 HIGH 층대 river_view=True: "
      f"{han_300m_high_rate * 100:.1f}% (n={int(han_300m_high.sum())})")

han_over_3km = han_dist > 3000.0
han_over_3km_rate = float(river_flag[han_over_3km].mean()) if han_over_3km.any() else np.nan
han_over_3km_ok = bool(han_over_3km.any() and han_over_3km_rate < 0.05)
print(f"  한강 3km 초과 river_view=True: {han_over_3km_rate * 100:.2f}% "
      f"(n={int(han_over_3km.sum())})")

mountain_rate = float(obs_view["mountain_view"].mean())
mountain_rate_ok = 0.20 <= mountain_rate <= 0.80
print(f"  mountain_view 관측점 비율: {mountain_rate * 100:.2f}% "
      f"(판정 {int(obs_view['mountain_view'].notna().sum()):,}/{len(obs_view):,})")

monotonic_ok = True
for col in ["river_view", "park_view", "mountain_view"]:
    rate = view_rates_by_floor.loc[floor_order, col]
    ok = rate.notna().all() and rate["LOW"] <= rate["MID"] <= rate["HIGH"]
    monotonic_ok &= bool(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] 층대별 {col} 비율 단조 증가: {rate.to_dict()}")

range_ok = bool(((band["open_span_max"] >= 0) & (band["open_span_max"] <= 360)).all())
keys_ok = not band.duplicated(["aptSeq", "floor_band"]).any()
checks = [
    ("river_view 단지의 한강 인접성", river_cluster_ok, "중앙값 및 Mann–Whitney one-sided p<0.05"),
    ("river_view=True 한강 최근접거리 중앙값", river_median_ok, "중앙값 < 1,500m"),
    ("한강 300m 이내 HIGH 층대 river_view", han_300m_high_ok, "True 비율 ≥ 70%"),
    ("한강 3km 초과 river_view", han_over_3km_ok, "True 비율 < 5%"),
    ("mountain_view 관측점 비율", mountain_rate_ok, "20% ≤ 비율 ≤ 80%"),
    ("층대별 조망 비율 단조 증가", monotonic_ok, "LOW ≤ MID ≤ HIGH (세 대상)"),
    ("open_span_max 범위", range_ok, "0° ≤ 값 ≤ 360°"),
    ("aptSeq x floor_band 키 유일", keys_ok, f"{len(band):,}행"),
]
all_passed = True
for label, passed, detail in checks:
    all_passed &= bool(passed)
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}: {detail}")
assert all_passed, "자체 검증 실패 — 위 [FAIL]의 원인을 확인할 것"
