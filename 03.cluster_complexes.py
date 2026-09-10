# ============================================================================
# 03.cluster_complexes.py
# ============================================================================
# Author:      yjkim
# Purpose:     단지 폴리곤 없이 아파트 동을 뭉쳐 단지를 복원할 수 있는지 검증
# Description: 02에서 OSM landuse=residential 단지 경계가 없는 아파트가 45%임이
#              확인됐다. 단지 폴리곤에 의존하는 설계로는 서울 전역을 커버할 수 없다.
#              대안으로 건물 폴리곤을 buffer-union하여 연결 요소(clustering)를
#              단지로 삼는 방법을 검증한다.
#
#              검증 설계: landuse 폴리곤이 있는 동(정답 라벨 보유)만 뽑아
#              클러스터 결과와 대조한다. 정답을 재현하면 나머지 45%에도 쓸 수 있다.
#              - homogeneity: 한 클러스터가 여러 단지를 섞지 않는가 (과병합)
#              - completeness: 한 단지가 여러 클러스터로 쪼개지지 않는가 (과분할)
#
#              통과 기준: buffer 거리 하나에서 homogeneity/completeness 모두 >= 0.90
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union

work_dir = Path(__file__).parent
output_dir = work_dir / "output"
output_dir.mkdir(exist_ok=True)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "imnjang-prototype/0.1 (contest research)"
TARGET_BBOX = (37.4600, 127.0100, 37.5400, 127.1200)
METRIC_CRS = "EPSG:5179"
CACHE_PATH = output_dir / "cache_overpass_gangnam.json"

# 아파트 동 간 간격은 통상 20~50m. 넓히면 인접 단지가 붙고, 좁히면 한 단지가 쪼개진다.
BUFFER_CANDIDATES = [10, 15, 20, 25, 30, 40]


# ============================================================================
# 1. 데이터 로드 (02의 캐시 재사용)
# ============================================================================

def fetch_overpass(query, cache_path):
    if cache_path.exists():
        print(f"  캐시 사용: {cache_path.name}")
        return json.loads(cache_path.read_text(encoding="utf-8"))["elements"]

    last_error = None
    for attempt in range(3):
        try:
            response = requests.post(
                OVERPASS_URL, data={"data": query}, timeout=300,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            cache_path.write_text(response.text, encoding="utf-8")
            return response.json()["elements"]
        except requests.exceptions.RequestException as error:
            last_error = error
            print(f"  실패 {attempt + 1}/3 ({error}). 30초 후 재시도")
            time.sleep(30)
    raise RuntimeError(f"Overpass 호출 실패: {last_error}")


south, west, north, east = TARGET_BBOX
box = f"{south},{west},{north},{east}"
query = f"""
[out:json][timeout:180];
(
  way["building"~"^(apartments|residential)$"]({box});
  way["landuse"="residential"]["name"]({box});
);
out tags geom;
"""

print("===== 1. 데이터 로드 =====")
elements = fetch_overpass(query, CACHE_PATH)


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
            "geometry": Polygon([(p["lon"], p["lat"]) for p in geometry]),
        })
    return gpd.GeoDataFrame(records, crs="EPSG:4326").to_crs(METRIC_CRS)


buildings = to_gdf(elements, want_building=True)
complexes = to_gdf(elements, want_building=False)

# 빌라/다세대는 대상이 아니다 (02 가설 A)
apartments = buildings[buildings["building_type"] == "apartments"].reset_index(drop=True)
print(f"  아파트 동: {len(apartments)}, 단지 폴리곤: {len(complexes)}")


# ============================================================================
# 2. 정답 라벨 부여 (landuse 폴리곤에 포함되는 동만)
# ============================================================================

print("\n===== 2. 정답 라벨 부여 =====")

complexes["complex_area_m2"] = complexes.geometry.area
centroids = apartments[["osm_id"]].copy()
centroids["geometry"] = apartments.geometry.centroid
centroids = gpd.GeoDataFrame(centroids, crs=METRIC_CRS)

truth = gpd.sjoin(
    centroids,
    complexes[["osm_id", "name", "complex_area_m2", "geometry"]].rename(
        columns={"osm_id": "truth_id", "name": "truth_name"}
    ),
    how="left", predicate="within",
)
truth = (truth
    .sort_values("complex_area_m2")
    .drop_duplicates(subset="osm_id", keep="first")
    [["osm_id", "truth_id", "truth_name"]]
)
apartments = apartments.merge(truth, on="osm_id", how="left")

labeled = apartments[apartments["truth_id"].notna()].copy()
print(f"  정답 보유 동: {len(labeled)}/{len(apartments)} ({100 * len(labeled) / len(apartments):.1f}%)")
print(f"  정답 단지 수: {labeled['truth_id'].nunique()}")


# ============================================================================
# 3. buffer-union 클러스터링
# ============================================================================
# 각 건물을 buffer만큼 부풀려 합집합을 만들면, 서로 닿는 건물들이 하나의
# 연결 요소가 된다. sklearn 없이 shapely만으로 되고 기하적 의미가 명확하다.

def cluster_by_buffer(gdf, buffer_m):
    merged = unary_union(gdf.geometry.buffer(buffer_m))
    parts = gpd.GeoSeries(
        [merged] if merged.geom_type == "Polygon" else list(merged.geoms),
        crs=METRIC_CRS,
    )
    clusters = gpd.GeoDataFrame({"cluster_id": range(len(parts))}, geometry=parts, crs=METRIC_CRS)

    points = gdf[["osm_id"]].copy()
    points["geometry"] = gdf.geometry.centroid
    points = gpd.GeoDataFrame(points, crs=METRIC_CRS)

    assigned = gpd.sjoin(points, clusters, how="left", predicate="within")
    return assigned.drop_duplicates(subset="osm_id")[["osm_id", "cluster_id"]]


def score_clustering(labeled_df):
    """과병합/과분할을 각각 측정한다. 가중 평균으로 1.0이 완벽."""
    # homogeneity: 각 클러스터에서 최다 정답 단지가 차지하는 비율
    homogeneity = (labeled_df
        .groupby("cluster_id")["truth_id"]
        .agg(lambda s: s.value_counts().iloc[0] / len(s))
    )
    homogeneity_weight = labeled_df.groupby("cluster_id").size()
    homogeneity_score = float(np.average(homogeneity, weights=homogeneity_weight))

    # completeness: 각 정답 단지에서 최다 클러스터가 차지하는 비율
    completeness = (labeled_df
        .groupby("truth_id")["cluster_id"]
        .agg(lambda s: s.value_counts().iloc[0] / len(s))
    )
    completeness_weight = labeled_df.groupby("truth_id").size()
    completeness_score = float(np.average(completeness, weights=completeness_weight))

    return homogeneity_score, completeness_score


print("\n===== 3. buffer 거리별 클러스터링 성능 =====")
print(f"  {'buffer':>7} {'클러스터':>8} {'homogeneity':>12} {'completeness':>13} {'조화평균':>9}")

results = []
for buffer_m in BUFFER_CANDIDATES:
    cluster_map = cluster_by_buffer(apartments, buffer_m)
    scored = labeled.merge(cluster_map, on="osm_id", how="left").dropna(subset=["cluster_id"])
    homogeneity_score, completeness_score = score_clustering(scored)
    harmonic = 2 * homogeneity_score * completeness_score / (homogeneity_score + completeness_score)
    n_clusters = int(cluster_map["cluster_id"].nunique())
    results.append({
        "buffer_m": buffer_m,
        "n_clusters": n_clusters,
        "homogeneity": round(homogeneity_score, 3),
        "completeness": round(completeness_score, 3),
        "harmonic": round(harmonic, 3),
    })
    print(f"  {buffer_m:5d}m {n_clusters:8d} {homogeneity_score:12.3f} {completeness_score:13.3f} {harmonic:9.3f}")

results_df = pd.DataFrame(results)
best = results_df.loc[results_df["harmonic"].idxmax()]


# ============================================================================
# 4. 최적 buffer로 전체 커버리지 재계산
# ============================================================================

print(f"\n===== 4. 최적 buffer {int(best['buffer_m'])}m 적용 =====")

best_map = cluster_by_buffer(apartments, int(best["buffer_m"]))
apartments = apartments.merge(best_map, on="osm_id", how="left")

cluster_sizes = apartments.groupby("cluster_id").size()
# 동 1개짜리 클러스터는 단지가 아니라 나홀로아파트일 가능성이 크다
multi_dong = cluster_sizes[cluster_sizes >= 2]

coverage = 100 * apartments["cluster_id"].notna().mean()
print(f"  클러스터 배정률: {coverage:.1f}% (전 아파트 동)")
print(f"  총 클러스터: {len(cluster_sizes)} (동 2개 이상: {len(multi_dong)})")
print(f"  클러스터당 동 수 중앙값: {cluster_sizes.median():.0f}, 최대 {cluster_sizes.max()}")

# landuse 폴리곤이 없던 동들도 이제 단지에 소속되는가
previously_unassigned = apartments[apartments["truth_id"].isna()]
now_covered = 100 * previously_unassigned["cluster_id"].notna().mean()
print(f"\n  기존 미배정 동 {len(previously_unassigned)}개 중 클러스터 배정: {now_covered:.1f}%")


# ============================================================================
# 5. 판정 및 저장
# ============================================================================

print("\n===== 5. 판정 =====")
checks = [
    ("homogeneity >= 0.90 (과병합 없음)", best["homogeneity"], 0.90),
    ("completeness >= 0.90 (과분할 없음)", best["completeness"], 0.90),
    ("전체 아파트 동 클러스터 배정률 >= 95%", coverage, 95.0),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:38s} 실측 {value:.3f}")

results_path = output_dir / "03.1.buffer_tuning.txt"
cluster_path = output_dir / "03.2.apartment_clusters.txt"
results_df.to_csv(results_path, sep="\t", index=False, lineterminator="\n")
(apartments
    .drop(columns=["geometry"])
    .to_csv(cluster_path, sep="\t", index=False, lineterminator="\n")
)
print(f"\nbuffer 튜닝: {results_path}")
print(f"클러스터 배정: {cluster_path}")
print(f"\n===== 클러스터링 {'통과' if all_passed else '미통과'} =====")
