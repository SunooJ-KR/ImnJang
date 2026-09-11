# ============================================================================
# 31.match_redevelop.py
# ============================================================================
# Author:      yjkim
# Purpose:     서울시 정비사업 구역의 대표 지번을 아파트 단지에 엄격 매칭한다
# Description: 정비사업 원본은 구역 전체가 아닌 대표 지번 한 개만 제공한다.
#              따라서 이 파일은 자치구·법정동·본번·부번이 모두 같은 경우만
#              연결한다. 결과에 없는 단지는 '정비사업 구역이 아님'이 아니라
#              대표 지번이 달라 매칭하지 못했을 수도 있는 미확인 상태다.
#
#              구역 polygon 또는 대표 지번 좌표가 제공되지 않아 spatial 매칭은
#              수행하지 않는다. 동 단위 등 느슨한 매칭은 오매칭 위험 때문에
#              의도적으로 적용하지 않는다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import re
from pathlib import Path

import pandas as pd


work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
RAW_PATH = work_dir / "output" / "raw" / "redevelop" / "redevelop_2606.xlsx"
MASTER_PATH = work_dir / "output" / "14.1.geocoded_master.txt"
RESULT_PATH = work_dir / "output" / "31.1.complex_redevelop.txt"

EXPECTED_HEADER = {
    0: "CODE",
    1: "순번",
    2: "자치구",
    3: "구역명",
    4: "위치1(지번주소)",
    5: "위치2(도로명주소)",
    6: "공공/민간",
    7: "일반/재촉지구",
    8: "사업유형",
    9: "사업추진단계",
    10: "기존 가구수(멸실량)",
    23: "총합계",
    24: "분양",
    25: "임대",
}

EXPECTED_TYPES = {
    "주택정비형 재개발": 164,
    "도시정비형 재개발": 138,
    "공동주택재건축": 126,
    "아파트지구재건축": 40,
    "단독주택재건축": 28,
}
EXPECTED_STAGES = {
    "조합설립": 124,
    "관리처분": 75,
    "추진위": 73,
    "사업시행": 66,
    "착공": 62,
    "구역지정": 52,
    "건축심의": 44,
}
RECONSTRUCTION_TYPES = {"공동주택재건축", "아파트지구재건축"}
REDEVELOPMENT_TYPES = {"주택정비형 재개발", "도시정비형 재개발"}


def header_at(raw_headers, column):
    """병합 헤더의 네 행 중 해당 열에서 기대 문자열을 찾는다."""
    values = raw_headers.iloc[:, column].dropna().astype(str).tolist()
    return values


def parse_zone_jibun(value, gu):
    """정비사업 대표 지번에서 법정동·본번·부번을 안전하게 분리한다.

    '번지', '일대', '일원'은 지번 뒤의 범위 표기이므로 제거한다. 쉼표 등으로
    복수 지번을 적은 값은 어느 하나를 고르지 않고 None으로 남긴다.
    """
    if pd.isna(value):
        return None

    address = re.sub(r"\s+", "", str(value).strip())
    gu = re.sub(r"\s+", "", str(gu).strip())
    if address.startswith(gu):
        address = address[len(gu):]
    address = re.sub(r"(?:번지)?(?:일대|일원)?$", "", address)

    # 동명 내부 숫자(신문로1가, 을지로6가)는 마지막 한글 문자 뒤 숫자만 번지다.
    matched = re.fullmatch(
        r"(?P<umd>.*[가-힣])(?P<bon>\d+)(?:-(?P<bub>\d+))?", address
    )
    if matched is None:
        return None
    return (
        matched.group("umd"),
        int(matched.group("bon")),
        int(matched.group("bub") or 0),
    )


def parse_master_jibun(value):
    """단지 master의 jibun을 본번·부번 정수로 분리한다."""
    if pd.isna(value):
        return None
    matched = re.fullmatch(r"\s*(?P<bon>\d+)(?:\s*-\s*(?P<bub>\d+))?\s*", str(value))
    if matched is None:
        return None
    return int(matched.group("bon")), int(matched.group("bub") or 0)


def add_zone_parts(zones):
    """원문 지번을 분해하고 분해 실패 원문을 보존한다."""
    parsed = zones.apply(lambda row: parse_zone_jibun(row["jibun_raw"], row["gu"]), axis=1)
    zones["umd"] = parsed.map(lambda item: item[0] if item else pd.NA)
    zones["bon"] = parsed.map(lambda item: item[1] if item else pd.NA).astype("Int64")
    zones["bub"] = parsed.map(lambda item: item[2] if item else pd.NA).astype("Int64")
    return zones


# ============================================================================
# 1. 원본 읽기 및 병합 헤더 위치 검증
# ============================================================================

print("===== 1. 원본 읽기 및 헤더 위치 검증 =====")
raw_headers = pd.read_excel(RAW_PATH, header=None, nrows=4)
header_checks = {
    f"{column}번 열 {label}": label in header_at(raw_headers, column)
    for column, label in EXPECTED_HEADER.items()
}
for label, passed in header_checks.items():
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}")

# 병합 헤더 네 행을 제외한 행만 데이터로 읽는다. 열 위치는 위 검증 뒤에 사용한다.
source = pd.read_excel(RAW_PATH, header=None, skiprows=4)
zones = source[[0, 1, 2, 3, 4, 8, 9]].copy()
zones.columns = [
    "zone_code", "zone_order", "gu", "redevelop_zone_name", "jibun_raw",
    "redevelop_type", "redevelop_stage",
]
zones["zone_code"] = zones["zone_code"].astype("string")
zones["gu"] = zones["gu"].astype("string").str.strip()

print(f"  정비사업 원본 행: {len(zones):,}건")
print(f"  사업유형 분포: {zones['redevelop_type'].value_counts().to_dict()}")
print(f"  추진단계 분포: {zones['redevelop_stage'].value_counts().to_dict()}")


# ============================================================================
# 2. 지번 분해 및 단지 master 정리
# ============================================================================

print("\n===== 2. 지번 분해 및 완전일치 매칭 =====")
zones = add_zone_parts(zones)
parse_failed = zones[zones["umd"].isna()]
print(f"  정비사업 지번 분해 실패: {len(parse_failed):,}건")
if not parse_failed.empty:
    print("  실패 원문 예시 (최대 10건):")
    for raw_address in parse_failed["jibun_raw"].head(10):
        print(f"    - {raw_address}")

master = pd.read_csv(MASTER_PATH, sep="\t", dtype="string")
required_master_columns = {"aptSeq", "gu", "umd_name", "jibun", "lon", "lat"}
missing_master_columns = required_master_columns - set(master.columns)
if missing_master_columns:
    raise ValueError(f"geocoded master 필수 컬럼 없음: {sorted(missing_master_columns)}")

master["gu"] = master["gu"].str.strip()
master["umd"] = master["umd_name"].str.strip()
master_parts = master["jibun"].map(parse_master_jibun)
master["bon"] = master_parts.map(lambda item: item[0] if item else pd.NA).astype("Int64")
master["bub"] = master_parts.map(lambda item: item[1] if item else pd.NA).astype("Int64")
master_valid = master.dropna(subset=["gu", "umd", "bon", "bub"]).copy()
zone_valid = zones.dropna(subset=["gu", "umd", "bon", "bub"]).copy()

# 법정동명·본번·부번에 자치구까지 더해 동명 충돌(예: 서로 다른 구의 신사동)을 막는다.
matched = zone_valid.merge(
    master_valid[["aptSeq", "gu", "umd", "bon", "bub"]],
    on=["gu", "umd", "bon", "bub"],
    how="inner",
    validate="many_to_many",
)
matched["redevelop_matched"] = True
matched["redevelop_match_method"] = "jibun_exact"
result = matched[
    [
        "aptSeq", "redevelop_type", "redevelop_stage", "redevelop_zone_name",
        "redevelop_matched", "redevelop_match_method",
    ]
].drop_duplicates()
result = result.sort_values(["aptSeq", "redevelop_zone_name"], kind="stable")

print("  spatial 매칭: 생략 (구역 polygon·대표 지번 좌표가 없고 API 호출을 추가하지 않음)")
print(f"  지번 완전일치 연결: {len(result):,}건 / 단지 {result['aptSeq'].nunique():,}개")


# ============================================================================
# 3. 자체 검증 및 저장
# ============================================================================

print("\n===== 3. 자체 검증 =====")
matched_zone_codes = set(matched["zone_code"])
matched_zone_count = len(matched_zone_codes)
complex_count = master["aptSeq"].nunique()
zone_match_rate = matched_zone_count / len(zones)
complex_match_rate = result["aptSeq"].nunique() / complex_count
zone_complex_counts = matched.groupby("zone_code")["aptSeq"].nunique()
multi_complex_zone_count = int((zone_complex_counts >= 2).sum())

reconstruction_mask = zones["redevelop_type"].isin(RECONSTRUCTION_TYPES)
redevelopment_mask = zones["redevelop_type"].isin(REDEVELOPMENT_TYPES)
reconstruction_total = int(reconstruction_mask.sum())
redevelopment_total = int(redevelopment_mask.sum())
reconstruction_matched = int(zones.loc[reconstruction_mask, "zone_code"].isin(matched_zone_codes).sum())
redevelopment_matched = int(zones.loc[redevelopment_mask, "zone_code"].isin(matched_zone_codes).sum())
reconstruction_rate = reconstruction_matched / reconstruction_total
redevelopment_rate = redevelopment_matched / redevelopment_total

checks = [
    ("엑셀 데이터 496행", len(zones) == 496, f"{len(zones):,}건"),
    ("병합 헤더 위치", all(header_checks.values()), f"실패 {sum(not value for value in header_checks.values())}개"),
    ("사업유형 분포", zones["redevelop_type"].value_counts().to_dict() == EXPECTED_TYPES,
     str(zones["redevelop_type"].value_counts().to_dict())),
    ("추진단계 분포", zones["redevelop_stage"].value_counts().to_dict() == EXPECTED_STAGES,
     str(zones["redevelop_stage"].value_counts().to_dict())),
    ("결과는 완전일치만 포함", result["redevelop_match_method"].eq("jibun_exact").all(),
     f"{len(result):,}건"),
    ("결과의 매칭값은 모두 True", result["redevelop_matched"].eq(True).all(),
     f"{len(result):,}건"),
    ("재건축 매칭률이 재개발보다 높음", reconstruction_rate > redevelopment_rate,
     f"재건축 {reconstruction_matched}/{reconstruction_total} ({reconstruction_rate:.1%}), "
     f"재개발 {redevelopment_matched}/{redevelopment_total} ({redevelopment_rate:.1%})"),
]
all_passed = True
for label, passed, detail in checks:
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}: {detail}")

print(f"\n  매칭 구역: {matched_zone_count:,}/{len(zones):,} ({zone_match_rate:.1%})")
print(f"  매칭 단지: {result['aptSeq'].nunique():,}/{complex_count:,} ({complex_match_rate:.1%})")
print(f"  한 구역에 2개 이상 단지가 붙은 경우: {multi_complex_zone_count:,}건")
print("  미매칭 단지는 결과에 행을 만들지 않음 (구역 아님/대표 지번 불일치 미구분)")

result.to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n")
print(f"\n결과: {RESULT_PATH}")
print(f"\n===== 정비사업 단지 매칭 {'통과' if all_passed else '미통과'} =====")
assert all_passed, "자체 검증 실패"
