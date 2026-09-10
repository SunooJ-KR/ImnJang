# ============================================================================
# 21.horizon_batch.py
# ============================================================================
# Author:      yjkim
# Purpose:     서울 전역 배정 동에 horizon 엔진(일조·조망)을 배치 적용한다
# Description: plan.md D4. 05.horizon_prototype.py에서 D1 검증 4항목을
#              통과한 horizon 엔진(자기 건물 제외 / AMSL datum / near-field
#              해상도 / 층↑→일조↑ 단조성)을 서울 전역 배정 동 x 층대로
#              확장한다. 05는 프로토타입이라 import 대상이 아니라고 명시돼
#              있지만, 파일명이 숫자로 시작해 `import 05.horizon_prototype`
#              문법 자체가 불가능하므로 importlib.util로 모듈을 로드해
#              함수/상수를 그대로 가져다 쓴다 (알고리즘 재구현 없음).
#
#              관측점 = 배정된 동 x 층대(LOW/MID/HIGH). 위치는 동 폴리곤
#              centroid(EPSG:5179), 높이는 (대표층-1)*2.8+1.5. 대표층은
#              05.main()이 실데이터 벤치마크에 쓴 것과 동일한 공식
#              (LOW~전체의 1/6, MID~1/2, HIGH~5/6 지점)을 그대로 쓴다.
#              이는 13.cell_coverage.py의 floor_band 경계(1/3, 2/3)로 만든
#              3분할의 중앙점이므로 downstream 셀과 정의가 맞는다. 대표층이
#              그 동의 levels를 넘으면(예: 15층 동의 HIGH가 13층인데 levels
#              가 3층인 경우) 해당 층대는 계산하지 않는다.
#
#              차폐물은 12.1(OSM 건물 전체)에서 height_m이 있는 모든
#              건물이다. building 태그(apartments/residential/yes 등)와
#              배정 여부는 차폐 여부와 무관하다 — 아파트가 아닌 건물도
#              그림자를 만든다. 자기 동만 osm_id로 차폐 후보에서 뺀다.
#
#              [대표층 분모] "levels"(동별 총 층수)는 OSM building:levels가
#              아니라 건축물대장(19) 지상층수를 1순위로 쓴다. 13.cell_coverage.py의
#              floor_band도 같은 분모(대장)로 바꿨다 — 분모가 다르면 학습 셀의
#              MID/HIGH와 이 스크립트의 MID/HIGH가 다른 물리적 층을 가리킨다
#              (기존 OSM levels 분모 기준 3층+ 불일치 27.6%). 매칭 우선순위:
#                1순위 ledger_name   동 이름(20.verify_dong_names.normalize_dong)으로
#                                    대장 dong_nm과 직접 매칭 — 가장 정확
#                2순위 ledger_median 이름이 없거나 매칭 실패 시, 그 지번(join_key)
#                                    대장 층수의 중앙값 — 근사지만 대장 데이터
#                3순위 osm_levels    대장을 아예 못 받은 지번은 기존 OSM levels로
#                                    폴백 — 데이터 없다고 관측점을 버리지 않는다
#
#              [알려진 한계] DEM(수치표고모델)이 없어 지면을 평지(z=0)로
#              가정한다. 05는 합성 데이터로 AMSL datum(지반이 높을수록
#              같은 건물이 더 크게 가리는 것)을 검증했지만, 실데이터에는
#              표고 컬럼이 없어 이 검증 결과를 그대로 적용할 뿐 실측
#              표고로 재현하지는 못한다. 경사지(예: 북한산 인근 저지대
#              단지)에서는 실제보다 차폐가 과소/과대 추정될 수 있다.
#              D4 이후 국가공간정보포털 DEM을 확보하면 ground_elev를
#              0 대신 실제값으로 교체해 개선한다.
#
#              [스펙과 다르게 구현한 부분 1] 작업 지시서는 15.2에 geometry
#              (WKT) 컬럼이 있다고 했으나 실제 파일에는 없다
#              (15.assign_dong_all.py 480행이 저장 직전 geometry를 drop).
#              osm_id로 12.1과 merge해 geometry를 가져온다.
#
#              [스펙과 다르게 구현한 부분 2] 단일 프로세스 실측 결과 캐시
#              생성 156초 + 관측점 계산 550초 = 706초(11.8분)로 "10분을
#              넘기면 안 된다"는 요구를 초과했다. 서울 전역 차폐 후보
#              48,211동이 D1 강남 프로토타입보다 밀도가 높아 관측점당
#              8.9ms로 나와 2.9ms 벤치마크의 3배였다. 건물별 계산은 서로
#              완전히 독립이므로 05의 함수(build_occluder_cache,
#              compute_horizon 등)는 그대로 두고 건물 단위 청크를 여러
#              프로세스(fork)에 나눠 호출하는 방식만 추가했다. 알고리즘
#              변경이 아니라 같은 계산을 병렬 실행한 것이다.
#
#              [일조 포화 결함 수정] winter_sunlight_hours(09~15시 총합, 상한
#              6.08h) 하나로 정렬하면 LOW 21.4%/MID 30.4%/HIGH 62.7%가 만점을
#              받아 상위 500위가 전부 동점이 됐다. 원인은 지표 자체가 법정
#              일조권 기준 어느 쪽도 아니었던 데 있다 - 09~15시 창을 쓰면서
#              "총합"을 재는 것은 두 법정 기준(아래) 중 어느 것도 아니다.
#              한국 일조권 수인한도 판단 기준(대법원 판례 · 환경분쟁조정위원회,
#              동지일 기준)은 다음 둘 중 하나를 충족하면 침해로 보지 않는다:
#                기준1: 09:00~15:00(6시간) 중 "연속" 2시간 이상 일조
#                기준2: 08:00~16:00(8시간) 중 "총합" 4시간 이상 일조
#              (출처: 찾기쉬운 생활법령정보 easylaw.go.kr "일조권 방해 분쟁",
#               서울고등법원 1996.3.29. 선고 94나11806 판결 등 다수 후속 판례)
#              그래서 관측점당 세 지표를 낸다:
#                winter_sunlight_hours    기존 유지 (09~15시 총합, 상한 6.08h).
#                                         두 법정 기준 어디에도 대응하지 않지만
#                                         하위 호환을 위해 남긴다.
#                winter_continuous_hours 09~15시 중 연속 최대 노출 시간.
#                                         기준1의 실측값.
#                winter_total_hours_8_16 08~16시 총 일조 시간(상한 약 8.08h).
#                                         기준2의 실측값.
#              horizon profile(72방위 최대 앙각)은 태양 궤적과 무관하게
#              관측점당 1회만 계산한다. 세 지표는 그 profile 하나를 09~15시
#              궤적과 08~16시 궤적 각각에 대조해 뽑아낸다 - compute_horizon
#              호출 횟수는 그대로다(3배로 늘리지 않음). 연속-최대 구간 계산은
#              05에 없는 로직이라 05를 건드리지 않고 이 파일에 새로 추가한다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import importlib.util
import multiprocessing as mp
import re
import time
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from shapely import wkt
from shapely.geometry import Point
from shapely.strtree import STRtree

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

# 05는 프로토타입이라 정식 import 대상이 아니고, 파일명이 숫자로 시작해
# `import 05.horizon_prototype`도 문법상 불가능하다. importlib로 직접 로드한다.
proto_spec = importlib.util.spec_from_file_location(
    "horizon_prototype", Path(__file__).resolve().parent / "05.horizon_prototype.py")
horizon_prototype = importlib.util.module_from_spec(proto_spec)
proto_spec.loader.exec_module(horizon_prototype)   # main()은 __name__ 가드로 실행되지 않는다

METRIC_CRS = horizon_prototype.METRIC_CRS
FLOOR_HEIGHT_M = horizon_prototype.FLOOR_HEIGHT_M
EYE_HEIGHT_M = horizon_prototype.EYE_HEIGHT_M
SEARCH_RADIUS_M = horizon_prototype.SEARCH_RADIUS_M
TALL_BUILDING_M = horizon_prototype.TALL_BUILDING_M
TALL_SEARCH_RADIUS_M = horizon_prototype.TALL_SEARCH_RADIUS_M
WINTER_SOLSTICE = horizon_prototype.WINTER_SOLSTICE
N_BINS = horizon_prototype.N_BINS               # winter_continuous_hours 계산에 필요 (아래)
BIN_WIDTH_DEG = horizon_prototype.BIN_WIDTH_DEG
build_occluder_cache = horizon_prototype.build_occluder_cache
compute_horizon = horizon_prototype.compute_horizon
sun_track = horizon_prototype.sun_track
sunlight_hours = horizon_prototype.sunlight_hours
view_metrics = horizon_prototype.view_metrics


def continuous_exposed_hours(profile, sun_azimuth, sun_elevation, interval_minutes=5):
    """태양 궤적 시계열을 profile과 대조해 "연속" 노출 최대 시간을 구한다.

    05.sunlight_hours()는 노출 총합만 낸다. 법정 기준1(09~15시 중 연속
    2시간 이상)은 총합이 아니라 최장 연속 구간이 필요해 05를 건드리지 않고
    여기 새로 추가한다. exposed 판정 로직 자체는 05.sunlight_hours()와
    동일 - bin 분해 + (태양고도 > 지평선 프로파일) & (태양고도 > 0).
    """
    bins = (sun_azimuth / BIN_WIDTH_DEG).astype(int) % N_BINS
    exposed = (sun_elevation > profile[bins]) & (sun_elevation > 0)
    if not exposed.any():
        return 0.0
    # 앞뒤에 False를 붙여 경계에서도 run이 잡히게 한 뒤, 0->1/1->0 전이 지점 간
    # 거리로 각 연속 구간 길이를 구한다 (시계열이므로 자정 넘어가는 wrap 불필요).
    padded = np.concatenate(([False], exposed, [False])).astype(int)
    starts = np.where(np.diff(padded) == 1)[0]
    ends = np.where(np.diff(padded) == -1)[0]
    return (ends - starts).max() * interval_minutes / 60.0

BUILDING_ASSIGNED_PATH = output_dir / "15.2.building_assigned.txt"
OSM_BUILDINGS_PATH = output_dir / "12.1.osm_buildings.txt"
COMPLEX_FINAL_PATH = output_dir / "15.1.complex_final.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"     # aptSeq -> 지번(join_key)
LEDGER_PATH = output_dir / "19.1.building_ledger.txt"      # 지번/동명칭 -> 대장 지상층수
COMPLEX_METRICS_RESULT = output_dir / "21.1.complex_metrics.txt"
OBSERVATION_RESULT = output_dir / "21.2.observation_points.txt"

# 대표층 공식: 05.main()의 실데이터 벤치마크와 동일 (재사용, 재발명 아님)
FLOOR_BANDS = [
    ("LOW", lambda levels: max(int(np.ceil(levels / 6)), 2)),
    ("MID", lambda levels: int(np.ceil(levels / 2))),
    ("HIGH", lambda levels: int(np.ceil(levels * 5 / 6))),
]

PROGRESS_EVERY = 5000

# 단일 프로세스 실측: 캐시 생성 156초 + 관측점 계산 550초 = 706초로 10분 상한을
# 초과했다(주 원인: 서울 전역 차폐 후보 48,211동은 D1 강남 벤치마크보다
# 밀도가 높아 관측점당 8.9ms로 3배 느림). 건물별 계산은 서로 독립이므로
# 알고리즘은 그대로 두고(05 함수 재사용) 청크 단위 멀티프로세싱만 추가한다.
N_WORKERS = max(1, mp.cpu_count() - 2)
MP_CONTEXT = mp.get_context("fork")   # 캐시·STRtree를 재직렬화 없이 자식에 상속


# ============================================================================
# 1. 데이터 로드
# ============================================================================

print("===== 1. 데이터 로드 =====")
for path in [BUILDING_ASSIGNED_PATH, OSM_BUILDINGS_PATH, COMPLEX_FINAL_PATH, MASTER_PATH, LEDGER_PATH]:
    if not path.exists():
        raise SystemExit(f"{path} 없음. 15/14/19를 먼저 실행할 것")

osm_all = pd.read_csv(OSM_BUILDINGS_PATH, sep="\t")
assigned = pd.read_csv(BUILDING_ASSIGNED_PATH, sep="\t", dtype={"aptSeq": str})
complex_final = pd.read_csv(COMPLEX_FINAL_PATH, sep="\t", dtype={"aptSeq": str})

assigned = assigned[assigned["aptSeq"].notna()].copy()
print(f"  배정된 동 {len(assigned)}개 (15.2 전체 {len(pd.read_csv(BUILDING_ASSIGNED_PATH, sep=chr(9)))}개 중)")

# geometry는 15.2에 없으므로 12.1에서 osm_id로 가져온다 (헤더 참고)
assigned = assigned.merge(osm_all[["osm_id", "geometry"]], on="osm_id", how="left")
missing_geom = assigned["geometry"].isna().sum()
assert missing_geom == 0, f"geometry 매칭 실패 {missing_geom}건 — 12.1과 osm_id 불일치"


def normalize_address(text):
    """20.verify_dong_names.py와 동일 — 대장 join_key(지번) 포맷에 맞춘다."""
    text = text.fillna("").astype(str).str.strip()
    text = text.str.replace(r"^서울(특별시)?\s*", "", regex=True)
    text = text.str.replace(r"\s+", " ", regex=True)
    return text.str.replace(r"산\s+(?=\d)", "산", regex=True)


def normalize_dong(value):
    """20.verify_dong_names.py와 동일 — '제904동'/'904동'/'904' -> '904'."""
    text = str(value).strip().upper()
    text = re.sub(r"^제", "", text)
    text = re.sub(r"동$", "", text)
    text = re.sub(r"\s+", "", text)
    return re.sub(r"^0+(?=\d)", "", text)


# --- 대표층 분모: 동별 OSM levels -> 동별 대장 지상층수로 교체 (헤더 참고) ---
master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})
master["join_key"] = normalize_address(
    master["gu"].fillna("") + " " + master["umd_name"].fillna("")
    + " " + master["jibun"].fillna(""))
apt_to_key = master.set_index("aptSeq")["join_key"]
assigned["join_key"] = assigned["aptSeq"].map(apt_to_key)

ledger = pd.read_csv(LEDGER_PATH, sep="\t")
ledger = ledger[ledger["hhld_cnt"] > 0].copy()                 # 주거동만 (19 헤더 참고)
ledger["dong_key"] = ledger["dong_nm"].apply(normalize_dong)
named_ledger = ledger[ledger["dong_key"] != ""]
# 동일 (join_key, dong_key) 조합에 값이 2개 이상인 경우는 18,879건 중 1건뿐이라 max()로 정리
ledger_by_name = named_ledger.groupby(["join_key", "dong_key"])["grnd_flr_cnt"].max()
ledger_median_by_key = ledger.groupby("join_key")["grnd_flr_cnt"].median()

assigned["name_key"] = assigned["name"].apply(lambda v: None if pd.isna(v) else normalize_dong(v))
osm_levels = pd.to_numeric(assigned["levels"], errors="coerce")   # 3순위 폴백 입력(원본 OSM levels)

ledger_by_name_dict = ledger_by_name.to_dict()
name_pairs = list(zip(assigned["join_key"], assigned["name_key"]))
ledger_name_denom = pd.Series([ledger_by_name_dict.get(p) for p in name_pairs], index=assigned.index)
ledger_median_denom = assigned["join_key"].map(ledger_median_by_key)

denom_source = np.select(
    [ledger_name_denom.notna(), ledger_median_denom.notna(), osm_levels.notna()],
    ["ledger_name", "ledger_median", "osm_levels"],
    default="missing")
# levels 컬럼을 대장 우선 분모로 덮어쓴다 — 이후 로직(대표층 계산 등)은 이 값을 그대로 쓴다
assigned["levels"] = ledger_name_denom.combine_first(ledger_median_denom).combine_first(osm_levels)

n_no_levels = (denom_source == "missing").sum()
assigned = assigned[denom_source != "missing"].reset_index(drop=True)
print(f"  분모 결측으로 제외: {n_no_levels}동 (대장·OSM levels 모두 없어 대표층을 정할 수 없음)")
print("  분모 소스별 동 수 (대장 직접 매칭 / 지번 중앙값 / OSM levels):")
source_counts = pd.Series(denom_source[denom_source != "missing"]).value_counts()
print(source_counts.reindex(["ledger_name", "ledger_median", "osm_levels"]).fillna(0).astype(int).to_string())
print(f"  관측점 생성 대상 동: {len(assigned)}개")

# 차폐물 전체: building 태그 무관, height_m 있는 모든 건물 (배정 여부 무관)
occluders = osm_all[osm_all["height_m"].notna()].reset_index(drop=True)
print(f"  차폐 후보 건물: {len(occluders)}개 (12.1 전체 {len(osm_all)}개 중 height_m 보유분)")


# ============================================================================
# 2. 좌표 변환 (EPSG:5179) 및 차폐 캐시
# ============================================================================

print("\n===== 2. 좌표 변환 · 차폐 캐시 =====")

assigned_gdf = gpd.GeoDataFrame(
    assigned.drop(columns="geometry"),
    geometry=assigned["geometry"].apply(wkt.loads), crs="EPSG:4326",
).to_crs(METRIC_CRS)
obs_centroids_xy = np.column_stack(
    [assigned_gdf.geometry.centroid.x, assigned_gdf.geometry.centroid.y])

occluder_gdf = gpd.GeoDataFrame(
    occluders[["osm_id", "height_m"]],
    geometry=occluders["geometry"].apply(wkt.loads), crs="EPSG:4326",
).to_crs(METRIC_CRS)
# DEM 없음 -> 지반고 0 (AMSL 계약 유지, 헤더의 알려진 한계 참고)
occluder_gdf["ground_elev"] = 0.0
occluder_gdf["top_z"] = occluder_gdf["ground_elev"] + occluder_gdf["height_m"]
occluder_centroids_xy = np.column_stack(
    [occluder_gdf.geometry.centroid.x, occluder_gdf.geometry.centroid.y])
osm_id_to_occluder_idx = {v: i for i, v in enumerate(occluder_gdf["osm_id"].to_numpy())}

def _build_cache_chunk(chunk_gdf):
    """05.build_occluder_cache를 그대로 호출한다 — 알고리즘은 안 바꾸고
    건물 청크만 나눠 여러 프로세스에서 병렬 실행한다."""
    return build_occluder_cache(chunk_gdf, "top_z")


cache_start = time.time()
# np.array_split은 GeoDataFrame을 ndarray로 바꿔버리므로 인덱스로 나눠 iloc한다
cache_chunk_idx = np.array_split(np.arange(len(occluder_gdf)), N_WORKERS * 4)
cache_chunks = [occluder_gdf.iloc[idx] for idx in cache_chunk_idx]
with MP_CONTEXT.Pool(N_WORKERS) as pool:
    # pool.map은 입력 순서를 보존한다 -> occluder_gdf 행 순서와 정렬이 유지된다
    cache_parts = pool.map(_build_cache_chunk, cache_chunks)
occluder_cache = [entry for part in cache_parts for entry in part]
print(f"  외곽점 캐시 생성: {time.time() - cache_start:.1f}초 "
      f"({len(occluder_gdf)}동, {N_WORKERS}개 프로세스)")

tree = STRtree(occluder_gdf.geometry.values)
tall_indices = np.where(occluder_gdf["height_m"].to_numpy() >= TALL_BUILDING_M)[0]
print(f"  {TALL_BUILDING_M}m 이상 초고층: {len(tall_indices)}동 (거리 무관 항상 후보)")

sun_azimuth, sun_elevation = sun_track(WINTER_SOLSTICE)                       # 09~15시 (기존, 기준1용)
sun_azimuth_8_16, sun_elevation_8_16 = sun_track(
    WINTER_SOLSTICE, start="08:00", end="16:00")                              # 08~16시 (기준2용)


# ============================================================================
# 3. 관측점별 horizon 계산
# ============================================================================

print(f"\n===== 3. 관측점 계산 ({len(assigned_gdf)}동 x 최대 3층대, {N_WORKERS}개 프로세스) =====")

# fork 자식이 상속할 전역 배열 (Pool 태스크 인자로는 건물 위치(정수)만 보낸다 —
# occluder_cache·tree 같은 큰 객체를 매 태스크마다 재직렬화하지 않기 위함)
osm_ids = assigned_gdf["osm_id"].to_numpy()
apt_seqs = assigned_gdf["aptSeq"].to_numpy()
levels_arr = assigned_gdf["levels"].to_numpy()


def _process_building(row_pos):
    """동 1개(최대 3층대)의 관측점 레코드를 계산한다.

    occluder_cache/tree/occluder_centroids_xy/sun_azimuth 등은 fork로 상속받은
    전역을 그대로 읽는다. 본문 로직은 병렬화 전 단일 루프와 동일하다
    (05의 compute_horizon/sunlight_hours/view_metrics를 그대로 호출).
    """
    obs_xy = obs_centroids_xy[row_pos]
    levels = levels_arr[row_pos]
    osm_id = osm_ids[row_pos]
    apt_seq = apt_seqs[row_pos]
    self_idx = osm_id_to_occluder_idx.get(osm_id)

    neighbors = np.asarray(tree.query(Point(obs_xy).buffer(SEARCH_RADIUS_M)))
    candidates = neighbors if self_idx is None else neighbors[neighbors != self_idx]

    exclude = candidates if self_idx is None else np.append(candidates, self_idx)
    far_tall = np.setdiff1d(tall_indices, exclude)
    if len(far_tall) > 0:
        distances = np.hypot(*(occluder_centroids_xy[far_tall] - obs_xy).T)
        candidates = np.concatenate([candidates, far_tall[distances <= TALL_SEARCH_RADIUS_M]])

    # D1 검증1(자기 건물 제외)을 실데이터에서도 계속 지키는지 매 관측점마다 확인한다
    assert self_idx is None or not np.any(candidates == self_idx), \
        f"자기 건물(osm_id={osm_id})이 차폐 후보에 포함됨"

    building_records = []
    for band_name, repr_floor_fn in FLOOR_BANDS:
        repr_floor = repr_floor_fn(levels)
        if repr_floor > levels:
            continue   # 이 동은 그 층대를 갖지 않는다

        obs_z = 0.0 + (repr_floor - 1) * FLOOR_HEIGHT_M + EYE_HEIGHT_M
        # horizon profile은 태양 궤적과 무관 -> 관측점당 1회만 계산하고
        # 세 일조 지표 모두 이 profile 하나를 재사용해 뽑는다 (3배 계산 금지).
        profile = compute_horizon(obs_xy, obs_z, candidates, occluder_cache, occluder_centroids_xy)
        metrics = view_metrics(profile)

        building_records.append({
            "aptSeq": apt_seq,
            "osm_id": osm_id,
            "floor_band": band_name,
            "repr_floor": repr_floor,
            "levels": levels,
            "n_candidates": len(candidates),
            "winter_sunlight_hours": round(sunlight_hours(profile, sun_azimuth, sun_elevation), 2),
            "winter_continuous_hours": round(
                continuous_exposed_hours(profile, sun_azimuth, sun_elevation), 2),
            "winter_total_hours_8_16": round(
                sunlight_hours(profile, sun_azimuth_8_16, sun_elevation_8_16), 2),
            **metrics,
        })
    return building_records


records = []
loop_start = time.time()

with MP_CONTEXT.Pool(N_WORKERS) as pool:
    for building_records in pool.imap_unordered(_process_building, range(len(assigned_gdf)), chunksize=32):
        records.extend(building_records)
        if len(records) // PROGRESS_EVERY > (len(records) - len(building_records)) // PROGRESS_EVERY:
            elapsed = time.time() - loop_start
            print(f"  [{len(records):,}]개 관측점 처리 ({elapsed:.0f}초, "
                  f"{1000 * elapsed / len(records):.1f}ms/점)")

elapsed = time.time() - loop_start
obs_df = pd.DataFrame(records)
print(f"\n  총 {len(obs_df):,}개 관측점, {elapsed:.1f}초 "
      f"({1000 * elapsed / len(obs_df):.2f}ms/점)")


# ============================================================================
# 4. 단지 x 층대 집계
# ============================================================================

print("\n===== 4. 단지 x 층대 집계 =====")

complex_metrics = (obs_df
    .groupby(["aptSeq", "floor_band"])
    .agg(
        n_obs=("osm_id", "count"),
        winter_sunlight_hours=("winter_sunlight_hours", "median"),
        winter_continuous_hours=("winter_continuous_hours", "median"),
        winter_total_hours_8_16=("winter_total_hours_8_16", "median"),
        view_block_pct=("view_block_pct", "median"),
        open_angle_mean=("open_angle_mean", "median"),
    )
    .reset_index()
    .merge(complex_final[["aptSeq", "confidence"]].rename(
        columns={"confidence": "assign_confidence"}), on="aptSeq", how="left")
)

print(f"  단지 x 층대 조합: {len(complex_metrics)}개 / 단지 {complex_metrics['aptSeq'].nunique()}개")
print("\n  층대별 지표 분포 (중앙값 기준 describe):")
print(complex_metrics.groupby("floor_band")[
    ["winter_sunlight_hours", "winter_continuous_hours", "winter_total_hours_8_16",
     "view_block_pct", "open_angle_mean"]].median().to_string())


# ============================================================================
# 5. 자체 검증 (assert)
# ============================================================================

print("\n===== 5. 자체 검증 =====")

assert not complex_metrics.duplicated(subset=["aptSeq", "floor_band"]).any(), \
    "aptSeq x floor_band 키 중복 발생"
SUN_METRICS = ["winter_sunlight_hours", "winter_continuous_hours", "winter_total_hours_8_16"]
assert complex_metrics[SUN_METRICS + ["view_block_pct", "open_angle_mean"]].notna().all().all(), \
    "집계 지표에 NaN 존재"

# 단조성(층↑ -> 일조↑)은 세 일조 지표 모두에 대해 확인한다 - 지표를 늘리며
# 새로 추가한 두 지표가 기존과 다른 방식으로 어긋나지 않는지 보는 것이 목적.
monotonic = True
for metric in SUN_METRICS:
    band_summary = (complex_metrics
        .groupby("floor_band")[metric]
        .median()
        .reindex(["LOW", "MID", "HIGH"]))
    metric_ok = band_summary["HIGH"] >= band_summary["MID"] >= band_summary["LOW"]
    monotonic &= metric_ok
    print(f"  층대별 {metric} 중앙값: {band_summary.to_dict()}")
    print(f"  [{'PASS' if metric_ok else 'FAIL'}] 층이 높을수록 {metric} 증가 (단조성)")
assert monotonic, "층↑ -> 일조↑ 단조성 위반 (세 지표 중 하나 이상)"

print("  [PASS] 자기 건물 차폐 후보 제외 (관측점별 assert 통과)")
print("  [PASS] aptSeq x floor_band 키 유일성")
print("  [PASS] 집계 지표 결측 없음")


# ============================================================================
# 6. 저장
# ============================================================================

print("\n===== 6. 저장 =====")

complex_metrics.to_csv(COMPLEX_METRICS_RESULT, sep="\t", index=False, lineterminator="\n")
obs_df.to_csv(OBSERVATION_RESULT, sep="\t", index=False, lineterminator="\n")

print(f"  단지 x 층대 지표: {COMPLEX_METRICS_RESULT}")
print(f"  관측점 원본:      {OBSERVATION_RESULT}")
print(f"\n===== D4 horizon 배치 완료 (관측점 {len(obs_df):,}개, {elapsed:.1f}초) =====")
