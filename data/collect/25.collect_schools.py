# ============================================================================
# 25.collect_schools.py
# ============================================================================
# Author:      yjkim
# Purpose:     NEIS 교육정보 개방포털에서 서울 전체 학교의 학교급(초/중/고)을 받는다
# Description: OSM의 'school' 태그만으로는 초/중/고를 가를 수 없다. NEIS
#              schoolInfo API(ATPT_OFCDC_SC_CODE=B10 서울)는 학교급을 준다.
#
#              [중요] 최초 지시서는 "인증키 없이 1,415건 전부 온다"였으나 실측
#              결과 틀렸다. 인증키(KEY) 없이 호출하면 head의 list_total_count는
#              정확한 1,415를 보여주지만, 실제 row는 pIndex/pSize를 완전히 무시
#              하고 항상 동일한 5건(샘플 모드)만 온다 — pIndex=1과 pIndex=283이
#              완전히 같은 5건을 반환하는 것으로 직접 확인했다. NEIS 학교기본정보
#              API 명세서에도 "샘플 키 사용 시 pIndex 고정 1, pSize 5 제한"이라
#              명시돼 있다.
#              (https://open.neis.go.kr/portal/data/service/selectServicePage.do
#               ?infId=OPEN17020190531110010104913&infSeq=2)
#              전량을 받으려면 KEY가 필요하다. 발급: portal.neis.go.kr 로그인
#              (구글/네이버/다음 소셜 가능) 후 활용가이드 > 인증키 신청 —
#              신청 즉시 발급, 별도 회원가입 불필요.
#
#              그래서 이 스크립트는 .env의 NEIS_KEY를 읽는다(DATA_GO_KR_KEY,
#              KAKAO_REST_KEY와는 별개 키). NEIS_KEY가 없으면 즉시 중단하고
#              발급 방법을 안내한다.
#
#              좌표(LAT/LON)는 API 응답에 없다. 14.geocode_all.py의 geocode()
#              캐시 방식(output/cache_geocode.json)을 그대로 재사용한다 —
#              이미 10,000건 넘게 쌓여 있어 일부는 바로 맞고, 학교 주소로 새로
#              부르는 호출도 같은 캐시에 쌓여 다음에 이득이 된다.
#
#              총 1,415건이면 pSize=1000 기준 페이지 2회면 끝나 raw 응답을
#              디스크에 따로 캐시할 실익이 없다(11/19와 달리 중단-재개 대비가
#              불필요한 규모). 재호출 비용이 커지면 그때 추가한다.
#
#              사용법: python data/collect/25.collect_schools.py
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
import time
from pathlib import Path

import pandas as pd
import requests

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

NEIS_URL = "https://open.neis.go.kr/hub/schoolInfo"
SEOUL_OFFICE_CODE = "B10"
PAGE_SIZE = 1000
NEIS_SLEEP_SEC = 0.1

KAKAO_URL = "https://dapi.kakao.com/v2/local/search/address.json"
GEOCODE_CACHE = output_dir / "cache_geocode.json"
GEOCODE_FLUSH_EVERY = 200
GEOCODE_SLEEP_SEC = 0.05

RESULT_PATH = output_dir / "25.1.schools.txt"


def load_env(key):
    for line in (work_dir / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return None


NEIS_KEY = load_env("NEIS_KEY")
if not NEIS_KEY:
    raise SystemExit(
        ".env에 NEIS_KEY 없음. 인증키 없이는 pIndex/pSize가 무시되고 5건 샘플만 온다.\n"
        "  발급: https://portal.neis.go.kr 로그인(구글/네이버/다음 소셜) 후\n"
        "  활용가이드 > 인증키 신청 (즉시 발급). .env에 NEIS_KEY=발급값 추가할 것"
    )

KAKAO_KEY = load_env("KAKAO_REST_KEY")
if not KAKAO_KEY:
    raise SystemExit(".env에 KAKAO_REST_KEY 없음")


def mask_key(text):
    """requests의 예외/응답 메시지에는 요청 URL이 통째로 들어간다. 이 저장소에서
    실제로 키 유출 사고가 있었다(11.collect_trades.py 참고) — NEIS_KEY와
    KAKAO_KEY 둘 다 가린다."""
    text = str(text)
    for key in (NEIS_KEY, KAKAO_KEY):
        if key:
            text = text.replace(key, "***")
    return text


# ============================================================================
# 1. NEIS 학교기본정보 수집 (페이지네이션)
# ============================================================================

print("===== 1. NEIS 학교기본정보 =====")


def fetch_page(page_index):
    """1페이지 요청. 반환: (rows, list_total_count)."""
    params = {
        "KEY": NEIS_KEY, "Type": "json",
        "pIndex": page_index, "pSize": PAGE_SIZE,
        "ATPT_OFCDC_SC_CODE": SEOUL_OFFICE_CODE,
    }
    try:
        response = requests.get(NEIS_URL, params=params, timeout=20)
    except requests.RequestException as error:
        raise SystemExit(f"NEIS 요청 실패: {mask_key(error)}")
    if response.status_code != 200:
        raise SystemExit(f"NEIS HTTP {response.status_code}: {mask_key(response.text)[:200]}")

    body = response.json()
    info = body.get("schoolInfo")
    if info is None:
        # 인증키 오류 등은 schoolInfo 없이 최상위 RESULT로 온다
        result = body.get("RESULT", {})
        raise SystemExit(f"NEIS 오류 [{result.get('CODE')}] {mask_key(result.get('MESSAGE'))}")

    head = info[0]["head"]
    total_count = head[0]["list_total_count"]
    code = head[1]["RESULT"]["CODE"]
    message = head[1]["RESULT"]["MESSAGE"]

    if code == "INFO-200":   # 더 가져올 데이터 없음(정상)
        return [], total_count
    if code != "INFO-000":
        raise SystemExit(f"NEIS 오류 [{code}] {mask_key(message)}")

    return info[1]["row"], total_count


all_rows = []
page = 1
total_count = None
while True:
    rows, total_count = fetch_page(page)
    if not rows:
        break
    all_rows.extend(rows)
    print(f"  처리 중 [{len(all_rows)}/{total_count}]")
    if len(all_rows) >= total_count:
        break
    page += 1
    time.sleep(NEIS_SLEEP_SEC)

assert len(all_rows) == total_count, (
    f"수집 {len(all_rows)}건이 list_total_count {total_count}와 다르다 — 페이지네이션 확인 필요")
print(f"  수집 {len(all_rows)}건 (list_total_count {total_count}와 일치)")

schools = pd.DataFrame(all_rows)
print(f"  학교급 분포: {schools['SCHUL_KND_SC_NM'].value_counts().to_dict()}")


# ============================================================================
# 2. 카카오 지오코딩 (14.geocode_all.py의 geocode()/캐시 그대로 재사용)
# ============================================================================

print("\n===== 2. 카카오 지오코딩 =====")

geocode_cache = {}
if GEOCODE_CACHE.exists():
    geocode_cache = json.loads(GEOCODE_CACHE.read_text(encoding="utf-8"))
    print(f"  캐시 {len(geocode_cache)}건 (14 서울 전역 지오코딩 포함)")

n_calls = 0


def save_cache():
    GEOCODE_CACHE.write_text(json.dumps(geocode_cache, ensure_ascii=False), encoding="utf-8")


def geocode(address):
    """주소 -> 좌표. 14.geocode_all.py와 동일한 함수(인증 오류는 즉시 중단)."""
    global n_calls
    if address in geocode_cache:
        return geocode_cache[address]
    response = requests.get(
        KAKAO_URL, params={"query": address, "size": 1},
        headers={"Authorization": f"KakaoAK {KAKAO_KEY}"}, timeout=20,
    )
    if response.status_code in (401, 403):
        save_cache()
        raise SystemExit(f"카카오 인증 오류 [{response.status_code}] {mask_key(response.text)[:200]}")
    if response.status_code != 200:
        result = None
    else:
        documents = response.json().get("documents", [])
        result = ({"lon": float(documents[0]["x"]), "lat": float(documents[0]["y"])}
                  if documents else None)
    geocode_cache[address] = result
    n_calls += 1
    if n_calls % GEOCODE_FLUSH_EVERY == 0:
        save_cache()
    time.sleep(GEOCODE_SLEEP_SEC)
    return result


coordinates = []
for i, row in schools.iterrows():
    address = (row["ORG_RDNMA"] or "").strip() if pd.notna(row["ORG_RDNMA"]) else ""
    result = geocode(address) if address else None
    coordinates.append({"lat": result["lat"] if result else None,
                        "lon": result["lon"] if result else None})
    if (i + 1) % 300 == 0:
        print(f"  처리 중 [{i + 1}/{len(schools)}]: 신규 호출 {n_calls}건")

save_cache()
schools = pd.concat([schools.reset_index(drop=True), pd.DataFrame(coordinates)], axis=1)

geocode_rate = 100 * schools["lat"].notna().mean()
print(f"  지오코딩 성공 {int(schools['lat'].notna().sum())}/{len(schools)} ({geocode_rate:.1f}%)")

failed = schools[schools["lat"].isna()]
if len(failed):
    print(f"  실패 {len(failed)}건 학교급 분포: {failed['SCHUL_KND_SC_NM'].value_counts().to_dict()}")
    print(f"  실패 예시(도로명주소 결측 또는 지오코딩 미매칭): "
          f"{failed[['SCHUL_NM', 'ORG_RDNMA']].head(5).to_dict('records')}")


# ============================================================================
# 3. 저장
# ============================================================================

print("\n===== 3. 저장 =====")

result = schools.rename(columns={
    "SCHUL_NM": "학교명", "SCHUL_KND_SC_NM": "학교급", "ORG_RDNMA": "도로명주소",
    "FOND_SC_NM": "설립구분", "HS_SC_NM": "고교계열",
})[["학교명", "학교급", "도로명주소", "lat", "lon", "설립구분", "고교계열"]]

# --- 자체 검증 ---
assert len(result) == total_count, "저장 직전 행 수가 수집 건수와 다르다 — 중간에 행이 늘거나 줄었다"
assert list(result.columns) == ["학교명", "학교급", "도로명주소", "lat", "lon", "설립구분", "고교계열"], \
    "출력 컬럼 스펙과 다르다"
# 지오코딩 실패를 0으로 채우면 위경도 0,0(기니만)이 서울로 둔갑한다 — 결측 유지 확인
assert not ((result["lat"] == 0) | (result["lon"] == 0)).any(), "실패를 0으로 채운 흔적이 있다"

result.to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n")
print(f"  학교급별 건수: {result['학교급'].value_counts().to_dict()}")
print(f"  지오코딩 성공률: {geocode_rate:.1f}% (실패 {int(result['lat'].isna().sum())}건은 결측으로 유지)")
print(f"\n결과: {RESULT_PATH}")
