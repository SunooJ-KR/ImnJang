# ============================================================================
# 06.probe_molit_api.py
# ============================================================================
# Author:      yjkim
# Purpose:     공공데이터포털 실거래 API 키 검증 및 응답 스키마 확인
# Description: D1 블로킹 항목. 사슬의 마지막 구간(단지 -> 실거래)을 열기 위해
#              다음을 실측한다.
#              - 발급 키가 실제로 동작하는가
#              - aptSeq(단지 키)가 응답에 존재하는가 (일반 자료 vs 상세 자료)
#              - 해제여부/거래유형 등 정제에 필요한 필드가 있는가
#              - 한 번에 받을 수 있는 행 수와 총건수 (호출량 산정)
#              - 단지 마스터(K-apt) 접근 가능 여부
#
#              키는 .env에서 읽는다. 스크립트에 하드코딩하지 않는다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

work_dir = Path(__file__).parent
output_dir = work_dir / "output"
output_dir.mkdir(exist_ok=True)


def load_key():
    env_path = work_dir / ".env"
    if not env_path.exists():
        raise SystemExit(".env 없음. DATA_GO_KR_KEY를 넣어주세요")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATA_GO_KR_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(".env에 DATA_GO_KR_KEY 없음")


SERVICE_KEY = load_key()
GANGNAM_LAWD_CD = "11680"
TEST_YMD = "202606"

ENDPOINTS = {
    "아파트 매매 (일반)":
        "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade",
    "아파트 매매 (상세)":
        "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev",
    "아파트 전월세":
        "https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent",
}


# ============================================================================
# 1. 호출 및 파싱
# ============================================================================

def call_api(url, params, timeout=30):
    try:
        response = requests.get(url, params=params, timeout=timeout)
    except requests.exceptions.RequestException as error:
        return None, f"요청 실패: {error}"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}: {response.text[:200]}"
    return response.text, None


def parse_response(text):
    """공공데이터포털은 성공/실패 모두 XML로 반환한다. resultCode를 먼저 본다."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, f"XML 파싱 실패: {text[:200]}"

    code_node = root.find(".//resultCode")
    msg_node = root.find(".//resultMsg")
    code = code_node.text.strip() if code_node is not None and code_node.text else None
    message = msg_node.text.strip() if msg_node is not None and msg_node.text else ""

    # 성공 코드가 데이터셋마다 "00" / "0" / "000"으로 다르다. 숫자 0이면 성공으로 본다.
    if code is not None and code.strip("0") != "":
        return None, f"API 오류 [{code}] {message}"

    items = root.findall(".//item")
    total_node = root.find(".//totalCount")
    total = int(total_node.text) if total_node is not None and total_node.text else None

    records = []
    for item in items:
        records.append({child.tag: (child.text or "").strip() for child in item})
    return {"records": records, "total": total}, None


# ============================================================================
# 2. 엔드포인트별 검증
# ============================================================================

print("===== 1. 엔드포인트 검증 (강남구 " + TEST_YMD + ") =====")

probe_results = {}
for label, url in ENDPOINTS.items():
    params = {
        "serviceKey": SERVICE_KEY,
        "LAWD_CD": GANGNAM_LAWD_CD,
        "DEAL_YMD": TEST_YMD,
        "pageNo": "1",
        "numOfRows": "100",
    }
    text, error = call_api(url, params)
    if error:
        print(f"\n[{label}]\n  실패: {error}")
        continue

    parsed, error = parse_response(text)
    if error:
        print(f"\n[{label}]\n  실패: {error}")
        continue

    records = parsed["records"]
    print(f"\n[{label}]")
    print(f"  총건수 {parsed['total']}, 수신 {len(records)}행")
    if records:
        fields = sorted(records[0].keys())
        print(f"  필드 {len(fields)}개: {', '.join(fields)}")
        probe_results[label] = {"fields": set(fields), "records": records, "total": parsed["total"]}
    time.sleep(0.5)


# ============================================================================
# 3. 핵심 필드 존재 여부
# ============================================================================

print("\n===== 2. 핵심 필드 확보 여부 =====")

# plan.md §5.4, §6.5에서 필요하다고 명시한 필드들
REQUIRED = {
    "aptSeq": "단지 키 (사슬 연결의 핵심)",
    "aptNm": "단지명",
    "umdNm": "법정동",
    "jibun": "지번",
    "excluUseAr": "전용면적",
    "floor": "층",
    "dealAmount": "거래금액",
    "buildYear": "건축년도",
    "cdealType": "해제여부",
    "dealingGbn": "거래유형(직거래/중개)",
    "aptDong": "동 (등기완료 건만)",
}

rows = []
for field, purpose in REQUIRED.items():
    row = {"field": field, "purpose": purpose}
    for label in probe_results:
        row[label] = "O" if field in probe_results[label]["fields"] else "-"
    rows.append(row)
print(pd.DataFrame(rows).to_string(index=False))


# ============================================================================
# 4. aptSeq / aptDong 실제 결측률
# ============================================================================

print("\n===== 3. aptSeq / aptDong 실측 결측률 =====")

for label, result in probe_results.items():
    df = pd.DataFrame(result["records"])
    parts = [f"[{label}] n={len(df)}"]
    for field in ["aptSeq", "aptDong"]:
        if field in df.columns:
            filled = (df[field].astype(str).str.strip() != "").mean()
            parts.append(f"{field} 확보 {100 * filled:.0f}%")
        else:
            parts.append(f"{field} 없음")
    print("  " + " | ".join(parts))

    if "aptSeq" in df.columns and "aptNm" in df.columns:
        sample = df[["aptSeq", "aptNm", "umdNm"]].drop_duplicates("aptSeq").head(5)
        print(sample.to_string(index=False))
        break


# ============================================================================
# 5. 호출량 산정
# ============================================================================

print("\n===== 4. 호출량 산정 =====")

if probe_results:
    label = next(iter(probe_results))
    monthly_total = probe_results[label]["total"] or 0
    print(f"  강남구 {TEST_YMD} 매매 {monthly_total}건 -> numOfRows=1000이면 1페이지로 충분")
    print(f"  서울 25구 x 60개월 x 2종 = 3,000회 (페이지네이션 제외)")
    print(f"  개발계정 일 1,000건 가정 시 3일 소요 -> CSV 벌크 병행 필요")


# ============================================================================
# 6. 저장
# ============================================================================

if probe_results:
    label = next(iter(probe_results))
    sample_path = output_dir / "06.1.molit_sample.txt"
    pd.DataFrame(probe_results[label]["records"]).to_csv(sample_path, sep="\t", index=False, lineterminator="\n")
    print(f"\n샘플 응답: {sample_path}")

has_aptseq = any("aptSeq" in r["fields"] for r in probe_results.values())
print(f"\n===== API 검증 {'통과' if probe_results and has_aptseq else '미통과'} "
      f"(aptSeq {'확보' if has_aptseq else '미확보'}) =====")
