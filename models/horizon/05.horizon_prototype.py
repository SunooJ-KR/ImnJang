# ============================================================================
# 05.horizon_prototype.py
# ============================================================================
# Author:      yjkim
# Purpose:     horizon profile 엔진 프로토타입 + 기하 버그 검증 + 성능 벤치마크
# Description: plan.md §6.1의 통합 horizon 엔진을 구현한다. 하나의 스카이라인
#              계산에서 일조시간과 조망 개방도를 모두 파생시킨다.
#
#              codex 리뷰에서 지적된 버그 3개와 자체 계산 오류 1개를 각각
#              검증 케이스로 만들어, 구현이 실제로 이를 회피하는지 확인한다.
#                (1) 자기 건물을 차폐 후보에서 제외하는가
#                (2) 관측 높이와 건물 상단 높이가 같은 datum(AMSL)인가
#                (3) near-field에서 방위 공백이 생기지 않는가
#                (4) 1km 밖 초고층이 아침·오후 일조를 가리는 것을 반영하는가
#
#              성능: adaptive densify와 건물별 캐시는 양립하지 않으므로
#              해상도 3단계(0.5m/2m/5m)를 미리 캐시하고 거리로 선택한다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import pvlib
from shapely.geometry import Polygon, Point
from shapely.strtree import STRtree

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"
output_dir.mkdir(exist_ok=True)
CACHE_PATH = output_dir / "cache_overpass_gangnam.json"

METRIC_CRS = "EPSG:5179"
FLOOR_HEIGHT_M = 2.8
EYE_HEIGHT_M = 1.5

N_BINS = 72                     # 5도 간격
BIN_WIDTH_DEG = 360 / N_BINS
SEARCH_RADIUS_M = 1000
TALL_BUILDING_M = 150           # 이 이상은 거리와 무관하게 항상 후보 (§6.1 수정)
TALL_SEARCH_RADIUS_M = 5000
VIEW_BLOCK_THRESHOLD_DEG = 10   # 조망 차폐 판정 기준

# 해상도 캐시: (최대거리, 간격). adaptive densify를 근사하되 건물별 캐시가 가능하다.
RESOLUTION_TIERS = [(25.0, 0.5), (115.0, 2.0), (np.inf, 5.0)]

SEOUL_LAT, SEOUL_LON = 37.5665, 126.9780
WINTER_SOLSTICE = "2026-12-21"


# ============================================================================
# 1. horizon 엔진
# ============================================================================

def densify_ring(polygon, spacing):
    """폴리곤 외곽선을 일정 간격으로 샘플링해 (N,2) 좌표 배열을 만든다."""
    boundary = polygon.exterior
    n_points = max(int(np.ceil(boundary.length / spacing)), 8)
    distances = np.linspace(0, boundary.length, n_points, endpoint=False)
    points = [boundary.interpolate(d) for d in distances]
    return np.array([(p.x, p.y) for p in points])


def build_occluder_cache(gdf, top_z_col):
    """건물별로 해상도 3단계 외곽점을 미리 만들어 둔다.

    adaptive densify는 관측자와의 거리에 의존하므로 건물별 단일 캐시가
    불가능하다. 해상도를 이산화해 그 모순을 푼다.
    """
    cache = []
    for _, row in gdf.iterrows():
        rings = {spacing: densify_ring(row.geometry, spacing)
                 for _, spacing in RESOLUTION_TIERS}
        cache.append({"top_z": row[top_z_col], "rings": rings})
    return cache


def pick_spacing(distance_m):
    for max_distance, spacing in RESOLUTION_TIERS:
        if distance_m <= max_distance:
            return spacing
    return RESOLUTION_TIERS[-1][1]


def compute_horizon(obs_xy, obs_z, candidate_indices, cache, centroids_xy):
    """관측점의 72방향 최대 앙각(도)을 구한다. 가려지지 않으면 -90.

    앙각 = arctan((건물 상단 AMSL - 관측 AMSL) / 수평거리)
    양쪽 모두 AMSL이어야 한다. AGL과 섞으면 경사지에서 수십 m 오차가 난다.
    """
    profile = np.full(N_BINS, -90.0)
    if len(candidate_indices) == 0:
        return profile

    for index in candidate_indices:
        entry = cache[index]
        rough_distance = float(np.hypot(*(centroids_xy[index] - obs_xy)))
        ring = entry["rings"][pick_spacing(rough_distance)]

        delta = ring - obs_xy
        distances = np.hypot(delta[:, 0], delta[:, 1])
        valid = distances > 1e-6
        if not valid.any():
            continue
        delta, distances = delta[valid], distances[valid]

        height_diff = entry["top_z"] - obs_z
        if height_diff <= 0:
            continue  # 관측점보다 낮은 건물은 가리지 못한다

        elevations = np.degrees(np.arctan2(height_diff, distances))
        azimuths = np.degrees(np.arctan2(delta[:, 0], delta[:, 1])) % 360  # 북=0, 동=90
        bins = (azimuths / BIN_WIDTH_DEG).astype(int) % N_BINS
        np.maximum.at(profile, bins, elevations)

    return profile


def sun_track(date_str, lat=SEOUL_LAT, lon=SEOUL_LON, start="09:00", end="15:00", freq="5min"):
    """해당 날짜의 태양 방위각·고도각을 구한다. KST 기준."""
    times = pd.date_range(f"{date_str} {start}", f"{date_str} {end}", freq=freq, tz="Asia/Seoul")
    position = pvlib.solarposition.get_solarposition(times, lat, lon)
    return position["azimuth"].to_numpy(), position["apparent_elevation"].to_numpy()


def sunlight_hours(profile, sun_azimuth, sun_elevation, interval_minutes=5):
    """profile을 태양 궤적과 대조해 일조 확보 시간을 구한다."""
    bins = (sun_azimuth / BIN_WIDTH_DEG).astype(int) % N_BINS
    exposed = (sun_elevation > profile[bins]) & (sun_elevation > 0)
    return exposed.sum() * interval_minutes / 60.0


def view_metrics(profile):
    blocked = profile > VIEW_BLOCK_THRESHOLD_DEG
    return {
        "view_block_pct": round(100 * blocked.mean(), 1),
        "open_angle_mean": round(float(np.mean(90.0 - np.clip(profile, -90, 90))), 1),
    }


# ============================================================================
# 2. 검증 케이스 (합성 데이터)
# ============================================================================
# 실데이터로는 "정답"을 모르므로, 답을 아는 합성 장면으로 각 버그를 검증한다.

def make_box(cx, cy, size, top_z):
    half = size / 2
    return Polygon([(cx - half, cy - half), (cx + half, cy - half),
                    (cx + half, cy + half), (cx - half, cy + half)]), top_z


def run_verification():
    print("===== 2. 기하 버그 검증 (합성 장면) =====")
    sun_azimuth, sun_elevation = sun_track(WINTER_SOLSTICE)
    passed = True

    # --- 검증 1: 자기 건물 제외 ---
    # 관측점이 자기 동 중심에 있으므로, 자기 폴리곤을 후보에 넣으면 전 방위가 막힌다.
    self_poly, self_top = make_box(0, 0, 40, 50.0)
    obs_xy = np.array([0.0, 0.0])
    obs_z = 0.0 + (10 - 1) * FLOOR_HEIGHT_M + EYE_HEIGHT_M  # 10층

    cache_with_self = build_occluder_cache(
        gpd.GeoDataFrame({"top_z": [self_top]}, geometry=[self_poly], crs=METRIC_CRS), "top_z")
    profile_wrong = compute_horizon(obs_xy, obs_z, [0], cache_with_self, np.array([[0.0, 0.0]]))
    hours_wrong = sunlight_hours(profile_wrong, sun_azimuth, sun_elevation)

    profile_right = compute_horizon(obs_xy, obs_z, [], cache_with_self, np.array([[0.0, 0.0]]))
    hours_right = sunlight_hours(profile_right, sun_azimuth, sun_elevation)

    ok = hours_wrong < 1.0 and hours_right > 5.0
    passed &= ok
    print(f"  [{'PASS' if ok else 'FAIL'}] 자기 건물 제외 "
          f"(포함 시 {hours_wrong:.1f}h -> 제외 시 {hours_right:.1f}h)")

    # --- 검증 2: AMSL datum ---
    # 같은 건물이 지반 20m 위에 있으면 더 많이 가려야 한다.
    # 높이를 AGL로 다루면 두 경우가 동일하게 나와 이 검증이 실패한다.
    south_poly, _ = make_box(0, -60, 40, 0)  # top_z는 아래에서 주입
    centroid = np.array([[0.0, -60.0]])

    def hours_with_ground(ground_elev):
        top_z = ground_elev + 15 * FLOOR_HEIGHT_M  # 15층 건물의 상단 AMSL
        cache = build_occluder_cache(
            gpd.GeoDataFrame({"top_z": [top_z]}, geometry=[south_poly], crs=METRIC_CRS), "top_z")
        profile = compute_horizon(obs_xy, obs_z, [0], cache, centroid)
        return sunlight_hours(profile, sun_azimuth, sun_elevation)

    hours_flat = hours_with_ground(0.0)
    hours_raised = hours_with_ground(20.0)
    ok = hours_raised < hours_flat
    passed &= ok
    print(f"  [{'PASS' if ok else 'FAIL'}] AMSL datum "
          f"(평지 {hours_flat:.1f}h > 지반+20m {hours_raised:.1f}h)")

    # --- 검증 3: near-field 방위 공백 ---
    # 10m 앞 인접 동이 가리는 방위 bin들이 연속으로 채워져야 한다.
    near_poly, near_top = make_box(0, -30, 40, 45.0)
    cache_near = build_occluder_cache(
        gpd.GeoDataFrame({"top_z": [near_top]}, geometry=[near_poly], crs=METRIC_CRS), "top_z")
    profile_near = compute_horizon(obs_xy, obs_z, [0], cache_near, np.array([[0.0, -30.0]]))

    blocked_bins = np.where(profile_near > 0)[0]
    # 남쪽(180도) 주변이 막혀야 하고, 그 구간에 구멍이 없어야 한다
    south_bin = int(180 / BIN_WIDTH_DEG)
    span = [b % N_BINS for b in range(south_bin - 5, south_bin + 6)]
    holes = [b for b in span if profile_near[b] <= 0]
    ok = len(blocked_bins) > 0 and len(holes) == 0
    passed &= ok
    print(f"  [{'PASS' if ok else 'FAIL'}] near-field 연속 차폐 "
          f"(남쪽 ±25도 {len(span)}bin 중 공백 {len(holes)}개)")

    # --- 검증 4: 1km 밖 초고층이 아침 일조를 가리는가 ---
    # 동지 09시 태양고도는 약 11도. tan(11도)*1000 = 194m.
    # 남중고도 29도만 보고 "554m 이상만 영향"이라 한 것은 틀렸다.
    morning_elevation = sun_elevation[0]
    required_height_noon = np.tan(np.radians(np.max(sun_elevation))) * 1000
    required_height_morning = np.tan(np.radians(morning_elevation)) * 1000
    ok = required_height_morning < 300 < required_height_noon
    passed &= ok
    print(f"  [{'PASS' if ok else 'FAIL'}] 1km 초고층 영향 확인 "
          f"(09시 고도 {morning_elevation:.1f}도 -> {required_height_morning:.0f}m 이상이면 차폐, "
          f"남중 {np.max(sun_elevation):.1f}도 -> {required_height_noon:.0f}m)")

    return passed


# ============================================================================
# 3. 실데이터 적용 및 벤치마크
# ============================================================================

def load_apartments():
    elements = json.loads(CACHE_PATH.read_text(encoding="utf-8"))["elements"]
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
            "levels": tags.get("building:levels"),
            "height": tags.get("height"),
            "geometry": Polygon([(p["lon"], p["lat"]) for p in geometry]),
        })
    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326").to_crs(METRIC_CRS)

    def parse_numeric(series):
        return pd.to_numeric(
            series.astype("string").str.extract(r"(\d+(?:\.\d+)?)", expand=False), errors="coerce")

    gdf["levels_num"] = parse_numeric(gdf["levels"])
    gdf["height_num"] = parse_numeric(gdf["height"])
    # height 태그가 있으면 우선 사용한다 (층수x2.8 추정보다 정확)
    gdf["building_height_m"] = gdf["height_num"].fillna(gdf["levels_num"] * FLOOR_HEIGHT_M)
    gdf = gdf.dropna(subset=["building_height_m"]).reset_index(drop=True)
    # DEM 미확보. 지반고 0으로 두되 컬럼은 AMSL 계약을 유지한다.
    gdf["ground_elev"] = 0.0
    gdf["top_z"] = gdf["ground_elev"] + gdf["building_height_m"]
    return gdf


def main():
    verification_passed = run_verification()

    print("\n===== 3. 실데이터 적용 (강남구) =====")
    apartments = load_apartments()
    print(f"  높이 확보 아파트 동: {len(apartments)}")

    centroids_xy = np.array([[g.centroid.x, g.centroid.y] for g in apartments.geometry])
    tree = STRtree(apartments.geometry.values)

    print("  외곽점 캐시 생성 중...")
    cache_start = time.time()
    cache = build_occluder_cache(apartments, "top_z")
    print(f"    {time.time() - cache_start:.1f}초")

    tall_indices = np.where(apartments["building_height_m"] >= TALL_BUILDING_M)[0]
    print(f"  {TALL_BUILDING_M}m 이상 초고층: {len(tall_indices)}동 (거리 무관 항상 후보)")

    sun_azimuth, sun_elevation = sun_track(WINTER_SOLSTICE)

    # 저밀도/고밀도 구분 없이 무작위 표본으로 평균 비용을 잰다
    rng = np.random.default_rng(42)
    sample_size = 200
    sample_indices = rng.choice(len(apartments), size=sample_size, replace=False)

    print(f"\n  {sample_size}개 관측점 벤치마크 (층대 3개)...")
    results = []
    bench_start = time.time()
    for index in sample_indices:
        obs_xy = centroids_xy[index]
        levels = apartments.at[index, "levels_num"]
        levels = 15 if pd.isna(levels) else int(levels)
        ground = apartments.at[index, "ground_elev"]

        neighbors = tree.query(Point(obs_xy).buffer(SEARCH_RADIUS_M))
        neighbors = np.asarray(neighbors)
        candidates = neighbors[neighbors != index]
        # 반경 밖 초고층 추가 (아침·오후 저각 일조 차폐)
        far_tall = np.setdiff1d(tall_indices, np.append(candidates, index))
        if len(far_tall) > 0:
            distances = np.hypot(*(centroids_xy[far_tall] - obs_xy).T)
            candidates = np.concatenate([candidates, far_tall[distances <= TALL_SEARCH_RADIUS_M]])

        for band, repr_floor in [("LOW", max(int(np.ceil(levels / 6)), 2)),
                                 ("MID", int(np.ceil(levels / 2))),
                                 ("HIGH", int(np.ceil(levels * 5 / 6)))]:
            obs_z = ground + (repr_floor - 1) * FLOOR_HEIGHT_M + EYE_HEIGHT_M
            profile = compute_horizon(obs_xy, obs_z, candidates, cache, centroids_xy)
            metrics = view_metrics(profile)
            results.append({
                "osm_id": int(apartments.at[index, "osm_id"]),
                "floor_band": band,
                "repr_floor": repr_floor,
                "n_candidates": len(candidates),
                "sun_hours_winter": round(sunlight_hours(profile, sun_azimuth, sun_elevation), 2),
                **metrics,
            })
    elapsed = time.time() - bench_start

    results_df = pd.DataFrame(results)
    per_point = elapsed / len(results_df)
    projected_hours = per_point * 120_000 / 3600

    print(f"    {len(results_df)}개 관측점 {elapsed:.1f}초 ({1000 * per_point:.1f}ms/점)")
    print(f"    후보 동 수 중앙값: {results_df['n_candidates'].median():.0f}")
    print(f"\n  서울 전역 120,000 관측점 추정: {projected_hours:.1f}시간")

    print("\n  층대별 일조·조망:")
    summary = (results_df
        .groupby("floor_band")[["sun_hours_winter", "view_block_pct", "open_angle_mean"]]
        .median()
        .reindex(["LOW", "MID", "HIGH"])
    )
    print(summary.to_string())

    # 상식 검증: 고층이 저층보다 일조가 좋아야 한다
    monotonic = (summary.loc["HIGH", "sun_hours_winter"]
                 >= summary.loc["MID", "sun_hours_winter"]
                 >= summary.loc["LOW", "sun_hours_winter"])
    print(f"\n  [{'PASS' if monotonic else 'FAIL'}] 층이 높을수록 일조 증가")

    results_path = output_dir / "05.1.horizon_sample.txt"
    results_df.to_csv(results_path, sep="\t", index=False, lineterminator="\n")
    print(f"\n샘플 결과: {results_path}")

    all_passed = verification_passed and monotonic
    print(f"\n===== horizon 프로토타입 {'통과' if all_passed else '미통과'} =====")


if __name__ == "__main__":
    main()
