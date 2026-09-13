# ============================================================================
# 17.verify_registry.py
# ============================================================================
# Author:      yjkim
# Purpose:     동 배정 결과를 한국부동산원 공식 등록 정보와 대조해 검증한다
# Description: 15까지의 검증은 OSM start_date와 실거래 buildYear를 맞대보는
#              간접 방식이었다. 준공년도가 맞아도 동 수가 맞다는 보장은 없다.
#
#              한국부동산원 '공동주택 단지 식별정보'는 단지당 1행으로
#              동수·세대수·사용승인일을 제공한다. 서울 아파트 9,458건이 있고
#              우리 모집단 9,164건과 규모가 맞는다. 이것이 정답 데이터다.
#
#              대조 방식: 지번주소로 조인한다. 실거래 API의 umdNm+jibun과
#              등록 정보의 주소가 같은 체계이므로 문자열 정규화만 하면 붙는다.
#
#              데이터 출처 (로그인 불필요, 재다운로드 가능):
#                https://www.data.go.kr/data/15106861/fileData.do
#
#              주의 — 순환성: 15가 등록 동수를 배정 상한으로 쓰기 시작한 뒤로
#              이 대조는 더 이상 완전한 독립 검증이 아니다. 상한이 과다를 구조적으로
#              막으므로 여기서 남는 오차는 과소뿐이고, "맞는 동을 골랐는가"는
#              등록 정보로 알 수 없다. 그것은 여전히 사람이 봐야 한다(16).
#              이 스크립트가 실제로 재는 것은 (a) 주소 조인 품질 (b) 과소 배정이다.
#
#              통과 기준:
#                (1) 지번주소 매칭률 >= 90%
#                (2) 동수 정확 일치율 >= 90% (매칭된 단지 중)
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import re
from difflib import SequenceMatcher

import numpy as np
import pandas as pd
from pathlib import Path

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

REGISTRY_PATH = output_dir / "raw" / "reb" / "apt_registry.csv"
COMPLEX_PATH = output_dir / "15.1.complex_final.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"
RESULT_PATH = output_dir / "17.1.registry_verified.txt"
MISMATCH_PATH = output_dir / "17.2.dong_mismatch.txt"

APARTMENT_CODE = "1"     # 단지종류 1=아파트, 2=연립, 3=다세대
DONG_TOLERANCE = 0       # 동 수는 정수라 오차를 허용하지 않는다
YEAR_TOLERANCE = 2


# ============================================================================
# 1. 공식 등록 정보 로드
# ============================================================================

print("===== 1. 한국부동산원 등록 정보 =====")
registry = pd.read_csv(REGISTRY_PATH, encoding="utf-8-sig", dtype=str)
registry["reg_dong"] = pd.to_numeric(registry["동수"], errors="coerce")
registry["reg_units"] = pd.to_numeric(registry["세대수"], errors="coerce")
registry["reg_year"] = pd.to_numeric(
    registry["사용승인일"].str[:4], errors="coerce")

seoul = registry[registry["주소"].str.startswith("서울", na=False)
                 & (registry["단지종류"] == APARTMENT_CODE)].copy()
seoul["reg_name"] = (seoul["단지명_공시가격"]
                     .fillna(seoul["단지명_건축물대장"])
                     .fillna(seoul["단지명_도로명주소"]))
print(f"  전국 {len(registry)}행 -> 서울 아파트 {len(seoul)}건")
print(f"  동수 중앙값 {seoul['reg_dong'].median():.0f}, 최대 {seoul['reg_dong'].max():.0f}")


# ============================================================================
# 2. 지번주소 정규화 및 조인
# ============================================================================
# 등록 정보 "서울특별시 강서구 마곡동 744"
# 실거래     gu="강서구" umdNm="마곡동" jibun="744"
# 시도 표기만 떼면 같은 문자열이 된다.

print("\n===== 2. 지번주소 조인 =====")


def normalize(text):
    """공백 중복과 시도 접두를 없앤다. 산번지의 '산 12'/'산12' 표기 차이도 흡수한다"""
    text = text.fillna("").astype(str).str.strip()
    text = text.str.replace(r"^서울(특별시)?\s*", "", regex=True)
    text = text.str.replace(r"\s+", " ", regex=True)
    return text.str.replace(r"산\s+(?=\d)", "산", regex=True)


seoul["join_key"] = normalize(seoul["주소"])


# 아래 세 함수는 15.assign_dong_all.py 1절과 동일하다. 17은 15의 조인을
# 독립적으로 재현해 검증하는 스크립트라서, 15와 다른 매칭 규칙을 쓰면
# "조인이 맞았는가"가 아니라 "매칭 규칙이 다른가"를 재게 된다 — 반드시 같은
# 규칙을 써야 한다.
def normalize_complex_name(name):
    """단지명 비교용 정규화. 괄호 안 부연설명·공백·말미 '아파트'/'단지' 접미어를
    없애 표기 차이를 흡수한다. 예: '현대6차(78~81동)' / '현대6차 아파트' -> '현대6차'"""
    if pd.isna(name):
        return ""
    text = str(name).strip().upper()
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\s+", "", text)
    return re.sub(r"(아파트|단지)$", "", text)


def name_match_score(a, b):
    """완전일치(3) -> 부분포함(2) -> 최장공통부분문자열 2자+(1) -> 불일치(0)."""
    if not a or not b:
        return 0
    if a == b:
        return 3
    if a in b or b in a:
        return 2
    lcs = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return 1 if lcs.size >= 2 else 0


def match_complex_names(apt_names, reg_names):
    """한 지번 안 실거래 단지명과 등록 공식 단지명을 이름 유사도로 1:1
    매칭한다 (버그 1). 레벨이 높은 순으로 그리디 소진, 동점은 입력 순서
    (aptSeq 오름차순)로 결정. 매칭 실패분은 남은 공식 단지를 순서대로 받고,
    그마저 없으면 None(reg_dong 결측)."""
    norm_apt = [normalize_complex_name(n) for n in apt_names]
    norm_reg = [normalize_complex_name(n) for n in reg_names]
    matched = [None] * len(apt_names)
    used_reg = set()
    for level in (3, 2, 1):
        for i, a in enumerate(norm_apt):
            if matched[i] is not None or not a:
                continue
            for j, r in enumerate(norm_reg):
                if j in used_reg or not r:
                    continue
                if name_match_score(a, r) == level:
                    matched[i] = j
                    used_reg.add(j)
                    break
    unused_reg = iter(j for j in range(len(reg_names)) if j not in used_reg)
    for i in range(len(apt_names)):
        if matched[i] is None:
            matched[i] = next(unused_reg, None)
    return matched


def largest_remainder_split(total, n):
    """total을 정수 n등분해 합이 total과 정확히 같은 배열로 돌려준다 (버그 2).
    floor로 채우고 남는 몫을 앞에서부터(aptSeq 오름차순) 1씩 더한다."""
    base = int(total) // n
    remainder = int(total) - base * n
    shares = np.full(n, float(base))
    shares[:remainder] += 1
    return shares


master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})
complexes = pd.read_csv(COMPLEX_PATH, sep="\t", dtype={"aptSeq": str})
# 15가 이미 등록 동수를 상한으로 쓰므로 그 컬럼을 떨어뜨리고 여기서 다시 붙인다.
# 조인을 독립적으로 재현해야 조인 자체의 오류가 드러난다.
complexes = complexes.drop(columns=["reg_dong", "reg_units", "reg_year"], errors="ignore")
ours = complexes.merge(master[["aptSeq", "umd_name", "jibun"]], on="aptSeq", how="left")
ours["join_key"] = normalize(
    ours["gu"].fillna("") + " " + ours["umd_name"].fillna("") + " " + ours["jibun"].fillna(""))

# 한 지번에 여러 aptSeq가 등록된 경우가 있다(단지가 동별로 쪼개진 케이스).
duplicated_key = int(ours["join_key"].duplicated(keep=False).sum())
print(f"  우리 단지 {len(ours)}건 (같은 지번을 공유하는 aptSeq {duplicated_key}건)")

# 등록 정보도 drop_duplicates 하지 않는다 — 한 지번에 공식 단지가 여러 개면
# (예: 압구정동 456의 현대65동/7차/6차) 첫 행으로 뭉개면 안 된다 (버그 1).
# 공식 단지가 1개뿐인데 aptSeq가 여럿이면(분양/임대 분리 등) largest_remainder
# 로 나눈다 (버그 2). 15와 동일한 규칙이므로 두 스크립트의 reg_dong이 같아야
# "동수 대조"가 15의 배정을 실제로 검증한다.
match_cols = ["단지고유번호", "reg_name", "reg_dong", "reg_units", "reg_year"]
matched_values = {c: pd.Series([None] * len(ours), index=ours.index, dtype=object)
                  for c in match_cols}
seoul_by_key = {k: g for k, g in seoul.groupby("join_key")}

for join_key, group in ours.sort_values("aptSeq").groupby("join_key", sort=False):
    reg_group = seoul_by_key.get(join_key)
    if reg_group is None:
        continue
    idx = group.index
    if len(reg_group) == 1:
        row = reg_group.iloc[0]
        for c in match_cols:
            matched_values[c].loc[idx] = row[c]
        if pd.notna(row["reg_dong"]):
            matched_values["reg_dong"].loc[idx] = largest_remainder_split(row["reg_dong"], len(idx))
    else:
        matches = match_complex_names(group["apt_name"].tolist(), reg_group["reg_name"].tolist())
        for pos, i in enumerate(idx):
            j = matches[pos]
            if j is not None:
                row = reg_group.iloc[j]
                for c in match_cols:
                    matched_values[c][i] = row[c]

merged = ours.copy()
for c in match_cols:
    merged[c] = matched_values[c]
for c in ["reg_dong", "reg_units", "reg_year"]:
    merged[c] = pd.to_numeric(merged[c], errors="coerce")

matched = merged["reg_dong"].notna()
match_rate = 100 * matched.mean()
print(f"  주소 매칭: {int(matched.sum())}/{len(merged)} ({match_rate:.1f}%)")
print(f"  미매칭 상위 구: {merged[~matched]['gu'].value_counts().head(3).to_dict()}")


# ============================================================================
# 3. 동수 대조 — 배정이 실제로 맞았는지
# ============================================================================

print("\n===== 3. 동수 대조 =====")

target = merged[matched & merged["n_dong"].notna()].copy()
target["dong_diff"] = target["n_dong"] - target["reg_dong"]
exact = target["dong_diff"].abs() <= DONG_TOLERANCE
dong_accuracy = 100 * exact.mean() if len(target) else 0.0

print(f"  대조 가능 {len(target)}건 (배정된 단지 중 등록 정보가 있는 것)")
print(f"  동수 정확 일치: {int(exact.sum())} ({dong_accuracy:.1f}%)")
print(f"  과다 배정 {int((target['dong_diff'] > 0).sum())}건 / "
      f"과소 배정 {int((target['dong_diff'] < 0).sum())}건")
print(f"  차이 중앙값 {target['dong_diff'].median():+.0f}동, "
      f"최대 과다 {target['dong_diff'].max():+.0f}동")

print(f"\n  신뢰도 등급별 (15가 부여한 등급이 실제로 유효한가):")
for label in ["HIGH", "MIXED", "LOW"]:
    sub = target[target["confidence"] == label]
    if not len(sub):
        continue
    rate = 100 * (sub["dong_diff"].abs() <= DONG_TOLERANCE).mean()
    over = 100 * (sub["dong_diff"] > 0).mean()
    print(f"    {label:5s} n={len(sub):5d}  정확 {rate:5.1f}%  과다 {over:5.1f}%  "
          f"차이 중앙값 {sub['dong_diff'].median():+.0f}")

print(f"\n  준공년도 대조 (등록 정보 기준, OSM이 아니라):")
year_target = target.dropna(subset=["reg_year", "build_year"])
year_ok = (year_target["build_year"] - year_target["reg_year"]).abs() <= YEAR_TOLERANCE
print(f"    n={len(year_target)}  ±{YEAR_TOLERANCE}년 일치 {100 * year_ok.mean():.1f}%")


# ============================================================================
# 4. 판정 및 저장
# ============================================================================

print("\n===== 4. 판정 =====")
checks = [
    ("지번주소 매칭률 >= 90%", match_rate, 90.0),
    ("동수 정확 일치율 >= 90%", dong_accuracy, 90.0),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:26s} 실측 {value:.1f}%")

columns = ["aptSeq", "apt_name", "gu", "confidence", "n_dong", "reg_dong", "dong_diff",
           "reg_units", "build_year", "reg_year", "reg_name", "n_deals", "단지고유번호"]
merged["dong_diff"] = merged["n_dong"] - merged["reg_dong"]
merged[columns].to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n")

worst = target[target["dong_diff"] != 0].sort_values("dong_diff", ascending=False)
worst[columns].to_csv(MISMATCH_PATH, sep="\t", index=False, lineterminator="\n")
print(f"\n  과다 배정 상위 10건:")
print(worst.head(10)[["confidence", "apt_name", "gu", "n_dong", "reg_dong",
                      "dong_diff", "n_deals"]].to_string(index=False))

print(f"\n전체 대조: {RESULT_PATH}")
print(f"불일치만:  {MISMATCH_PATH}")
print(f"\n===== 등록 정보 대조 {'통과' if all_passed else '미통과'} =====")
