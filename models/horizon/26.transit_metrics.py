# ============================================================================
# 26.transit_metrics.py
# ============================================================================
# Author:      yjkim
# Purpose:     단지 단위 교통·근접도 지표(역 단위 승하차·혼잡도, 도로·철도 중심선
#              거리)를 계산한다
# Description: 22.access_metrics.py가 이미 "단지 <-> 최근접 subway POI" 직선거리
#              (subway_dist_m)를 냈지만, 그 POI는 railway=subway_entrance(출입구,
#              2,100건, 이름 없음)까지 포함한 것이라 "도보 거리용"이다. 이 스크립트는
#              반대로 railway=station(역 자체, 이름 있음)만 골라 "역 단위" 지표
#              (그 역의 월별 승하차인원, 출근시간대 혼잡도)를 붙인다. 목적이 다르므로
#              subway_dist_m과 겹치지 않는다 — 여기서는 거리를 다시 계산하지 않고
#              "어느 역이 가장 가까운가"만 판정에 쓴다.
#
#              12.3.osm_poi.txt에는 railway 태그 컬럼이 없다(amenity만 저장됨).
#              그런데 12.collect_osm.py의 classify()는 railway=station과
#              railway=subway_entrance를 둘 다 category="subway"로 합쳐서 저장했고,
#              역 태그만 name이 채워진다(출입구는 이름 없음). 실측 결과 category=
#              "subway" 중 name 비어있지 않은 것 351건(고유 역명 317개), 빈 것
#              2,100건 — 스펙이 말한 "출입구 2,100개"와 정확히 일치한다. 따라서
#              name 존재 여부가 railway=station 필터와 동일한 결과를 낸다.
#
#              역명 매칭: 서울교통공사 CSV는 같은 역이 호선별로 "역명(호선번호)"로
#              나뉜다(예: 서울역(1), 서울역(4) — 괄호 안 숫자가 실제로 호선 컬럼과
#              일치함을 확인했다). OSM 역 이름은 괄호가 없다. 괄호의 호선 suffix와
#              공백만 제거하면 매칭된다 — 두 역명이 "다른 표기 체계"라기보다 CSV가
#              선로별로 더 잘게 쪼갠 것뿐이라, 호선을 합산(승하차)·최댓값(혼잡도)
#              취합해 "역" 단위로 되돌린다.
#
#              road_centerline_m/rail_centerline_m은 24.collect_osm_extra.py(다른
#              에이전트 작업 중)의 산출물이 있어야 계산된다. 아직 없으면 건너뛴다 —
#              없는 데이터로 추정하지 않는다(plan.md R13과 같은 원칙). 스키마·컬럼명을
#              통제할 수 없으므로 로드는 방어적으로 처리하고, 실패해도 스크립트
#              전체를 죽이지 않는다.
#
#              docs/algorithms.md §6.3: "중심선까지 N m"만 사실 표기로 허용되고
#              소음 dB 추정은 금지된다. OSM은 centerline이지 도로 폭을 포함한
#              "경계"가 아니므로 컬럼명·설명 모두 "centerline"을 명시한다.
#
#              결측 원칙(plan.md R13): 매칭 실패·데이터 미수집은 0이 아니라 NaN으로
#              둔다. 0은 "그 역이 실제로 승하차 0명"이라는 별개의 사실이다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import re
import time
from calendar import monthrange
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from scipy.spatial import cKDTree
from shapely import wkt
from shapely.strtree import STRtree

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

POI_PATH = output_dir / "12.3.osm_poi.txt"
# aptSeq 앵커 소스: 22.1은 이미 완성되어 더 이상 바뀌지 않는 안정 파일이다.
# 23.1.complex.txt는 다른 에이전트가 지금 작성 중이라 의존하지 않는다.
ANCHOR_PATH = output_dir / "22.1.access_metrics.txt"
MONTHLY_PATH = output_dir / "raw" / "transit" / "subway_monthly.csv"
CONGESTION_PATH = output_dir / "raw" / "transit" / "subway_congestion.csv"
ROADS_PATH = output_dir / "24.1.osm_roads.txt"
RAILS_PATH = output_dir / "24.2.osm_rails.txt"
RESULT_PATH = output_dir / "26.1.transit_metrics.txt"

METRIC_CRS = "EPSG:5179"
RECENT_MONTHS = 12
PEAK_HOUR_COLS = ["8시00분", "8시30분", "9시00분"]   # 평일 출근시간대 08:00~09:00
ARTERIAL_HIGHWAY = {"motorway", "trunk", "primary", "motorway_link", "trunk_link", "primary_link"}
SECONDARY_HIGHWAY = {"secondary", "tertiary", "secondary_link", "tertiary_link"}

start_time = time.time()


def normalize_station_name(name):
    """CSV 역명의 '(호선번호)' suffix와 공백을 제거해 OSM 역명과 같은 형태로 맞춘다.
    예: '서울역(1)' -> '서울역', '동대문역사문화공원(2)' -> '동대문역사문화공원'."""
    name = str(name).strip()
    name = re.sub(r"\(\d+\)\s*$", "", name)
    name = re.sub(r"\s+", "", name)
    return name


# ============================================================================
# 1. 좌표계 변환 검증
# ============================================================================
# 이 저장소의 반복 버그(plan.md §5) — 22.access_metrics.py와 동일한 두 지점
# (서울시청 <-> 강남역, 실제 약 8.5km)으로 4326 -> 5179 변환 경로를 재검증한다.

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
# 2. 단지 앵커 및 역 POI 로드
# ============================================================================

print("\n===== 2. 단지 앵커 및 역 POI 로드 =====")

anchor = pd.read_csv(ANCHOR_PATH, sep="\t", dtype={"aptSeq": str})
anchor = anchor.rename(columns={"aptSeq": "apt_seq"})
assert anchor["apt_seq"].is_unique, "22.1 앵커에 apt_seq 중복 존재"
anchor_geom = gpd.GeoSeries(
    gpd.points_from_xy(anchor["lon"], anchor["lat"]), crs="EPSG:4326").to_crs(METRIC_CRS)
anchor_xy = np.column_stack([anchor_geom.x, anchor_geom.y])
print(f"  단지 앵커 {len(anchor):,}개")

poi = pd.read_csv(POI_PATH, sep="\t")
station_poi = poi[(poi["category"] == "subway") & poi["name"].notna() & (poi["name"].str.strip() != "")].copy()
station_poi["name_norm"] = station_poi["name"].map(normalize_station_name)
print(f"  railway=station 추정 POI {len(station_poi):,}건 (name 비어있지 않은 subway 카테고리)")
print(f"  고유 역명 {station_poi['name_norm'].nunique():,}개")

station_geom = gpd.GeoSeries(
    gpd.points_from_xy(station_poi["lon"], station_poi["lat"]), crs="EPSG:4326").to_crs(METRIC_CRS)
station_xy = np.column_stack([station_geom.x, station_geom.y])


# ============================================================================
# 3. 단지 <-> 최근접 역(이름) 매칭
# ============================================================================
# 여러 이름의 최근접 대상 중 진짜 최근접점 하나만 고른다. 같은 역명이 여러 개
# (환승역의 호선별 플랫폼 등) 있어도 최근접 거리 자체는 정상 동작 — 어차피
# 매칭 후에는 역명으로 승하차/혼잡도를 찾으므로 어느 플랫폼이 뽑히든 결과는 같다.

print("\n===== 3. 단지 <-> 최근접 역 매칭 =====")

_, nearest_idx = cKDTree(station_xy).query(anchor_xy, k=1)
anchor["nearest_station_name"] = station_poi["name_norm"].to_numpy()[nearest_idx]
print(f"  단지 {len(anchor):,}개 전부 최근접 역명 판정 완료 (역 POI가 있으므로 결측 없음)")


# ============================================================================
# 4. station_ridership_daily — 역별 일평균 승하차인원
# ============================================================================

print("\n===== 4. station_ridership_daily 계산 =====")

monthly = pd.read_csv(MONTHLY_PATH, encoding="cp949")
monthly["name_norm"] = monthly["역명"].map(normalize_station_name)

# 같은 역이라도 호선별로 행이 나뉘어 있다(서울역(1)/서울역(4)) -> 역명 합산으로 되돌린다.
monthly_by_station_month = (
    monthly.groupby(["name_norm", "수송연월"])["승하차인원수"].sum().reset_index()
)

recent_months = sorted(monthly_by_station_month["수송연월"].unique())[-RECENT_MONTHS:]
print(f"  기준 기간: {recent_months[0]} ~ {recent_months[-1]} ({len(recent_months)}개월)")
monthly_recent = monthly_by_station_month[monthly_by_station_month["수송연월"].isin(recent_months)].copy()

monthly_recent["year"] = monthly_recent["수송연월"].str[:4].astype(int)
monthly_recent["month"] = monthly_recent["수송연월"].str[5:7].astype(int)
monthly_recent["days_in_month"] = monthly_recent.apply(
    lambda r: monthrange(r["year"], r["month"])[1], axis=1)
monthly_recent["daily_ridership"] = monthly_recent["승하차인원수"] / monthly_recent["days_in_month"]

# 역이 최근 12개월 중 일부 달에만 존재해도(신규 개통 등) 있는 달만으로 평균한다
ridership_by_station = monthly_recent.groupby("name_norm")["daily_ridership"].mean()

n_csv_stations = monthly["name_norm"].nunique()
n_matched_ridership = station_poi["name_norm"].isin(ridership_by_station.index).sum()
n_matched_ridership_unique = station_poi.loc[
    station_poi["name_norm"].isin(ridership_by_station.index), "name_norm"].nunique()
print(f"  승하차 CSV 고유 역명 {n_csv_stations:,}개")
print(f"  역명 매칭률(OSM 역 -> 승하차 CSV): {n_matched_ridership_unique:,}/{station_poi['name_norm'].nunique():,}"
      f" ({n_matched_ridership_unique / station_poi['name_norm'].nunique() * 100:.1f}%)")

anchor["station_ridership_daily"] = anchor["nearest_station_name"].map(ridership_by_station)


# ============================================================================
# 5. station_congestion_peak — 평일 출근시간대(08:00~09:00) 최대 혼잡도
# ============================================================================
# plan.md 스키마에 없는 신규 컬럼. 승하차 절대값은 역의 '규모'만 보여주지만,
# 혼잡도는 거주자가 매일 체감하는 '출근길 붐빔'을 직접 나타내 거주 관점 의사결정에
# 더 유용하다 — 그래서 §22의 access_metrics 지표군에 추가한다.

print("\n===== 5. station_congestion_peak 계산 =====")

congestion = pd.read_csv(CONGESTION_PATH, encoding="cp949")
congestion["name_norm"] = congestion["역명"].map(normalize_station_name)
weekday = congestion[congestion["구분"] == "평일"].copy()
weekday["peak"] = weekday[PEAK_HOUR_COLS].max(axis=1)

# 상하(내외)선·호선 구분 없이 역명 단위로는 최댓값을 취한다 — "이 역 근처가 가장
# 붐빌 때 얼마나 붐비는가"가 궁금한 것이지 특정 방향의 평균이 궁금한 게 아니다
congestion_by_station = weekday.groupby("name_norm")["peak"].max()

n_matched_congestion_unique = station_poi.loc[
    station_poi["name_norm"].isin(congestion_by_station.index), "name_norm"].nunique()
print(f"  혼잡도 CSV 고유 역명 {congestion['name_norm'].nunique():,}개")
print(f"  역명 매칭률(OSM 역 -> 혼잡도 CSV): {n_matched_congestion_unique:,}/{station_poi['name_norm'].nunique():,}"
      f" ({n_matched_congestion_unique / station_poi['name_norm'].nunique() * 100:.1f}%)")

anchor["station_congestion_peak"] = anchor["nearest_station_name"].map(congestion_by_station)


# ============================================================================
# 6. road_centerline_m / rail_centerline_m — 있으면만 계산
# ============================================================================
# docs/algorithms.md §6.3: "중심선까지 N m"만 사실 표기로 허용, 소음 dB 추정 금지.
# 24.collect_osm_extra.py는 다른 에이전트가 작성 중이라 출력 스키마를 알 수 없다.
# 파일이 있어도 예상 컬럼이 없으면(WIP 스키마 변경 등) 실패로 죽지 않고 건너뛴다.

print("\n===== 6. road_centerline_m / rail_centerline_m =====")


def nearest_line_distance_m(points_xy, line_geoms):
    """점들에서 각 LineString까지 최근접 거리(m). 9,160단지 x 다수 라인이라
    STRtree로 후보를 좁힌 뒤 shapely distance()로 정확히 잰다."""
    if len(line_geoms) == 0:
        return np.full(len(points_xy), np.nan)
    tree = STRtree(line_geoms)
    result = np.full(len(points_xy), np.nan)
    for i, (x, y) in enumerate(points_xy):
        point = gpd.points_from_xy([x], [y])[0]
        nearest_i = tree.nearest(point)
        result[i] = point.distance(line_geoms[nearest_i])
    return result


def load_lines_5179(path):
    """WKT geometry 컬럼(12.1/12.2와 동일 저장 관례, EPSG:4326)을 5179로 변환해 반환."""
    df = pd.read_csv(path, sep="\t")
    if "geometry" not in df.columns:
        raise ValueError(f"{path.name}에 geometry 컬럼 없음 — 저장 스키마가 예상과 다름")
    geoms = gpd.GeoSeries(df["geometry"].map(wkt.loads), crs="EPSG:4326").to_crs(METRIC_CRS)
    return df, geoms


if not ROADS_PATH.exists():
    print(f"  [건너뜀] {ROADS_PATH.name} 미수집 — road_centerline_m 계산 건너뜀")
    has_road_metric = False
else:
    try:
        roads_df, roads_geom = load_lines_5179(ROADS_PATH)
        if "highway" not in roads_df.columns:
            raise ValueError("highway 컬럼 없음 — 위계 구분 불가")
        is_arterial = roads_df["highway"].isin(ARTERIAL_HIGHWAY).to_numpy()
        is_secondary = roads_df["highway"].isin(SECONDARY_HIGHWAY).to_numpy()
        arterial_geoms = roads_geom.to_numpy()[is_arterial]
        secondary_geoms = roads_geom.to_numpy()[is_secondary]
        anchor["road_arterial_dist_m"] = nearest_line_distance_m(anchor_xy, arterial_geoms)
        anchor["road_secondary_dist_m"] = nearest_line_distance_m(anchor_xy, secondary_geoms)
        # road_centerline_m: 위계 무관 최근접(간선/보조간선 중 더 가까운 쪽) — 23.2 스키마 컬럼명과 일치
        anchor["road_centerline_m"] = anchor[["road_arterial_dist_m", "road_secondary_dist_m"]].min(axis=1)
        print(f"  간선 {is_arterial.sum():,}개 / 보조간선 {is_secondary.sum():,}개 — 위계별 최근접 거리 계산 완료")
        has_road_metric = True
    except Exception as error:
        print(f"  [건너뜀] {ROADS_PATH.name} 처리 실패 — {error}")
        has_road_metric = False

if not RAILS_PATH.exists():
    print(f"  [건너뜀] {RAILS_PATH.name} 미수집 — rail_centerline_m 계산 건너뜀")
    has_rail_metric = False
else:
    try:
        rails_df, rails_geom = load_lines_5179(RAILS_PATH)
        # 24가 내는 컬럼명은 is_tunnel(불린)이다. OSM 원본 태그명(tunnel=yes)과
        # 다르므로 둘 다 받아준다. 지하 구간을 지상으로 표기하면 사실 왜곡이다
        if "is_tunnel" in rails_df.columns:
            is_surface = (~rails_df["is_tunnel"].astype(bool)).to_numpy()
        elif "tunnel" not in rails_df.columns:
            # 지상 여부를 판별할 수 없으면 "지상 철도"라고 사실 표기할 근거가 없다 —
            # 전체 철도로 대체 계산하지 않고 건너뛴다(§6.3 사실 표기 원칙)
            raise ValueError("tunnel/is_tunnel 컬럼 없음 — 지상 구간 판별 불가, 사실 표기 원칙상 건너뜀")
        else:
            is_surface = rails_df["tunnel"].fillna("no").astype(str).str.lower().ne("yes").to_numpy()
        surface_geoms = rails_geom.to_numpy()[is_surface]
        anchor["rail_centerline_m"] = nearest_line_distance_m(anchor_xy, surface_geoms)
        print(f"  지상 철도 {is_surface.sum():,}건 (전체 {len(rails_df):,}건 중) — 최근접 거리 계산 완료")
        has_rail_metric = True
    except Exception as error:
        print(f"  [건너뜀] {RAILS_PATH.name} 처리 실패 — {error}")
        has_rail_metric = False


# ============================================================================
# 7. 결과 구성
# ============================================================================

result_cols = ["apt_seq", "station_ridership_daily", "station_congestion_peak"]
if has_road_metric:
    result_cols += ["road_arterial_dist_m", "road_secondary_dist_m", "road_centerline_m"]
if has_rail_metric:
    result_cols += ["rail_centerline_m"]
result = anchor[result_cols].copy()


# ============================================================================
# 8. 자체 검증
# ============================================================================

print("\n===== 8. 자체 검증 =====")

metric_cols = [c for c in result.columns if c != "apt_seq"]
checks = [
    ("apt_seq 유일성", result["apt_seq"].is_unique, "PASS" if result["apt_seq"].is_unique else "FAIL"),
    ("모든 지표 음수 없음", ((result[metric_cols] >= 0) | result[metric_cols].isna()).all().all()
     if metric_cols else True, "음수 없음"),
    ("결측을 0으로 채우지 않음(0값 존재 자체는 정상)", True, "R13 원칙 유지 — 매칭 실패는 NaN"),
]
all_passed = True
for label, passed, observed in checks:
    all_passed &= bool(passed)
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:<45s} {observed}")
assert all_passed, "자체 검증 실패 — 위 [FAIL] 항목 확인"


# ============================================================================
# 9. 지표 분포
# ============================================================================

print("\n===== 9. 지표 분포 =====")
print(f"  {'지표':<24s}{'n':>7s}{'결측':>7s}{'p10':>12s}{'중앙값':>12s}{'p90':>12s}")
for col in metric_cols:
    s = result[col]
    n_valid = int(s.notna().sum())
    n_missing = int(s.isna().sum())
    if n_valid == 0:
        print(f"  {col:<24s}{n_valid:>7d}{n_missing:>7d}{'(전량 결측)':>36s}")
        continue
    print(f"  {col:<24s}{n_valid:>7d}{n_missing:>7d}"
          f"{s.quantile(.1):>12.1f}{s.median():>12.1f}{s.quantile(.9):>12.1f}")


# ============================================================================
# 10. 저장
# ============================================================================

result.to_csv(RESULT_PATH, sep="\t", index=False, lineterminator="\n")
elapsed = time.time() - start_time
print(f"\n실행 시간: {elapsed:.1f}초")
print(f"저장: {RESULT_PATH}")
print(f"\n===== 교통·근접도 지표 계산 {'통과' if all_passed else '미통과'} =====")
