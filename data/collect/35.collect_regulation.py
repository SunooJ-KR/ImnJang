# ============================================================================
# 35.collect_regulation.py
# ============================================================================
# Author:      yjkim
# Purpose:     서울 아파트 화면 표시용 토지거래허가·정비사업 규제 원본을 주기 수집한다
# Description: 인증키 없이 공개된 서울시 원본 두 종류를 수집한다.
#
#              1) 토지거래허가구역 지정현황은 단지 화면의 안내 문구에만 사용한다.
#                 2025-10-20 지정의 '市 전체 / 아파트'가 존재하면 서울 모든
#                 아파트에서 같은 값이므로 모델 feature로 사용하면 안 된다(상수).
#                 응답에서 이 지정이 사라지면 false가 되도록 하드코딩하지 않는다.
#
#              2) 정비사업 추진현황은 분기마다 파일 목록의 seq가 바뀐다. 목록
#                 HTML의 downloadFile('N')와 a 태그 title을 함께 읽어 파일명 기준
#                 최신 기준시점의 seq를 선택한다. 구·동 등 비정형 지정 문구는
#                 파싱하지 않고 dsgnTypeNm 원문을 그대로 보존한다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import csv
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import requests


work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

LAND_URL = "https://land.seoul.go.kr/land/other/searchAppointStatusList.do"
LAND_REFERER = "https://land.seoul.go.kr/land/other/appointStatusSeoul.do"
REDEVELOP_LIST_URL = "https://data.seoul.go.kr/dataList/OA-22856/S/1/datasetView.do"
REDEVELOP_DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?useCache=false"
REDEVELOP_REFERER = "https://data.seoul.go.kr/dataList/OA-22856/S/1/datasetView.do"

RAW_REGULATION_DIR = output_dir / "raw" / "regulation"
REDEVELOP_DIR = output_dir / "raw" / "redevelop"
LAND_CACHE_PATH = RAW_REGULATION_DIR / "land_permit_zones.json"
REDEVELOP_LIST_CACHE_PATH = RAW_REGULATION_DIR / "redevelop_file_list.html"
LAND_RESULT_PATH = output_dir / "35.1.land_permit_zones.txt"
SUMMARY_RESULT_PATH = output_dir / "35.2.regulation_summary.json"

LAND_FIELDS = [
    ("sn", "sn"),
    ("crtrYmd", "crtr_ymd"),
    ("dsgnAuthrNm", "dsgn_authr_nm"),
    ("dsgnTypeNm", "dsgn_type_nm"),
    ("frstDsgnYmd", "frst_dsgn_ymd"),
    ("areaCn", "area_cn"),
    ("rmrkCn", "rmrk_cn"),
]


def parse_as_of(value):
    """'2026. 8. 16.' 형식의 원문 기준일을 ISO 날짜로 바꾼다."""
    matched = re.fullmatch(r"\s*(\d{4})\.\s*(\d{1,2})\.\s*(\d{1,2})\.?\s*", str(value))
    if matched is None:
        raise ValueError(f"알 수 없는 crtrYmd 형식: {value!r}")
    year, month, day = (int(item) for item in matched.groups())
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        raise ValueError(f"유효하지 않은 crtrYmd: {value!r}")
    return f"{year:04d}-{month:02d}-{day:02d}"


def validate_land_payload(payload):
    """토지거래허가구역 응답의 최소 계약을 확인하고 원본 목록을 반환한다."""
    records = payload.get("result") if isinstance(payload, dict) else None
    if not isinstance(records, list) or not records:
        raise ValueError("토지거래허가구역 응답에 result 목록이 없거나 비어 있음")
    if any(not str(record.get("crtrYmd") or "").strip() for record in records):
        raise ValueError("토지거래허가구역 응답에 비어 있는 crtrYmd가 있음")
    return records


def load_land_records():
    """항상 최신 응답을 먼저 요청하고, 실패 시에만 직전 원본 캐시를 쓴다."""
    headers = {
        "Referer": LAND_REFERER,
        "X-Requested-With": "XMLHttpRequest",
    }
    try:
        response = requests.post(LAND_URL, headers=headers, data={"pageIndex": "1"}, timeout=30)
        response.raise_for_status()
        payload = response.json()
        records = validate_land_payload(payload)
    except (requests.RequestException, ValueError) as error:
        if not LAND_CACHE_PATH.exists():
            raise SystemExit(f"토지거래허가구역 요청 실패, 캐시도 없음: {str(error)[:200]}")
        print(f"  캐시 사용: {LAND_CACHE_PATH} (토지거래허가구역 요청 실패)")
        try:
            payload = json.loads(LAND_CACHE_PATH.read_text(encoding="utf-8"))
            records = validate_land_payload(payload)
        except (OSError, json.JSONDecodeError, ValueError) as cache_error:
            raise SystemExit(f"토지거래허가구역 캐시를 읽을 수 없음: {cache_error}")
    else:
        LAND_CACHE_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return records


class RedevelopFileParser(HTMLParser):
    """파일 목록 페이지에서 downloadFile seq와 같은 a 태그의 title을 짝지음."""

    def __init__(self):
        super().__init__()
        self.files = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attributes = dict(attrs)
        command = " ".join(
            value for key, value in attrs if key.lower() in {"href", "onclick"} and value
        )
        matched = re.search(r"downloadFile\(\s*['\"]?(\d+)", command)
        title = (attributes.get("title") or "").strip()
        period = re.search(r"(\d{2,4})\s*년\s*(\d{1,2})\s*월", title)
        if matched is None or period is None or not title.lower().endswith(".xlsx"):
            return

        raw_year, raw_month = period.groups()
        year = int(raw_year)
        year = 2000 + year if len(raw_year) == 2 else year
        month = int(raw_month)
        if 1 <= month <= 12:
            self.files.append({
                "seq": matched.group(1),
                "title": title,
                "year": year,
                "month": month,
            })


def parse_redevelop_links(html):
    parser = RedevelopFileParser()
    parser.feed(html)
    parser.close()
    if not parser.files:
        raise ValueError("파일 목록 HTML에서 기준시점이 있는 xlsx downloadFile 링크를 찾지 못함")
    return parser.files


def load_redevelop_links():
    """분기 파일 목록은 네트워크 실패 때만 직전 HTML 캐시를 사용한다."""
    try:
        response = requests.get(REDEVELOP_LIST_URL, headers={"Referer": REDEVELOP_REFERER}, timeout=30)
        response.raise_for_status()
        html = response.text
        links = parse_redevelop_links(html)
    except (requests.RequestException, ValueError) as error:
        if not REDEVELOP_LIST_CACHE_PATH.exists():
            raise SystemExit(f"정비사업 파일 목록 요청 실패, 캐시도 없음: {str(error)[:200]}")
        print(f"  캐시 사용: {REDEVELOP_LIST_CACHE_PATH} (정비사업 파일 목록 요청 실패)")
        try:
            html = REDEVELOP_LIST_CACHE_PATH.read_text(encoding="utf-8")
            links = parse_redevelop_links(html)
        except (OSError, ValueError) as cache_error:
            raise SystemExit(f"정비사업 파일 목록 캐시를 읽을 수 없음: {cache_error}")
    else:
        REDEVELOP_LIST_CACHE_PATH.write_text(html, encoding="utf-8")
    return links


def select_latest_redevelop(links):
    """파일명에 적힌 기준 연월이 가장 최신인 링크를 고른다."""
    selected = max(links, key=lambda item: (item["year"], item["month"], int(item["seq"])))
    if not any(
        item["seq"] == selected["seq"] and item["title"] == selected["title"]
        for item in links
    ):
        raise ValueError("선택한 seq와 파일명이 파일 목록 HTML에 함께 존재하지 않음")
    return selected


def is_xlsx(content):
    """xlsx는 ZIP 컨테이너이므로 최소한의 형식 오류를 다운로드 단계에서 막는다."""
    return len(content) > 4 and content.startswith(b"PK")


def load_redevelop_xlsx(selected):
    """선택한 분기 xlsx를 받고, 요청 실패 시 같은 기준시점 파일만 캐시로 쓴다."""
    cache_path = REDEVELOP_DIR / f"redevelop_{selected['year'] % 100:02d}{selected['month']:02d}.xlsx"
    request_data = {
        "infId": "OA-22856",
        "seq": selected["seq"],
        "seqNo": "",
        "infSeq": "2",
    }
    try:
        response = requests.post(
            REDEVELOP_DOWNLOAD_URL,
            headers={"Referer": REDEVELOP_REFERER},
            data=request_data,
            timeout=60,
        )
        response.raise_for_status()
        if not is_xlsx(response.content):
            raise ValueError("정비사업 다운로드 응답이 xlsx 형식이 아님")
    except (requests.RequestException, ValueError) as error:
        if not cache_path.exists() or not is_xlsx(cache_path.read_bytes()):
            raise SystemExit(f"정비사업 xlsx 다운로드 실패, 사용할 캐시도 없음: {str(error)[:200]}")
        print(f"  캐시 사용: {cache_path} (정비사업 xlsx 다운로드 실패)")
    else:
        temporary_path = cache_path.with_suffix(".xlsx.tmp")
        temporary_path.write_bytes(response.content)
        temporary_path.replace(cache_path)
    return cache_path


def is_citywide_apartment_designation(record):
    """서울 전역 아파트 허가구역 여부를 원문 문구로 판정한다."""
    area = re.sub(r"\s+", "", str(record.get("areaCn") or ""))
    designation_type = re.sub(r"\s+", "", str(record.get("dsgnTypeNm") or ""))
    remark = re.sub(r"\s+", "", str(record.get("rmrkCn") or ""))

    # '외국인 대상' 지정은 대상자가 한정되므로, 市 전체·아파트 문구가 있어도
    # 서울의 모든 아파트에 적용되는 허가구역으로 표시하면 안 된다.
    return (
        "市전체" in area
        and "외국인" not in designation_type
        and ("아파트" in designation_type or "아파트" in remark)
    )


def build_citywide_apartment_designation_basis(record):
    """판정을 통과한 원본 행에서 화면 표시용 근거만 추린다."""
    if not is_citywide_apartment_designation(record):
        return None
    return {
        output_name: "" if record.get(source_name) is None else str(record.get(source_name))
        for source_name, output_name in LAND_FIELDS
        if output_name != "crtr_ymd"
    }


# ============================================================================
# 1. 토지거래허가구역 수집 및 화면 표시 원본 저장
# ============================================================================

print("===== 1. 토지거래허가구역 수집 =====")
RAW_REGULATION_DIR.mkdir(parents=True, exist_ok=True)
REDEVELOP_DIR.mkdir(parents=True, exist_ok=True)
records = load_land_records()
as_of_values = [parse_as_of(record["crtrYmd"]) for record in records]
as_of = max(as_of_values)
seoul_apartment_permit_zone_basis = [
    basis
    for record in records
    if (basis := build_citywide_apartment_designation_basis(record)) is not None
]
seoul_apartment_permit_zone = bool(seoul_apartment_permit_zone_basis)

with LAND_RESULT_PATH.open("w", encoding="utf-8", newline="") as output_file:
    writer = csv.DictWriter(
        output_file,
        fieldnames=[output_name for _, output_name in LAND_FIELDS],
        delimiter="\t",
        lineterminator="\n",
    )
    writer.writeheader()
    for record in records:
        writer.writerow({
            output_name: "" if record.get(source_name) is None else record.get(source_name)
            for source_name, output_name in LAND_FIELDS
        })

summary = {
    "as_of": as_of,
    "seoul_apartment_permit_zone": seoul_apartment_permit_zone,
    "seoul_apartment_permit_zone_basis": seoul_apartment_permit_zone_basis,
    "source": "서울부동산정보광장 토지거래허가구역 지정현황",
    "source_url": LAND_REFERER,
    "designations": records,
}
SUMMARY_RESULT_PATH.write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
print(f"  지정 건수: {len(records):,}건")
print(f"  기준일: {as_of}")
print(f"  seoul_apartment_permit_zone: {seoul_apartment_permit_zone}")
print(f"  seoul_apartment_permit_zone 근거: {len(seoul_apartment_permit_zone_basis):,}건")


# ============================================================================
# 2. 정비사업 최신 분기 원본 수집
# ============================================================================

print("\n===== 2. 정비사업 최신 분기 원본 수집 =====")
links = load_redevelop_links()
selected = select_latest_redevelop(links)
redevelop_path = load_redevelop_xlsx(selected)
try:
    redevelop_row_count = len(pd.read_excel(redevelop_path, header=None))
except Exception as error:
    raise SystemExit(f"정비사업 xlsx를 읽을 수 없음: {redevelop_path}: {error}")

print(f"  선택 파일명: {selected['title']}")
print(f"  선택 seq: {selected['seq']}")
print(f"  저장 파일: {redevelop_path.name}")
print(f"  정비사업 원본 행: {redevelop_row_count:,}건")


# ============================================================================
# 3. 자체 검증
# ============================================================================

print("\n===== 3. 자체 검증 =====")
checks = [
    ("토지거래허가구역 응답 1건 이상", len(records) >= 1, f"{len(records):,}건"),
    ("crtr_ymd 비어있지 않음", all(str(record.get("crtrYmd") or "").strip() for record in records),
     f"기준일 {as_of}"),
    ("허가구역 true이면 근거 1건 이상",
     not seoul_apartment_permit_zone or len(seoul_apartment_permit_zone_basis) >= 1,
     f"값={seoul_apartment_permit_zone}, 근거={len(seoul_apartment_permit_zone_basis):,}건"),
    ("허가구역 boolean과 근거 배열 일치",
     seoul_apartment_permit_zone == bool(seoul_apartment_permit_zone_basis),
     f"값={seoul_apartment_permit_zone}, 근거={len(seoul_apartment_permit_zone_basis):,}건"),
    ("허가구역 근거에 외국인 대상 없음",
     all("외국인" not in basis["dsgn_type_nm"] for basis in seoul_apartment_permit_zone_basis),
     f"근거 {len(seoul_apartment_permit_zone_basis):,}건"),
    ("정비사업 xlsx 다운로드 및 100행 이상", redevelop_path.exists() and redevelop_row_count >= 100,
     f"{redevelop_row_count:,}건"),
    ("선택 seq·파일명이 파일 목록 HTML에 존재",
     any(item["seq"] == selected["seq"] and item["title"] == selected["title"] for item in links),
     f"seq={selected['seq']}, title={selected['title']}"),
]
all_passed = True
for label, passed, detail in checks:
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}: {detail}")

print(f"\n결과: {LAND_RESULT_PATH}")
print(f"결과: {SUMMARY_RESULT_PATH}")
print(f"\n===== 규제 정보 수집 {'통과' if all_passed else '미통과'} =====")
assert all_passed, "자체 검증 실패"
