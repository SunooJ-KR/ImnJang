# ============================================================================
# 04.cluster_refine.py
# ============================================================================
# Author:      yjkim
# Purpose:     기하 클러스터를 준공년도/층수로 재분할해 단지 복원 정확도를 올린다
# Description: 03에서 buffer-union 단독으로는 homogeneity/completeness 동시 0.90을
#              넘지 못했다 (최고 조화평균 0.812 @ 15m). 원인은 서울 아파트 단지가
#              물리적으로 맞닿아 있어 buffer가 연쇄 병합되는 것이다(최대 633동 한 덩어리).
#
#              기하 외 신호를 추가한다:
#              - start_date(준공년도): 같은 단지 동은 같은 해에 준공된다
#              - building:levels: 같은 단지 동은 층수가 비슷하다 (02에서 표준편차 중앙값 1.4층)
#
#              전략: completeness가 높은 큰 buffer로 먼저 뭉치고(과병합 허용),
#              그 안에서 속성으로 다시 쪼갠다(homogeneity 회복).
#
#              통과 기준: homogeneity/completeness 모두 >= 0.90
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import json
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Polygon
from shapely.ops import unary_union

work_dir = Path(__file__).parent
output_dir = work_dir / "output"
CACHE_PATH = output_dir / "cache_overpass_gangnam.json"
METRIC_CRS = "EPSG:5179"

# 03 결과: 25~30m에서 completeness 0.97~0.99. 과병합은 이후 속성으로 분해한다.
BASE_BUFFER_M = 25


# ============================================================================
# 1. 데이터 로드
# ============================================================================

print("===== 1. 데이터 로드 =====")
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
            "levels": tags.get("building:levels"),
            "start_date": tags.get("start_date"),
            "geometry": Polygon([(p["lon"], p["lat"]) for p in geometry]),
        })
    return gpd.GeoDataFrame(records, crs="EPSG:4326").to_crs(METRIC_CRS)


def parse_numeric(series):
    extracted = series.astype("string").str.extract(r"(\d+(?:\.\d+)?)", expand=False)
    return pd.to_numeric(extracted, errors="coerce")


buildings = to_gdf(elements, want_building=True)
complexes = to_gdf(elements, want_building=False)
apartments = buildings[buildings["building_type"] == "apartments"].reset_index(drop=True)
apartments["levels_num"] = parse_numeric(apartments["levels"])
apartments["built_year"] = parse_numeric(apartments["start_date"])

print(f"  아파트 동 {len(apartments)}, 준공년도 보유 {apartments['built_year'].notna().sum()}")


# ============================================================================
# 2. 정답 라벨
# ============================================================================

complexes["complex_area_m2"] = complexes.geometry.area
centroids = apartments[["osm_id"]].copy()
centroids["geometry"] = apartments.geometry.centroid
centroids = gpd.GeoDataFrame(centroids, crs=METRIC_CRS)

truth = gpd.sjoin(
    centroids,
    complexes[["osm_id", "complex_area_m2", "geometry"]].rename(columns={"osm_id": "truth_id"}),
    how="left", predicate="within",
)
truth = (truth
    .sort_values("complex_area_m2")
    .drop_duplicates(subset="osm_id", keep="first")
    [["osm_id", "truth_id"]]
)
apartments = apartments.merge(truth, on="osm_id", how="left")


# ============================================================================
# 3. 기하 클러스터 (과병합 허용)
# ============================================================================

def cluster_by_buffer(gdf, buffer_m):
    merged = unary_union(gdf.geometry.buffer(buffer_m))
    geoms = [merged] if merged.geom_type == "Polygon" else list(merged.geoms)
    clusters = gpd.GeoDataFrame(
        {"geo_cluster": range(len(geoms))},
        geometry=gpd.GeoSeries(geoms, crs=METRIC_CRS), crs=METRIC_CRS,
    )
    points = gdf[["osm_id"]].copy()
    points["geometry"] = gdf.geometry.centroid
    points = gpd.GeoDataFrame(points, crs=METRIC_CRS)
    joined = gpd.sjoin(points, clusters, how="left", predicate="within")
    return joined.drop_duplicates(subset="osm_id")[["osm_id", "geo_cluster"]]


apartments = apartments.merge(cluster_by_buffer(apartments, BASE_BUFFER_M), on="osm_id", how="left")
print(f"\n===== 2. 기하 클러스터 (buffer {BASE_BUFFER_M}m) =====")
print(f"  클러스터 {apartments['geo_cluster'].nunique()}개, 최대 크기 "
      f"{apartments.groupby('geo_cluster').size().max()}동")


# ============================================================================
# 4. 속성 기반 재분할
# ============================================================================
# 준공년도가 결측인 동은 같은 기하 클러스터 내 최빈 준공년도로 채운다.
# 층수는 보조 신호로만 쓴다(구간화해서 사용).

print("\n===== 3. 속성 재분할 =====")


def score_clustering(df, cluster_col):
    labeled = df.dropna(subset=["truth_id", cluster_col])
    homogeneity = (labeled.groupby(cluster_col)["truth_id"]
        .agg(lambda s: s.value_counts().iloc[0] / len(s)))
    h_weight = labeled.groupby(cluster_col).size()
    completeness = (labeled.groupby("truth_id")[cluster_col]
        .agg(lambda s: s.value_counts().iloc[0] / len(s)))
    c_weight = labeled.groupby("truth_id").size()
    h = float(np.average(homogeneity, weights=h_weight))
    c = float(np.average(completeness, weights=c_weight))
    return h, c, 2 * h * c / (h + c)


# 준공년도 결측 보정
year_mode = (apartments.dropna(subset=["built_year"])
    .groupby("geo_cluster")["built_year"]
    .agg(lambda s: s.mode().iloc[0])
    .rename("cluster_year_mode"))
apartments = apartments.join(year_mode, on="geo_cluster")
apartments["year_filled"] = apartments["built_year"].fillna(apartments["cluster_year_mode"])

print(f"  {'분할 기준':28s} {'클러스터':>8} {'homog':>8} {'compl':>8} {'조화':>8}")

variants = {}

# (a) 기하만
variants["기하만 (baseline)"] = apartments["geo_cluster"].astype("string")

# (b) 기하 + 준공년도 정확 일치
variants["기하 + 준공년도"] = (
    apartments["geo_cluster"].astype("string") + "_" + apartments["year_filled"].astype("string")
)

# (c) 기하 + 준공년도 3년 구간 (준공 시차가 있는 대단지 흡수)
year_band = (apartments["year_filled"] // 3).astype("string")
variants["기하 + 준공년도(3년구간)"] = apartments["geo_cluster"].astype("string") + "_" + year_band

# (d) 기하 + 준공년도 + 층수 5층 구간
levels_band = (apartments["levels_num"] // 5).astype("string")
variants["기하 + 준공년도 + 층수구간"] = (
    apartments["geo_cluster"].astype("string") + "_"
    + apartments["year_filled"].astype("string") + "_" + levels_band
)

results = []
for label, series in variants.items():
    apartments["_tmp"] = series
    h, c, harmonic = score_clustering(apartments, "_tmp")
    n = int(series.nunique())
    results.append({"variant": label, "n_clusters": n,
                    "homogeneity": round(h, 3), "completeness": round(c, 3),
                    "harmonic": round(harmonic, 3)})
    print(f"  {label:28s} {n:8d} {h:8.3f} {c:8.3f} {harmonic:8.3f}")

apartments = apartments.drop(columns=["_tmp"])
results_df = pd.DataFrame(results)
best = results_df.loc[results_df["harmonic"].idxmax()]


# ============================================================================
# 5. 판정
# ============================================================================

print(f"\n===== 4. 판정 (최적: {best['variant']}) =====")
checks = [
    ("homogeneity >= 0.90", best["homogeneity"], 0.90),
    ("completeness >= 0.90", best["completeness"], 0.90),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:24s} 실측 {value:.3f}")

results_path = output_dir / "04.1.cluster_refine.txt"
results_df.to_csv(results_path, sep="\t", index=False, lineterminator="\n")
print(f"\n결과: {results_path}")
print(f"\n===== 재분할 {'통과' if all_passed else '미통과'} =====")
