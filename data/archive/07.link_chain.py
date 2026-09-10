# ============================================================================
# 07.link_chain.py
# ============================================================================
# Author:      yjkim
# Purpose:     실거래(aptSeq) <-> OSM 단지 <-> 동 폴리곤 사슬을 실제로 연결한다
# Description: D1 최종 검증. 03~04에서 기하만으로는 단지 복원이 0.854에 그쳤고,
#              외부 단지 목록을 앵커로 쓰면 '발견'이 '매칭'으로 바뀌어 쉬워진다고
#              결론냈다. 06에서 aptSeq를 확보했으므로 이제 실제로 붙여본다.
#
#              연결 경로:
#                실거래 aptSeq 마스터 (단지명 + 법정동 + 지번 + 건축년도)
#                        |  단지명 정규화 매칭 + 건축년도 교차검증
#                OSM landuse 단지 폴리곤
#                        |  공간 포함
#                OSM 아파트 동 폴리곤 (05 horizon 엔진 입력)
#
#              통과 기준:
#                (1) 실거래 단지 중 OSM 단지에 매칭되는 비율 >= 60%
#                (2) 매칭된 것 중 건축년도 +-2년 일치 비율 >= 90% (오매칭 검출)
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
import re
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import requests
from shapely.geometry import Polygon

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"
output_dir.mkdir(exist_ok=True)

CACHE_PATH = output_dir / "cache_overpass_gangnam.json"
TRADE_CACHE = output_dir / "cache_molit_gangnam.json"
METRIC_CRS = "EPSG:5179"

GANGNAM_LAWD_CD = "11680"
TRADE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
# 최근 12개월. 단지 마스터를 만드는 게 목적이므로 1년이면 충분하다.
TARGET_MONTHS = [f"2025{m:02d}" for m in range(9, 13)] + [f"2026{m:02d}" for m in range(1, 9)]


def load_key():
    for line in (work_dir / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("DATA_GO_KR_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(".env에 DATA_GO_KR_KEY 없음")


SERVICE_KEY = load_key()


# ============================================================================
# 1. 실거래 수집 -> aptSeq 단지 마스터
# ============================================================================

def fetch_month(deal_ymd):
    params = {
        "serviceKey": SERVICE_KEY, "LAWD_CD": GANGNAM_LAWD_CD, "DEAL_YMD": deal_ymd,
        "pageNo": "1", "numOfRows": "1000",
    }
    response = requests.get(TRADE_URL, params=params, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    code_node = root.find(".//resultCode")
    if code_node is not None and (code_node.text or "").strip("0") != "":
        raise RuntimeError(f"{deal_ymd} API 오류: {code_node.text}")
    return [{child.tag: (child.text or "").strip() for child in item}
            for item in root.findall(".//item")]


print("===== 1. 실거래 수집 (강남구 12개월) =====")
if TRADE_CACHE.exists():
    trades = json.loads(TRADE_CACHE.read_text(encoding="utf-8"))
    print(f"  캐시 사용: {len(trades)}건")
else:
    trades = []
    for i, ymd in enumerate(TARGET_MONTHS):
        rows = fetch_month(ymd)
        trades.extend(rows)
        print(f"  [{i + 1}/{len(TARGET_MONTHS)}] {ymd}: {len(rows)}건")
        time.sleep(0.3)
    TRADE_CACHE.write_text(json.dumps(trades, ensure_ascii=False), encoding="utf-8")
    print(f"  총 {len(trades)}건 수집")

trade_df = pd.DataFrame(trades)
trade_df["buildYear_num"] = pd.to_numeric(trade_df["buildYear"], errors="coerce")

# 해제 거래는 마스터에서도 제외한다 (plan.md §6.5 정제 규칙)
cancelled = trade_df["cdealType"].astype(str).str.strip() == "O"
print(f"  해제 거래 제외: {int(cancelled.sum())}건")

trade_master = (trade_df[~cancelled]
    .groupby("aptSeq")
    .agg(
        apt_name=("aptNm", lambda s: s.mode().iloc[0]),
        umd_name=("umdNm", lambda s: s.mode().iloc[0]),
        jibun=("jibun", lambda s: s.mode().iloc[0]),
        build_year=("buildYear_num", "median"),
        n_trades=("aptNm", "count"),
    )
    .reset_index()
)
print(f"  단지 마스터: {len(trade_master)}개 단지")


# ============================================================================
# 2. OSM 단지/동 로드
# ============================================================================

print("\n===== 2. OSM 로드 =====")
elements = json.loads(CACHE_PATH.read_text(encoding="utf-8"))["elements"]


def to_gdf(elements, want_building):
    records = []
    for element in elements:
        tags = element.get("tags", {})
        if ("building" in tags) != want_building:
            continue
        geometry = element.get("geometry")
        if not geometry or len(geometry) < 4:
            continue
        records.append({
            "osm_id": element["id"],
            "building_type": tags.get("building"),
            "name": tags.get("name"),
            "start_date": tags.get("start_date"),
            "geometry": Polygon([(p["lon"], p["lat"]) for p in geometry]),
        })
    return gpd.GeoDataFrame(records, crs="EPSG:4326").to_crs(METRIC_CRS)


osm_complexes = to_gdf(elements, want_building=False)
osm_buildings = to_gdf(elements, want_building=True)
osm_apartments = osm_buildings[osm_buildings["building_type"] == "apartments"].reset_index(drop=True)
print(f"  OSM 단지 {len(osm_complexes)}, 아파트 동 {len(osm_apartments)}")

# 단지별 준공년도: 소속 동들의 최빈값
osm_complexes["complex_area_m2"] = osm_complexes.geometry.area
apt_centroids = osm_apartments[["osm_id", "start_date"]].copy()
apt_centroids["geometry"] = osm_apartments.geometry.centroid
apt_centroids = gpd.GeoDataFrame(apt_centroids, crs=METRIC_CRS)

contained = gpd.sjoin(
    apt_centroids,
    osm_complexes[["osm_id", "complex_area_m2", "geometry"]].rename(columns={"osm_id": "complex_id"}),
    how="inner", predicate="within",
).sort_values("complex_area_m2").drop_duplicates(subset="osm_id")

contained["year"] = pd.to_numeric(
    contained["start_date"].astype("string").str.extract(r"(\d{4})", expand=False), errors="coerce")
complex_year = contained.groupby("complex_id")["year"].median().rename("osm_build_year")
complex_ndong = contained.groupby("complex_id").size().rename("osm_n_dong")
osm_complexes = (osm_complexes
    .set_index("osm_id")
    .join(complex_year).join(complex_ndong)
    .reset_index()
)


# ============================================================================
# 3. 단지명 정규화 및 매칭
# ============================================================================
# 실거래 aptNm과 OSM name은 표기가 다르다.
#   "한신(개포)" vs "개포한신아파트", "래미안서초스위트아파트" vs "래미안 서초 스위트"
# 공백/괄호/접미사를 제거하고 문자 집합으로 비교한다.

print("\n===== 3. 단지명 정규화 매칭 =====")

SUFFIXES = ["아파트", "APT", "apt"]


def normalize_name(name):
    if not isinstance(name, str):
        return ""
    text = re.sub(r"[\(\)（）\[\]{}]", "", name)
    text = re.sub(r"\s+", "", text)
    for suffix in SUFFIXES:
        text = text.replace(suffix, "")
    text = re.sub(r"[^가-힣A-Za-z0-9]", "", text)
    return text.lower()


trade_master["name_key"] = trade_master["apt_name"].map(normalize_name)
osm_complexes["name_key"] = osm_complexes["name"].map(normalize_name)

osm_lookup = osm_complexes[osm_complexes["name_key"] != ""].copy()

# 1차: 정규화 이름 완전 일치
exact = trade_master.merge(
    osm_lookup[["osm_id", "name", "name_key", "osm_build_year", "osm_n_dong"]],
    on="name_key", how="left", suffixes=("", "_osm"),
)
exact = exact.sort_values("osm_n_dong", ascending=False).drop_duplicates("aptSeq")
n_exact = int(exact["osm_id"].notna().sum())
print(f"  1차 완전일치: {n_exact}/{len(trade_master)} ({100 * n_exact / len(trade_master):.1f}%)")

# 2차: 부분 포함 (한쪽이 다른 쪽을 포함). 짧은 이름의 오매칭을 막기 위해 4자 이상만.
unmatched = exact[exact["osm_id"].isna()].copy()
osm_keys = osm_lookup[["osm_id", "name", "name_key", "osm_build_year", "osm_n_dong"]].to_dict("records")

partial_rows = []
for _, row in unmatched.iterrows():
    key = row["name_key"]
    if len(key) < 4:
        continue
    hits = [c for c in osm_keys
            if len(c["name_key"]) >= 4 and (key in c["name_key"] or c["name_key"] in key)]
    if len(hits) == 1:  # 유일할 때만 채택. 후보가 여럿이면 사람이 봐야 한다
        partial_rows.append({"aptSeq": row["aptSeq"], **{
            "osm_id": hits[0]["osm_id"], "name_osm": hits[0]["name"],
            "osm_build_year": hits[0]["osm_build_year"], "osm_n_dong": hits[0]["osm_n_dong"]}})

partial_df = pd.DataFrame(partial_rows)
print(f"  2차 부분일치(유일): {len(partial_df)}개 추가")

matched = exact.copy()
if len(partial_df) > 0:
    matched = matched.set_index("aptSeq")
    partial_indexed = partial_df.set_index("aptSeq")
    for col in ["osm_id", "osm_build_year", "osm_n_dong"]:
        matched.loc[partial_indexed.index, col] = partial_indexed[col]
    matched = matched.reset_index()

n_matched = int(matched["osm_id"].notna().sum())
match_rate = 100 * n_matched / len(trade_master)
print(f"  최종 매칭: {n_matched}/{len(trade_master)} ({match_rate:.1f}%)")


# ============================================================================
# 4. 건축년도 교차검증 (오매칭 검출)
# ============================================================================

print("\n===== 4. 건축년도 교차검증 =====")

verified = matched[matched["osm_id"].notna() & matched["osm_build_year"].notna()].copy()
verified["year_diff"] = (verified["build_year"] - verified["osm_build_year"]).abs()
within_2y = verified["year_diff"] <= 2
consistency = 100 * within_2y.mean() if len(verified) > 0 else 0.0

print(f"  년도 대조 가능: {len(verified)}건")
print(f"  +-2년 일치: {int(within_2y.sum())}/{len(verified)} ({consistency:.1f}%)")
if len(verified) > 0:
    print(f"  년도 차이 중앙값: {verified['year_diff'].median():.0f}년")
    mismatches = verified[~within_2y].nlargest(5, "year_diff")
    if len(mismatches) > 0:
        print("\n  오매칭 의심 상위:")
        print(mismatches[["apt_name", "build_year", "osm_build_year", "year_diff"]]
              .to_string(index=False))


# ============================================================================
# 5. 사슬 완성도 - 매칭된 단지가 실제로 동 폴리곤을 갖는가
# ============================================================================

print("\n===== 5. 사슬 완성도 =====")

linked = matched[matched["osm_id"].notna()].copy()
linked_with_dong = linked[linked["osm_n_dong"].notna() & (linked["osm_n_dong"] > 0)]
print(f"  실거래 단지 {len(trade_master)}개 중")
print(f"    OSM 단지 매칭:        {n_matched} ({match_rate:.1f}%)")
print(f"    동 폴리곤까지 연결:   {len(linked_with_dong)} "
      f"({100 * len(linked_with_dong) / len(trade_master):.1f}%)")
print(f"    연결된 동 총합:       {int(linked_with_dong['osm_n_dong'].sum())}동")

# 거래량 가중: 거래가 많은 단지가 연결되는 게 실사용에서 더 중요하다
trade_weighted = 100 * linked_with_dong["n_trades"].sum() / trade_master["n_trades"].sum()
print(f"    거래량 가중 커버리지: {trade_weighted:.1f}%")


# ============================================================================
# 6. 판정 및 저장
# ============================================================================

print("\n===== 6. 판정 =====")
checks = [
    ("실거래 -> OSM 단지 매칭률 >= 60%", match_rate, 60.0),
    ("건축년도 +-2년 일치율 >= 90%", consistency, 90.0),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:32s} 실측 {value:.1f}%")

master_path = output_dir / "07.1.trade_master_linked.txt"
unmatched_path = output_dir / "07.2.unmatched_complexes.txt"
matched.to_csv(master_path, sep="\t", index=False, lineterminator="\n")
(matched[matched["osm_id"].isna()]
    .sort_values("n_trades", ascending=False)
    [["aptSeq", "apt_name", "umd_name", "jibun", "build_year", "n_trades"]]
    .to_csv(unmatched_path, sep="\t", index=False, lineterminator="\n")
)
print(f"\n연결 결과: {master_path}")
print(f"미매칭 단지: {unmatched_path}")
print(f"\n===== 사슬 연결 {'통과' if all_passed else '미통과'} =====")
