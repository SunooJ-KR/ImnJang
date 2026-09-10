# ============================================================================
# 11.collect_trades.py
# ============================================================================
# Author:      yjkim
# Purpose:     서울 25개 구 아파트 실거래(매매 + 전월세) 5년치를 수집한다
# Description: D1에서 검증된 엔드포인트로 서울 전역·60개월을 내려받는다.
#              - 매매는 aptSeq가 있는 '상세' 엔드포인트만 쓴다 (일반은 aptSeq 없음)
#              - 응답 키를 전부 소문자로 정규화한 뒤 표준 이름으로 되돌린다.
#                매매는 roadNm, 전월세는 roadnm 이라 공통 처리하면 조용히 결측이 된다
#              - (구, 연월, 종류) 단위로 JSON 캐시를 남겨 중단 후 재개할 수 있다.
#                공공데이터포털 일 호출 한도에 걸려도 다음 날 이어받으면 된다
#
#              통과 기준:
#                (1) 미수집 (구, 연월, 종류) 조합 0건
#                (2) 매매·전월세 모두 aptSeq 결측률 < 1%
#                (3) 25개 구 전부 거래 1건 이상
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

work_dir = Path(__file__).parent
output_dir = work_dir / "output"
cache_dir = output_dir / "raw"

END_YM = "202608"        # 2026-09는 아직 진행 중인 달이라 제외한다
N_MONTHS = 60
ROWS_PER_PAGE = 1000
SLEEP_SEC = 0.2          # 포털 권장 간격. 제거하면 간헐적 500이 늘어난다

ENDPOINTS = {
    "sale": "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "rent": "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
}

SEOUL_LAWD = {
    "11110": "종로구", "11140": "중구", "11170": "용산구", "11200": "성동구",
    "11215": "광진구", "11230": "동대문구", "11260": "중랑구", "11290": "성북구",
    "11305": "강북구", "11320": "도봉구", "11350": "노원구", "11380": "은평구",
    "11410": "서대문구", "11440": "마포구", "11470": "양천구", "11500": "강서구",
    "11530": "구로구", "11545": "금천구", "11560": "영등포구", "11590": "동작구",
    "11620": "관악구", "11650": "서초구", "11680": "강남구", "11710": "송파구",
    "11740": "강동구",
}

# 소문자 정규화 후 되돌릴 표준 이름. 여기 없는 필드는 버린다
FIELD_MAP = {
    "aptseq": "aptSeq", "aptnm": "aptNm", "aptdong": "aptDong",
    "umdnm": "umdNm", "jibun": "jibun", "bonbun": "bonbun", "bubun": "bubun",
    "roadnm": "roadNm", "roadnmbonbun": "roadNmBonbun", "roadnmbubun": "roadNmBubun",
    "buildyear": "buildYear", "excluusear": "excluUseAr", "floor": "floor",
    "dealyear": "dealYear", "dealmonth": "dealMonth", "dealday": "dealDay",
    "sggcd": "sggCd",
    "dealamount": "dealAmount", "cdealtype": "cdealType",   # 매매 전용
    "deposit": "deposit", "monthlyrent": "monthlyRent",     # 전월세 전용
}


def load_key():
    env_path = work_dir / ".env"
    if not env_path.exists():
        raise SystemExit(".env 없음. DATA_GO_KR_KEY를 넣어주세요")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATA_GO_KR_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(".env에 DATA_GO_KR_KEY 없음")


SERVICE_KEY = load_key()


def mask_key(text):
    """requests의 예외 메시지에는 요청 URL이 통째로 들어간다. 키가 파일로 새지 않게 가린다."""
    return str(text).replace(SERVICE_KEY, "***")


# ============================================================================
# 1. API 호출
# ============================================================================

def parse_response(text):
    """공공데이터포털은 성공/실패 모두 XML이다. resultCode를 먼저 본다."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, f"XML 파싱 실패: {text[:200]}"

    code_node = root.find(".//resultCode")
    code = code_node.text.strip() if code_node is not None and code_node.text else None
    msg_node = root.find(".//resultMsg")
    message = msg_node.text.strip() if msg_node is not None and msg_node.text else ""

    # 성공 코드가 데이터셋마다 "00" / "0" / "000"으로 다르다. 숫자 0이면 성공으로 본다
    if code is not None and code.strip("0") != "":
        return None, f"API 오류 [{code}] {message}"

    total_node = root.find(".//totalCount")
    total = int(total_node.text) if total_node is not None and total_node.text else 0

    records = []
    for item in root.findall(".//item"):
        raw = {child.tag.lower(): (child.text or "").strip() for child in item}
        records.append({std: raw[low] for low, std in FIELD_MAP.items() if low in raw})
    return {"records": records, "total": total}, None


def fetch_month(kind, lawd_cd, deal_ymd):
    """한 (구, 연월)의 전체 페이지를 받는다. 실패하면 (None, 사유)."""
    collected = []
    page = 1
    while True:
        params = {
            "serviceKey": SERVICE_KEY,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "pageNo": str(page),
            "numOfRows": str(ROWS_PER_PAGE),
        }
        try:
            response = requests.get(ENDPOINTS[kind], params=params, timeout=30)
        except requests.exceptions.RequestException as error:
            return None, f"요청 실패: {error}"
        if response.status_code != 200:
            return None, f"HTTP {response.status_code}: {response.text[:150]}"

        parsed, error = parse_response(response.text)
        if error:
            return None, error

        collected.extend(parsed["records"])
        if len(collected) >= parsed["total"] or not parsed["records"]:
            return collected, None
        page += 1
        time.sleep(SLEEP_SEC)


def month_list():
    end = pd.Period(END_YM, freq="M")
    return [(end - offset).strftime("%Y%m") for offset in range(N_MONTHS - 1, -1, -1)]


# ============================================================================
# 2. 수집 (캐시가 있으면 건너뛴다)
# ============================================================================

months = month_list()
print(f"===== 1. 수집 대상 =====")
print(f"  {len(SEOUL_LAWD)}개 구 x {len(months)}개월 ({months[0]} ~ {months[-1]}) x 2종")
print(f"  총 {len(SEOUL_LAWD) * len(months) * 2}회 호출 예정 (캐시분 제외)")

failures = []
for kind in ENDPOINTS:
    (cache_dir / kind).mkdir(parents=True, exist_ok=True)
    done = 0
    for lawd_cd, gu_name in SEOUL_LAWD.items():
        for deal_ymd in months:
            cache_path = cache_dir / kind / f"{lawd_cd}_{deal_ymd}.json"
            if cache_path.exists():
                done += 1
                continue

            records, error = fetch_month(kind, lawd_cd, deal_ymd)
            if error:
                failures.append({"kind": kind, "lawd_cd": lawd_cd, "gu": gu_name,
                                 "deal_ymd": deal_ymd, "error": mask_key(error)[:200]})
                print(f"  [실패] {kind} {gu_name} {deal_ymd}: {mask_key(error)[:200]}")
                continue

            cache_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
            done += 1
            time.sleep(SLEEP_SEC)

        print(f"진행 [{kind}] {gu_name}: 누적 {done}/{len(SEOUL_LAWD) * len(months)}")

print("\n===== 2. 수집 완료 =====")
print(f"  실패 {len(failures)}건")
if failures:
    fail_path = output_dir / "11.0.collect_failures.txt"
    pd.DataFrame(failures).to_csv(fail_path, sep="\t", index=False, lineterminator="\n")
    print(f"  실패 목록: {fail_path}")
    print("  ※ 일 호출 한도라면 내일 이 스크립트를 다시 실행하면 이어받는다")


# ============================================================================
# 3. 적재 및 저장
# ============================================================================

def load_kind(kind):
    frames = []
    for cache_path in sorted((cache_dir / kind).glob("*.json")):
        records = json.loads(cache_path.read_text(encoding="utf-8"))
        if records:
            frames.append(pd.DataFrame(records))
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


print("\n===== 3. 적재 =====")
sale = load_kind("sale")
rent = load_kind("rent")

for name, frame in [("매매", sale), ("전월세", rent)]:
    if frame.empty:
        raise SystemExit(f"{name} 수집 결과가 비어 있다. 캐시를 확인할 것")
    frame["deal_ym"] = frame["dealYear"] + frame["dealMonth"].str.zfill(2)
    frame["gu"] = frame["sggCd"].map(SEOUL_LAWD)
    print(f"  {name} {len(frame):,}행 / 단지 {frame['aptSeq'].nunique():,}개")

def to_manwon(frame, column):
    """금액은 '145,000' 형태의 문자열이다. 결측·빈칸은 NaN으로 남긴다."""
    if column not in frame.columns:
        raise SystemExit(f"기대한 금액 필드 {column}가 없다. FIELD_MAP을 확인할 것")
    return pd.to_numeric(
        frame[column].str.replace(",", "", regex=False).replace("", pd.NA), errors="coerce"
    )


# 취소 거래는 실제 체결이 아니다. 지우지 않고 표시만 해 downstream이 고르게 한다
sale["is_cancelled"] = sale.get("cdealType", pd.Series("", index=sale.index)).fillna("").eq("O")
sale["deal_amount_manwon"] = to_manwon(sale, "dealAmount")
rent["deposit_manwon"] = to_manwon(rent, "deposit")
rent["monthly_rent_manwon"] = to_manwon(rent, "monthlyRent")
rent["is_jeonse"] = rent["monthly_rent_manwon"].eq(0)

sale_path = output_dir / "11.1.trades_sale.txt"
rent_path = output_dir / "11.2.trades_rent.txt"
sale.to_csv(sale_path, sep="\t", index=False, lineterminator="\n")
rent.to_csv(rent_path, sep="\t", index=False, lineterminator="\n")


# ============================================================================
# 4. 자체 검증
# ============================================================================

print("\n===== 4. 판정 =====")

expected = len(SEOUL_LAWD) * len(months)
missing = {
    kind: expected - len(list((cache_dir / kind).glob("*.json")))
    for kind in ENDPOINTS
}
sale_null = sale["aptSeq"].isna().mean() * 100 if "aptSeq" in sale else 100.0
rent_null = rent["aptSeq"].isna().mean() * 100 if "aptSeq" in rent else 100.0
gu_covered = sale["gu"].nunique()

checks = [
    ("미수집 조합 0건", sum(missing.values()) == 0, f"매매 {missing['sale']} / 전월세 {missing['rent']}"),
    ("매매 aptSeq 결측 < 1%", sale_null < 1, f"{sale_null:.2f}%"),
    ("전월세 aptSeq 결측 < 1%", rent_null < 1, f"{rent_null:.2f}%"),
    ("25개 구 전부 거래 존재", gu_covered == len(SEOUL_LAWD), f"{gu_covered}개 구"),
]
for label, passed, observed in checks:
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:<28} 실측 {observed}")

print(f"\n  전세 비율 {rent['is_jeonse'].mean() * 100:.1f}% / 전월세 거래량은 매매의 {len(rent) / len(sale):.1f}배")
print(f"\n매매: {sale_path}")
print(f"전월세: {rent_path}")
