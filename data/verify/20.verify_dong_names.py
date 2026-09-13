# ============================================================================
# 20.verify_dong_names.py
# ============================================================================
# Author:      yjkim
# Purpose:     배정된 동이 실제로 그 단지 동인지 건축물대장 동명칭으로 검증한다
# Description: 17의 등록 동수 대조는 15가 그 값을 배정 상한으로 쓴 뒤로 순환
#              지표가 됐다. 상한이 과다를 구조적으로 막으므로 "동 수가 맞다"는
#              사실이 "맞는 동을 골랐다"를 뜻하지 않는다.
#
#              건축물대장 표제부(19)는 단지마다 동명칭 목록을 준다. 배정된 OSM
#              건물의 name이 그 목록에 있는지 보면 배정의 옳고 그름이 직접 판정된다.
#              배정에 전혀 쓰지 않은 정보이므로 순환이 없다.
#
#              OSM name 보유율은 아파트 동의 65.0%이고, 그중 89.7%가 숫자 기반
#              ('904' 41.6% / '904동' 48.1%)이라 정규화하면 대장 표기와 붙는다.
#              이름이 없는 35%는 층수 다중집합으로 근사 대조한다.
#
#              판정 등급:
#                NAME_OK     이름 있는 동이 전부 대장 목록에 있음 (확실)
#                NAME_BAD    하나라도 대장에 없음 = 남의 동을 물어옴 (확실)
#                FLOOR_OK    이름이 없어 층수 분포로만 확인 (근사)
#                FLOOR_BAD   층수 분포가 대장과 어긋남 (근사)
#                NO_LEDGER   대장을 못 받은 단지 (판정 불가)
#
#              통과 기준: NAME 판정 가능한 단지에서 NAME_OK 비율 >= 90%
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import re
from pathlib import Path

import numpy as np
import pandas as pd

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

LEDGER_PATH = output_dir / "19.1.building_ledger.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"
COMPLEX_PATH = output_dir / "15.1.complex_final.txt"
ASSIGNED_PATH = output_dir / "15.2.building_assigned.txt"
RESULT_PATH = output_dir / "20.1.dong_name_verified.txt"
BAD_PATH = output_dir / "20.2.name_mismatch.txt"

FLOOR_TOLERANCE = 1      # OSM building:levels와 대장 지상층수의 허용 차이


# ============================================================================
# 1. 대장 동명칭 목록
# ============================================================================
# 세대수>0 만 주거동이다. 주차장·주민운동시설도 주용도가 '공동주택'으로 오므로
# 주용도로는 거를 수 없다 (19의 헤더 참고).

print("===== 1. 건축물대장 =====")
ledger = pd.read_csv(LEDGER_PATH, sep="\t")
ledger = ledger[ledger["hhld_cnt"] > 0].copy()


def normalize_dong(value):
    """'제904동' '904동' '904' -> '904' / '가동' -> '가' / 'A동' -> 'A'"""
    text = str(value).strip().upper()
    text = re.sub(r"^제", "", text)
    text = re.sub(r"동$", "", text)
    text = re.sub(r"\s+", "", text)
    return re.sub(r"^0+(?=\d)", "", text)     # '0904' -> '904'


ledger["dong_key"] = ledger["dong_nm"].apply(normalize_dong)
ledger_names = ledger.groupby("join_key")["dong_key"].apply(set)
ledger_floors = ledger.groupby("join_key")["grnd_flr_cnt"].apply(list)
print(f"  주거동 {len(ledger)}건 / 단지(지번) {ledger['join_key'].nunique()}개")
print(f"  지번당 동 수 중앙값 {ledger.groupby('join_key').size().median():.0f}")


# ============================================================================
# 2. 배정 결과에 지번 키 붙이기
# ============================================================================

print("\n===== 2. 배정 결과 =====")


def normalize_address(text):
    text = text.fillna("").astype(str).str.strip()
    text = text.str.replace(r"^서울(특별시)?\s*", "", regex=True)
    text = text.str.replace(r"\s+", " ", regex=True)
    return text.str.replace(r"산\s+(?=\d)", "산", regex=True)


master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})
master["join_key"] = normalize_address(
    master["gu"].fillna("") + " " + master["umd_name"].fillna("")
    + " " + master["jibun"].fillna(""))

complexes = pd.read_csv(COMPLEX_PATH, sep="\t", dtype={"aptSeq": str})
complexes = complexes.merge(master[["aptSeq", "join_key"]], on="aptSeq", how="left")

assigned = pd.read_csv(ASSIGNED_PATH, sep="\t", dtype={"aptSeq": str})
assigned = assigned[assigned["aptSeq"].notna()].copy()
assigned["name_key"] = assigned["name"].apply(
    lambda v: None if pd.isna(v) else normalize_dong(v))
print(f"  배정된 동 {len(assigned)}개 / 이름 있음 {int(assigned['name_key'].notna().sum())} "
      f"({100 * assigned['name_key'].notna().mean():.1f}%)")


# ============================================================================
# 3. 동명칭 대조
# ============================================================================
# 한 지번에 여러 aptSeq가 있으면 대장 목록을 공유한다. 그 경우 "이 단지 것이
# 아니다"까지는 못 가리고 "이 지번 것이 아니다"까지만 판정된다.

print("\n===== 3. 동명칭 대조 =====")

key_of = complexes.set_index("aptSeq")["join_key"].to_dict()
assigned["join_key"] = assigned["aptSeq"].map(key_of)
# pandas가 None을 NaN으로 바꾸므로 `is not None`으로 거르면 이름 없는 동이
# 통과해 버린다. NaN in set 은 항상 False라 단지 전체가 불일치로 뒤집혔다.
assigned["ledger_has_name"] = [
    (name in ledger_names[key])
    if (not pd.isna(name) and key in ledger_names.index) else None
    for name, key in zip(assigned["name_key"], assigned["join_key"])
]

checkable = assigned[assigned["ledger_has_name"].notna()]
building_rate = 100 * checkable["ledger_has_name"].mean() if len(checkable) else 0.0
print(f"  대조 가능 동 {len(checkable)}개 / 대장에 있는 이름 "
      f"{int(checkable['ledger_has_name'].sum())} ({building_rate:.1f}%)")


def verdict_of(group):
    named = group[group["ledger_has_name"].notna()]
    if len(named):
        return "NAME_OK" if named["ledger_has_name"].all() else "NAME_BAD"
    return None


name_verdict = assigned.groupby("aptSeq").apply(verdict_of, include_groups=False)


def floor_verdict(apt_seq, levels):
    """이름이 없으면 층수 다중집합으로 근사한다. 정렬해 짝지어 비교"""
    key = key_of.get(apt_seq)
    if key not in ledger_floors.index:
        return "NO_LEDGER"
    mine = sorted(v for v in levels if pd.notna(v))
    theirs = sorted(v for v in ledger_floors[key] if pd.notna(v))
    if not mine or not theirs:
        return "NO_LEDGER"
    pairs = min(len(mine), len(theirs))
    diffs = [abs(mine[i] - theirs[i]) for i in range(pairs)]
    return "FLOOR_OK" if all(d <= FLOOR_TOLERANCE for d in diffs) else "FLOOR_BAD"


levels_by_apt = assigned.groupby("aptSeq")["levels"].apply(list)
verdicts = {}
for apt_seq in levels_by_apt.index:
    verdict = name_verdict.get(apt_seq)
    # Series가 None을 NaN으로 바꾸므로 `is None`으로는 못 걸러진다.
    # 이 때문에 층수 대조 분기가 통째로 죽어 FLOOR_* 판정이 하나도 안 나왔다
    if pd.isna(verdict):
        verdict = floor_verdict(apt_seq, levels_by_apt[apt_seq])
    verdicts[apt_seq] = verdict

complexes["name_verdict"] = complexes["aptSeq"].map(verdicts)
counts = complexes["name_verdict"].value_counts(dropna=False)
print(f"\n  단지 판정: {counts.to_dict()}")

decided = complexes[complexes["name_verdict"].isin(["NAME_OK", "NAME_BAD"])]
name_accuracy = 100 * (decided["name_verdict"] == "NAME_OK").mean() if len(decided) else 0.0
print(f"  이름으로 판정된 단지 {len(decided)}개 중 정상 {int((decided['name_verdict']=='NAME_OK').sum())} "
      f"({name_accuracy:.1f}%)")

print("\n  15의 신뢰도 등급이 실제로 유효한가:")
for label in ["HIGH", "MIXED", "LOW"]:
    sub = decided[decided["confidence"] == label]
    if len(sub):
        print(f"    {label:5s} n={len(sub):5d}  NAME_OK {100 * (sub['name_verdict']=='NAME_OK').mean():5.1f}%")


# ============================================================================
# 4. 판정 및 저장
# ============================================================================

print("\n===== 4. 판정 =====")
passed = name_accuracy >= 90.0
print(f"  [{'PASS' if passed else 'FAIL'}] 동명칭 일치율 >= 90%      실측 {name_accuracy:.1f}%")

columns = ["aptSeq", "apt_name", "gu", "confidence", "name_verdict",
           "n_dong", "reg_dong", "dong_diff", "n_deals", "join_key"]
complexes[columns].to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n")

bad = assigned[assigned["ledger_has_name"] == False].merge(
    complexes[["aptSeq", "apt_name", "gu", "confidence"]], on="aptSeq", how="left")
bad[["aptSeq", "apt_name", "gu", "confidence", "osm_id", "name", "name_key",
     "levels", "height_m", "dist_m", "assign_method"]].to_csv(
    BAD_PATH, sep="\t", index=False, lineterminator="\n")
print(f"\n  대장에 없는 이름으로 배정된 동 {len(bad)}개")
if len(bad):
    print(bad.groupby(["apt_name", "gu"]).size().nlargest(8).to_string())

print(f"\n전체 판정: {RESULT_PATH}")
print(f"불일치 동: {BAD_PATH}")
print(f"\n===== 동명칭 검증 {'통과' if passed else '미통과'} =====")
