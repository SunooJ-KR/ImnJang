# ============================================================================
# 08.geocode_match.py
# ============================================================================
# Author:      yjkim
# Purpose:     실거래 주소를 지오코딩해 좌표로 OSM 동 클러스터와 매칭한다
# Description: 07에서 문자열 매칭(단지명 32.5%, 도로명 38.9%)이 목표 60%에
#              못 미쳤다. 원인은 OSM의 addr 태그 커버리지(단지 44%, 동 15%)이며
#              실거래 쪽은 도로명주소를 100% 갖고 있다.
#
#              따라서 문자열 매칭을 좌표 매칭으로 대체한다:
#                실거래 도로명/지번 주소 --카카오 지오코딩--> 좌표
#                        |  공간 포함 또는 최근접
#                OSM 아파트 동 buffer 클러스터 (동 100% 커버, 03/04)
#
#              통과 기준:
#                (1) 지오코딩 성공률 >= 95%
#                (2) 실거래 단지 -> 동 클러스터 매칭률 >= 60%
#                (3) 건축년도 +-2년 일치율 >= 90% (오매칭 검출)
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import requests
from shapely.geometry import Polygon, Point
from shapely.ops import unary_union

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"
output_dir.mkdir(exist_ok=True)

OSM_CACHE = output_dir / "cache_overpass_gangnam.json"
TRADE_CACHE = output_dir / "cache_molit_gangnam.json"
GEOCODE_CACHE = output_dir / "cache_geocode.json"

METRIC_CRS = "EPSG:5179"
SGG_NAME = "서울 강남구"
BUFFER_M = 25            # 04에서 completeness 0.968
MAX_SNAP_M = 150         # 이보다 먼 클러스터는 같은 단지로 보지 않는다

KAKAO_URL = "https://dapi.kakao.com/v2/local/search/address.json"


def load_env(key):
    for line in (work_dir / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f".env에 {key} 없음")


KAKAO_KEY = load_env("KAKAO_REST_KEY")


# ============================================================================
# 1. 실거래 단지 마스터 + 주소 조립
# ============================================================================

print("===== 1. 실거래 단지 마스터 =====")
trades = json.loads(TRADE_CACHE.read_text(encoding="utf-8"))
trade_df = pd.DataFrame(trades)
trade_df = trade_df[trade_df["cdealType"].astype(str).str.strip() != "O"]
trade_df["build_year"] = pd.to_numeric(trade_df["buildYear"], errors="coerce")


def first_mode(series):
    values = series.dropna().astype(str).str.strip()
    values = values[values != ""]
    return values.mode().iloc[0] if len(values) else None


trade_master = (trade_df
    .groupby("aptSeq")
    .agg(
        apt_name=("aptNm", first_mode),
        umd_name=("umdNm", first_mode),
        jibun=("jibun", first_mode),
        road_nm=("roadNm", first_mode),
        road_bon=("roadNmBonbun", first_mode),
        road_bub=("roadNmBubun", first_mode),
        build_year=("build_year", "median"),
        n_trades=("aptNm", "count"),
    )
    .reset_index()
)


def road_address(row):
    if not row["road_nm"] or not row["road_bon"]:
        return None
    bon = str(row["road_bon"]).lstrip("0")
    bub = str(row["road_bub"]).lstrip("0")
    number = f"{bon}-{bub}" if bub else bon
    return f"{SGG_NAME} {row['road_nm']} {number}"


def jibun_address(row):
    if not row["umd_name"] or not row["jibun"]:
        return None
    return f"{SGG_NAME} {row['umd_name']} {row['jibun']}"


trade_master["addr_road"] = trade_master.apply(road_address, axis=1)
trade_master["addr_jibun"] = trade_master.apply(jibun_address, axis=1)
print(f"  단지 {len(trade_master)} / 도로명주소 {trade_master['addr_road'].notna().sum()} "
      f"/ 지번주소 {trade_master['addr_jibun'].notna().sum()}")


# ============================================================================
# 2. 카카오 지오코딩
# ============================================================================
# 도로명주소를 먼저 시도하고, 실패하면 지번주소로 재시도한다.
# 응답 좌표 x=경도, y=위도 (WGS84).

print("\n===== 2. 카카오 지오코딩 =====")

geocode_cache = {}
if GEOCODE_CACHE.exists():
    geocode_cache = json.loads(GEOCODE_CACHE.read_text(encoding="utf-8"))
    print(f"  캐시 {len(geocode_cache)}건")


def geocode(address):
    """주소 -> 좌표. 인증 오류는 주소 문제가 아니므로 즉시 중단시킨다."""
    if address in geocode_cache:
        return geocode_cache[address]
    response = requests.get(
        KAKAO_URL, params={"query": address, "size": 1},
        headers={"Authorization": f"KakaoAK {KAKAO_KEY}"}, timeout=20,
    )
    if response.status_code in (401, 403):
        raise SystemExit(
            f"카카오 인증 오류 [{response.status_code}] {response.text[:200]}\n"
            "  -> developers.kakao.com > 내 애플리케이션 > 제품 설정 > 카카오맵 활성화 ON 확인"
        )
    if response.status_code != 200:
        result = None
    else:
        documents = response.json().get("documents", [])
        result = ({"lon": float(documents[0]["x"]), "lat": float(documents[0]["y"])}
                  if documents else None)
    geocode_cache[address] = result
    time.sleep(0.05)
    return result


coordinates = []
for i, row in trade_master.iterrows():
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
    if (i + 1) % 100 == 0:
        print(f"  [{i + 1}/{len(trade_master)}]")

GEOCODE_CACHE.write_text(json.dumps(geocode_cache, ensure_ascii=False), encoding="utf-8")

coord_df = pd.DataFrame(coordinates)
trade_master = trade_master.merge(coord_df, on="aptSeq", how="left")
geocode_rate = 100 * trade_master["lat"].notna().mean()
print(f"  성공: {int(trade_master['lat'].notna().sum())}/{len(trade_master)} ({geocode_rate:.1f}%)")
print(f"  경로별: {trade_master['geocode_source'].value_counts().to_dict()}")


# ============================================================================
# 3. OSM 아파트 동 클러스터 생성
# ============================================================================

print("\n===== 3. OSM 동 클러스터 =====")
elements = json.loads(OSM_CACHE.read_text(encoding="utf-8"))["elements"]

records = []
for element in elements:
    tags = element.get("tags", {})
    if tags.get("building") != "apartments":
        continue
    geometry = element.get("geometry")
    if not geometry or len(geometry) < 4:
        continue
    records.append({
        "osm_id": element["id"],
        "start_date": tags.get("start_date"),
        "levels": tags.get("building:levels"),
        "height": tags.get("height"),
        "geometry": Polygon([(p["lon"], p["lat"]) for p in geometry]),
    })
apartments = gpd.GeoDataFrame(records, crs="EPSG:4326").to_crs(METRIC_CRS)
apartments["year"] = pd.to_numeric(
    apartments["start_date"].astype("string").str.extract(r"(\d{4})", expand=False), errors="coerce")

merged = unary_union(apartments.geometry.buffer(BUFFER_M))
geoms = [merged] if merged.geom_type == "Polygon" else list(merged.geoms)
clusters = gpd.GeoDataFrame(
    {"cluster_id": range(len(geoms))},
    geometry=gpd.GeoSeries(geoms, crs=METRIC_CRS), crs=METRIC_CRS)

apt_points = apartments[["osm_id", "year"]].copy()
apt_points["geometry"] = apartments.geometry.centroid
apt_points = gpd.GeoDataFrame(apt_points, crs=METRIC_CRS)
apt_in_cluster = (gpd.sjoin(apt_points, clusters, how="left", predicate="within")
                  .drop_duplicates("osm_id"))

cluster_info = (apt_in_cluster
    .groupby("cluster_id")
    .agg(n_dong=("osm_id", "count"), cluster_year=("year", "median"))
    .reset_index()
)
clusters = clusters.merge(cluster_info, on="cluster_id", how="left")
print(f"  아파트 동 {len(apartments)} -> 클러스터 {len(clusters)} "
      f"(동 2개 이상 {int((clusters['n_dong'] >= 2).sum())})")


# ============================================================================
# 4. 좌표 -> 클러스터 공간 매칭
# ============================================================================

print("\n===== 4. 좌표 매칭 =====")

located = trade_master[trade_master["lat"].notna()].copy()
points = gpd.GeoDataFrame(
    located[["aptSeq", "build_year", "n_trades", "apt_name"]],
    geometry=gpd.points_from_xy(located["lon"], located["lat"]),
    crs="EPSG:4326",
).to_crs(METRIC_CRS)

# 1차: 클러스터 내부에 포함
inside = gpd.sjoin(points, clusters, how="left", predicate="within").drop_duplicates("aptSeq")
n_inside = int(inside["cluster_id"].notna().sum())
print(f"  클러스터 내부 포함: {n_inside}/{len(points)} ({100 * n_inside / len(points):.1f}%)")

# 2차: 밖에 떨어진 것은 MAX_SNAP_M 이내 최근접으로 스냅
outside = points[points["aptSeq"].isin(inside[inside["cluster_id"].isna()]["aptSeq"])]
snapped = pd.DataFrame()
if len(outside) > 0:
    nearest = gpd.sjoin_nearest(
        outside, clusters, how="left", distance_col="snap_m").drop_duplicates("aptSeq")
    snapped = nearest[nearest["snap_m"] <= MAX_SNAP_M]
    print(f"  {MAX_SNAP_M}m 이내 스냅: {len(snapped)}개 추가 "
          f"(밖에 있던 {len(outside)}개 중)")

matched = inside[inside["cluster_id"].notna()][["aptSeq", "cluster_id"]].copy()
if len(snapped) > 0:
    matched = pd.concat([matched, snapped[["aptSeq", "cluster_id"]]], ignore_index=True)

trade_master = trade_master.merge(matched, on="aptSeq", how="left")
trade_master = trade_master.merge(
    clusters[["cluster_id", "n_dong", "cluster_year"]], on="cluster_id", how="left")

match_rate = 100 * trade_master["cluster_id"].notna().mean()
weighted_rate = (100 * trade_master[trade_master["cluster_id"].notna()]["n_trades"].sum()
                 / trade_master["n_trades"].sum())
print(f"\n  최종 매칭: {int(trade_master['cluster_id'].notna().sum())}/{len(trade_master)} "
      f"({match_rate:.1f}%)")
print(f"  거래량 가중: {weighted_rate:.1f}%")
print(f"  연결된 동 총합: {int(trade_master['n_dong'].sum())}동")


# ============================================================================
# 5. 건축년도 교차검증
# ============================================================================

print("\n===== 5. 건축년도 교차검증 =====")
verified = trade_master.dropna(subset=["cluster_id", "cluster_year", "build_year"]).copy()
verified["year_diff"] = (verified["build_year"] - verified["cluster_year"]).abs()
within_2y = verified["year_diff"] <= 2
consistency = 100 * within_2y.mean() if len(verified) else 0.0
print(f"  대조 가능 {len(verified)}건 / ±2년 일치 {int(within_2y.sum())} ({consistency:.1f}%)")
if len(verified):
    print(f"  년도 차이 중앙값 {verified['year_diff'].median():.0f}년")

# 한 클러스터에 여러 단지가 붙으면 과병합. 실제로 인접 단지가 붙은 것일 수 있다.
collision = (trade_master.dropna(subset=["cluster_id"])
    .groupby("cluster_id").size().rename("n_apt_seq"))
multi = collision[collision > 1]
print(f"\n  1클러스터-다단지 충돌: {len(multi)}개 클러스터 "
      f"(최대 {int(multi.max()) if len(multi) else 0}개 단지)")


# ============================================================================
# 6. 판정 및 저장
# ============================================================================

print("\n===== 6. 판정 =====")
checks = [
    ("지오코딩 성공률 >= 95%", geocode_rate, 95.0),
    ("단지 -> 동 클러스터 매칭률 >= 60%", match_rate, 60.0),
    ("건축년도 ±2년 일치율 >= 90%", consistency, 90.0),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:34s} 실측 {value:.1f}%")

print(f"\n  [참고] 07 문자열 매칭 32.5% -> 08 좌표 매칭 {match_rate:.1f}%")

result_path = output_dir / "08.1.geocoded_master.txt"
trade_master.to_csv(result_path, sep="\t", index=False, lineterminator="\n")
print(f"\n결과: {result_path}")
print(f"\n===== 좌표 매칭 {'통과' if all_passed else '미통과'} =====")
