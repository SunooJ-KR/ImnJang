# ============================================================================
# 38.regulation_prep.py   (가번호 — PR 직전에 확정)
# ============================================================================
# Author:      jieun
# Purpose:     토지거래허가 지정 전후 분석용 매매 거래 테이블을 만든다
# Description: docs/analysis-plan-regulation-break.md 절차 01.
#              product-identity.md §8-2의 미검증 항목(2025-10-20 서울 전역
#              아파트 토지거래허가 지정 전후 가격 구조)을 다루는 분석의 입력이다.
#
#              - 지정 시작일은 자치구별로 준다. 강남·서초·송파·용산은 2025-03-24,
#                나머지 21개 구는 2025-10-20. 월이 아니라 거래일로 전후를 가른다
#                (두 지정 모두 월중에 시작했다)
#              - 2020~2021에 먼저 지정된 재건축 구역이 있는 법정동 9곳(성수동1·2가 포함)은 플래그만
#                남긴다. 주 분석 제외 여부는 39~41이 정한다
#              - 층대는 팀 공통 규칙(_floor_band.py, 커밋 6f2024a)과 같은 정의를
#                독립 구현한다: 15.1 max_levels의 1/3·2/3 경계, 결측은 UNKNOWN.
#                팀원 가격 모듈은 수정 중이라 import하지 않는다
#              - feature는 팀 서비스 사양(SERVICE_MODEL_FEATURES: 기본 3 + 입지 17 +
#                정비사업 2)의 원천 컬럼을 붙인다. 물리·전세 feature는 붙이지 않는다
#              - 2026-08은 신고 미완결이라 eval_complete=False로 표시만 한다
#
#              입력 11.1은 커밋되지 않는 파일이라 팀원 작업 폴더에서 읽기만 해서
#              로컬 output/로 복사했다. 원본 수정시각을 38.2에 남긴다.
#
#              통과 기준:
#                (1) 거래일 파싱 실패 0건
#                (2) merge로 행 수가 변하지 않음 (many_to_one)
#                (3) 자치구 코드 집합이 서울 25개 구와 정확히 일치
#                (4) 층대 UNKNOWN 비율이 거래 기준 50% 미만
#                (5) 선지정 법정동 9곳 모두 거래가 존재
#                (6) 원본 deal_ym과 거래일에서 만든 월이 전부 일치
#
#              외부 검토(codex) 반영:
#                - 선지정 9개 동은 이미 2020~2021부터 지정이라 treat_start 기준의
#                  전/후 라벨이 틀린다. 플래그만으로는 뒤 단계에서 섞일 수 있어
#                  in_main_sample 마스크로 강제 제외한다
#                - 2026-08(신고 미완결)과 같은 동으로 확정된 중복 거래도
#                  in_main_sample에서 뺀다. 동 정보가 없는 반복과 일괄 매각은 유지·플래그
#                - 가격 극단값은 제거하지 않고 플래그만 둔다. 학습용 절단은 각 학습
#                  표본 안에서만 경계를 정한다(41)
#                - 거래층 > max_levels는 팀 규칙대로 HIGH로 두고 충돌 플래그를 남긴다
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

SALE_PATH = output_dir / "11.1.trades_sale.txt"
COMPLEX_FINAL_PATH = output_dir / "15.1.complex_final.txt"
COMPLEX_PATH = output_dir / "23.1.complex.txt"
METRICS_PATH = output_dir / "23.2.complex_metrics.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"

RESULT_PATH = output_dir / "38.1.trades_regulation.txt"
SUMMARY_PATH = output_dir / "38.2.window_summary.txt"

WINDOW_START = pd.Timestamp("2024-09-01")
WINDOW_END = pd.Timestamp("2026-08-31")
LAST_COMPLETE_YM = 202607          # 2026-08은 신고 미완결 (계획서 분석 데이터 항목 참조)
LATE_MONTH_DAY = 21                # 월말 거래 비중 — 신고 지연 진단용

EARLY_SGG = {"11680": "강남구", "11650": "서초구", "11710": "송파구", "11170": "용산구"}
SEOUL_SGG = {"11110", "11140", "11170", "11200", "11215", "11230", "11260", "11290",
             "11305", "11320", "11350", "11380", "11410", "11440", "11470", "11500",
             "11530", "11545", "11560", "11590", "11620", "11650", "11680", "11710",
             "11740"}
# 명백한 입력 오류만 거르는 넓은 물리 범위(만원/㎡). 분석 절단 기준이 아니다
GROSS_PPM2_RANGE = (300, 15000)
EARLY_START = pd.Timestamp("2025-03-24")
LATE_START = pd.Timestamp("2025-10-20")

# 2020-06-23 잠실·삼성·대치·청담 / 2021-04-27 압구정·여의도·목동·성수 (35.2 기준)
PRE_DESIGNATED_DONG = {
    ("11710", "잠실동"), ("11680", "삼성동"), ("11680", "대치동"), ("11680", "청담동"),
    ("11680", "압구정동"), ("11560", "여의도동"), ("11470", "목동"),
    ("11200", "성수동1가"), ("11200", "성수동2가"),
}

# 팀 서비스 사양 원천 컬럼 (_features.py SERVICE_MODEL_FEATURES, 커밋 6f2024a)
COMPLEX_FEATURE_COLS = ["built_year", "total_households", "far", "bcr", "parking_per_hh"]
LOCATION_FEATURE_COLS = [
    "station_dist_m", "station_ridership_daily", "elem_school_m", "mid_school_m",
    "general_hosp_m", "park_m", "park_area_m2", "cvs_500m", "restaurant_500m",
    "nightlife_300m", "dept_store_m", "mart_m", "road_arterial_dist_m",
    "rail_centerline_m",
]
ADVANCED_STAGES = ["관리처분", "착공"]


# ============================================================================
# 1. 입력 출처 기록
# ============================================================================

print("===== 1. 입력 =====")
provenance = []
for path in [SALE_PATH, COMPLEX_FINAL_PATH, COMPLEX_PATH, METRICS_PATH, MASTER_PATH]:
    if not path.exists():
        raise SystemExit(f"입력 없음: {path}")
    mtime = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
    provenance.append({"file": path.name, "bytes": path.stat().st_size, "mtime": mtime})
    print(f"  {path.name:32s} {path.stat().st_size / 1e6:7.1f}MB  수정 {mtime}")


# ============================================================================
# 2. 매매 거래 정제
# ============================================================================

print("\n===== 2. 매매 거래 정제 =====")
sale = pd.read_csv(
    SALE_PATH, sep="\t",
    dtype={"aptSeq": str, "sggCd": str, "umdNm": str, "aptDong": str},
    usecols=["aptSeq", "sggCd", "umdNm", "aptDong", "excluUseAr", "floor", "dealYear",
             "dealMonth", "dealDay", "deal_ym", "is_cancelled", "deal_amount_manwon"],
)
if sale["is_cancelled"].dtype != bool:
    raise SystemExit(f"is_cancelled dtype이 bool이 아니다: {sale['is_cancelled'].dtype}")

steps = [("원본", len(sale))]
sale = sale[~sale["is_cancelled"]]
steps.append(("취소 거래 제외", len(sale)))

sale["floor"] = pd.to_numeric(sale["floor"], errors="coerce")
sale = sale[(sale["floor"] > 0) & (sale["deal_amount_manwon"] > 0) & (sale["excluUseAr"] > 0)]
steps.append(("지하층·금액·면적 결측 제외", len(sale)))

sale["deal_date"] = pd.to_datetime(
    dict(year=sale["dealYear"], month=sale["dealMonth"], day=sale["dealDay"]),
    errors="coerce")
n_bad_date = int(sale["deal_date"].isna().sum())
# 월별 집계·완결성 판정이 원본 deal_ym에 기대지 않도록 거래일에서 다시 만든다
derived_ym = (sale["deal_date"].dt.year * 100 + sale["deal_date"].dt.month).astype("Int64")
n_ym_mismatch = int((derived_ym != sale["deal_ym"]).fillna(True).sum())
sale["deal_ym"] = derived_ym
sale = sale[sale["deal_date"].between(WINDOW_START, WINDOW_END)].copy()
steps.append((f"분석 창 {WINDOW_START:%Y-%m}~{WINDOW_END:%Y-%m}", len(sale)))

# 거래의 자치구 코드(sggCd)가 단지 ID 앞자리와 다른 행이 있다(실측 4행 — 신당동이
# 성동구로, 청량리동이 성북구로 적힌 식). 단지 ID 앞자리가 법정동명과 일치하므로
# 그것을 기준으로 바로잡는다. 그대로 두면 한 단지가 두 자치구 클러스터에 걸린다
apt_prefix = sale["aptSeq"].str.split("-").str[0]
n_sgg_fixed = int((apt_prefix != sale["sggCd"]).sum())
sale["sggCd"] = apt_prefix

for label, n in steps:
    print(f"  {label:28s} {n:>9,}행")
print(f"  자치구 코드를 단지 ID 기준으로 보정 {n_sgg_fixed}행")
print(f"  거래일 파싱 실패 {n_bad_date}건")


# ============================================================================
# 3. 지정 시점과 집단
# ============================================================================

print("\n===== 3. 지정 시점 =====")
sale["group"] = np.where(sale["sggCd"].isin(EARLY_SGG), "early4", "late21")
sale["treat_start"] = np.where(sale["group"] == "early4", EARLY_START, LATE_START)
sale["treat_start"] = pd.to_datetime(sale["treat_start"])
sale["post"] = sale["deal_date"] >= sale["treat_start"]
sale["rel_month"] = ((sale["deal_date"].dt.year - sale["treat_start"].dt.year) * 12
                     + (sale["deal_date"].dt.month - sale["treat_start"].dt.month))
sale["transition_month"] = sale["rel_month"] == 0     # 지정일이 속한 달

sale["pre_designated_dong"] = [
    (sgg, dong) in PRE_DESIGNATED_DONG for sgg, dong in zip(sale["sggCd"], sale["umdNm"])]
sale["region_key"] = sale["sggCd"] + "_" + sale["umdNm"].fillna("")

observed_sgg = set(sale["sggCd"].unique())
sgg_exact = observed_sgg == SEOUL_SGG
print(f"  자치구 {len(observed_sgg)}개 / 서울 25개 코드와 정확히 일치: {sgg_exact} "
      f"(누락 {sorted(SEOUL_SGG - observed_sgg)}, 초과 {sorted(observed_sgg - SEOUL_SGG)})")
print(f"  집단별 전후 거래: "
      f"{sale.groupby(['group', 'post']).size().rename('n').to_dict()}")
pre_counts = sale[sale["pre_designated_dong"]].groupby(["sggCd", "umdNm"]).size()
print(f"  선지정 법정동 거래 {int(pre_counts.sum()):,}건 ({pre_counts.size}곳)")


# ============================================================================
# 4. 층대 — 팀 공통 규칙과 같은 정의
# ============================================================================

print("\n===== 4. 층대 =====")
final = pd.read_csv(COMPLEX_FINAL_PATH, sep="\t", dtype={"aptSeq": str},
                    usecols=["aptSeq", "max_levels"])
if final["aptSeq"].duplicated().any():
    raise SystemExit("15.1 aptSeq가 유일하지 않다")
max_levels = pd.to_numeric(final.set_index("aptSeq")["max_levels"], errors="coerce")
maximum = sale["aptSeq"].map(max_levels)
present = maximum.notna() & (maximum > 0)
sale["floor_band"] = np.select(
    [present & (sale["floor"] <= maximum / 3),
     present & (sale["floor"] > maximum / 3) & (sale["floor"] <= maximum * 2 / 3),
     present & (sale["floor"] > maximum * 2 / 3)],
    ["LOW", "MID", "HIGH"], default="UNKNOWN")
# 거래층이 max_levels보다 높으면 OSM 층수가 낮게 잡힌 것이다. 규칙상 HIGH로 들어가지만
# 규모를 기록해 둔다
sale["floor_above_max"] = present & (sale["floor"] > maximum)
n_above_max = int(sale["floor_above_max"].sum())
unknown_share = 100 * (sale["floor_band"] == "UNKNOWN").mean()
print(f"  층대 분포: {sale['floor_band'].value_counts().to_dict()}")
print(f"  UNKNOWN {unknown_share:.1f}% (거래 기준) / 거래층 > max_levels {n_above_max:,}건")


# ============================================================================
# 5. 단지 속성·입지·정비사업 결합
# ============================================================================

print("\n===== 5. 단지 feature 결합 =====")
complexes = pd.read_csv(COMPLEX_PATH, sep="\t", dtype={"apt_seq": str}).rename(
    columns={"apt_seq": "aptSeq"})
metrics = pd.read_csv(METRICS_PATH, sep="\t", dtype={"apt_seq": str}).rename(
    columns={"apt_seq": "aptSeq"})
master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str},
                     usecols=["aptSeq", "umd_name"])

complexes["is_redevelop"] = complexes["redevelop_type"].notna().astype(float)
complexes["redevelop_stage_advanced"] = complexes["redevelop_stage"].isin(
    ADVANCED_STAGES).astype(float)

n_before = len(sale)
sale = (sale
        .merge(complexes[["aptSeq"] + COMPLEX_FEATURE_COLS
                         + ["is_redevelop", "redevelop_stage_advanced", "match_confidence"]],
               on="aptSeq", how="left", validate="many_to_one")
        .merge(metrics[["aptSeq"] + LOCATION_FEATURE_COLS],
               on="aptSeq", how="left", validate="many_to_one")
        .merge(master, on="aptSeq", how="left", validate="many_to_one"))
if len(sale) != n_before:
    raise SystemExit(f"merge로 행이 변했다: {n_before:,} -> {len(sale):,}")

sale["price_per_m2"] = sale["deal_amount_manwon"] / sale["excluUseAr"]
sale["y_log_ppm2"] = np.log(sale["price_per_m2"])
sale["area_type"] = (sale["excluUseAr"] / 3).round() * 3
sale["eval_complete"] = sale["deal_ym"] <= LAST_COMPLETE_YM
sale["gross_price_flag"] = ~sale["price_per_m2"].between(*GROSS_PPM2_RANGE)

# 실거래에는 동·호수가 없어, 같은 날 같은 층·면적·금액이면 다른 동의 정상 거래일 수 있다.
# 실측(2024-09~): 동 정보가 2건 이상인 그룹 526개 중 466개는 같은 동, 60개는 다른 동.
# 따라서 세 가지로 나눈다.
#   is_duplicate       같은 동이 확인된 반복 행 — 주 표본에서 제외
#   possible_duplicate 동 정보가 없어 판별 불가 — 유지, 민감도용 플래그
#   bulk_trade         같은 키에 3건 이상 — 일괄 매각 가능성, 유지, 민감도용 플래그
dup_keys = ["aptSeq", "deal_date", "floor", "excluUseAr", "deal_amount_manwon"]
group_size = sale.groupby(dup_keys)["aptSeq"].transform("size")
same_dong_rank = sale.groupby(dup_keys + ["aptDong"], dropna=True).cumcount()
dong_known = sale["aptDong"].notna() & sale["aptDong"].str.strip().ne("")
sale["is_duplicate"] = (group_size > 1) & dong_known & (same_dong_rank > 0)
n_dong_in_group = sale.assign(k=dong_known).groupby(dup_keys)["k"].transform("sum")
sale["possible_duplicate"] = (group_size > 1) & ~sale["is_duplicate"] & (n_dong_in_group < 2)
sale["bulk_trade"] = group_size >= 3

sale["in_main_sample"] = (~sale["pre_designated_dong"] & sale["eval_complete"]
                          & ~sale["is_duplicate"])
print(f"  가격 극단값 플래그 {int(sale['gross_price_flag'].sum()):,}건 "
      f"(범위 {GROSS_PPM2_RANGE[0]}~{GROSS_PPM2_RANGE[1]}만원/㎡ 밖)")
print(f"  같은 키 반복: 확정 중복(같은 동) {int(sale['is_duplicate'].sum()):,}건 제외 / "
      f"판별 불가 {int(sale['possible_duplicate'].sum()):,}건 유지 / "
      f"일괄 매각 가능(3건+) {int(sale['bulk_trade'].sum()):,}건 유지")
print(f"  주 분석 표본 {int(sale['in_main_sample'].sum()):,}행 "
      f"(선지정 동 -{int(sale['pre_designated_dong'].sum()):,} / 2026-08 "
      f"-{int((~sale['eval_complete']).sum()):,} / 중복 -{int(sale['is_duplicate'].sum()):,}, 중첩 포함)")

no_attr = sale["built_year"].isna().mean() * 100
no_loc = sale["station_dist_m"].isna().mean() * 100
print(f"  merge 후 {len(sale):,}행 (불변) / 단지 속성 결측 {no_attr:.1f}% / 입지 결측 {no_loc:.1f}%")
print(f"  정비사업 거래 {int(sale['is_redevelop'].sum()):,}건 "
      f"(관리처분·착공 {int(sale['redevelop_stage_advanced'].sum()):,}건)")


# ============================================================================
# 6. 완결성 표 (월별 거래량 · 월말 거래 비중)
# ============================================================================

print("\n===== 6. 월별 완결성 =====")
monthly = (sale.assign(late=sale["deal_date"].dt.day >= LATE_MONTH_DAY)
           .groupby(["deal_ym", "group"])
           .agg(n=("late", "size"), late_share=("late", "mean"))
           .unstack("group"))
monthly.columns = [f"{a}_{b}" for a, b in monthly.columns]
monthly = monthly.reset_index()
monthly["n_total"] = monthly["n_early4"] + monthly["n_late21"]
total_late = sale.assign(late=sale["deal_date"].dt.day >= LATE_MONTH_DAY).groupby(
    "deal_ym")["late"].mean()
monthly["late_share_total"] = monthly["deal_ym"].map(total_late)
print(monthly.tail(6).round(3).to_string(index=False))


# ============================================================================
# 7. 판정 및 저장
# ============================================================================

print("\n===== 7. 판정 =====")
checks = [
    ("거래일 파싱 실패 0건", n_bad_date == 0, f"{n_bad_date}건"),
    ("merge 행 수 불변", True, f"{len(sale):,}행"),
    ("자치구 코드 = 서울 25개 구", sgg_exact, f"{len(observed_sgg)}개"),
    ("단지 → 자치구 1:1 (보정 후)", bool((sale.groupby("aptSeq")["sggCd"].nunique() == 1).all()),
     f"보정 {n_sgg_fixed}행"),
    ("원본 deal_ym = 거래일 월", n_ym_mismatch == 0, f"불일치 {n_ym_mismatch}건"),
    ("층대 UNKNOWN < 50%", unknown_share < 50, f"{unknown_share:.1f}%"),
    ("선지정 법정동 9곳 모두 거래 존재", pre_counts.size == len(PRE_DESIGNATED_DONG),
     f"{pre_counts.size}곳"),
]
all_passed = True
for label, passed, observed in checks:
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:28s} {observed}")

keep = (["aptSeq", "sggCd", "umdNm", "umd_name", "region_key", "deal_date", "deal_ym",
         "group", "treat_start", "post", "rel_month", "transition_month",
         "pre_designated_dong", "eval_complete", "is_duplicate", "possible_duplicate",
         "bulk_trade", "in_main_sample",
         "gross_price_flag", "floor", "floor_band", "floor_above_max", "excluUseAr",
         "area_type", "deal_amount_manwon", "price_per_m2", "y_log_ppm2",
         "match_confidence", "is_redevelop", "redevelop_stage_advanced"]
        + COMPLEX_FEATURE_COLS + LOCATION_FEATURE_COLS)
sale[keep].to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n")

with SUMMARY_PATH.open("w", encoding="utf-8") as fh:
    fh.write("# 38.2 토지거래허가 분석 창 요약\n\n## 입력 출처\n")
    pd.DataFrame(provenance).to_csv(fh, sep="\t", index=False, lineterminator="\n")
    fh.write("\n## 정제 단계\n")
    pd.DataFrame(steps, columns=["step", "rows"]).to_csv(fh, sep="\t", index=False,
                                                        lineterminator="\n")
    fh.write("\n## 집단 x 전후 거래 수\n")
    (sale.groupby(["group", "post"]).size().rename("n").reset_index()
     .to_csv(fh, sep="\t", index=False, lineterminator="\n"))
    fh.write("\n## 층대 분포\n")
    (sale["floor_band"].value_counts().rename_axis("floor_band").rename("n").reset_index()
     .to_csv(fh, sep="\t", index=False, lineterminator="\n"))
    fh.write(f"\n거래층 > max_levels: {n_above_max}건 (HIGH로 배정, floor_above_max 플래그)\n")
    fh.write("\n## 층대 UNKNOWN 비율 — 주 분석 표본, 집단 x 전후 (%)\n")
    (sale[sale["in_main_sample"]].assign(unknown=lambda d: d["floor_band"].eq("UNKNOWN"))
     .groupby(["group", "post"])["unknown"].mean().mul(100).round(2).rename("unknown_pct")
     .reset_index().to_csv(fh, sep="\t", index=False, lineterminator="\n"))
    fh.write("\n## 주 분석 표본 구성\n")
    fh.write(f"in_main_sample\t{int(sale['in_main_sample'].sum())}\n")
    fh.write(f"pre_designated_dong\t{int(sale['pre_designated_dong'].sum())}\n")
    fh.write(f"eval_incomplete(2026-08)\t{int((~sale['eval_complete']).sum())}\n")
    fh.write(f"is_duplicate(같은 동 확정, 제외)\t{int(sale['is_duplicate'].sum())}\n")
    fh.write(f"possible_duplicate(동 정보 없음, 유지)\t{int(sale['possible_duplicate'].sum())}\n")
    fh.write(f"bulk_trade(같은 키 3건+, 유지)\t{int(sale['bulk_trade'].sum())}\n")
    fh.write(f"gross_price_flag\t{int(sale['gross_price_flag'].sum())}\n")
    fh.write("\n## 월별 거래량과 월말(21일 이후) 거래 비중\n")
    monthly.round(4).to_csv(fh, sep="\t", index=False, lineterminator="\n")
    fh.write("\n## 판정\n")
    for label, passed, observed in checks:
        fh.write(f"[{'PASS' if passed else 'FAIL'}]\t{label}\t{observed}\n")

print(f"\n거래 테이블: {RESULT_PATH}  ({len(sale):,}행)")
print(f"요약:        {SUMMARY_PATH}")
print(f"\n===== 38 분석 입력 구축 {'통과' if all_passed else '미통과'} =====")
