# ============================================================================
# 27.collect_hospitals.py
# ============================================================================
# Author:      yjkim
# Purpose:     건강보험심사평가원 병원정보로 서울 요양기관 전량을 받는다
# Description: docs/data-model-and-ui.md 7절의 complex_metrics에 의료 지표
#              4종(tertiary_hosp_m / general_hosp_m / clinic_1km /
#              pediatric_1km)이 정의돼 있으나 근거 데이터가 없었다.
#
#              12가 수집한 OSM amenity=hospital 627건으로는 등급을 가를 수
#              없다. 심평원은 clCdNm에 상급종합·종합병원·병원·요양병원·
#              정신병원·의원 구분을 주고 좌표(XPos/YPos)까지 포함하므로
#              지오코딩도 필요 없다.
#
#              엔드포인트 (2026-09 확인, 활용신청 완료):
#                https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList
#                serviceKey / sidoCd=110000(서울) / numOfRows / pageNo / _type=json
#              서울 총 19,905건.
#
#              [한계] 소아과를 가릴 필드가 없다. 응답의 진료과목 관련 필드는
#              mdept(의과)·dety(치과)·cmdc(한방)의 일반의/인턴/레지던트/전문의
#              수일 뿐 과목별 구분이 아니다. pediatric_1km은 기관명에 '소아'가
#              들어가는지로 근사하며, 이는 소아청소년과 표방 의원을 대체로
#              잡지만 종합병원 안의 소아과는 놓친다. 근사임을 산출물과
#              docs/data-contract.md에 명시한다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
import re
import time
from pathlib import Path

import pandas as pd
import requests

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

CACHE_DIR = output_dir / "raw" / "hospital"
RESULT_PATH = output_dir / "27.1.hospitals.txt"

API_URL = "https://apis.data.go.kr/B551182/hospInfoServicev2/getHospBasisList"
SEOUL_SIDO_CD = "110000"
ROWS_PER_PAGE = 1000
SLEEP_SEC = 0.2

KEEP_FIELDS = {
    "ykiho": "ykiho", "yadmNm": "name", "clCd": "class_cd", "clCdNm": "class_nm",
    "sgguCdNm": "sggu_nm", "emdongNm": "emdong_nm", "addr": "addr",
    "XPos": "lon", "YPos": "lat", "estbDd": "estb_day",
    "drTotCnt": "doctor_total", "telno": "telno",
}


def load_env(key):
    for line in (work_dir / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f".env에 {key} 없음")


SERVICE_KEY = load_env("DATA_GO_KR_KEY")


def mask_key(text):
    """requests 예외 메시지에는 요청 URL이 통째로 들어간다. 키가 평문으로 새면 안 된다"""
    return re.sub(re.escape(SERVICE_KEY), "<SERVICE_KEY>", str(text))


# ============================================================================
# 1. 페이지 단위 수집
# ============================================================================
# 페이지마다 캐시한다. totalCount로 마지막 페이지를 계산해 끊는다.

print("===== 1. 심평원 요양기관 수집 =====")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_page(page_no):
    """한 페이지. 캐시가 있으면 API를 부르지 않는다."""
    cache_path = CACHE_DIR / f"seoul_{page_no:03d}.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8")), True

    params = {"serviceKey": SERVICE_KEY, "sidoCd": SEOUL_SIDO_CD,
              "numOfRows": ROWS_PER_PAGE, "pageNo": page_no, "_type": "json"}
    try:
        response = requests.get(API_URL, params=params, timeout=30)
    except requests.RequestException as error:
        raise SystemExit(f"요청 실패 [{page_no}쪽]: {mask_key(error)[:160]}")

    if response.status_code in (401, 403):
        raise SystemExit(
            f"인증/권한 오류 [{response.status_code}] {mask_key(response.text)[:200]}\n"
            "  -> https://www.data.go.kr/data/15001698/openapi.do 활용신청 확인")

    body = response.json()["response"]["body"]
    cache_path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    time.sleep(SLEEP_SEC)
    return body, False


first_body, _ = fetch_page(1)
total_count = int(first_body["totalCount"])
last_page = (total_count + ROWS_PER_PAGE - 1) // ROWS_PER_PAGE
print(f"  서울 요양기관 {total_count:,}건 / {last_page}쪽")

records, n_calls = [], 0
for page_no in range(1, last_page + 1):
    body, from_cache = fetch_page(page_no)
    n_calls += 0 if from_cache else 1
    items = (body.get("items") or {}).get("item") or []
    items = items if isinstance(items, list) else [items]
    records.extend(items)
    if page_no % 5 == 0 or page_no == last_page:
        print(f"  처리 중 [{page_no}/{last_page}쪽]: 누적 {len(records):,}건")

print(f"  신규 API 호출 {n_calls}건 (나머지는 캐시)")


# ============================================================================
# 2. 정리 및 소아과 근사
# ============================================================================

print("\n===== 2. 정리 =====")
hospitals = pd.DataFrame(records)[list(KEEP_FIELDS)].rename(columns=KEEP_FIELDS)
for column in ["lon", "lat", "doctor_total"]:
    hospitals[column] = pd.to_numeric(hospitals[column], errors="coerce")

# 소아과 전용 필드가 없어 기관명으로 근사한다. 헤더의 한계 설명 참고
hospitals["is_pediatric"] = hospitals["name"].astype(str).str.contains("소아", na=False)

print(f"  등급 분포: {hospitals['class_nm'].value_counts().to_dict()}")
print(f"  좌표 결측 {int(hospitals['lat'].isna().sum())}건")
print(f"  소아 표방 기관 {int(hospitals['is_pediatric'].sum())}건 (기관명 근사)")


# ============================================================================
# 3. 검증 및 저장
# ============================================================================

print("\n===== 3. 자체 검증 =====")
located = hospitals.dropna(subset=["lat", "lon"])
in_seoul = (located["lat"].between(37.4, 37.72)
            & located["lon"].between(126.7, 127.25))
checks = [
    ("수집 건수가 totalCount와 일치", len(hospitals) == total_count,
     f"{len(hospitals):,} vs {total_count:,}"),
    ("ykiho 유일", hospitals["ykiho"].is_unique, f"중복 {int(hospitals['ykiho'].duplicated().sum())}건"),
    ("좌표가 서울 범위 안", bool(in_seoul.all()), f"범위 밖 {int((~in_seoul).sum())}건"),
    ("상급종합이 존재", bool((hospitals["class_nm"] == "상급종합").any()),
     f"{int((hospitals['class_nm'] == '상급종합').sum())}건"),
]
all_passed = True
for label, passed, detail in checks:
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:26s} {detail}")

hospitals.to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n")
print(f"\n결과: {RESULT_PATH}")
print(f"\n===== 요양기관 수집 {'통과' if all_passed else '미통과'} =====")
assert all_passed, "자체 검증 실패"
