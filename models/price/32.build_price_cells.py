# ============================================================================
# 32.build_price_cells.py
# ============================================================================
# Author:      yjkim
# Purpose:     단지×면적타입×층대의 최근 매매 가격 셀과 5년 가격 시계열을 만든다.
# Description: 최근 24개월의 같은 셀 실거래를 우선 사용하고, 셀 거래가 없으면
#              같은 면적의 다른 층대 최근 거래를 우선하고, 없으면 단지 평균으로
#              보완한다. 최근 거래가 전혀 없는
#              단지는 cold-start 모델(Task 2)의 대상이므로 이 산출물에 넣지 않는다.
#
#              층대는 15.1.complex_final의 실제 max_levels를 사용해 최고층의
#              1/3 이하 LOW, 2/3 이하 MID, 초과 HIGH로 구분한다.
# ============================================================================

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from _floor_band import load_actual_max_levels
from _price_router import (
    CELL_COLUMNS,
    build_service_snapshot,
    clean_sale_history,
    latest_valid_sale_month,
)


work_dir = Path(__file__).resolve().parents[2]
output_dir = work_dir / "output"

TRADES_PATH = output_dir / "11.1.trades_sale.txt"
COMPLEX_PATH = output_dir / "23.1.complex.txt"
PROFILE_PATH = output_dir / "23.3.horizon_profile.txt"
COMPLEX_FINAL_PATH = output_dir / "15.1.complex_final.txt"
CELLS_PATH = output_dir / "32.1.price_cells.txt"
SERIES_PATH = output_dir / "32.2.price_series.txt"

SERIES_COLUMNS = ["apt_seq", "area_type", "deal_ym", "n_trades", "median_price_per_m2"]


def require_unique(frame: pd.DataFrame, keys: list[str], label: str) -> pd.DataFrame:
    """입력 key 중복을 먼저 막아 이후 집계와 merge의 의미를 보존한다."""
    if frame.duplicated(keys).any():
        raise ValueError(f"{label}: {'×'.join(keys)} key가 유일하지 않습니다.")
    return frame


def load_and_clean_trades() -> tuple[pd.DataFrame, pd.Period, pd.Period]:
    """공통 router로 최신 서비스 history를 정제한다."""
    raw_trades = pd.read_csv(TRADES_PATH, sep="\t", low_memory=False)
    complex_df = require_unique(pd.read_csv(COMPLEX_PATH, sep="\t", low_memory=False), ["apt_seq"], "complex")
    profile = pd.read_csv(PROFILE_PATH, sep="\t", low_memory=False)
    require_unique(profile, ["apt_seq", "floor_band"], "horizon_profile")
    complex_final = pd.read_csv(COMPLEX_FINAL_PATH, sep="\t", low_memory=False).rename(columns={"aptSeq": "apt_seq"})
    known_complexes = set(complex_df["apt_seq"].astype(str))
    latest_month = latest_valid_sale_month(raw_trades, known_complexes)
    series_start = latest_month - 59
    trades = clean_sale_history(raw_trades, known_complexes, load_actual_max_levels(complex_final), latest_month)
    print(f"  공통 router 정제 거래: {len(trades):,}건")
    print(f"  기준월: {latest_month}, 최근 24개월 시작월: {latest_month - 23}, 최근 60개월 시작월: {series_start}")
    return trades, latest_month, series_start


def build_price_cells(trades: pd.DataFrame, latest_month: pd.Period) -> pd.DataFrame:
    """공통 router snapshot을 32.1의 기존 schema로 반환한다."""
    return build_service_snapshot(trades, latest_month)


def build_price_series(trades: pd.DataFrame) -> pd.DataFrame:
    """층대를 합친 단지×면적타입×월 중앙 가격/m² 시계열을 만든다."""
    series = (
        trades.groupby(["apt_seq", "area_type", "deal_ym"], observed=True)["price_per_m2"]
        .agg(n_trades="size", median_price_per_m2="median")
        .reset_index()
    )
    return series[SERIES_COLUMNS].sort_values(["apt_seq", "area_type", "deal_ym"], kind="stable").reset_index(drop=True)


def assert_frame_exact(expected: pd.DataFrame, actual: pd.DataFrame, label: str) -> None:
    """설계 1 gate 1: schema·순서·문자열/정수·float을 엄격히 대조한다."""
    if list(expected.columns) != list(actual.columns):
        raise AssertionError(f"{label}: 열 schema 또는 순서가 다릅니다.")
    if len(expected) != len(actual):
        raise AssertionError(f"{label}: 행 수가 다릅니다 ({len(expected)} != {len(actual)}).")
    for column in expected.columns:
        left, right = expected[column], actual[column]
        if pd.api.types.is_float_dtype(left) or pd.api.types.is_float_dtype(right):
            equal = np.isclose(left.to_numpy(dtype=float), right.to_numpy(dtype=float), rtol=1e-12, atol=1e-12, equal_nan=True)
        else:
            equal = left.fillna("<NA>").astype(str).to_numpy() == right.fillna("<NA>").astype(str).to_numpy()
        if not np.all(equal):
            mismatch = int(np.flatnonzero(~equal)[0])
            raise AssertionError(f"{label}: {column} {mismatch}번째 행이 다릅니다.")


def print_validation(cells: pd.DataFrame, series: pd.DataFrame, trades: pd.DataFrame,
                     latest_month: pd.Period, series_start: pd.Period) -> None:
    """산출물 계약과 화면 사용 범위를 자체 검증한다."""
    print("\n===== 4. 자체 검증 =====")
    active_apts = set(trades.loc[trades["deal_period"].between(latest_month - 23, latest_month), "apt_seq"])
    key_unique = not cells.duplicated(["apt_seq", "area_type", "floor_band"]).any()
    cell_last_has_price = cells.loc[cells["price_source"].eq("CELL_LAST"), "last_price_manwon"].notna().all()
    area_last_has_evidence = (
        cells.loc[cells["price_source"].eq("AREA_LAST"), ["last_deal_ym", "last_price_manwon", "last_price_per_m2", "area_last_floor_band"]]
        .notna().all().all()
    )
    series_periods = pd.PeriodIndex(series["deal_ym"].astype(str), freq="M")
    series_in_range = ((series_periods >= series_start) & (series_periods <= latest_month)).all()
    cells_are_active = set(cells["apt_seq"]).issubset(active_apts)
    source_valid = set(cells["price_source"]).issubset({"CELL_LAST", "AREA_LAST", "COMPLEX_MEAN"})

    checks = [
        ("apt_seq×area_type×floor_band key 유일", key_unique, f"{len(cells):,}행"),
        ("CELL_LAST의 last_price_manwon 결측 0건", cell_last_has_price,
         f"결측 {cells.loc[cells['price_source'].eq('CELL_LAST'), 'last_price_manwon'].isna().sum():,}건"),
        ("AREA_LAST의 거래월·가격·근거 층대 결측 0건", area_last_has_evidence,
         f"결측 {cells.loc[cells['price_source'].eq('AREA_LAST'), ['last_deal_ym', 'last_price_manwon', 'last_price_per_m2', 'area_last_floor_band']].isna().any(axis=1).sum():,}건"),
        ("32.2 deal_ym이 최근 60개월 안", series_in_range,
         f"{series_periods.min()} ~ {series_periods.max()}"),
        ("32.1 단지가 최근 24개월 거래 단지에 포함", cells_are_active,
         f"32.1 {cells['apt_seq'].nunique():,}단지 / 최근 거래 {len(active_apts):,}단지"),
        ("price_source 값이 CELL_LAST·AREA_LAST·COMPLEX_MEAN", source_valid, "MODEL은 Task 2에서만 추가"),
    ]
    for label, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}: {detail}")

    outside_cutoff = cells["mean_price_per_m2_24m"].notna() & ~cells["mean_price_per_m2_24m"].between(548, 4903)
    print(f"\n  참고: mean_price_per_m2_24m 548~4,903만원 범위 밖: {outside_cutoff.sum():,}행 (제거하지 않음)")
    print("  참고: price_source 분포")
    print(cells["price_source"].value_counts(dropna=False).to_string())
    print("  참고: floor_band 분포")
    print(cells["floor_band"].value_counts(dropna=False).to_string())
    print(f"  참고: 셀 수 {len(cells):,}행 / {cells['apt_seq'].nunique():,}단지")

    assert all(passed for _, passed, _ in checks), "자체 검증 실패: 위 [FAIL] 항목을 확인하십시오."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="최근 매매 가격 셀과 60개월 가격 시계열을 생성합니다.")
    parser.add_argument(
        "--verify-against-existing",
        action="store_true",
        help="기존 32.1/32.2 산출물과 완전히 일치하는지 저장 전에 검증합니다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    previous_cells: pd.DataFrame | None = None
    previous_series: pd.DataFrame | None = None
    if args.verify_against_existing:
        if not CELLS_PATH.exists() or not SERIES_PATH.exists():
            raise FileNotFoundError("--verify-against-existing에는 기존 32.1/32.2 산출물이 필요합니다.")
        previous_cells = pd.read_csv(CELLS_PATH, sep="\t", low_memory=False)
        previous_series = pd.read_csv(SERIES_PATH, sep="\t", low_memory=False)
    print("===== 1. 입력 및 정제 =====")
    trades, latest_month, series_start = load_and_clean_trades()

    print("\n===== 2. 최근 24개월 가격 셀 =====")
    cells = build_price_cells(trades, latest_month)

    print("\n===== 3. 최근 60개월 가격 시계열 =====")
    series = build_price_series(trades)
    if args.verify_against_existing:
        assert previous_cells is not None and previous_series is not None
        assert_frame_exact(previous_cells, cells, "32.1 기존 산출물")
        assert_frame_exact(previous_series, series, "32.2 기존 산출물")
        print("  기존 산출물 완전 일치 검증: PASS")
    cells.to_csv(CELLS_PATH, sep="\t", index=False)
    print(f"  저장: {CELLS_PATH.relative_to(work_dir)} ({len(cells):,}행)")
    series.to_csv(SERIES_PATH, sep="\t", index=False)
    print(f"  저장: {SERIES_PATH.relative_to(work_dir)} ({len(series):,}행)")

    print_validation(cells, series, trades, latest_month, series_start)


if __name__ == "__main__":
    main()
