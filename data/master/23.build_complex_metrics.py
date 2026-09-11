# ============================================================================
# 23.build_complex_metrics.py
# ============================================================================
# Author:      yjkim
# Purpose:     지금까지의 산출물을 웹앱이 그대로 소비할 최종 3종 테이블로 합친다
# Description: docs/data-model-and-ui.md §7 스키마(complex / complex_metrics /
#              horizon_profile)를 계약으로 삼아, 있는 데이터만 채우고 없는
#              컬럼은 만들되 전량 NULL로 남긴다(plan.md R13 — 추정/0채움 금지).
#
#              match_confidence 매핑(HIGH/MEDIUM/LOW/FAILED)은 준공년도 독립
#              검증 실측치 기준이다(docs/decisions.md 166행대):
#                HIGH 99.1% / NAME 94.6% / LOW 84.4% / LEVEL_MISMATCH 19.0%
#              LEVEL_MISMATCH는 거의 무작위 수준(19.0%)이라 FAILED로 묶는다.
#              MIXED(단지 내 동들의 등급이 섞임)는 표에 실측치가 없으므로,
#              15.2(building_assigned)에서 그 단지에 속한 동들의 실제 등급 중
#              가장 낮은 등급(worst-case)을 골라 같은 표로 다시 매핑한다 —
#              단지 신뢰도를 낙관적으로 부풀리지 않기 위해서다.
#
#              최종 통합(6종 신규 입력)에서 추가한 것:
#              - 19.1(건축물대장) bcr/far/bjd_code/주차대수: 표제부는 이 값들을
#                지번(대지) 단위로 동마다 반복 기재한다. 그대로 합치면 동 수만큼
#                부풀려지므로(실측: 종로구 평창동 108 — 16개 동 전부 동일값
#                반복) 지번별 0이 아닌 값 중 최댓값 하나만 대표값으로 쓴다.
#                (기존 주석에 있던 "19를 다시 조인하면 안 된다"는 15.1의
#                reg_dong/reg_units 얘기였다 — 그 두 컬럼은 여기서도 그대로
#                17의 결과를 쓰고, bcr/far/bjd_code/parking_per_hh만 새로 잡는다.)
#              - 26.1(교통) station_ridership_daily/station_congestion_peak/
#                road_centerline_m/rail_centerline_m — apt_seq 키로 그대로 병합.
#                road_arterial_dist_m/road_secondary_dist_m은 스키마 외 컬럼이지만
#                위계별 근거를 남기려 station_congestion_peak과 같은 방식으로 追加.
#              - 24.3(공원 폴리곤) park_area_m2 + park_m 재계산: 22.1의 park_m은
#                POI(node, 대표점 1개) 기준이라 폴리곤 경계 기준과 달라진다.
#                STRtree로 폴리곤 최근접을 다시 잡아 park_m과 park_area_m2가
#                같은 공원을 가리키도록 통일한다(기존 park_m을 덮어씀).
#              - 24.4(야간상권) nightlife_300m, 24.5(상점) dept_store_m/
#                supermarket_m: cKDTree 반경/최근접.
#              - 24.6(학교) mid_school_m/high_school_m: is_middle/is_high가
#                이미 isced:level 태그(우선) + 이름(보완)으로 판정되어 있어
#                NEIS 없이도 급별 구분이 가능하다. elem_school_m은 22.1(이름
#                기반) 값을 그대로 둔다 — 24.6(isced 기반)으로 다시 재 보니
#                9,160단지 중 99.96%가 10m 이내로 사실상 동일해 교체 실익이 없다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import geopandas as gpd
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.spatial import cKDTree
from shapely import wkt
from shapely.strtree import STRtree

work_dir = Path(__file__).resolve().parents[2]
output_dir = work_dir / "output"

COMPLEX_PATH = output_dir / "15.1.complex_final.txt"
BUILDING_ASSIGNED_PATH = output_dir / "15.2.building_assigned.txt"
GEOCODED_PATH = output_dir / "14.1.geocoded_master.txt"
HORIZON_PATH = output_dir / "21.1.complex_metrics.txt"
ACCESS_PATH = output_dir / "22.1.access_metrics.txt"
LEDGER_PATH = output_dir / "19.1.building_ledger.txt"
TRANSIT_PATH = output_dir / "26.1.transit_metrics.txt"
PARKS_PATH = output_dir / "24.3.osm_parks.txt"
NIGHTLIFE_PATH = output_dir / "24.4.osm_nightlife.txt"
SHOPS_PATH = output_dir / "24.5.osm_shops.txt"
SCHOOLS_PATH = output_dir / "24.6.osm_schools.txt"

OUT_COMPLEX = output_dir / "23.1.complex.txt"
OUT_METRICS = output_dir / "23.2.complex_metrics.txt"
OUT_HORIZON = output_dir / "23.3.horizon_profile.txt"

METRIC_CRS = "EPSG:5179"
NIGHTLIFE_RADIUS_M = 300.0

# docs/decisions.md 166행대 — 준공년도 ±2년 일치율 실측
CONFIDENCE_MAP = {
    "HIGH": "HIGH",             # 99.1%
    "NAME": "MEDIUM",           # 94.6%
    "LOW": "LOW",               # 84.4%
    "LEVEL_MISMATCH": "FAILED", # 19.0% — 사실상 무작위 수준이라 FAILED로 묶음
}
# MIXED 재해석용 등급 순위 (낮을수록 신뢰도 낮음)
CONFIDENCE_RANK = {"LEVEL_MISMATCH": 0, "LOW": 1, "NAME": 2, "HIGH": 3}
RANK_TO_LABEL = {v: k for k, v in CONFIDENCE_RANK.items()}

# 스펙에는 있으나 이번 입력으로도 만들 수 없는 컬럼 (전량 NULL)
NULL_ONLY_METRICS = [
    "river_view_ratio", "traffic_weekday", "traffic_weekend",
    "elem_safe_route", "daycare_500m",
    "tertiary_hosp_m", "general_hosp_m", "clinic_1km", "pediatric_1km",
    "dawn_delivery",
]
NULL_ONLY_HORIZON = [
    "repr_floor", "obs_height", "sun_hours_spring", "open_span_max",
    "river_view", "park_view", "mountain_view",
]


def normalize_address(text):
    """등록 정보 '서울특별시 강서구 마곡동 744' <-> 실거래 gu/umd/jibun 를 같은 키로.
    15.assign_dong_all.py / 20.verify_dong_names.py / 21.horizon_batch.py와 동일."""
    text = text.fillna("").astype(str).str.strip()
    text = text.str.replace(r"^서울(특별시)?\s*", "", regex=True)
    text = text.str.replace(r"\s+", " ", regex=True)
    return text.str.replace(r"산\s+(?=\d)", "산", regex=True)


# ============================================================================
# 1. 데이터 로드
# ============================================================================

print("===== 1. 데이터 로드 =====")

complex_df = pd.read_csv(COMPLEX_PATH, sep="\t")
building_df = pd.read_csv(BUILDING_ASSIGNED_PATH, sep="\t")
geocoded_df = pd.read_csv(GEOCODED_PATH, sep="\t")
horizon_df = pd.read_csv(HORIZON_PATH, sep="\t")
access_df = pd.read_csv(ACCESS_PATH, sep="\t")

assert complex_df["aptSeq"].is_unique, "15.1에 aptSeq 중복이 있다"
print(f"  15.1 단지 마스터: {len(complex_df)}행")
print(f"  21.1 층대 지표:   {len(horizon_df)}행 ({horizon_df['aptSeq'].nunique()}단지)")
print(f"  22.1 접근성 지표: {len(access_df)}행")


# ============================================================================
# 2. match_confidence 매핑 (MIXED worst-case 재해석 포함)
# ============================================================================

print("\n===== 2. match_confidence 매핑 =====")

confidence = complex_df["confidence"].copy()
is_mixed = confidence == "MIXED"
print(f"  MIXED 단지: {int(is_mixed.sum())}건 — worst-case 등급으로 재해석")

mixed_apts = complex_df.loc[is_mixed, "aptSeq"]
graded = building_df[
    building_df["aptSeq"].isin(mixed_apts) & building_df["assign_confidence"].notna()
].copy()
graded["rank"] = graded["assign_confidence"].map(CONFIDENCE_RANK)
worst_rank = graded.groupby("aptSeq")["rank"].min()
worst_label = worst_rank.map(RANK_TO_LABEL)

confidence.loc[is_mixed] = complex_df.loc[is_mixed, "aptSeq"].map(worst_label).to_numpy()
assert confidence.loc[is_mixed].isna().sum() == 0, "MIXED 단지 중 worst-case로 못 채운 건이 있다"

match_confidence = confidence.map(CONFIDENCE_MAP)
polygon_matched = confidence.notna()
match_confidence = match_confidence.fillna("FAILED")  # 미배정(confidence 결측)도 FAILED

n_unassigned = int(confidence.isna().sum())
print(f"  미배정(polygon_matched=False) 단지: {n_unassigned}건 — 행은 유지한다 (decisions.md #42)")
print("  match_confidence 분포:")
print(match_confidence.value_counts().to_string())


# ============================================================================
# 3. 건축물대장(19.1) 지번 단위 대표값 계산
# ============================================================================
# bcr·far·주차대수는 표제부에 동마다 반복 기재되는 "지번(대지) 단위" 값이다.
# 실측 결과 같은 지번의 동들 중 90%가 동일 값을 반복하고 나머지 10%는 한
# 레코드에만 기재된 부분 누락이다. 그대로 합치면 동 수만큼 부풀려지므로
# 지번별 0이 아닌 값 중 최댓값 하나만 대표값으로 쓴다. 전부 0(=미기재)이면
# 대표값도 NaN — 0으로 채우지 않는다(plan.md R13).

print("\n===== 3. 건축물대장 지번 대표값 계산 =====")

ledger_df = pd.read_csv(LEDGER_PATH, sep="\t")

# join_key = 정규화된 지번. 15/20/21과 동일 키로 aptSeq <-> 19.1을 잇는다.
geocoded_df["join_key"] = normalize_address(
    geocoded_df["gu"].fillna("") + " " + geocoded_df["umd_name"].fillna("")
    + " " + geocoded_df["jibun"].fillna(""))
apt_to_join_key = geocoded_df.set_index("aptSeq")["join_key"]

apt_join_key_all = complex_df["aptSeq"].map(apt_to_join_key)
n_matched_jibun = int(apt_join_key_all.isin(ledger_df["join_key"]).sum())
print(f"  지번(join_key) 매칭: {n_matched_jibun}/{len(complex_df)}단지 "
      f"({n_matched_jibun / len(complex_df) * 100:.1f}%) — 이게 채움률의 상한이다")


def rep_max_nonzero(series):
    """지번별로 0(미기재)을 제외한 값 중 최댓값 하나를 대표값으로 취한다."""
    nonzero = series[series != 0]
    return nonzero.max() if len(nonzero) else np.nan


PARKING_COLS = ["indr_auto_utcnt", "oudr_auto_utcnt", "indr_mech_utcnt", "oudr_mech_utcnt"]
ledger_rep = ledger_df.groupby("join_key")[["bcr", "far"] + PARKING_COLS].agg(rep_max_nonzero)
# bjd_code는 지번 내에서 항상 일치함을 실측 확인(8,181개 지번 전부 유일값) — first()로 충분
ledger_bjd = ledger_df.groupby("join_key")["bjd_code"].first()
# 4개 유형 중 있는 값만 더한다(min_count=1) — 전부 미기재인 지번만 NaN으로 남긴다
parking_total_by_key = ledger_rep[PARKING_COLS].sum(axis=1, min_count=1)

print(f"  bcr/far 지번 대표값 채움률: {ledger_rep['bcr'].notna().mean() * 100:.1f}% "
      f"({ledger_rep['bcr'].notna().sum()}/{len(ledger_rep)}개 지번)")
print(f"  주차대수 지번 대표값(4종 중 1개 이상) 채움률: "
      f"{parking_total_by_key.notna().mean() * 100:.1f}%")

# 참고: 대표값(최댓값 1개) 대신 동별 전량을 그대로 합쳤다면 얼마나 부풀려졌을지 — 버그 재현 비교
naive_sum_by_key = ledger_df.groupby("join_key")[PARKING_COLS].sum().sum(axis=1)
inflation = (naive_sum_by_key / parking_total_by_key).replace([np.inf, -np.inf], np.nan).dropna()
print(f"  참고: 동별 전량 합산(버그) 대비 대표값 배율 — 중앙값 {inflation.median():.1f}배, "
      f"최대 {inflation.max():.1f}배 (지번당 동 수만큼 부풀려지는 것을 막음)")

n_bcr_over_100 = int((ledger_rep["bcr"] > 100).sum())
if n_bcr_over_100:
    print(f"  [참고] 원본 표제부 자체에 건폐율(bcr) 100%를 넘는 값이 {n_bcr_over_100}건 있다 "
          f"(예: {ledger_rep['bcr'].idxmax()} = {ledger_rep['bcr'].max():.0f}%) — 대장 원본 오기재로 "
          f"추정되나 추정치로 고치지 않고 원본값을 그대로 통과시킨다")


# ============================================================================
# 4. output/23.1.complex.txt
# ============================================================================

print("\n===== 4. complex 테이블 조립 =====")

complex_out = complex_df[["aptSeq", "apt_name", "build_year", "reg_dong", "reg_units"]].rename(
    columns={
        "aptSeq": "apt_seq",
        "apt_name": "name",
        "build_year": "built_year",
        "reg_dong": "building_count",   # 등록 동수 — OSM 매칭 실패 단지도 채워짐
        "reg_units": "total_households",  # 등록 세대수
    }
)
complex_out = complex_out.merge(
    geocoded_df[["aptSeq", "lat", "lon"]].rename(columns={"aptSeq": "apt_seq", "lon": "lng"}),
    on="apt_seq", how="left",
)
complex_out["polygon_matched"] = polygon_matched.to_numpy()
complex_out["match_confidence"] = match_confidence.to_numpy()

apt_join_key = complex_out["apt_seq"].map(apt_to_join_key)
complex_out["bjd_code"] = apt_join_key.map(ledger_bjd).to_numpy()
complex_out["far"] = apt_join_key.map(ledger_rep["far"]).to_numpy()
complex_out["bcr"] = apt_join_key.map(ledger_rep["bcr"]).to_numpy()
parking_total = apt_join_key.map(parking_total_by_key)
complex_out["parking_per_hh"] = (parking_total / complex_out["total_households"]).to_numpy()

complex_out = complex_out[[
    "apt_seq", "name", "bjd_code", "lat", "lng", "built_year", "total_households",
    "building_count", "far", "bcr", "parking_per_hh", "polygon_matched", "match_confidence",
]]

complex_out.to_csv(OUT_COMPLEX, sep="\t", index=False)
print(f"  저장: {OUT_COMPLEX} ({len(complex_out)}행)")


# ============================================================================
# 5. output/23.2.complex_metrics.txt
# ============================================================================

print("\n===== 5. complex_metrics 테이블 조립 =====")

# 좌표계 변환 검증 — 이 저장소의 반복 버그(plan.md §5). 22.access_metrics.py /
# 26.transit_metrics.py와 동일한 두 지점(서울시청 <-> 강남역, 실제 약 8.5km)으로 재검증한다.
landmarks = gpd.GeoSeries(
    gpd.points_from_xy([126.9780, 127.0276], [37.5665, 37.4979]), crs="EPSG:4326"
).to_crs(METRIC_CRS)
landmark_dist_m = landmarks.iloc[0].distance(landmarks.iloc[1])
assert 8_000 <= landmark_dist_m <= 9_500, (
    f"좌표계 변환 오류 의심 — 실제 약 8.5km인데 {landmark_dist_m / 1000:.2f}km로 계산됨")
print(f"  [PASS] 좌표계 변환 정상 (서울시청 <-> 강남역 {landmark_dist_m:,.0f}m)")

# horizon 파생 — 정렬 기본축은 decisions.md #37: winter_total_hours_8_16
sun_view_agg = (horizon_df
    .groupby("aptSeq")
    .agg(
        sun_hours_avg=("winter_total_hours_8_16", "mean"),
        sun_hours_best=("winter_total_hours_8_16", "max"),
        view_open_avg=("open_angle_mean", "mean"),
    )
    .reset_index()
    .rename(columns={"aptSeq": "apt_seq"})
)

access_selected = access_df[[
    "aptSeq", "subway_dist_m", "subway_elev_diff_m", "elementary_dist_m",
    "mart_dist_m", "park_dist_m", "convenience_500m", "restaurant_500m",
]].rename(columns={
    "aptSeq": "apt_seq",
    "subway_dist_m": "station_dist_m",
    "subway_elev_diff_m": "station_elev_diff",
    "elementary_dist_m": "elem_school_m",
    "mart_dist_m": "mart_m",
    "park_dist_m": "park_m",
    "convenience_500m": "cvs_500m",
    "restaurant_500m": "restaurant_500m",
})
# 추정치. algorithms.md §6.4: 직선거리 x 1.3(우회계수) / 4km/h
access_selected["station_walk_min_est"] = access_selected["station_dist_m"] * 1.3 / (4000 / 60)

# apt_seq 전체 모집단은 complex와 동일한 9,160단지로 둔다. access 지표는 좌표만
# 있으면 계산되므로 폴리곤 매칭 실패 단지도 채워진다 — decisions.md #42.
metrics_out = complex_out[["apt_seq"]].merge(sun_view_agg, on="apt_seq", how="left")
metrics_out = metrics_out.merge(access_selected, on="apt_seq", how="left")

# 26.1 교통·근접도 — apt_seq 키로 이미 정렬돼 있어 그대로 병합
transit_df = pd.read_csv(TRANSIT_PATH, sep="\t")
assert transit_df["apt_seq"].is_unique, "26.1에 apt_seq 중복이 있다"
metrics_out = metrics_out.merge(transit_df, on="apt_seq", how="left")
print(f"  26.1 교통 지표 병합: station_ridership_daily 채움률 "
      f"{metrics_out['station_ridership_daily'].notna().mean() * 100:.1f}%, "
      f"road_centerline_m 채움률 {metrics_out['road_centerline_m'].notna().mean() * 100:.1f}%")

# --- 신규 OSM POI(공원 폴리곤/야간상권/상점/학교) 최근접 계산용 단지 앵커 ---
# 좌표 결측 단지는 22.1과 동일 원칙으로 제외 후 apt_seq로 다시 매핑한다(값 없으면 NaN).
anchor = geocoded_df.dropna(subset=["lat", "lon"])[["aptSeq", "lat", "lon"]].rename(
    columns={"aptSeq": "apt_seq"})
anchor_geom = gpd.GeoSeries(
    gpd.points_from_xy(anchor["lon"], anchor["lat"]), crs="EPSG:4326").to_crs(METRIC_CRS)
anchor_xy = np.column_stack([anchor_geom.x, anchor_geom.y])
anchor_apt_seq = anchor["apt_seq"].to_numpy()


def to_xy(lon, lat):
    geom = gpd.GeoSeries(gpd.points_from_xy(lon, lat), crs="EPSG:4326").to_crs(METRIC_CRS)
    return np.column_stack([geom.x, geom.y])


def nearest_point_dist_m(target_xy):
    """단지 앵커 각각에서 target_xy까지 최근접 직선거리(m). target이 비어 있으면
    전량 NaN — 22.access_metrics.py와 동일 원칙(없는 값을 0/상수로 메우지 않음)."""
    if len(target_xy) == 0:
        return np.full(len(anchor_xy), np.nan)
    dist, _ = cKDTree(target_xy).query(anchor_xy, k=1)
    return dist


def count_within_radius_m(target_xy, radius):
    """반경 radius(m) 내 target_xy 개수. 0건은 결측이 아니라 관측된 사실이다."""
    if len(target_xy) == 0:
        return np.zeros(len(anchor_xy), dtype=int)
    neighbor_lists = cKDTree(target_xy).query_ball_point(anchor_xy, r=radius)
    return np.array([len(lst) for lst in neighbor_lists])


# --- 공원(24.3, 폴리곤): park_m을 폴리곤 경계 기준으로 재계산해 park_area_m2와
#     같은 공원을 가리키도록 통일한다. STRtree로 후보를 좁힌 뒤 정확한 거리를 잰다
#     (26.transit_metrics.py의 nearest_line_distance_m과 동일 패턴, 대상만 폴리곤).
parks_df = pd.read_csv(PARKS_PATH, sep="\t")
park_geoms = gpd.GeoSeries(
    parks_df["geometry"].map(wkt.loads), crs="EPSG:4326").to_crs(METRIC_CRS).to_numpy()
park_areas = parks_df["park_area_m2"].to_numpy()
park_tree = STRtree(park_geoms)
anchor_points = gpd.points_from_xy(anchor_xy[:, 0], anchor_xy[:, 1])
park_nearest_idx = park_tree.nearest(anchor_points)
park_dist = np.array([
    anchor_points[i].distance(park_geoms[park_nearest_idx[i]]) for i in range(len(anchor_points))
])
park_m_by_apt = pd.Series(park_dist, index=anchor_apt_seq)
park_area_by_apt = pd.Series(park_areas[park_nearest_idx], index=anchor_apt_seq)

# --- 야간상권(24.4): 반경 300m 개수 ---
nightlife_df = pd.read_csv(NIGHTLIFE_PATH, sep="\t")
nightlife_xy = to_xy(nightlife_df["lon"], nightlife_df["lat"])
nightlife_by_apt = pd.Series(count_within_radius_m(nightlife_xy, NIGHTLIFE_RADIUS_M), index=anchor_apt_seq)

# --- 상점(24.5): shop 원본 태그로 department_store/supermarket만 추려 최근접 ---
shops_df = pd.read_csv(SHOPS_PATH, sep="\t")
dept_xy = to_xy(*shops_df.loc[shops_df["shop_type"] == "department_store", ["lon", "lat"]].to_numpy().T)
mart2_xy = to_xy(*shops_df.loc[shops_df["shop_type"] == "supermarket", ["lon", "lat"]].to_numpy().T)
dept_store_by_apt = pd.Series(nearest_point_dist_m(dept_xy), index=anchor_apt_seq)
supermarket_by_apt = pd.Series(nearest_point_dist_m(mart2_xy), index=anchor_apt_seq)

# --- 학교(24.6): is_middle/is_high는 isced:level 태그(우선) + 이름(보완)으로 이미
#     판정돼 있다("2;3" 중고통합도 set 분할로 둘 다 True 처리됨) — NEIS 없이도 급별
#     구분이 가능하다. level_source == "none"(211건, 태그도 이름도 없어 급별 판정
#     불가)은 is_middle/is_high가 이미 False라 최근접 대상에서 자동 제외된다.
#     제외로 인해 특정 구가 통째로 비는지 단지 앵커 최근접 구 기준으로 확인한 결과,
#     25개 구 전부 중/고교 판정 1건 이상을 보유해 구 단위 공백은 없다(보고 참고).
schools_df = pd.read_csv(SCHOOLS_PATH, sep="\t")
mid_xy = to_xy(schools_df.loc[schools_df["is_middle"], "lon"], schools_df.loc[schools_df["is_middle"], "lat"])
high_xy = to_xy(schools_df.loc[schools_df["is_high"], "lon"], schools_df.loc[schools_df["is_high"], "lat"])
mid_school_by_apt = pd.Series(nearest_point_dist_m(mid_xy), index=anchor_apt_seq)
high_school_by_apt = pd.Series(nearest_point_dist_m(high_xy), index=anchor_apt_seq)

metrics_out["park_m"] = metrics_out["apt_seq"].map(park_m_by_apt)          # 22.1 node 기반 값을 폴리곤 기준으로 교체
metrics_out["park_area_m2"] = metrics_out["apt_seq"].map(park_area_by_apt)
metrics_out["nightlife_300m"] = metrics_out["apt_seq"].map(nightlife_by_apt)
metrics_out["dept_store_m"] = metrics_out["apt_seq"].map(dept_store_by_apt)
metrics_out["supermarket_m"] = metrics_out["apt_seq"].map(supermarket_by_apt)
metrics_out["mid_school_m"] = metrics_out["apt_seq"].map(mid_school_by_apt)
metrics_out["high_school_m"] = metrics_out["apt_seq"].map(high_school_by_apt)

for col in NULL_ONLY_METRICS:
    metrics_out[col] = np.nan

metrics_out = metrics_out[[
    "apt_seq", "sun_hours_avg", "sun_hours_best", "view_open_avg", "river_view_ratio",
    "road_centerline_m", "road_arterial_dist_m", "road_secondary_dist_m", "rail_centerline_m",
    "station_dist_m", "station_elev_diff", "station_walk_min_est",
    "station_ridership_daily", "station_congestion_peak", "traffic_weekday", "traffic_weekend",
    "elem_school_m", "elem_safe_route", "mid_school_m", "high_school_m", "daycare_500m",
    "tertiary_hosp_m", "general_hosp_m", "clinic_1km", "pediatric_1km", "mart_m",
    "dept_store_m", "supermarket_m", "cvs_500m", "restaurant_500m", "park_m",
    "park_area_m2", "nightlife_300m", "dawn_delivery",
]]

metrics_out.to_csv(OUT_METRICS, sep="\t", index=False)
print(f"  저장: {OUT_METRICS} ({len(metrics_out)}행)")


# ============================================================================
# 6. output/23.3.horizon_profile.txt
# ============================================================================

print("\n===== 6. horizon_profile 테이블 조립 =====")
print("  주의: 스키마 PK는 (building_id, floor_band)이나 21.1은 단지×층대 집계")
print("        (building_id 없음)이라 apt_seq×floor_band로 대체한다.")
print("  주의: profile REAL[72]는 21이 저장하지 않아 컬럼 자체를 제외한다.")

horizon_out = horizon_df[[
    "aptSeq", "floor_band", "winter_total_hours_8_16", "view_block_pct", "open_angle_mean",
]].rename(columns={
    "aptSeq": "apt_seq",
    "winter_total_hours_8_16": "sun_hours_winter",  # decisions.md #37 축과 통일
})
for col in NULL_ONLY_HORIZON:
    horizon_out[col] = np.nan

horizon_out = horizon_out[[
    "apt_seq", "floor_band", "repr_floor", "obs_height", "sun_hours_winter",
    "sun_hours_spring", "view_block_pct", "open_angle_mean", "open_span_max",
    "river_view", "park_view", "mountain_view",
]]

horizon_out.to_csv(OUT_HORIZON, sep="\t", index=False)
print(f"  저장: {OUT_HORIZON} ({len(horizon_out)}행)")


# ============================================================================
# 7. 자체 검증
# ============================================================================

print("\n===== 7. 자체 검증 =====")

assert complex_out["apt_seq"].is_unique, "complex.apt_seq 중복"
assert metrics_out["apt_seq"].is_unique, "complex_metrics.apt_seq 중복"
assert not horizon_out.duplicated(subset=["apt_seq", "floor_band"]).any(), \
    "horizon_profile (apt_seq, floor_band) 중복"

complex_keys = set(complex_out["apt_seq"])
metrics_keys = set(metrics_out["apt_seq"])
horizon_keys = set(horizon_out["apt_seq"])
assert metrics_keys <= complex_keys, "complex_metrics에 complex에 없는 단지가 있다"
assert horizon_keys <= metrics_keys, "horizon_profile에 complex_metrics에 없는 단지가 있다"
print(f"  키 정합성: complex({len(complex_keys)}) ⊇ complex_metrics({len(metrics_keys)}) "
      f"⊇ horizon_profile({len(horizon_keys)})")

n_failed = int((complex_out["match_confidence"] == "FAILED").sum())
n_unmatched_polygon = int((~complex_out["polygon_matched"]).sum())
assert n_unmatched_polygon >= n_unassigned, "미배정 단지가 결과에서 빠졌다"
print(f"  polygon_matched=False: {n_unmatched_polygon}건 / match_confidence=FAILED: {n_failed}건")

for col in NULL_ONLY_METRICS:
    assert metrics_out[col].isna().all(), f"complex_metrics.{col}이 NULL이 아닌 값을 포함한다"
for col in NULL_ONLY_HORIZON:
    assert horizon_out[col].isna().all(), f"horizon_profile.{col}이 NULL이 아닌 값을 포함한다"
assert "profile" not in horizon_out.columns
print("  결측 컬럼이 0/추정치로 채워지지 않았음을 확인")

# 신규 조인분 값 범위 검증 — 음수/불일치 여부만 본다(상한은 raw 표제부 자체가
# 건폐율 100%를 넘는 오기재를 포함하고 있어 걸지 않는다 — 위 3절 참고)
assert (complex_out["bcr"].dropna() >= 0).all(), "complex.bcr에 음수가 있다"
assert (complex_out["far"].dropna() >= 0).all(), "complex.far에 음수가 있다"
assert (complex_out["parking_per_hh"].dropna() >= 0).all(), "complex.parking_per_hh에 음수가 있다"
for col in ["road_centerline_m", "road_arterial_dist_m", "road_secondary_dist_m", "rail_centerline_m",
            "mid_school_m", "high_school_m", "dept_store_m", "supermarket_m", "park_m", "park_area_m2"]:
    assert (metrics_out[col].dropna() >= 0).all(), f"complex_metrics.{col}에 음수가 있다"
assert (metrics_out["nightlife_300m"] >= 0).all(), "nightlife_300m에 음수가 있다"
assert (metrics_out["park_m"].isna() == metrics_out["park_area_m2"].isna()).all(), \
    "park_m과 park_area_m2의 결측 패턴이 다르다 — 같은 공원을 가리켜야 한다"
print("  신규 조인 컬럼 음수 없음 / park_m·park_area_m2 결측 패턴 일치 확인")


# ============================================================================
# 8. 컬럼별 채움률
# ============================================================================

def print_fill_rate(df, table_name):
    print(f"\n  [{table_name}] (n={len(df)})")
    rate = (df.notna().mean() * 100).round(1)
    for col, pct in rate.items():
        print(f"    {col:<24s} {pct:5.1f}%")

print("\n===== 8. 컬럼별 채움률 =====")
print_fill_rate(complex_out, "complex")
print_fill_rate(metrics_out, "complex_metrics")
print_fill_rate(horizon_out, "horizon_profile")

print("\n===== 완료 =====")
