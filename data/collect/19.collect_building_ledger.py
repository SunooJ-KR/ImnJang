# ============================================================================
# 19.collect_building_ledger.py
# ============================================================================
# Author:      yjkim
# Purpose:     건축물대장 표제부에서 단지별 동 정보(동명칭·층수·높이·세대수)를 받는다
# Description: 지금까지의 검증은 전부 간접이었다. 한국부동산원 등록 정보는 단지의
#              총 동수만 주므로 "동 수가 맞는가"까지만 재고, "맞는 동을 골랐는가"는
#              사람 눈에 맡길 수밖에 없었다.
#
#              건축물대장 표제부는 동(棟) 단위로 동명칭·지상층수·높이·세대수·
#              사용승인일을 준다. 이것을 OSM 건물의 name/levels/height/flats/
#              start_date와 맞대보면 배정된 동이 실제로 그 단지 동인지 직접
#              검증된다. 위성사진이 필요 없어진다.
#
#              엔드포인트 (2026-09 확인):
#                https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo
#              활용신청이 안 돼 있으면 403 SERVICE_KEY_IS_NOT_REGISTERED_ERROR가
#              온다. https://www.data.go.kr/data/15134735/openapi.do 에서 신청
#              (자동승인, 개발계정 일 10,000건).
#
#              호출 단위는 지번(시군구+법정동+본번+부번)이다. bjdongCd(법정동코드)는
#              필수이며 빈 값이면 totalCount 0이 온다. 실거래에는 시군구코드만
#              있으므로 한국부동산원 등록 정보의 필지고유번호(PNU 19자리)에서 뽑는다.
#                PNU = 법정동코드(10) + 필지구분(1) + 본번(4) + 부번(4)
#              한 번 호출하면 그 지번의 모든 동이 온다. 고유 지번 약 8,800개라
#              일 한도에 근접하므로 캐시를 반드시 쓴다.
#
#              표제부에는 아파트 동 말고 부속건축물도 섞여 온다. 주용도는 주차장·
#              주민운동시설까지 '공동주택'으로 오므로 쓸 수 없고, 세대수>0이
#              정확한 필터다. 마곡동 744: 표제부 51건 -> 세대수>0 19건
#              (901~919동, 세대 합계 1,529)로 등록 동수·세대수와 정확히 일치했다.
#
#              사용법:
#                python data/collect/19.collect_building_ledger.py probe   # 1건만
#                python data/collect/19.collect_building_ledger.py         # 전량
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

MASTER_PATH = output_dir / "14.1.geocoded_master.txt"
REGISTRY_PATH = output_dir / "raw" / "reb" / "apt_registry.csv"
CACHE_PATH = output_dir / "raw" / "ledger" / "cache_title_info.json"
RESULT_PATH = output_dir / "19.1.building_ledger.txt"

API_URL = "https://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
ROWS_PER_CALL = 100
SLEEP_SEC = 0.06
CACHE_FLUSH_EVERY = 100
DAILY_LIMIT = 10000      # 개발계정. 넘기면 다음 날 이어서 돌린다

PROBE = len(sys.argv) > 1 and sys.argv[1] == "probe"


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
# 1. 호출 대상 지번 목록
# ============================================================================
# aptSeq는 시군구코드-일련번호라 그대로는 못 쓴다. 실거래의 법정동명+지번을
# 시군구코드(5) + 법정동코드(5) + 본번(4) + 부번(4)로 바꿔야 한다.
# sggCd는 실거래에 있으므로 법정동코드만 별도로 필요하다.

print("===== 1. 호출 대상 =====")
master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})
master = master.dropna(subset=["jibun"]).copy()


def normalize_address(text):
    text = text.fillna("").astype(str).str.strip()
    text = text.str.replace(r"^서울(특별시)?\s*", "", regex=True)
    text = text.str.replace(r"\s+", " ", regex=True)
    return text.str.replace(r"산\s+(?=\d)", "산", regex=True)


# 법정동코드는 등록 정보의 PNU에서 가져온다. 별도 코드표를 받을 필요가 없다
registry = pd.read_csv(REGISTRY_PATH, encoding="utf-8-sig", dtype=str)
registry = registry[registry["주소"].str.startswith("서울", na=False)
                    & (registry["단지종류"] == "1")].copy()
registry["join_key"] = normalize_address(registry["주소"])
registry = registry.drop_duplicates("join_key")
pnu = registry["필지고유번호"]
registry["sigungu_cd"] = pnu.str[:5]
registry["bjdong_cd"] = pnu.str[5:10]
registry["bun"] = pnu.str[11:15]
registry["ji"] = pnu.str[15:19]

master["join_key"] = normalize_address(
    master["gu"].fillna("") + " " + master["umd_name"].fillna("")
    + " " + master["jibun"].fillna(""))
master = master.merge(
    registry[["join_key", "sigungu_cd", "bjdong_cd", "bun", "ji", "단지명_공시가격"]],
    on="join_key", how="left")

matched = master["bjdong_cd"].notna()
print(f"  단지 {len(master)}개 / PNU 확보 {int(matched.sum())} ({100 * matched.mean():.1f}%)")

targets = (master[matched][["sigungu_cd", "bjdong_cd", "bun", "ji", "join_key"]]
           .drop_duplicates(subset=["sigungu_cd", "bjdong_cd", "bun", "ji"])
           .reset_index(drop=True))
print(f"  고유 지번 {len(targets)}개")
if PROBE:
    targets = targets[targets["join_key"].str.contains("마곡동 744")].head(1)
    if targets.empty:
        targets = master[matched].head(1)[["sigungu_cd", "bjdong_cd", "bun", "ji", "join_key"]]
    print(f"  [probe] 1건만 호출: {targets.iloc[0]['join_key']}")


# ============================================================================
# 2. API 호출
# ============================================================================

print("\n===== 2. 건축물대장 표제부 호출 =====")

CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
cache = json.loads(CACHE_PATH.read_text(encoding="utf-8")) if CACHE_PATH.exists() else {}
print(f"  캐시 {len(cache)}건")

n_calls = 0


def save_cache():
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def fetch(sigungu_cd, bjdong_cd, bun, ji):
    """지번 하나의 표제부 전체. 인증·권한 오류는 즉시 중단시킨다."""
    global n_calls
    cache_key = f"{sigungu_cd}|{bjdong_cd}|{bun}|{ji}"
    if cache_key in cache:
        return cache[cache_key]

    params = {"serviceKey": SERVICE_KEY, "sigunguCd": sigungu_cd,
              "bjdongCd": bjdong_cd, "bun": bun, "ji": ji,
              "numOfRows": ROWS_PER_CALL, "pageNo": 1, "_type": "json"}
    try:
        response = requests.get(API_URL, params=params, timeout=20)
    except requests.RequestException as error:
        print(f"  [경고] 요청 실패 {cache_key}: {mask_key(error)[:120]}")
        return None

    if response.status_code in (401, 403):
        save_cache()
        raise SystemExit(
            f"인증/권한 오류 [{response.status_code}] {mask_key(response.text)[:200]}\n"
            "  -> https://www.data.go.kr/data/15134735/openapi.do 에서 활용신청 필요")

    items = []
    if response.status_code == 200:
        try:
            body = response.json().get("response", {}).get("body", {})
            raw_items = (body.get("items") or {}).get("item") or []
            items = raw_items if isinstance(raw_items, list) else [raw_items]
        except ValueError:
            print(f"  [경고] JSON 아님 {cache_key}: {mask_key(response.text)[:120]}")

    cache[cache_key] = items
    n_calls += 1
    if n_calls % CACHE_FLUSH_EVERY == 0:
        save_cache()
    time.sleep(SLEEP_SEC)
    return items


records = []
for i, row in targets.iterrows():
    if n_calls >= DAILY_LIMIT:
        print(f"  [중단] 일 한도 {DAILY_LIMIT}건 도달. 캐시를 두고 내일 이어서 돌린다")
        break
    items = fetch(row["sigungu_cd"], row["bjdong_cd"], row["bun"], row["ji"]) or []
    for item in items:
        records.append({
            "join_key": row["join_key"],
            "sigungu_cd": row["sigungu_cd"], "bjdong_cd": row["bjdong_cd"],
            "bun": row["bun"], "ji": row["ji"],
            "bld_nm": item.get("bldNm"),          # 동명칭
            "dong_nm": item.get("dongNm"),
            "plat_plc": item.get("platPlc"),      # 대지위치(지번주소)
            "new_plat_plc": item.get("newPlatPlc"),
            "grnd_flr_cnt": item.get("grndFlrCnt"),   # 지상층수
            "heit": item.get("heit"),                 # 높이(m)
            "hhld_cnt": item.get("hhldCnt"),          # 세대수
            "use_apr_day": item.get("useAprDay"),     # 사용승인일
            "main_purps_cd_nm": item.get("mainPurpsCdNm"),
            "mgm_bldrgst_pk": item.get("mgmBldrgstPk"),
        })
    if (i + 1) % 200 == 0:
        print(f"  처리 중 [{i + 1}/{len(targets)}]: 신규 호출 {n_calls}건, 누적 동 {len(records)}")

save_cache()
print(f"  호출 {n_calls}건 / 수집 동 {len(records)}개")


# ============================================================================
# 3. 저장
# ============================================================================

if not records:
    raise SystemExit("수집된 동이 없다. 활용신청 상태와 응답 스키마를 먼저 확인할 것")

ledger = pd.DataFrame(records)
for column in ["grnd_flr_cnt", "heit", "hhld_cnt"]:
    ledger[column] = pd.to_numeric(ledger[column], errors="coerce")
ledger["use_apr_year"] = pd.to_numeric(
    ledger["use_apr_day"].astype(str).str[:4], errors="coerce")

print("\n===== 3. 요약 =====")
print(f"  표제부 {len(ledger)}건 / 고유 지번 {ledger['join_key'].nunique()}")
residential = ledger[ledger["hhld_cnt"] > 0]
print(f"  주거동(세대수>0) {len(residential)}건 — 주차장·부속동은 세대수 0으로 걸러진다")
print(f"  지번당 주거동 중앙값 {residential.groupby('join_key').size().median():.0f}")
print(f"  동명칭 있음 {int(ledger['bld_nm'].notna().sum())} / "
      f"높이 있음 {int(ledger['heit'].notna().sum())} / "
      f"층수 있음 {int(ledger['grnd_flr_cnt'].notna().sum())}")
if PROBE:
    print("\n  [probe] 첫 응답 전문:")
    print(ledger.head(3).to_string())

ledger.to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n")
print(f"\n결과: {RESULT_PATH}")
