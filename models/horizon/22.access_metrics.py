# ============================================================================
# 22.access_metrics.py
# ============================================================================
# Author:      yjkim
# Purpose:     단지 단위 부가 지표(역 접근성 · 생활 인프라 밀도 · 최근접 거리)를 계산한다
# Description: algorithms.md §6.4(표고 및 접근성)를 따른다. D4 horizon 배치(21)의
#              나머지 절반 — horizon 엔진과 무관하게 "단지 앵커점 ↔ POI" 거리·개수만
#              계산하므로 21의 동 배정 결과에 의존하지 않는다.
#
#              단위 목록은 14.1(지오코딩 마스터, 9,164단지)을 그대로 쓴다. 15.1은
#              동 배정 신뢰도 등급이 붙은 하위집합(9,160단지)인데, 이 지표는 동
#              배정과 무관한 단지 중심점 거리 계산이라 15.1로 좁힐 이유가 없다.
#
#              - 역 접근성: 단지 중심 ↔ 최근접 subway POI 직선거리. 카카오 도보경로
#                API는 일 1,000건 제한으로 배제됐다(§5). "추정" 표기는 downstream
#                (앱)의 책임이므로 여기서는 raw 직선거리만 낸다.
#              - 표고차(subway_elev_diff_m): DEM이 없어 계산하지 않는다. 컬럼은
#                만들되 전량 NaN — 값을 만들어내지 않는다.
#              - 도로·철도 근접도(§6.3)는 이번 범위에서 제외한다. OSM 도로·철도
#                centerline을 아직 수집하지 않았다(12.collect_osm.py는 POI/건물만
#                수집). 없는 데이터로 추정하지 않는다. 별도 수집 스크립트가 필요하다.
#              - 결측 원칙(plan.md R13과 같은 취지): 반경 500m 내 POI가 0개인 것은
#                "관측된 사실(0개)"이지 결측이 아니다. 0으로 채우지 않는다 —
#                query_ball_point가 빈 리스트를 반환하면 그대로 0을 센다.
#                최근접 거리 컬럼은 각 카테고리가 전부 서울 전역에 620건 이상
#                있어 (12.3 기준) 탐색 반경 제한 없이 계산하므로 결측이 나지 않는다.
#                (subway_elev_diff_m만 예외 — DEM 부재로 의도된 전량 결측)
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import time

import geopandas as gpd
import numpy as np
import pandas as pd
from pathlib import Path
from scipy.spatial import cKDTree

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

POI_PATH = output_dir / "12.3.osm_poi.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"
RESULT_PATH = output_dir / "22.1.access_metrics.txt"

METRIC_CRS = "EPSG:5179"
RADIUS_M = 500.0

# 생활 인프라 밀도(500m 카운트) 대상. subway는 제외 — 역 접근성은 거리로만 본다
DENSITY_CATEGORIES = ["convenience", "mart", "park", "hospital", "school", "restaurant"]
# 최근접 거리 대상. subway는 "역 접근성"(항목 1), 나머지는 "최근접 거리"(항목 3)
NEAREST_CATEGORIES = ["subway", "park", "hospital", "mart"]

start_time = time.time()


# ============================================================================
# 1. 좌표계 변환 검증
# ============================================================================
# 변환 누락은 이 프로젝트의 반복 버그다(plan.md §5). 실제 배치에 쓰는 것과
# 동일한 4326 -> 5179 변환 경로로, 알려진 두 지점(서울시청 <-> 강남역, 실제
# 약 8.5km)을 재보고 어긋나면 즉시 멈춘다.

print("===== 1. 좌표계 변환 검증 =====")

landmarks = gpd.GeoSeries(
    gpd.points_from_xy([126.9780, 127.0276], [37.5665, 37.4979]), crs="EPSG:4326"
).to_crs(METRIC_CRS)
landmark_dist_m = landmarks.iloc[0].distance(landmarks.iloc[1])
print(f"  서울시청 <-> 강남역 (EPSG:5179 계산): {landmark_dist_m:,.0f}m")
assert 8_000 <= landmark_dist_m <= 9_500, (
    f"좌표계 변환 오류 의심 — 실제 약 8.5km인데 {landmark_dist_m / 1000:.2f}km로 계산됨")
print("  [PASS] 좌표계 변환 정상")


# ============================================================================
# 2. 데이터 로드
# ============================================================================

print("\n===== 2. 데이터 로드 =====")

master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})
assert master["aptSeq"].is_unique, "14.1 원본에 aptSeq 중복 존재"
n_missing_coord = int(master[["lat", "lon"]].isna().any(axis=1).sum())
master = master.dropna(subset=["lat", "lon"]).reset_index(drop=True)
print(f"  단지 앵커 {len(master):,}개 (좌표 결측 {n_missing_coord}개 제외)")

anchor_geom = gpd.GeoSeries(
    gpd.points_from_xy(master["lon"], master["lat"]), crs="EPSG:4326").to_crs(METRIC_CRS)
anchor_xy = np.column_stack([anchor_geom.x, anchor_geom.y])

poi = pd.read_csv(POI_PATH, sep="\t")
poi_geom = gpd.GeoSeries(
    gpd.points_from_xy(poi["lon"], poi["lat"]), crs="EPSG:4326").to_crs(METRIC_CRS)
poi_xy = np.column_stack([poi_geom.x, poi_geom.y])
print(f"  POI {len(poi):,}건")
print(poi.groupby("category").size().rename("n").reset_index().to_string(index=False))

category_xy = {cat: poi_xy[poi["category"].to_numpy() == cat] for cat in poi["category"].unique()}
elementary_xy = poi_xy[poi["is_elementary"].to_numpy()]
print(f"  초등학교(is_elementary=True) {len(elementary_xy):,}건")


# ============================================================================
# 3. 거리·개수 계산
# ============================================================================

print("\n===== 3. 거리·개수 계산 =====")


def nearest_distance(target_xy):
    """단지 앵커 각각에서 target_xy까지 최근접 직선거리(m). target이 비어 있으면
    전량 NaN — 없는 값을 0이나 임의 상수로 메우지 않는다."""
    if len(target_xy) == 0:
        return np.full(len(anchor_xy), np.nan)
    dist, _ = cKDTree(target_xy).query(anchor_xy, k=1)
    return dist


def count_within_radius(target_xy, radius):
    """반경 radius(m) 내 target_xy 개수. 0건은 결측이 아니라 관측된 사실이다."""
    if len(target_xy) == 0:
        return np.zeros(len(anchor_xy), dtype=int)
    neighbor_lists = cKDTree(target_xy).query_ball_point(anchor_xy, r=radius)
    return np.array([len(lst) for lst in neighbor_lists])


result = master[["aptSeq", "apt_name", "gu", "lat", "lon"]].copy()

for name in NEAREST_CATEGORIES:
    result[f"{name}_dist_m"] = nearest_distance(category_xy.get(name, np.empty((0, 2))))
result["elementary_dist_m"] = nearest_distance(elementary_xy)

# 표고차: DEM 미보유로 계산하지 않는다. 컬럼만 두고 전량 결측 처리(위 헤더 참고)
result["subway_elev_diff_m"] = np.nan

for name in DENSITY_CATEGORIES:
    result[f"{name}_500m"] = count_within_radius(category_xy.get(name, np.empty((0, 2))), RADIUS_M)
result["elementary_500m"] = count_within_radius(elementary_xy, RADIUS_M)

result = result[[
    "aptSeq", "apt_name", "gu", "lat", "lon",
    "subway_dist_m", "subway_elev_diff_m",
    "convenience_500m", "mart_500m", "park_500m", "hospital_500m",
    "school_500m", "elementary_500m", "restaurant_500m",
    "park_dist_m", "hospital_dist_m", "mart_dist_m", "elementary_dist_m",
]]
print(f"  단지 {len(result):,}개 x 지표 {result.shape[1] - 5}개 계산 완료")


# ============================================================================
# 4. 자체 검증
# ============================================================================

print("\n===== 4. 자체 검증 =====")

dist_cols = [c for c in result.columns if c.endswith("_dist_m") and c != "subway_elev_diff_m"]
count_cols = [c for c in result.columns if c.endswith("_500m")]

checks = [
    ("aptSeq 유일성", result["aptSeq"].is_unique, "PASS" if result["aptSeq"].is_unique else "FAIL"),
    ("거리 지표 결측 없음 (elev_diff 제외)", not result[dist_cols].isna().any().any(),
     f"{'결측 없음' if not result[dist_cols].isna().any().any() else '결측 발생'}"),
    ("거리 지표 음수 없음", (result[dist_cols] >= 0).all().all(), "음수 없음"),
    ("카운트 지표 음수 없음", (result[count_cols] >= 0).all().all(), "음수 없음"),
    ("subway_elev_diff_m 전량 결측 (의도된 값)", result["subway_elev_diff_m"].isna().all(), "의도대로 전량 결측"),
]
all_passed = True
for label, passed, observed in checks:
    all_passed &= bool(passed)
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:<38s} {observed}")
assert all_passed, "자체 검증 실패 — 위 [FAIL] 항목 확인"


# ============================================================================
# 5. 지표 분포 및 변별력
# ============================================================================

print("\n===== 5. 지표 분포 =====")
print(f"  {'지표':<20s}{'n':>7s}{'결측':>7s}{'p10':>10s}{'중앙값':>10s}{'p90':>10s}")
for col in dist_cols + ["subway_elev_diff_m"] + count_cols:
    s = result[col]
    n_valid = int(s.notna().sum())
    n_missing = int(s.isna().sum())
    if n_valid == 0:
        print(f"  {col:<20s}{n_valid:>7d}{n_missing:>7d}{'(전량 결측)':>30s}")
        continue
    print(f"  {col:<20s}{n_valid:>7d}{n_missing:>7d}"
          f"{s.quantile(.1):>10.1f}{s.median():>10.1f}{s.quantile(.9):>10.1f}")


# ============================================================================
# 6. 저장
# ============================================================================

result.to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n")
elapsed = time.time() - start_time
print(f"\n실행 시간: {elapsed:.1f}초")
print(f"저장: {RESULT_PATH}")
print(f"\n===== 부가 지표 계산 {'통과' if all_passed else '미통과'} =====")
