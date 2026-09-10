# ============================================================================
# 15.assign_dong_all.py
# ============================================================================
# Author:      yjkim
# Purpose:     서울 전역 아파트 동을 실거래 단지(aptSeq)에 1:1 배정한다
# Description: 09의 "동 -> 단지" 방향은 유지하되, 배정 근거를 두 가지로 바꾼다.
#
#              (1) 단지 경계 폴리곤 (12.2, OSM landuse 4,285개)
#                  같은 경계 안의 동은 그 경계 안의 앵커에게만 갈 수 있다.
#                  최근접 원형 반경은 3ha+ 단지에서 앵커가 단지 중심을 중앙값
#                  112m 벗어나 구조적으로 틀렸다.
#
#              (2) 한국부동산원 등록 동수를 배정 상한으로 (17에서 확보)
#                  경계만으로는 부족했다. 17의 대조 결과 동수 정확 일치율이
#                  60.8%였고 과다 배정이 1,760건이었다(한영해시안 실제 1동에
#                  257동 배정). 공식 등록 동수를 capacity로 걸면 과다가
#                  구조적으로 차단된다.
#
#              (3) 건축물대장 동명칭 (19에서 확보)
#                  20의 검증 결과 동수 상한만으로는 "맞는 동"을 고르지 못했다
#                  (이름으로 판정 가능한 2,985단지 중 정상 51.1%).
#                  대장은 지번마다 동명칭 목록을 주고 지번 안에서 그 이름은 사실상
#                  유일하다(22,925건 중 중복 14건). OSM 이름이 그 목록에 있으면
#                  최우선으로 붙이고, 목록에 없으면 그 쌍을 아예 금지한다.
#                  이름은 전역 유일하지 않으므로(서울에 '101동'이 1,845개) 공간으로
#                  후보를 좁힌 뒤 이름으로 가르는 순서여야 한다.
#
#              (4) 대장 지상층수 (이름 없는 동에만)
#                  OSM 아파트 동의 35%는 이름이 없어 이름 근거를 못 쓴다. 이들은
#                  거리·경계 추정에 남는데, 대장 층수와 맞지 않는 쌍은 버릴 수 있다.
#                  층수는 지번 안에서는 변별력이 없지만(8,158지번 중 6,427은 모든
#                  동이 같은 층수) 지번 사이에서는 있다.
#                  정답쌍 12,121건으로 교정: OSM levels와 대장 지상층수가
#                  정확 일치 91.1% / ±1층 93.3% / ±2층 94.5%.
#                  층수가 어긋나는 쌍을 버리면 정밀도가 오르지만(독립 지표인
#                  높이 ±3m 일치 82.0% -> 94.9%) 커버리지가 7%p 빠진다.
#                  그래서 버리지 않고 최하 순위로 내린 뒤 등급을 LEVEL_MISMATCH로
#                  남긴다. 정렬 근거로 쓸지는 downstream이 등급 보고 정한다.
#
#              순환 방지: 배정에는 층수만 쓰고 높이는 검증 전용으로 남긴다.
#              등록 동수와 동명칭을 배정에 쓴 뒤 그 값으로 검증하려다 두 번
#              순환에 빠졌다. 같은 실수를 반복하지 않는다.
#
#              배정은 용량 제약 greedy다. 우선순위별로 쌍을 거리순 소진하며
#              앵커별 잔여 용량을 넘지 않는다.
#
#              준공년도는 배정에 쓰지 않는다. 초판은 년도가 맞는 앵커를 고른 뒤
#              년도로 검증해 일치율이 정의상 100%였다. 검증 전용으로 격리한다.
#
#              통과 기준:
#                (1) 동수 정확 일치율 >= 90% (등록 정보 대조, 17이 최종 판정)
#                (2) 동을 1개 이상 받은 단지 비율 >= 80%
#                (3) 지번 단위 과다 배정 0건 (aptSeq 단위는 capacity 상한이
#                    숨겨 무의미하다 — 6절 참고. 지번 단위로 재집계해야
#                    한 지번을 나눠 가진 aptSeq들의 합이 실제로 드러난다)
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import sys

import re
from difflib import SequenceMatcher

import numpy as np
import pandas as pd
import geopandas as gpd
from pathlib import Path
from scipy.spatial import cKDTree
from shapely import wkt

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

BUILDING_PATH = output_dir / "12.1.osm_buildings.txt"
BOUNDARY_PATH = output_dir / "12.2.osm_complex_boundary.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"
REGISTRY_PATH = output_dir / "raw" / "reb" / "apt_registry.csv"
COMPLEX_RESULT = output_dir / "15.1.complex_final.txt"
BUILDING_RESULT = output_dir / "15.2.building_assigned.txt"

METRIC_CRS = "EPSG:5179"
# 최근접 경로의 반경. 고정값 300m는 대단지에서 구조적으로 모자랐다 —
# DMC래미안e편한세상(등록 51동)은 앵커에서 단지 끝까지가 300m를 넘어 30동을
# 놓쳤고, 못 가져온 29동의 최소 거리가 301m였다.
# 정확 배정 5,994건에서 앵커→최원거리 동의 p90을 등록 동수별로 재보니
# 1동 136m / 6-8동 211m / 13-20동 367m / 21-35동 455m로 sqrt(동수)에 비례했다.
# 그 측정 자체가 300m 상한 아래 절단된 값이므로 계수에 여유를 둔다.
# 반경을 크게 풀어도 과다는 등록 동수 상한이 막는다(그것이 원래 300m의 역할이었다).
#
# 계수 K 교정 (0/170/350, 하한 300m 고정):
#   고정300  커버 73.2%  동수일치 93.4%  연도독립 94.2%  13동+ 일치 64.9%
#   K=170    커버 73.3%  동수일치 94.4%  연도독립 93.6%  13동+ 일치 73.8%  <- 채택
#   K=350    커버 74.9%  동수일치 94.9%  연도독립 93.0%  13동+ 일치 73.5%
# 동수일치는 상한 때문에 순환 지표라 믿을 수 없다. 배정에 쓰지 않은 OSM
# start_date vs 등록 사용승인일 대조(연도독립)가 실제 정밀도다. K를 350까지
# 밀면 커버리지 1.6%p를 얻지만 대단지 효과는 더 없고 연도독립만 깎인다.
RADIUS_BASE = 60.0
RADIUS_K = float(sys.argv[1]) if len(sys.argv) > 1 else 170.0
RADIUS_MIN = float(sys.argv[2]) if len(sys.argv) > 2 else 300.0
RADIUS_MAX = 1200.0
RADIUS_UNKNOWN = 300.0   # 등록 정보 미매칭 단지는 종전 고정값을 쓴다
N_NEIGHBORS = 12         # 반경이 커져 후보 앵커가 늘었다. 용량이 차면 차순위로 넘어간다
YEAR_TOLERANCE = 2       # 검증 전용. 배정에는 쓰지 않는다
YEAR_RANGE = (1930, 2027)  # OSM start_date에 '19997', '202' 같은 오타 태그가 있다
MIN_HEIGHT_M = 5.0       # levels 15인데 height 4m 같은 태그 오류를 걸러낸다
APARTMENT_CODE = "1"     # 등록 정보 단지종류 1=아파트

# 대장 동명칭이 그 지번의 등록 동수를 다 담고 있을 때만 "목록에 없으면 금지"를
# 적용한다. 여러 지번에 걸친 단지는 대장이 일부만 담고 있어 금지하면 굶는다
LEDGER_PATH = output_dir / "19.1.building_ledger.txt"
# 이름 없는 동을 층수로 거를 때의 허용 오차. 정답쌍 12,121건 기준 ±2층이
# 참쌍의 94.5%를 덮는다. 0이면 필터를 끈다 (argv로 덮어씀)
LEVEL_TOLERANCE = float(sys.argv[3]) if len(sys.argv) > 3 else 2.0

UNASSIGNED = -1
PRIORITY_LEVEL_MISMATCH = 3   # 층수가 대장과 어긋나는 쌍. 버리지 않고 최후에 쓴다


# ============================================================================
# 1. 단지 앵커 + 등록 동수(배정 상한)
# ============================================================================

print("===== 1. 단지 앵커 =====")
master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})
master = master.dropna(subset=["lat", "lon"]).reset_index(drop=True)


def normalize_address(text):
    """등록 정보 '서울특별시 강서구 마곡동 744' <-> 실거래 gu/umd/jibun 를 같은 키로"""
    text = text.fillna("").astype(str).str.strip()
    text = text.str.replace(r"^서울(특별시)?\s*", "", regex=True)
    text = text.str.replace(r"\s+", " ", regex=True)
    return text.str.replace(r"산\s+(?=\d)", "산", regex=True)


def normalize_complex_name(name):
    """단지명 비교용 정규화. 괄호 안 부연설명·공백·말미 '아파트'/'단지' 접미어를
    없애 표기 차이를 흡수한다. 예: '현대6차(78~81동)' / '현대6차 아파트' -> '현대6차'"""
    if pd.isna(name):
        return ""
    text = str(name).strip().upper()
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\s+", "", text)
    return re.sub(r"(아파트|단지)$", "", text)


def name_match_score(a, b):
    """완전일치(3) -> 부분포함(2) -> 최장공통부분문자열 2자+(1) -> 불일치(0).
    등급을 나눠야 '현대6차'가 '현대65동'에 우연히 겹치는 얕은 유사도가
    다른 단지의 완전일치보다 앞서지 않는다."""
    if not a or not b:
        return 0
    if a == b:
        return 3
    if a in b or b in a:
        return 2
    lcs = SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b))
    return 1 if lcs.size >= 2 else 0


def match_complex_names(apt_names, reg_names):
    """한 지번 안 실거래 단지명(apt_names)과 등록 공식 단지명(reg_names)을
    이름 유사도로 1:1 매칭한다 (버그 1: drop_duplicates로 버리면 압구정동 456의
    현대65동/7차/6차 같은 서로 다른 공식 단지가 첫 행 하나로 뭉개졌다 — registry
    duplicate 118행/49개 지번, 그중 44개가 서로 다른 동수).
    점수가 높은 레벨부터 그리디로 소진하며, 레벨 안에서는 입력 순서(호출 쪽에서
    aptSeq 오름차순으로 넘김)대로 먼저 오는 쪽이 이겨 동점을 결정적으로 가른다.
    매칭 실패분은 아직 안 쓰인 공식 단지를 입력 순서대로 배정하고, 그마저
    없으면 None을 남긴다(reg_dong 결측 = 상한 없음, 기존 미매칭과 동일 처리)."""
    norm_apt = [normalize_complex_name(n) for n in apt_names]
    norm_reg = [normalize_complex_name(n) for n in reg_names]
    matched = [None] * len(apt_names)
    used_reg = set()
    for level in (3, 2, 1):
        for i, a in enumerate(norm_apt):
            if matched[i] is not None or not a:
                continue
            for j, r in enumerate(norm_reg):
                if j in used_reg or not r:
                    continue
                if name_match_score(a, r) == level:
                    matched[i] = j
                    used_reg.add(j)
                    break
    unused_reg = iter(j for j in range(len(reg_names)) if j not in used_reg)
    for i in range(len(apt_names)):
        if matched[i] is None:
            matched[i] = next(unused_reg, None)
    return matched


def largest_remainder_split(total, n):
    """total을 정수 n등분해 합이 total과 정확히 같은 배열로 돌려준다 (버그 2).
    ceil을 쓰면 각자 올림돼 합이 total을 넘는다 — reg_dong=5를 2개가 나누면
    ceil(2.5)=3씩 되어 합이 6이 된다. floor로 먼저 채우고 남는 몫을 앞에서부터
    1씩 더한다. 한 지번을 n개의 aptSeq가 똑같이 나누는 구조라 나머지는 전원
    동률이므로, 그 동률은 호출 쪽이 넘긴 순서(aptSeq 오름차순)로 결정적으로 깬다."""
    base = int(total) // n
    remainder = int(total) - base * n
    shares = np.full(n, float(base))
    shares[:remainder] += 1
    return shares


registry = pd.read_csv(REGISTRY_PATH, encoding="utf-8-sig", dtype=str)
registry = registry[registry["주소"].str.startswith("서울", na=False)
                    & (registry["단지종류"] == APARTMENT_CODE)].copy()
registry["reg_dong"] = pd.to_numeric(registry["동수"], errors="coerce")
registry["reg_units"] = pd.to_numeric(registry["세대수"], errors="coerce")
registry["reg_year"] = pd.to_numeric(registry["사용승인일"].str[:4], errors="coerce")
registry["join_key"] = normalize_address(registry["주소"])
registry["reg_name"] = (registry["단지명_공시가격"]
                        .fillna(registry["단지명_건축물대장"])
                        .fillna(registry["단지명_도로명주소"]))
# 더 이상 drop_duplicates 하지 않는다 — 한 지번에 공식 단지가 여러 개 등록된
# 경우(118행/49개 지번, 그중 44개가 서로 다른 동수)를 첫 행으로 뭉개면 안 된다

master["join_key"] = normalize_address(
    master["gu"].fillna("") + " " + master["umd_name"].fillna("")
    + " " + master["jibun"].fillna(""))

# 지번(join_key) 단위로 등록 정보를 aptSeq에 붙인다.
#   공식 단지가 1개뿐: master aptSeq가 여럿이면(분양/임대 분리 등) 그 1건을
#                     largest_remainder_split으로 나눠 갖는다 (버그 2).
#   공식 단지가 여럿: 단지명으로 1:1 매칭한다 (버그 1). 매칭 실패분은 남은
#                     공식 단지를 순서대로 받고, 그마저 없으면 결측(상한 없음).
# 지번에 공식 단지가 1개뿐이고 aptSeq도 1개인 대다수(9,409/9,458)는 위 두
# 분기 모두 n=1 분할이라 결과가 기존과 같다.
registry_by_key = {k: g for k, g in registry.groupby("join_key")}
reg_dong_col = pd.Series(np.nan, index=master.index)
reg_units_col = pd.Series(np.nan, index=master.index)
reg_year_col = pd.Series(np.nan, index=master.index)

for join_key, group in master.sort_values("aptSeq").groupby("join_key", sort=False):
    reg_group = registry_by_key.get(join_key)
    if reg_group is None:
        continue
    idx = group.index
    if len(reg_group) == 1:
        row = reg_group.iloc[0]
        if pd.notna(row["reg_dong"]):
            reg_dong_col.loc[idx] = largest_remainder_split(row["reg_dong"], len(idx))
        reg_units_col.loc[idx] = row["reg_units"]
        reg_year_col.loc[idx] = row["reg_year"]
    else:
        matches = match_complex_names(group["apt_name"].tolist(), reg_group["reg_name"].tolist())
        for pos, i in enumerate(idx):
            j = matches[pos]
            if j is not None:
                row = reg_group.iloc[j]
                reg_dong_col[i] = row["reg_dong"]
                reg_units_col[i] = row["reg_units"]
                reg_year_col[i] = row["reg_year"]

master["reg_dong"] = reg_dong_col.to_numpy()
master["reg_units"] = reg_units_col.to_numpy()
master["reg_year"] = reg_year_col.to_numpy()

anchors = gpd.GeoDataFrame(
    master[["aptSeq", "apt_name", "gu", "build_year", "n_deals",
            "reg_dong", "reg_units", "reg_year"]],
    geometry=gpd.points_from_xy(master["lon"], master["lat"]),
    crs="EPSG:4326",
).to_crs(METRIC_CRS)

# 상한 = 이 aptSeq에 배정된 등록 동수 그대로. 위에서 이미 지번 단위로 정확히
# 나눠 놓았으므로(이름 매칭 또는 largest_remainder_split) 여기서 다시 나눌
# 필요가 없다. 예전처럼 join_key당 aptSeq 수로 나눠 ceil하면(reg_dong=5를
# 2개가 나눌 때 ceil(2.5)=3씩 -> 합 6) 등록 동수를 초과했다 (버그 2).
capacity = anchors["reg_dong"].to_numpy()
capacity = np.where(np.isnan(capacity), np.inf, capacity)

# 단지 규모에 비례한 반경. 구조가 다른 단지를 하나의 원으로 재던 문제를 없앤다
radius = np.where(
    np.isnan(anchors["reg_dong"].to_numpy()), RADIUS_UNKNOWN,
    np.clip(RADIUS_BASE + RADIUS_K * np.sqrt(anchors["reg_dong"].to_numpy()),
            RADIUS_MIN, RADIUS_MAX))

n_capped = int(np.isfinite(capacity).sum())
print(f"  단지 앵커 {len(anchors)}개 / 등록 동수 확보 {n_capped} "
      f"({100 * n_capped / len(anchors):.1f}%)")
print(f"  상한 없는 단지 {len(anchors) - n_capped}개 (등록 정보 미매칭 — 과다 배정 가능)")
print(f"  탐색 반경 (계수 {RADIUS_K:.0f}): 중앙값 {np.median(radius):.0f}m, "
      f"최소 {radius.min():.0f}m, 최대 {radius.max():.0f}m")


# ============================================================================
# 1b. 건축물대장 동명칭
# ============================================================================

print("\n===== 1b. 건축물대장 동명칭 =====")


def normalize_dong(value):
    """'제904동' '904동' '904' -> '904'. 지번 안에서 이 키는 사실상 유일하다"""
    text = str(value).strip().upper()
    text = re.sub(r"^제", "", text)
    text = re.sub(r"동$", "", text)
    text = re.sub(r"\s+", "", text)
    return re.sub(r"^0+(?=\d)", "", text)


if LEDGER_PATH.exists():
    ledger = pd.read_csv(LEDGER_PATH, sep="\t")
    ledger = ledger[ledger["hhld_cnt"] > 0].copy()
    ledger["dong_key"] = ledger["dong_nm"].apply(normalize_dong)
    ledger_named = ledger[ledger["dong_key"] != ""]
    ledger_names = ledger_named.groupby("join_key")["dong_key"].apply(set)
    ledger_count = ledger_named.groupby("join_key").size()
    # 층수는 동명칭이 없는 단독동에도 있으므로 전체 주거동에서 모은다
    ledger_levels = ledger.groupby("join_key")["grnd_flr_cnt"].apply(
        lambda s: np.array(sorted(v for v in s if pd.notna(v))))
else:
    ledger_names, ledger_count = pd.Series(dtype=object), pd.Series(dtype=int)
    ledger_levels = pd.Series(dtype=object)
    print("  [주의] 대장이 없다. 이름 근거 없이 배정한다 (19를 먼저 돌릴 것)")

anchor_keys = master["join_key"].to_numpy()
anchor_name_set = [ledger_names.get(k, frozenset()) for k in anchor_keys]
# 대장이 등록 동수를 다 담았는지 — 금지 규칙을 적용해도 되는 단지인가
anchor_ledger_full = np.array([
    ledger_count.get(k, 0) >= (d if not np.isnan(d) else np.inf)
    for k, d in zip(anchor_keys, anchors["reg_dong"].to_numpy())
])
anchor_levels = [ledger_levels.get(k) if k in ledger_levels.index else None
                 for k in anchor_keys]
n_named = sum(1 for s in anchor_name_set if s)
print(f"  동명칭 확보 단지 {n_named}/{len(anchors)} ({100 * n_named / len(anchors):.1f}%)")
print(f"  그중 대장이 등록 동수를 다 담은 단지 {int(anchor_ledger_full.sum())} (금지 규칙 적용 대상)")
print(f"  층수 확보 단지 {sum(1 for v in anchor_levels if v is not None and len(v))} "
      f"(허용 오차 ±{LEVEL_TOLERANCE:.0f}층, 0이면 미적용)")


# ============================================================================
# 2. 아파트 동 · 단지 경계 로드
# ============================================================================

print("\n===== 2. 아파트 동 · 단지 경계 =====")


def read_wkt_layer(path, query=None):
    df = pd.read_csv(path, sep="\t")
    if query is not None:
        df = df[query(df)]
    df = df.reset_index(drop=True)
    return gpd.GeoDataFrame(
        df.drop(columns="geometry"),
        geometry=df["geometry"].apply(wkt.loads), crs="EPSG:4326",
    ).to_crs(METRIC_CRS)


buildings = read_wkt_layer(BUILDING_PATH, lambda d: d["building"] == "apartments")
boundaries = read_wkt_layer(BOUNDARY_PATH)
boundaries["boundary_id"] = np.arange(len(boundaries))

year_valid = buildings["build_year"].between(*YEAR_RANGE)
n_bad_year = int(buildings["build_year"].notna().sum() - year_valid.sum())
buildings.loc[~year_valid, "build_year"] = np.nan

too_short = buildings["height_m"] < MIN_HEIGHT_M
buildings.loc[too_short, "height_m"] = np.nan
buildings.loc[too_short, "height_source"] = "none"

print(f"  아파트 동 {len(buildings)}개 (준공년도 이상치 {n_bad_year}건, "
      f"height {MIN_HEIGHT_M}m 미만 {int(too_short.sum())}건 무효화)")
print(f"  단지 경계 폴리곤 {len(boundaries)}개")


# ============================================================================
# 3. 용량 제약 배정
# ============================================================================
# 후보 쌍 (동, 앵커, 거리)을 만들고 우선순위대로 소진한다.
#   0순위: OSM 이름이 그 앵커의 대장 동명칭 목록에 있음 (가장 강한 근거)
#   1순위: 같은 경계 폴리곤 안
#   2순위: 반경 안의 최근접
# 각 순위 안에서는 거리 오름차순. 앵커의 잔여 용량이 0이면 건너뛴다.
#
# 금지 규칙: OSM 이름이 있는데 그 앵커의 대장 목록에 없으면 쌍 자체를 버린다.
# 단 대장이 등록 동수를 다 담은 단지에만 적용한다. 여러 지번에 걸친 단지는
# 대장이 일부만 담고 있어 금지하면 자기 동까지 못 받는다.

print("\n===== 3. 용량 제약 배정 =====")

building_points = gpd.GeoDataFrame(
    buildings[["osm_id"]], geometry=buildings.geometry.centroid, crs=METRIC_CRS)


def containing_boundary(points):
    """겹치는 폴리곤이 있으면 한 점이 여러 행으로 늘어난다. 첫 번째만 쓴다"""
    joined = gpd.sjoin(points[["geometry"]], boundaries[["boundary_id", "geometry"]],
                       how="left", predicate="within")
    return joined["boundary_id"].groupby(level=0).first().reindex(points.index)


anchor_boundary = containing_boundary(anchors)
building_boundary = containing_boundary(building_points)
print(f"  경계 안 앵커 {int(anchor_boundary.notna().sum())}/{len(anchors)}, "
      f"경계 안 동 {int(building_boundary.notna().sum())}/{len(buildings)}")

anchor_xy = np.column_stack([anchors.geometry.x, anchors.geometry.y])
building_xy = np.column_stack([building_points.geometry.x, building_points.geometry.y])

# --- 후보 쌍 생성 ---
building_name_key = [
    None if pd.isna(v) else normalize_dong(v) for v in buildings["name"]]

pairs = []   # (우선순위, 거리, 동 index, 앵커 index)


building_levels = buildings["levels"].to_numpy(dtype=float)


def level_compatible(building_idx, anchor_idx):
    """이름 없는 동은 층수로라도 거른다. 대장에 비슷한 층수의 동이 하나도
    없으면 그 단지 동이 아니다. 층수 정보가 없으면 판단하지 않는다."""
    if LEVEL_TOLERANCE <= 0:
        return True
    level = building_levels[building_idx]
    theirs = anchor_levels[anchor_idx]
    if np.isnan(level) or theirs is None or not len(theirs):
        return True
    return bool(np.min(np.abs(theirs - level)) <= LEVEL_TOLERANCE)


def pair_priority(building_idx, anchor_idx, base_priority):
    """이름이 맞으면 0순위, 이름이 어긋나면 버린다.
    이름이 없으면 층수 적합성으로 거른 뒤 원래 순위를 준다."""
    name = building_name_key[building_idx]
    names = anchor_name_set[anchor_idx]
    if name is not None and names:
        if name in names:
            return 0
        return None if anchor_ledger_full[anchor_idx] else base_priority
    if level_compatible(building_idx, anchor_idx):
        return base_priority
    return PRIORITY_LEVEL_MISMATCH

anchors_by_boundary = (anchor_boundary.dropna().astype(int)
                       .reset_index().groupby("boundary_id")["index"].apply(list))
for boundary_id, building_idx in (building_boundary.dropna().astype(int)
                                  .reset_index().groupby("boundary_id")["index"]):
    candidate_idx = anchors_by_boundary.get(boundary_id)
    if not candidate_idx:
        continue
    building_idx = np.asarray(building_idx)
    candidate_idx = np.asarray(candidate_idx)
    dist = np.linalg.norm(
        building_xy[building_idx][:, None, :] - anchor_xy[candidate_idx][None, :, :], axis=2)
    for row, b in enumerate(building_idx):
        for col, a in enumerate(candidate_idx):
            priority = pair_priority(b, a, 1)
            if priority is not None:
                pairs.append((priority, dist[row, col], b, a))

# 전역 최대 반경으로 한 번 조회한 뒤 앵커별 반경으로 거른다
near_dist, near_idx = cKDTree(anchor_xy).query(
    building_xy, k=N_NEIGHBORS, distance_upper_bound=radius.max())
for b in range(len(buildings)):
    for d, a in zip(near_dist[b], near_idx[b]):
        if a >= len(anchors):
            break
        if d > radius[a]:
            continue
        priority = pair_priority(b, a, 2)
        if priority is not None:
            pairs.append((priority, d, b, a))

pairs.sort(key=lambda p: (p[0], p[1]))
print(f"  후보 쌍 {len(pairs)}개 (이름 {sum(1 for p in pairs if p[0] == 0)} / "
      f"경계 {sum(1 for p in pairs if p[0] == 1)} / 최근접 {sum(1 for p in pairs if p[0] == 2)} / "
      f"층수불일치 {sum(1 for p in pairs if p[0] == 3)})")

# --- greedy 소진 ---
owner = np.full(len(buildings), UNASSIGNED)
method = np.full(len(buildings), "none", dtype=object)
remaining = capacity.copy()
PRIORITY_NAME = {0: "ledger_name", 1: "boundary", 2: "nearest",
                 3: "level_mismatch"}

for priority, dist, b, a in pairs:
    if owner[b] != UNASSIGNED or remaining[a] <= 0:
        continue
    owner[b], method[b] = a, PRIORITY_NAME[priority]
    remaining[a] -= 1

assigned = owner != UNASSIGNED
buildings["aptSeq"] = np.where(assigned, anchors["aptSeq"].to_numpy()[owner], None)
buildings["boundary_id"] = building_boundary.to_numpy()
buildings["assign_method"] = method
# 근거가 다르면 정확도가 다르다. 20이 등급별 실측을 낸다
buildings["assign_confidence"] = np.select(
    [method == "ledger_name", method == "boundary",
     method == "nearest", method == "level_mismatch"],
    ["NAME", "HIGH", "LOW", "LEVEL_MISMATCH"], default=None)
buildings["dist_m"] = np.where(
    assigned, np.linalg.norm(building_xy - anchor_xy[owner], axis=1), np.nan)

n_assigned = int(assigned.sum())
print(f"  배정 성공: {n_assigned}/{len(buildings)} ({100 * n_assigned / len(buildings):.1f}%)")
print(f"  방식별: {pd.Series(method).value_counts().to_dict()}")
print(f"  배정 거리 중앙값: {buildings['dist_m'].median():.0f}m")
assert buildings["osm_id"].is_unique, "동 중복 배정 발생"


# ============================================================================
# 4. height 결측 대체 (같은 단지 다른 동의 중앙값)
# ============================================================================
# HANDOFF 4절 결정: 대체 플래그를 남겨 리포트에 공개한다.

print("\n===== 4. height 결측 대체 =====")

complex_median = buildings.groupby("aptSeq")["height_m"].transform("median")
needs_fill = buildings["height_m"].isna() & complex_median.notna()
buildings.loc[needs_fill, "height_m"] = complex_median[needs_fill]
buildings.loc[needs_fill, "height_source"] = "complex_median"

print(f"  단지 중앙값으로 대체: {int(needs_fill.sum())}동")
print(f"  대체 불가: {int(buildings['height_m'].isna().sum())}동")


# ============================================================================
# 5. 단지별 집계 및 교차검증
# ============================================================================

print("\n===== 5. 단지별 집계 =====")

complex_agg = (buildings[buildings["aptSeq"].notna()]
    .groupby("aptSeq")
    .agg(
        n_dong=("osm_id", "count"),
        n_with_height=("height_m", lambda s: int(s.notna().sum())),
        n_height_imputed=("height_source", lambda s: int((s == "complex_median").sum())),
        n_by_boundary=("assign_method", lambda s: int((s == "boundary").sum())),
        confidence=("assign_confidence",
                    lambda s: s.iloc[0] if s.nunique() == 1 else "MIXED"),
        max_height_m=("height_m", "max"),
        max_levels=("levels", "max"),
        osm_year_median=("build_year", "median"),
        mean_dist=("dist_m", "mean"),
    )
    .reset_index()
)
result = anchors.drop(columns="geometry").merge(complex_agg, on="aptSeq", how="left")
result["boundary_id"] = anchor_boundary.to_numpy()
result["dong_diff"] = result["n_dong"] - result["reg_dong"]

covered = result["n_dong"].notna()
coverage_rate = 100 * covered.mean()
weighted_coverage = 100 * result[covered]["n_deals"].sum() / result["n_deals"].sum()
print(f"  동을 받은 단지: {int(covered.sum())}/{len(result)} ({coverage_rate:.1f}%)")
print(f"  거래량 가중: {weighted_coverage:.1f}%")

# 등록 동수 대조 — 이것이 실제 정확도다 (17이 같은 계산을 독립 실행한다)
comparable = result.dropna(subset=["n_dong", "reg_dong"])
exact = (comparable["dong_diff"] == 0)
dong_accuracy = 100 * exact.mean() if len(comparable) else 0.0
n_over = int((comparable["dong_diff"] > 0).sum())
print(f"\n  등록 동수 대조 {len(comparable)}건: 정확 {int(exact.sum())} ({dong_accuracy:.1f}%)")
print(f"    과다 {n_over}건 / 과소 {int((comparable['dong_diff'] < 0).sum())}건")
for label in ["NAME", "HIGH", "MIXED", "LOW", "LEVEL_MISMATCH"]:
    sub = comparable[comparable["confidence"] == label]
    if len(sub):
        print(f"    {label:5s} n={len(sub):5d}  정확 {100 * (sub['dong_diff'] == 0).mean():5.1f}%  "
              f"과다 {100 * (sub['dong_diff'] > 0).mean():5.1f}%")

horizon_ready = int(result["n_with_height"].sum())
print(f"\n  horizon 투입 가능 동: {horizon_ready}/{n_assigned} "
      f"({100 * horizon_ready / max(n_assigned, 1):.1f}%)")


# ============================================================================
# 6. 판정 및 저장
# ============================================================================

print("\n===== 6. 판정 =====")
# aptSeq 단위 과다(n_over)는 capacity 상한이 구조적으로 막으므로 항상 0에
# 가깝게 나와 게이트로서 무의미하다 (실제로 초과가 나도 숨긴다 — 버그 2).
# 진짜 검사는 지번(join_key) 단위다: 한 지번에 aptSeq가 여러 개면 각자의
# capacity가 나뉘어 있어도, 그 지번에 실제 배정된 동 수의 합이 그 지번의
# 공식 등록 동수 합(registry 여러 행의 reg_dong 합)을 넘을 수 있다.
apt_join_key = result["aptSeq"].map(master.set_index("aptSeq")["join_key"])
assigned_by_jibun = result["n_dong"].fillna(0).groupby(apt_join_key).sum()
registered_by_jibun = registry.groupby("join_key")["reg_dong"].sum()
jibun_compare = registered_by_jibun.to_frame("registered").join(
    assigned_by_jibun.rename("assigned"), how="left")
jibun_compare["assigned"] = jibun_compare["assigned"].fillna(0)
n_over_jibun = int((jibun_compare["assigned"] > jibun_compare["registered"]).sum())
print(f"  지번 단위 과다 배정 검사: {len(jibun_compare)}개 지번 중 "
      f"배정 동 수 합이 등록 동수 합을 초과한 지번 {n_over_jibun}건")

checks = [
    ("등록 동수 정확 일치 >= 90%", dong_accuracy, 90.0),
    ("동을 받은 단지 >= 80%", coverage_rate, 80.0),
    ("지번 단위 과다 배정 0건", -n_over_jibun, 0.0),
]
all_passed = True
for label, value, threshold in checks:
    passed = value >= threshold
    all_passed &= passed
    shown = f"{n_over_jibun}건" if label.startswith("지번 단위 과다") else f"실측 {value:.1f}%"
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:26s} {shown}")

result.to_csv(COMPLEX_RESULT, sep="\t", index=False, lineterminator="\n")
buildings.drop(columns="geometry").to_csv(
    BUILDING_RESULT, sep="\t", index=False, lineterminator="\n")
print(f"\n단지 최종: {COMPLEX_RESULT}")
print(f"동 배정:   {BUILDING_RESULT}")
print(f"\n===== 동 배정 {'통과' if all_passed else '미통과'} =====")
