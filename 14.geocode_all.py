# ============================================================================
# 14.geocode_all.py
# ============================================================================
# Author:      yjkim
# Purpose:     서울 전역 실거래 단지를 카카오 지오코딩해 단지 앵커 좌표를 만든다
# Description: 08은 강남구 391단지 전용이었다(cache_molit_gangnam.json 의존).
#              11에서 서울 25개구를 모두 수집했으므로 같은 로직을 전역으로 넓힌다.
#
#              08과 달라지는 점:
#                - 입력이 강남 캐시 -> 11.1(매매) + 11.2(전월세) 테이블
#                - 구 이름을 상수가 아니라 각 행의 gu 컬럼에서 가져온다
#                - 단지 모집단이 매매 단지 + 전세 단지의 합집합
#                  (plan.md 44행: 예측 대상 거래 유형은 매매 + 전세.
#                   월세만 거래된 단지는 예측 대상이 아니므로 제외한다)
#                - 9천 건 규모라 중간에 끊기면 손실이 크므로 캐시를 주기적으로 저장
#
#              통과 기준: 지오코딩 성공률 >= 95%
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

work_dir = Path(__file__).parent
output_dir = work_dir / "output"

SALE_PATH = output_dir / "11.1.trades_sale.txt"
RENT_PATH = output_dir / "11.2.trades_rent.txt"
GEOCODE_CACHE = output_dir / "cache_geocode.json"
RESULT_PATH = output_dir / "14.1.geocoded_master.txt"

KAKAO_URL = "https://dapi.kakao.com/v2/local/search/address.json"
CACHE_FLUSH_EVERY = 200
ADDRESS_COLS = ["aptSeq", "aptNm", "gu", "umdNm", "jibun",
                "roadNm", "roadNmBonbun", "roadNmBubun", "buildYear"]


def load_env(key):
    for line in (work_dir / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f".env에 {key} 없음")


KAKAO_KEY = load_env("KAKAO_REST_KEY")


# ============================================================================
# 1. 단지 마스터 (매매 + 전월세 합집합)
# ============================================================================

print("===== 1. 단지 마스터 =====")


def load_trades(path, label, extra_cols=()):
    # 본번/부번은 zero-padded 문자열이다. 숫자로 읽으면 원본 자릿수가 사라진다
    df = pd.read_csv(path, sep="\t", dtype=str, usecols=ADDRESS_COLS + list(extra_cols))
    print(f"  {label}: {len(df)}행 / {df['aptSeq'].nunique()}단지")
    return df


sale = load_trades(SALE_PATH, "매매")
rent = load_trades(RENT_PATH, "전월세", ["is_jeonse"])
jeonse = rent[rent["is_jeonse"] == "True"].drop(columns="is_jeonse")
print(f"  전세만: {len(jeonse)}행 / {jeonse['aptSeq'].nunique()}단지")

trades = pd.concat([sale, jeonse], ignore_index=True)
trades["build_year"] = pd.to_numeric(trades["buildYear"], errors="coerce")


def first_mode(series):
    values = series.dropna().astype(str).str.strip()
    values = values[values != ""]
    return values.mode().iloc[0] if len(values) else None


master = (trades
    .groupby("aptSeq")
    .agg(
        apt_name=("aptNm", first_mode),
        gu=("gu", first_mode),
        umd_name=("umdNm", first_mode),
        jibun=("jibun", first_mode),
        road_nm=("roadNm", first_mode),
        road_bon=("roadNmBonbun", first_mode),
        road_bub=("roadNmBubun", first_mode),
        build_year=("build_year", "median"),
        n_deals=("aptNm", "count"),
    )
    .reset_index()
)


def text(value):
    """first_mode가 None을, groupby가 NaN을 남긴다. NaN은 truthy라 조용히 'nan'이 새어든다"""
    return "" if pd.isna(value) else str(value).strip()


def strip_road_number(value):
    """전월세 API의 roadNm은 건물번호까지 포함한다('경교장길 35'). 매매는 도로명만('경교장길').
    두 소스를 섞어 mode를 뽑으면 전월세 쪽이 이겨 '경교장길 35 35'가 만들어진다.
    도로명은 항상 로/길/가로 끝나므로 뒤에 붙은 번호 토큰은 잘라낸다."""
    return re.sub(r"\s*\d+(-\d+)?$", "", text(value))


def road_address(row):
    gu, road, bon = text(row["gu"]), strip_road_number(row["road_nm"]), text(row["road_bon"]).lstrip("0")
    if not (gu and road and bon):
        return None
    bub = text(row["road_bub"]).lstrip("0")
    number = f"{bon}-{bub}" if bub else bon
    return f"서울 {gu} {road} {number}"


def jibun_address(row):
    gu, umd, jibun = text(row["gu"]), text(row["umd_name"]), text(row["jibun"])
    if not (gu and umd and jibun):
        return None
    return f"서울 {gu} {umd} {jibun}"


master["addr_road"] = master.apply(road_address, axis=1)
master["addr_jibun"] = master.apply(jibun_address, axis=1)
print(f"  합집합 단지 {len(master)} / 도로명주소 {master['addr_road'].notna().sum()} "
      f"/ 지번주소 {master['addr_jibun'].notna().sum()}")


# ============================================================================
# 2. 카카오 지오코딩
# ============================================================================
# 도로명주소를 먼저 시도하고 실패하면 지번주소로 재시도한다 (08과 동일).
# 응답 좌표는 x=경도, y=위도 (WGS84).

print("\n===== 2. 카카오 지오코딩 =====")

geocode_cache = {}
if GEOCODE_CACHE.exists():
    geocode_cache = json.loads(GEOCODE_CACHE.read_text(encoding="utf-8"))
    print(f"  캐시 {len(geocode_cache)}건 (08 강남구 포함)")

n_calls = 0


def save_cache():
    GEOCODE_CACHE.write_text(json.dumps(geocode_cache, ensure_ascii=False), encoding="utf-8")


def geocode(address):
    """주소 -> 좌표. 인증 오류는 주소 문제가 아니므로 즉시 중단시킨다."""
    global n_calls
    if address in geocode_cache:
        return geocode_cache[address]
    response = requests.get(
        KAKAO_URL, params={"query": address, "size": 1},
        headers={"Authorization": f"KakaoAK {KAKAO_KEY}"}, timeout=20,
    )
    if response.status_code in (401, 403):
        save_cache()
        raise SystemExit(f"카카오 인증 오류 [{response.status_code}] {response.text[:200]}")
    if response.status_code != 200:
        result = None
    else:
        documents = response.json().get("documents", [])
        result = ({"lon": float(documents[0]["x"]), "lat": float(documents[0]["y"])}
                  if documents else None)
    geocode_cache[address] = result
    n_calls += 1
    # 9천 건 중간에 끊기면 이미 쓴 호출이 통째로 날아간다
    if n_calls % CACHE_FLUSH_EVERY == 0:
        save_cache()
    time.sleep(0.05)
    return result


coordinates = []
for i, row in master.iterrows():
    result = geocode(row["addr_road"]) if row["addr_road"] else None
    source = "road"
    if result is None and row["addr_jibun"]:
        result = geocode(row["addr_jibun"])
        source = "jibun"
    coordinates.append({
        "aptSeq": row["aptSeq"],
        "lon": result["lon"] if result else None,
        "lat": result["lat"] if result else None,
        "geocode_source": source if result else None,
    })
    if (i + 1) % 500 == 0:
        print(f"  처리 중 [{i + 1}/{len(master)}]: 신규 호출 {n_calls}건")

save_cache()

master = master.merge(pd.DataFrame(coordinates), on="aptSeq", how="left")
geocode_rate = 100 * master["lat"].notna().mean()
print(f"  성공: {int(master['lat'].notna().sum())}/{len(master)} ({geocode_rate:.1f}%)")
print(f"  경로별: {master['geocode_source'].value_counts().to_dict()}")


# ============================================================================
# 3. 판정 및 저장
# ============================================================================

print("\n===== 3. 판정 =====")
passed = geocode_rate >= 95.0
print(f"  [{'PASS' if passed else 'FAIL'}] 지오코딩 성공률 >= 95%    실측 {geocode_rate:.1f}%")

failed = master[master["lat"].isna()]
if len(failed):
    print(f"\n  실패 {len(failed)}건 구별 분포: {failed['gu'].value_counts().head(5).to_dict()}")
    print(f"  실패 예시: {failed['addr_road'].dropna().head(3).tolist()}")

gu_rate = (master.assign(ok=master["lat"].notna())
    .groupby("gu")["ok"].agg(n="count", rate=lambda s: 100 * s.mean())
    .sort_values("rate").head(5))
print(f"\n  성공률 하위 5개 구:\n{gu_rate}")

master.to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n")
print(f"\n결과 테이블: {RESULT_PATH}")
print(f"\n===== 지오코딩 {'통과' if passed else '미통과'} =====")
