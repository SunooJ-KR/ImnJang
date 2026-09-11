# ============================================================================
# 32.build_price_cells.py
# ============================================================================
# Author:      yjkim
# Purpose:     단지×면적타입×층대의 최근 매매 가격 셀과 5년 가격 시계열을 만든다.
# Description: 최근 24개월의 같은 셀 실거래를 우선 사용하고, 셀 거래가 없으면
#              같은 단지의 최근 거래 평균으로만 보완한다. 최근 거래가 전혀 없는
#              단지는 cold-start 모델(Task 2)의 대상이므로 이 산출물에 넣지 않는다.
#
#              층대는 23.3.horizon_profile의 HIGH repr_floor를 단지 최고층 proxy로
#              사용해 최고층의 1/3 이하 LOW, 2/3 이하 MID, 초과 HIGH로 구분한다.
# ============================================================================

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd


work_dir = Path(__file__).resolve().parents[2]
output_dir = work_dir / "output"

TRADES_PATH = output_dir / "11.1.trades_sale.txt"
COMPLEX_PATH = output_dir / "23.1.complex.txt"
PROFILE_PATH = output_dir / "23.3.horizon_profile.txt"
CELLS_PATH = output_dir / "32.1.price_cells.txt"
SERIES_PATH = output_dir / "32.2.price_series.txt"

CELL_COLUMNS = [
    "apt_seq", "area_type", "floor_band", "n_trades_24m", "last_deal_ym",
    "last_price_manwon", "last_price_per_m2", "mean_price_per_m2_24m", "price_source",
]
SERIES_COLUMNS = ["apt_seq", "area_type", "deal_ym", "n_trades", "median_price_per_m2"]
PROFILE_BANDS = ["LOW", "MID", "HIGH"]


def require_unique(frame: pd.DataFrame, keys: list[str], label: str) -> pd.DataFrame:
    """입력 key 중복을 먼저 막아 이후 집계와 merge의 의미를 보존한다."""
    if frame.duplicated(keys).any():
        raise ValueError(f"{label}: {'×'.join(keys)} key가 유일하지 않습니다.")
    return frame


def build_floor_proxy(profile: pd.DataFrame) -> pd.Series:
    """단지별 HIGH repr_floor를 최고층 proxy로 반환한다."""
    high_proxy = profile.loc[profile["floor_band"].eq("HIGH"), ["apt_seq", "repr_floor"]].copy()
    high_proxy["repr_floor"] = pd.to_numeric(high_proxy["repr_floor"], errors="coerce")
    high_proxy = require_unique(high_proxy, ["apt_seq"], "horizon_profile HIGH")
    return high_proxy.set_index("apt_seq")["repr_floor"]


def assign_floor_band(trades: pd.DataFrame, high_floor_proxy: pd.Series) -> pd.Series:
    """HIGH repr_floor proxy의 삼등분 규칙으로 거래 층을 LOW/MID/HIGH로 구분한다."""
    floor = pd.to_numeric(trades["floor"], errors="coerce")
    max_floor = trades["apt_seq"].map(high_floor_proxy)
    has_proxy = max_floor.notna() & max_floor.gt(0)
    return pd.Series(
        np.select(
            [has_proxy & floor.le(max_floor / 3),
             has_proxy & floor.gt(max_floor / 3) & floor.le(max_floor * 2 / 3),
             has_proxy & floor.gt(max_floor * 2 / 3)],
            ["LOW", "MID", "HIGH"],
            default="UNKNOWN",
        ),
        index=trades.index,
        dtype="string",
    )


def load_and_clean_trades() -> tuple[pd.DataFrame, pd.Period, pd.Period]:
    """취소·명백한 입력 오류와 단지×면적타입 가격/m² 양끝 1%를 제거한다."""
    trades = pd.read_csv(TRADES_PATH, sep="\t", low_memory=False).rename(columns={"aptSeq": "apt_seq"})
    complex_df = require_unique(pd.read_csv(COMPLEX_PATH, sep="\t", low_memory=False), ["apt_seq"], "complex")
    profile = pd.read_csv(PROFILE_PATH, sep="\t", low_memory=False)
    require_unique(profile, ["apt_seq", "floor_band"], "horizon_profile")

    # complex는 입력 계약의 단지 universe를 명시적으로 제한하는 데만 사용한다.
    known_complexes = set(complex_df["apt_seq"].astype(str))
    trades["apt_seq"] = trades["apt_seq"].astype(str)
    trades["deal_period"] = pd.PeriodIndex(trades["deal_ym"].astype(str), freq="M")
    trades["deal_amount_manwon"] = pd.to_numeric(trades["deal_amount_manwon"], errors="coerce")
    trades["excluUseAr"] = pd.to_numeric(trades["excluUseAr"], errors="coerce")
    not_cancelled = trades["is_cancelled"].astype("string").str.strip().str.lower().ne("true")
    trades = trades.loc[
        trades["apt_seq"].isin(known_complexes)
        & not_cancelled
        & trades["deal_amount_manwon"].gt(0)
        & trades["excluUseAr"].gt(0)
        & trades["deal_period"].notna()
    ].copy()
    if trades.empty:
        raise ValueError("유효한 매매 거래가 없습니다.")

    latest_month = trades["deal_period"].max()
    series_start = latest_month - 59
    trades = trades.loc[trades["deal_period"].between(series_start, latest_month)].copy()
    trades["area_type"] = np.round(trades["excluUseAr"] / 3) * 3
    trades["price_per_m2"] = trades["deal_amount_manwon"] / trades["excluUseAr"]

    # 작은 group에 보간 분위수를 그대로 적용하면 n=2에서 양 끝 두 거래가 모두
    # 사라진다. 각 tail의 제거 수를 floor(n×1%)로 정의해 실제 상·하위 1%만 뺀다.
    group_keys = ["apt_seq", "area_type"]
    group_size = trades.groupby(group_keys, observed=True)["price_per_m2"].transform("size")
    trim_count = np.floor(group_size * 0.01).astype(int)
    ascending_rank = trades.groupby(group_keys, observed=True)["price_per_m2"].rank(method="first")
    descending_rank = trades.groupby(group_keys, observed=True)["price_per_m2"].rank(method="first", ascending=False)
    before_outlier = len(trades)
    trades = trades.loc[(ascending_rank > trim_count) & (descending_rank > trim_count)].copy()
    if trades.empty:
        raise ValueError("가격/m² 양끝 1% 정제 후 거래가 없습니다.")

    high_floor_proxy = build_floor_proxy(profile)
    trades["floor_band"] = assign_floor_band(trades, high_floor_proxy)
    trades["deal_date"] = pd.to_datetime(
        dict(
            year=pd.to_numeric(trades["dealYear"], errors="coerce"),
            month=pd.to_numeric(trades["dealMonth"], errors="coerce"),
            day=pd.to_numeric(trades["dealDay"], errors="coerce"),
        ),
        errors="coerce",
    ).fillna(trades["deal_period"].dt.to_timestamp())
    trades["source_order"] = np.arange(len(trades))

    print(f"  유효 거래: {before_outlier:,}건 -> 양끝 1% 정제: {len(trades):,}건 "
          f"(제거 {before_outlier - len(trades):,}건)")
    print(f"  기준월: {latest_month}, 최근 24개월 시작월: {latest_month - 23}, 최근 60개월 시작월: {series_start}")
    return trades, latest_month, series_start


def build_price_cells(trades: pd.DataFrame, latest_month: pd.Period) -> pd.DataFrame:
    """최근 거래 단지에 대해 실제 셀 또는 단지 평균 가격 셀을 만든다."""
    recent_start = latest_month - 23
    recent = trades.loc[trades["deal_period"].between(recent_start, latest_month)].copy()
    if recent.empty:
        raise ValueError("최근 24개월의 정제된 매매 거래가 없습니다.")

    # 최근 60개월에 관측된 면적타입을 화면의 후보 면적으로 쓰며, profile이 있는
    # 단지는 세 층대를 모두 만든다. 따라서 해당 층대 거래가 없으면 단지 평균 fallback이 된다.
    area_candidates = trades[["apt_seq", "area_type"]].drop_duplicates()
    profiled_apts = set(recent.loc[recent["floor_band"].ne("UNKNOWN"), "apt_seq"])
    candidate_rows = []
    for row in area_candidates.itertuples(index=False):
        bands = PROFILE_BANDS if row.apt_seq in profiled_apts else ["UNKNOWN"]
        candidate_rows.extend((row.apt_seq, row.area_type, band) for band in bands)
    candidates = pd.DataFrame(candidate_rows, columns=["apt_seq", "area_type", "floor_band"])

    # 최근 거래가 있는 단지만 남긴다. 그 외 단지는 Task 2의 MODEL 대상이다.
    active_apts = set(recent["apt_seq"])
    candidates = candidates.loc[candidates["apt_seq"].isin(active_apts)].copy()

    cell_stats = (
        recent.sort_values(["deal_date", "source_order"])
        .groupby(["apt_seq", "area_type", "floor_band"], observed=True)
        .agg(
            n_trades_24m=("price_per_m2", "size"),
            last_deal_ym=("deal_ym", "last"),
            last_price_manwon=("deal_amount_manwon", "last"),
            last_price_per_m2=("price_per_m2", "last"),
            cell_mean_price_per_m2_24m=("price_per_m2", "mean"),
        )
        .reset_index()
    )
    complex_mean = recent.groupby("apt_seq", observed=True)["price_per_m2"].mean().rename("complex_mean_price_per_m2_24m")

    cells = candidates.merge(cell_stats, on=["apt_seq", "area_type", "floor_band"], how="left", validate="one_to_one")
    cells = cells.join(complex_mean, on="apt_seq", validate="many_to_one")
    has_cell_trade = cells["n_trades_24m"].notna()
    cells["n_trades_24m"] = cells["n_trades_24m"].fillna(0).astype(int)
    cells["mean_price_per_m2_24m"] = cells["cell_mean_price_per_m2_24m"].where(
        has_cell_trade, cells["complex_mean_price_per_m2_24m"]
    )
    cells["price_source"] = np.where(has_cell_trade, "CELL_LAST", "COMPLEX_MEAN")
    cells = cells.drop(columns=["cell_mean_price_per_m2_24m", "complex_mean_price_per_m2_24m"])
    return cells[CELL_COLUMNS].sort_values(["apt_seq", "area_type", "floor_band"], kind="stable").reset_index(drop=True)


def build_price_series(trades: pd.DataFrame) -> pd.DataFrame:
    """층대를 합친 단지×면적타입×월 중앙 가격/m² 시계열을 만든다."""
    series = (
        trades.groupby(["apt_seq", "area_type", "deal_ym"], observed=True)["price_per_m2"]
        .agg(n_trades="size", median_price_per_m2="median")
        .reset_index()
    )
    return series[SERIES_COLUMNS].sort_values(["apt_seq", "area_type", "deal_ym"], kind="stable").reset_index(drop=True)


def print_validation(cells: pd.DataFrame, series: pd.DataFrame, trades: pd.DataFrame,
                     latest_month: pd.Period, series_start: pd.Period) -> None:
    """산출물 계약과 화면 사용 범위를 자체 검증한다."""
    print("\n===== 4. 자체 검증 =====")
    active_apts = set(trades.loc[trades["deal_period"].between(latest_month - 23, latest_month), "apt_seq"])
    key_unique = not cells.duplicated(["apt_seq", "area_type", "floor_band"]).any()
    cell_last_has_price = cells.loc[cells["price_source"].eq("CELL_LAST"), "last_price_manwon"].notna().all()
    series_periods = pd.PeriodIndex(series["deal_ym"].astype(str), freq="M")
    series_in_range = ((series_periods >= series_start) & (series_periods <= latest_month)).all()
    cells_are_active = set(cells["apt_seq"]).issubset(active_apts)
    source_valid = set(cells["price_source"]).issubset({"CELL_LAST", "COMPLEX_MEAN"})

    checks = [
        ("apt_seq×area_type×floor_band key 유일", key_unique, f"{len(cells):,}행"),
        ("CELL_LAST의 last_price_manwon 결측 0건", cell_last_has_price,
         f"결측 {cells.loc[cells['price_source'].eq('CELL_LAST'), 'last_price_manwon'].isna().sum():,}건"),
        ("32.2 deal_ym이 최근 60개월 안", series_in_range,
         f"{series_periods.min()} ~ {series_periods.max()}"),
        ("32.1 단지가 최근 24개월 거래 단지에 포함", cells_are_active,
         f"32.1 {cells['apt_seq'].nunique():,}단지 / 최근 거래 {len(active_apts):,}단지"),
        ("price_source 값이 CELL_LAST 또는 COMPLEX_MEAN", source_valid, "MODEL은 Task 2에서만 추가"),
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


def main() -> None:
    print("===== 1. 입력 및 정제 =====")
    trades, latest_month, series_start = load_and_clean_trades()

    print("\n===== 2. 최근 24개월 가격 셀 =====")
    cells = build_price_cells(trades, latest_month)
    cells.to_csv(CELLS_PATH, sep="\t", index=False)
    print(f"  저장: {CELLS_PATH.relative_to(work_dir)} ({len(cells):,}행)")

    print("\n===== 3. 최근 60개월 가격 시계열 =====")
    series = build_price_series(trades)
    series.to_csv(SERIES_PATH, sep="\t", index=False)
    print(f"  저장: {SERIES_PATH.relative_to(work_dir)} ({len(series):,}행)")

    print_validation(cells, series, trades, latest_month, series_start)


if __name__ == "__main__":
    main()
