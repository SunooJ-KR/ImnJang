# ============================================================================
# 23.build_complex_metrics.py
# ============================================================================
# Author:      yjkim
# Purpose:     지금까지의 산출물을 웹앱이 그대로 소비할 최종 3종 테이블로 합친다
# Description: docs/data-model-and-ui.md §7 스키마(complex / complex_metrics /
#              horizon_profile)를 계약으로 삼아, 있는 데이터만 채우고 없는
#              컬럼은 만들되 전량 NULL로 남긴다(plan.md R13 — 추정/0채움 금지).
#
#              입력 6종 중 19.1(건축물대장)은 별도 재조인하지 않는다. 15.1의
#              reg_dong/reg_units/reg_year는 이미 17(registry_verified)이 19를
#              largest-remainder-split으로 정리해 넣어둔 값이라, 여기서 19를
#              다시 조인하면 그 분배 로직을 깨뜨리고 값이 갈릴 위험만 생긴다.
#
#              match_confidence 매핑(HIGH/MEDIUM/LOW/FAILED)은 준공년도 독립
#              검증 실측치 기준이다(docs/decisions.md 166행대):
#                HIGH 99.1% / NAME 94.6% / LOW 84.4% / LEVEL_MISMATCH 19.0%
#              LEVEL_MISMATCH는 거의 무작위 수준(19.0%)이라 FAILED로 묶는다.
#              MIXED(단지 내 동들의 등급이 섞임)는 표에 실측치가 없으므로,
#              15.2(building_assigned)에서 그 단지에 속한 동들의 실제 등급 중
#              가장 낮은 등급(worst-case)을 골라 같은 표로 다시 매핑한다 —
#              단지 신뢰도를 낙관적으로 부풀리지 않기 위해서다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import numpy as np
import pandas as pd
from pathlib import Path

work_dir = Path(__file__).resolve().parents[2]
output_dir = work_dir / "output"

COMPLEX_PATH = output_dir / "15.1.complex_final.txt"
BUILDING_ASSIGNED_PATH = output_dir / "15.2.building_assigned.txt"
GEOCODED_PATH = output_dir / "14.1.geocoded_master.txt"
HORIZON_PATH = output_dir / "21.1.complex_metrics.txt"
ACCESS_PATH = output_dir / "22.1.access_metrics.txt"

OUT_COMPLEX = output_dir / "23.1.complex.txt"
OUT_METRICS = output_dir / "23.2.complex_metrics.txt"
OUT_HORIZON = output_dir / "23.3.horizon_profile.txt"

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

# 스펙에는 있으나 이번 입력 6종에서 만들 수 없는 컬럼 (전량 NULL)
NULL_ONLY_COMPLEX = ["bjd_code", "far", "bcr", "parking_per_hh"]
NULL_ONLY_METRICS = [
    "river_view_ratio", "road_centerline_m", "rail_centerline_m",
    "station_ridership_daily", "traffic_weekday", "traffic_weekend",
    "elem_safe_route", "mid_school_m", "high_school_m", "daycare_500m",
    "tertiary_hosp_m", "general_hosp_m", "clinic_1km", "pediatric_1km",
    "dept_store_m", "supermarket_m", "park_area_m2", "nightlife_300m",
    "dawn_delivery",
]
NULL_ONLY_HORIZON = [
    "repr_floor", "obs_height", "sun_hours_spring", "open_span_max",
    "river_view", "park_view", "mountain_view",
]


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
# 3. output/23.1.complex.txt
# ============================================================================

print("\n===== 3. complex 테이블 조립 =====")

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
for col in NULL_ONLY_COMPLEX:
    complex_out[col] = np.nan

complex_out = complex_out[[
    "apt_seq", "name", "bjd_code", "lat", "lng", "built_year", "total_households",
    "building_count", "far", "bcr", "parking_per_hh", "polygon_matched", "match_confidence",
]]

complex_out.to_csv(OUT_COMPLEX, sep="\t", index=False)
print(f"  저장: {OUT_COMPLEX} ({len(complex_out)}행)")


# ============================================================================
# 4. output/23.2.complex_metrics.txt
# ============================================================================

print("\n===== 4. complex_metrics 테이블 조립 =====")

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
for col in NULL_ONLY_METRICS:
    metrics_out[col] = np.nan

metrics_out = metrics_out[[
    "apt_seq", "sun_hours_avg", "sun_hours_best", "view_open_avg", "river_view_ratio",
    "road_centerline_m", "rail_centerline_m", "station_dist_m", "station_elev_diff",
    "station_walk_min_est", "station_ridership_daily", "traffic_weekday", "traffic_weekend",
    "elem_school_m", "elem_safe_route", "mid_school_m", "high_school_m", "daycare_500m",
    "tertiary_hosp_m", "general_hosp_m", "clinic_1km", "pediatric_1km", "mart_m",
    "dept_store_m", "supermarket_m", "cvs_500m", "restaurant_500m", "park_m",
    "park_area_m2", "nightlife_300m", "dawn_delivery",
]]

metrics_out.to_csv(OUT_METRICS, sep="\t", index=False)
print(f"  저장: {OUT_METRICS} ({len(metrics_out)}행)")


# ============================================================================
# 5. output/23.3.horizon_profile.txt
# ============================================================================

print("\n===== 5. horizon_profile 테이블 조립 =====")
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
# 6. 자체 검증
# ============================================================================

print("\n===== 6. 자체 검증 =====")

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

for col in NULL_ONLY_COMPLEX:
    assert complex_out[col].isna().all(), f"complex.{col}이 NULL이 아닌 값을 포함한다"
for col in NULL_ONLY_METRICS:
    assert metrics_out[col].isna().all(), f"complex_metrics.{col}이 NULL이 아닌 값을 포함한다"
for col in NULL_ONLY_HORIZON:
    assert horizon_out[col].isna().all(), f"horizon_profile.{col}이 NULL이 아닌 값을 포함한다"
assert "profile" not in horizon_out.columns
print("  결측 컬럼이 0/추정치로 채워지지 않았음을 확인")


# ============================================================================
# 7. 컬럼별 채움률
# ============================================================================

def print_fill_rate(df, table_name):
    print(f"\n  [{table_name}] (n={len(df)})")
    rate = (df.notna().mean() * 100).round(1)
    for col, pct in rate.items():
        print(f"    {col:<24s} {pct:5.1f}%")

print("\n===== 7. 컬럼별 채움률 =====")
print_fill_rate(complex_out, "complex")
print_fill_rate(metrics_out, "complex_metrics")
print_fill_rate(horizon_out, "horizon_profile")

print("\n===== 완료 =====")
