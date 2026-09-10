# ============================================================================
# 16.audit_sample.py
# ============================================================================
# Author:      yjkim
# Purpose:     동 배정 결과를 사람이 눈으로 검수할 층화 표본 100개를 뽑는다
# Description: plan.md 10절의 층화 정답셋. 15에서 부여한 배정 신뢰도를 층으로 쓴다.
#                LEVEL_MISMATCH 30건 — 층수가 대장과 어긋남. 독립 검증 1.5%로
#                                       가장 위험한 층. 최우선 검수 대상
#                MIXED 25건 — 근거가 섞인 단지
#                LOW   20건 — 최근접 거리만 (독립 검증 87.0%)
#                HIGH  15건 — 경계 폴리곤만 (97.2%). 무오류가 통과 기준
#                NAME  10건 — 대장 동명칭 일치 (94.4%). 표본 확인용
#
#              판정은 사람이 한다. 이 스크립트는 판정에 필요한 것을 한 줄에 모을 뿐
#              어떤 자동 판정도 하지 않는다. verdict 컬럼은 비워서 내보낸다.
#
#              15가 등록 동수를 배정 상한으로 쓴 뒤로 검수의 초점이 바뀌었다.
#              동 수가 넘치는 일(과다)은 구조적으로 불가능해졌으므로, 남은 질문은
#              "동 수는 맞는데 엉뚱한 동을 골랐는가" 하나다. 이것만은 등록 정보로
#              알 수 없어 위성지도를 봐야 한다.
#
#              검수 방법: kakao_map_url을 열어 위성지도에서 단지를 찾고,
#              배정된 동들이 그 단지 울타리 안에 있는지 본다.
#              dong_diff가 음수면 OSM에 그 동이 없다는 뜻이다(과소).
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import numpy as np
import pandas as pd
from pathlib import Path
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

COMPLEX_PATH = output_dir / "15.1.complex_final.txt"
BUILDING_PATH = output_dir / "15.2.building_assigned.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"
RESULT_PATH = output_dir / "16.1.audit_sample.txt"
EXCEL_PATH = output_dir / "16.2.audit_sample.xlsx"

# 15가 부여하는 배정 근거 등급을 그대로 층으로 쓴다. 20의 독립 검증(높이 ±3m)에서
# 등급별 정확도가 HIGH 97.2% / NAME 94.4% / LOW 87.0% / LEVEL_MISMATCH 1.5%로
# 갈렸으므로, 사람 눈이 가장 필요한 곳은 LEVEL_MISMATCH와 MIXED다.
STRATA = {"LEVEL_MISMATCH": 30, "MIXED": 25, "LOW": 20, "HIGH": 15, "NAME": 10}
MAX_DONG_LISTED = 12     # 이보다 많으면 줄여 적는다. 어차피 그 자체가 이상 신호다
RANDOM_SEED = 42
VERDICT_CHOICES = ["OK", "위치오배정", "일부오배정", "판정불가"]
# 흡수 의심 신호의 임계값. 판정이 아니라 눈으로 볼 순서를 정하는 용도다
LEVELS_SPREAD_SUSPECT = 8      # 한 단지 동들의 층수 차이가 이보다 크면 이상
YEAR_SPREAD_SUSPECT = 5        # 준공년도가 이만큼 벌어지면 다른 단지가 섞인 것이다
DIST_CAP_SUSPECT = 295         # 15의 MAX_DIST_M 300에 붙었으면 반경을 긁어모은 것
COLUMN_WIDTHS = {"C": 20, "E": 28, "U": 18, "V": 60, "W": 10, "X": 10, "Y": 30}


# ============================================================================
# 1. 데이터 로드
# ============================================================================

print("===== 1. 데이터 로드 =====")
complexes = pd.read_csv(COMPLEX_PATH, sep="\t", dtype={"aptSeq": str})
buildings = pd.read_csv(BUILDING_PATH, sep="\t", dtype={"aptSeq": str})
master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})

complexes = complexes.merge(
    master[["aptSeq", "addr_road", "addr_jibun", "lat", "lon"]], on="aptSeq", how="left")
print(f"  단지 {len(complexes)} / 배정된 동 {int(buildings['aptSeq'].notna().sum())}")
print(f"  신뢰도 분포: {complexes['confidence'].value_counts(dropna=False).to_dict()}")


# ============================================================================
# 2. 층화 추출
# ============================================================================
# 거래가 아예 없다시피 한 단지만 뽑히면 검수가 의미 없다. 층 안에서는 무작위로
# 뽑되 시드를 고정해 재현 가능하게 한다.

print("\n===== 2. 층화 추출 =====")

samples = []
for stratum, n_wanted in STRATA.items():
    pool = complexes[complexes["confidence"] == stratum]
    n_take = min(n_wanted, len(pool))
    if n_take < n_wanted:
        print(f"  [주의] {stratum} 층에 {len(pool)}건뿐이라 {n_take}건만 뽑는다")
    samples.append(pool.sample(n=n_take, random_state=RANDOM_SEED))
    print(f"  {stratum:5s}: {n_take}건 (모집단 {len(pool)})")

sample = pd.concat(samples, ignore_index=True)


# ============================================================================
# 3. 검수에 필요한 정보 부착
# ============================================================================

print("\n===== 3. 검수 정보 부착 =====")


def summarize_dong(group):
    """동 하나를 '이름:층수:높이m' 로 줄여 한 칸에 넣는다"""
    parts = []
    for _, row in group.iterrows():
        name = row["name"] if pd.notna(row["name"]) else "?"
        levels = f"{row['levels']:.0f}F" if pd.notna(row["levels"]) else "?F"
        height = f"{row['height_m']:.0f}m" if pd.notna(row["height_m"]) else "?m"
        parts.append(f"{name}:{levels}:{height}")
    if len(parts) > MAX_DONG_LISTED:
        return " | ".join(parts[:MAX_DONG_LISTED]) + f" | ...외 {len(parts) - MAX_DONG_LISTED}동"
    return " | ".join(parts)


assigned = buildings[buildings["aptSeq"].isin(sample["aptSeq"])]
dong_list = (assigned.sort_values("dist_m").groupby("aptSeq")
             .apply(summarize_dong, include_groups=False).rename("dong_list"))
dong_dist = (assigned.groupby("aptSeq")["dist_m"]
             .agg(dong_dist_max="max").round(0).reset_index())

# 흡수 의심 신호 — 위성사진 없이 배정 목록만으로 잡히는 것들
signals = (assigned.groupby("aptSeq")
    .agg(levels_spread=("levels", lambda s: s.max() - s.min()),
         year_spread=("build_year", lambda s: s.max() - s.min()),
         n_dong_names=("name", "nunique"))
    .reset_index())

sample = (sample.merge(dong_list, on="aptSeq", how="left")
                .merge(dong_dist, on="aptSeq", how="left")
                .merge(signals, on="aptSeq", how="left"))


def prescreen(row):
    """흡수를 의심할 근거를 나열한다. 비어 있으면 신호가 없다는 뜻일 뿐 정상 판정이 아니다"""
    reasons = []
    if row["levels_spread"] >= LEVELS_SPREAD_SUSPECT:
        reasons.append(f"층수편차{row['levels_spread']:.0f}F")
    if row["year_spread"] >= YEAR_SPREAD_SUSPECT:
        reasons.append(f"준공편차{row['year_spread']:.0f}년")
    if row["dong_dist_max"] >= DIST_CAP_SUSPECT:
        reasons.append("거리상한")
    if pd.notna(row["dong_diff"]) and row["dong_diff"] < 0:
        reasons.append(f"과소{abs(row['dong_diff']):.0f}동")
    return " ".join(reasons)


sample["prescreen"] = sample.apply(prescreen, axis=1)
sample["year_diff"] = (sample["build_year"] - sample["osm_year_median"]).abs()
# 위성지도로 봐야 동 배치가 보인다
sample["kakao_map_url"] = ("https://map.kakao.com/link/map/"
                           + sample["apt_name"].fillna("단지").astype(str) + ","
                           + sample["lat"].astype(str) + "," + sample["lon"].astype(str))
sample["verdict"] = ""       # 사람이 채운다: OK / 과다 / 과소 / 오배정
sample["note"] = ""

columns = ["confidence", "aptSeq", "apt_name", "gu", "addr_road", "n_deals",
           "build_year", "reg_year", "osm_year_median", "year_diff",
           "n_dong", "reg_dong", "dong_diff", "reg_units",
           "n_by_boundary", "n_height_imputed", "max_levels", "max_height_m",
           "mean_dist", "dong_dist_max", "levels_spread", "year_spread",
           "prescreen", "dong_list", "kakao_map_url", "verdict", "note"]
sample = sample[columns].sort_values(
    ["confidence", "n_deals"], ascending=[True, False]).reset_index(drop=True)


# ============================================================================
# 4. 저장 및 검수 안내
# ============================================================================

print("\n===== 4. 표본 요약 =====")
summary = (sample.groupby("confidence")
    .agg(n=("aptSeq", "count"), 거래중앙값=("n_deals", "median"),
         동수중앙값=("n_dong", "median"), 동수최대=("n_dong", "max"),
         년도차중앙값=("year_diff", "median"))
    .reset_index())
print(summary.to_string(index=False))

flagged = sample[sample["prescreen"] != ""]
print(f"\n  흡수 의심 신호가 붙은 단지: {len(flagged)}/{len(sample)}")
print(pd.crosstab(sample["confidence"], sample["prescreen"] != "").to_string())
print(f"\n  신호 상위 (동 수 순):")
print(flagged.nlargest(8, "n_dong")[
    ["confidence", "apt_name", "gu", "n_dong", "n_deals", "prescreen"]].to_string(index=False))

# 이 파일만 사람이 Windows 엑셀로 연다. BOM이 없으면 엑셀이 CP949로 읽어 한글이 깨진다
sample.to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n", encoding="utf-8-sig")


# ============================================================================
# 5. 엑셀본 — 사람이 실제로 채워 넣는 파일
# ============================================================================
# 100건을 손으로 채우는 작업이라 링크는 클릭되고 verdict는 골라지게 만든다.

with pd.ExcelWriter(EXCEL_PATH, engine="openpyxl") as writer:
    sample.to_excel(writer, sheet_name="검수", index=False)
    sheet = writer.sheets["검수"]

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for column, width in COLUMN_WIDTHS.items():
        sheet.column_dimensions[column].width = width

    url_col = columns.index("kakao_map_url") + 1
    for row in range(2, len(sample) + 2):
        cell = sheet.cell(row=row, column=url_col)
        cell.hyperlink = cell.value
        cell.value = "지도 열기"
        cell.style = "Hyperlink"

    verdict_col = get_column_letter(columns.index("verdict") + 1)
    dropdown = DataValidation(
        type="list", formula1=f'"{",".join(VERDICT_CHOICES)}"', allow_blank=True)
    dropdown.error = "OK / 과다 / 과소 / 오배정 중에서 고르세요"
    sheet.add_data_validation(dropdown)
    dropdown.add(f"{verdict_col}2:{verdict_col}{len(sample) + 1}")

print(f"\n검수 표본: {RESULT_PATH}")
print(f"엑셀본:    {EXCEL_PATH}")
print(f"""
검수 방법:
  1. kakao_map_url을 열고 위성지도로 전환한다
  2. 배정된 동들이 그 단지 울타리 안에 있는가 (동 수는 이미 등록 정보로 맞춰져 있다)
  3. max_levels가 실제 층수와 맞는가
  4. verdict: OK / 위치오배정(전부 남의 동) / 일부오배정 / 판정불가
통과 기준 (plan.md 10절): HIGH 무오류, MIXED 90% 이상""")
