# ============================================================================
# 13.cell_coverage.py
# ============================================================================
# Author:      yjkim
# Purpose:     시세 모델의 학습 셀 커버리지를 측정한다 (plan.md R11)
# Description: 예측 단위는 `단지 x 면적타입 x 층대`다. 이 단위로 5년 실거래를 쪼갰을 때
#              표본이 남아나는지를 D2에서 확인한다. 부족하면 R11 대응 순서대로
#              floor_band 제거 -> 면적 재구간화 -> 모델 폐기로 내려간다.
#
#              면적타입 정의가 문서에 없어 후보 3개를 같이 계산해 비교한다.
#              1m 반올림은 84.5978과 84.9984를 갈라놓고, 넓은 bin은 다른 평형을 섞는다.
#              어느 쪽 손해가 작은지는 실측으로 고른다.
#
#              층대는 동의 총 층수 N이 필요하다. 건축물대장 표제부(19) 지상층수를
#              1순위 분모로 쓴다. 실측 결과(output/19.1 기준 세 값 모두 있는
#              4,757단지) 대장 지상층수 vs 최고 거래층은 ±1층 이내 일치율이
#              70.3%뿐이라 21.horizon_batch.py(OSM levels 기반)와 분모가 3층+
#              어긋나는 단지가 27.6%였다. 대장을 못 받은 단지는 기존 방식
#              (최고 거래층)으로 폴백한다 — 데이터가 없다고 셀을 버리면 학습
#              표본이 준다.
#
#              통과 기준 (R11, D2에 교체 — plan.md 9절):
#                (1) 학습 가능 셀(거래 5건 이상)이 담는 거래 비율 >= 80%
#                당초 기준 '커버 단지 60%'는 폐기했다. 거래 5건 이상인 단지가
#                57.9%뿐이라 어떤 셀 설계로도 넘을 수 없는 값이었다. 병목은 셀 분할이
#                아니라 소규모 단지의 거래 희박이므로, 학습 신호가 어디 모였는지를 잰다.
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

import numpy as np
import pandas as pd
from pathlib import Path

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

SALE_PATH = output_dir / "11.1.trades_sale.txt"
MASTER_PATH = output_dir / "14.1.geocoded_master.txt"   # aptSeq -> 지번(join_key)
LEDGER_PATH = output_dir / "19.1.building_ledger.txt"    # 지번 -> 대장 지상층수
MIN_TRADES_PER_CELL = 5     # LightGBM 이전에, 셀 대표값이 의미를 갖는 최소 표본
AREA_BIN_CANDIDATES = {"round_1m": 1, "bin_3m": 3, "bin_5m": 5}


# ============================================================================
# 1. 거래 로드
# ============================================================================

print("===== 1. 거래 로드 =====")
if not SALE_PATH.exists():
    raise SystemExit(f"{SALE_PATH} 없음. 11.collect_trades.py를 먼저 실행할 것")

sale = pd.read_csv(SALE_PATH, sep="\t", dtype={"aptSeq": str, "sggCd": str})
sale = sale[~sale["is_cancelled"].astype(bool)]     # 취소 거래는 학습 표본이 아니다
sale = sale.dropna(subset=["aptSeq", "excluUseAr", "floor"])
sale["floor"] = pd.to_numeric(sale["floor"], errors="coerce")
sale = sale[sale["floor"] > 0]                      # 지하층(-1)은 아파트 세대가 아니다

print(f"  유효 거래 {len(sale):,}건 / 단지 {sale['aptSeq'].nunique():,}개")


# ============================================================================
# 2. 층대 배정
# ============================================================================

def normalize_address(text):
    """20.verify_dong_names.py와 동일한 정규화 — 대장 join_key(지번) 포맷에 맞춘다."""
    text = text.fillna("").astype(str).str.strip()
    text = text.str.replace(r"^서울(특별시)?\s*", "", regex=True)
    text = text.str.replace(r"\s+", " ", regex=True)
    return text.str.replace(r"산\s+(?=\d)", "산", regex=True)


if not MASTER_PATH.exists() or not LEDGER_PATH.exists():
    raise SystemExit(f"{MASTER_PATH} 또는 {LEDGER_PATH} 없음. 14/19를 먼저 실행할 것")

master = pd.read_csv(MASTER_PATH, sep="\t", dtype={"aptSeq": str})
master["join_key"] = normalize_address(
    master["gu"].fillna("") + " " + master["umd_name"].fillna("")
    + " " + master["jibun"].fillna(""))
apt_to_key = master.set_index("aptSeq")["join_key"]

ledger = pd.read_csv(LEDGER_PATH, sep="\t")
ledger = ledger[ledger["hhld_cnt"] > 0]                     # 주거동만 (19 헤더 참고)
ledger_top_by_key = ledger.groupby("join_key")["grnd_flr_cnt"].max()

# 1순위: 대장 지상층수(join_key로 매칭). 2순위(폴백): 최고 거래층(기존 방식)
trade_top_by_apt = sale.groupby("aptSeq")["floor"].max()
ledger_top_by_apt = apt_to_key.reindex(sale["aptSeq"].unique()).map(ledger_top_by_key)
top_floor = ledger_top_by_apt.combine_first(trade_top_by_apt).rename("top_floor")

used_ledger = ledger_top_by_apt.notna()
print(f"  분모 소스: 대장 지상층수 {used_ledger.sum():,}단지 / "
      f"최고거래층 폴백 {(~used_ledger).sum():,}단지")

sale = sale.join(top_floor, on="aptSeq")

ratio = sale["floor"] / sale["top_floor"]
sale["floor_band"] = np.where(ratio <= 1 / 3, "LOW",
                              np.where(ratio <= 2 / 3, "MID", "HIGH"))

print("\n===== 2. 층대 분포 =====")
print(sale["floor_band"].value_counts().rename("n").to_string())


# ============================================================================
# 3. 면적타입 후보별 커버리지
# ============================================================================

def coverage(frame, keys):
    cell = frame.groupby(keys).size().rename("n_trades").reset_index()
    usable = cell[cell["n_trades"] >= MIN_TRADES_PER_CELL]
    return {
        "n_cells": len(cell),
        "median_trades": cell["n_trades"].median(),
        "single_trade_pct": (cell["n_trades"] == 1).mean() * 100,
        "usable_cell_pct": len(usable) / len(cell) * 100,
        "usable_trade_pct": usable["n_trades"].sum() / cell["n_trades"].sum() * 100,
        "covered_complex_pct": usable["aptSeq"].nunique() / frame["aptSeq"].nunique() * 100,
    }


print("\n===== 3. 면적타입 후보 비교 =====")
rows = []
for label, width in AREA_BIN_CANDIDATES.items():
    sale[label] = (sale["excluUseAr"] / width).round() * width
    with_band = coverage(sale, ["aptSeq", label, "floor_band"])
    without_band = coverage(sale, ["aptSeq", label])
    rows.append({"area_type": label, "floor_band": "있음", **with_band})
    rows.append({"area_type": label, "floor_band": "없음", **without_band})

compare = pd.DataFrame(rows)
print(compare.round(1).to_string(index=False))


# ============================================================================
# 4. 저장 및 판정
# ============================================================================

compare_path = output_dir / "13.1.cell_coverage.txt"
compare.to_csv(compare_path, sep="\t", index=False, lineterminator="\n")

print("\n===== 4. 판정 (확정안: bin_3m x floor_band 있음) =====")
baseline = compare.query("area_type == 'bin_3m' and floor_band == '있음'").iloc[0]

passed = baseline["usable_trade_pct"] >= 80
print(f"  [{'PASS' if passed else 'FAIL'}] 5건+ 셀의 거래 점유율 >= 80%   "
      f"실측 {baseline['usable_trade_pct']:.1f}%")
print(f"  [참고] 학습 가능 셀 {baseline['usable_cell_pct']:.1f}% / "
      f"커버 단지 {baseline['covered_complex_pct']:.1f}%")

no_band = compare.query("area_type == 'bin_3m' and floor_band == '없음'").iloc[0]
gain = no_band["usable_cell_pct"] - baseline["usable_cell_pct"]
print(f"\n  floor_band 제거 시 학습 가능 셀 비율 {gain:+.1f}%p")
print("  ※ FAIL이면 R11 순서대로: floor_band 제거 -> 면적 재구간화 -> 모델 폐기")

print(f"\n비교표: {compare_path}")
