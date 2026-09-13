"""서비스 가격 셀의 시간 경계와 route 계약을 한곳에 둔다.

이 모듈의 함수는 입력 DataFrame만 다루며 파일을 읽거나 쓰지 않는다. 따라서
서비스 snapshot과 OOT 검증이 반드시 같은 가격 정제·key·fallback 규칙을 사용한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from _floor_band import FLOOR_BANDS, assign_floor_band


CELL_COLUMNS = [
    "apt_seq", "area_type", "floor_band", "n_trades_24m", "last_deal_ym",
    "last_price_manwon", "last_price_per_m2", "mean_price_per_m2_24m", "price_source",
]
ROUTE_COLUMNS = [
    "n_trades_24m", "last_deal_ym", "last_price_per_m2", "mean_price_per_m2_24m",
]


def _as_month(value: pd.Period | str) -> pd.Period:
    """월 단위 Period가 아닌 시간 단위를 route 경계에 쓰지 못하게 한다."""
    month = value if isinstance(value, pd.Period) else pd.Period(str(value), freq="M")
    if month.freqstr != "M":
        raise ValueError("as_of_month는 월 단위여야 합니다.")
    return month


def _sale_period(values: pd.Series) -> pd.PeriodIndex:
    """입력의 유효하지 않은 계약월은 NaT로 처리한다."""
    return pd.PeriodIndex(values.astype("string"), freq="M")


def _valid_sales(raw_sale: pd.DataFrame, known_complexes: set[str]) -> pd.DataFrame:
    """가격과 면적이 유효한 known complex 매매만 남기고 원본 순서를 붙인다."""
    required = {
        "aptSeq", "deal_ym", "is_cancelled", "deal_amount_manwon", "excluUseAr",
        "dealYear", "dealMonth", "dealDay", "floor",
    }
    missing = required - set(raw_sale.columns)
    if missing:
        raise ValueError(f"매매 입력에 필수 열이 없습니다: {sorted(missing)}")

    sales = raw_sale.copy().rename(columns={"aptSeq": "apt_seq"})
    sales["source_order"] = np.arange(len(sales), dtype=np.int64)
    sales["apt_seq"] = sales["apt_seq"].astype(str)
    sales["deal_period"] = _sale_period(sales["deal_ym"])
    sales["deal_amount_manwon"] = pd.to_numeric(sales["deal_amount_manwon"], errors="coerce")
    sales["excluUseAr"] = pd.to_numeric(sales["excluUseAr"], errors="coerce")
    not_cancelled = sales["is_cancelled"].astype("string").str.strip().str.lower().ne("true")
    return sales.loc[
        sales["apt_seq"].isin(known_complexes)
        & not_cancelled
        & sales["deal_amount_manwon"].gt(0)
        & sales["excluUseAr"].gt(0)
        & sales["deal_period"].notna()
    ].copy()


def latest_valid_sale_month(raw_sale: pd.DataFrame, known_complexes: set[str]) -> pd.Period:
    """서비스 입력 계약 안에서 사용할 수 있는 마지막 유효 거래월을 반환한다."""
    valid = _valid_sales(raw_sale, known_complexes)
    if valid.empty:
        raise ValueError("유효한 매매 거래가 없습니다.")
    return valid["deal_period"].max()


def clean_sale_history(
    raw_sale: pd.DataFrame,
    known_complexes: set[str],
    max_levels: pd.Series,
    as_of_month: pd.Period | str,
) -> pd.DataFrame:
    """as_of를 포함한 최근 60개월 서비스 이력만 정제한다.

    양끝 1%는 ``apt_seq×area_type`` 전체에서 각 tail ``floor(n×1%)``건을
    제거한다. target 가격은 이 함수에 전달되지 않으므로 미래 거래가 cutoff나
    snapshot에 들어갈 수 없다.
    """
    as_of = _as_month(as_of_month)
    history_start = as_of - 59
    sales = _valid_sales(raw_sale, known_complexes)
    sales = sales.loc[sales["deal_period"].between(history_start, as_of)].copy()
    if sales.empty:
        raise ValueError(f"{history_start}~{as_of}에 유효한 매매 거래가 없습니다.")

    sales["area_type"] = np.round(sales["excluUseAr"] / 3) * 3
    sales["price_per_m2"] = sales["deal_amount_manwon"] / sales["excluUseAr"]
    group_keys = ["apt_seq", "area_type"]
    group_size = sales.groupby(group_keys, observed=True)["price_per_m2"].transform("size")
    trim_count = np.floor(group_size * 0.01).astype(int)
    ascending_rank = sales.groupby(group_keys, observed=True)["price_per_m2"].rank(method="first")
    descending_rank = sales.groupby(group_keys, observed=True)["price_per_m2"].rank(method="first", ascending=False)
    sales = sales.loc[(ascending_rank > trim_count) & (descending_rank > trim_count)].copy()
    if sales.empty:
        raise ValueError("가격/m² 양끝 1% 정제 후 거래가 없습니다.")

    sales["floor_band"] = assign_floor_band(sales, max_levels)
    sales["deal_date"] = pd.to_datetime(
        dict(
            year=pd.to_numeric(sales["dealYear"], errors="coerce"),
            month=pd.to_numeric(sales["dealMonth"], errors="coerce"),
            day=pd.to_numeric(sales["dealDay"], errors="coerce"),
        ),
        errors="coerce",
    ).fillna(sales["deal_period"].dt.to_timestamp())
    if not sales["deal_period"].le(as_of).all():
        raise AssertionError("clean history에 as_of 이후 거래가 포함되었습니다.")
    return sales


def build_service_snapshot(clean_history: pd.DataFrame, as_of_month: pd.Period | str) -> pd.DataFrame:
    """정제 history에서 서비스의 최근 24개월 가격 셀을 만든다."""
    as_of = _as_month(as_of_month)
    required = {"apt_seq", "area_type", "floor_band", "deal_period", "deal_date", "source_order", "deal_ym", "deal_amount_manwon", "price_per_m2"}
    missing = required - set(clean_history.columns)
    if missing:
        raise ValueError(f"정제 history에 필수 열이 없습니다: {sorted(missing)}")
    if not clean_history["deal_period"].le(as_of).all():
        raise AssertionError("snapshot 입력에 미래 거래가 포함되었습니다.")
    recent_start = as_of - 23
    recent = clean_history.loc[clean_history["deal_period"].between(recent_start, as_of)].copy()
    if recent.empty:
        raise ValueError("최근 24개월의 정제된 매매 거래가 없습니다.")

    area_candidates = clean_history[["apt_seq", "area_type"]].drop_duplicates()
    profiled_apts = set(recent.loc[recent["floor_band"].ne("UNKNOWN"), "apt_seq"])
    candidate_rows = []
    for row in area_candidates.itertuples(index=False):
        bands = FLOOR_BANDS if row.apt_seq in profiled_apts else ["UNKNOWN"]
        candidate_rows.extend((row.apt_seq, row.area_type, band) for band in bands)
    candidates = pd.DataFrame(candidate_rows, columns=["apt_seq", "area_type", "floor_band"])
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
    cells = cells[CELL_COLUMNS].sort_values(["apt_seq", "area_type", "floor_band"], kind="stable").reset_index(drop=True)
    if cells.duplicated(["apt_seq", "area_type", "floor_band"]).any():
        raise AssertionError("서비스 snapshot key가 중복되었습니다.")
    return cells


def prepare_target_sales(
    raw_sale: pd.DataFrame,
    known_complexes: set[str],
    max_levels: pd.Series,
    target_start_month: pd.Period | str,
    target_end_month: pd.Period | str,
) -> pd.DataFrame:
    """가격 기반 제거 없이 평가 target의 유효 거래와 서비스 key를 만든다."""
    start, end = _as_month(target_start_month), _as_month(target_end_month)
    if end < start:
        raise ValueError("target 종료월이 시작월보다 앞섭니다.")
    target = _valid_sales(raw_sale, known_complexes)
    target = target.loc[target["deal_period"].between(start, end)].copy()
    target["area_type"] = np.round(target["excluUseAr"] / 3) * 3
    target["price_per_m2"] = target["deal_amount_manwon"] / target["excluUseAr"]
    target["floor_band"] = assign_floor_band(target, max_levels)
    target["deal_date"] = pd.to_datetime(
        dict(
            year=pd.to_numeric(target["dealYear"], errors="coerce"),
            month=pd.to_numeric(target["dealMonth"], errors="coerce"),
            day=pd.to_numeric(target["dealDay"], errors="coerce"),
        ),
        errors="coerce",
    ).fillna(target["deal_period"].dt.to_timestamp())
    # target에는 price outlier rule이나 cutoff를 적용하지 않는다. 이 flag는 OOT
    # caller의 hard gate에서 누수/사후 제거가 없었음을 명시적으로 확인하는 계약이다.
    target.attrs["target_price_removed_n"] = 0
    return target


def route_target(target_rows: pd.DataFrame, snapshot: pd.DataFrame) -> pd.DataFrame:
    """target key를 서비스 snapshot route에 맞춰 예측 또는 미예측으로 표시한다."""
    target_keys = {"apt_seq", "area_type", "floor_band"}
    missing = target_keys - set(target_rows.columns)
    if missing:
        raise ValueError(f"target에 서비스 key가 없습니다: {sorted(missing)}")
    if snapshot.duplicated(["apt_seq", "area_type", "floor_band"]).any():
        raise ValueError("snapshot key가 유일하지 않습니다.")
    merged = target_rows.merge(
        snapshot[["apt_seq", "area_type", "floor_band", "price_source", *ROUTE_COLUMNS]],
        on=["apt_seq", "area_type", "floor_band"],
        how="left",
        validate="many_to_one",
    )
    active_apts = set(snapshot["apt_seq"].astype(str))
    candidate_pairs = set(zip(snapshot["apt_seq"].astype(str), snapshot["area_type"]))
    exact = merged["price_source"].notna()
    active = merged["apt_seq"].astype(str).isin(active_apts)
    candidate_seen = pd.Series(
        [(apt, area) in candidate_pairs for apt, area in zip(merged["apt_seq"].astype(str), merged["area_type"])],
        index=merged.index,
        dtype=bool,
    )
    merged["active_24m"] = active
    merged["candidate_area_seen_60m"] = candidate_seen
    merged["service_route"] = np.select(
        [exact, ~active, active & ~candidate_seen],
        [merged["price_source"], "MODEL_REQUIRED", "NO_CELL_CANDIDATE"],
        default="NO_CELL_CANDIDATE",
    )
    merged["pred_price_per_m2"] = np.where(
        merged["service_route"].eq("CELL_LAST"),
        merged["last_price_per_m2"],
        np.where(merged["service_route"].eq("COMPLEX_MEAN"), merged["mean_price_per_m2_24m"], np.nan),
    )
    merged["is_scored"] = merged["service_route"].isin(["CELL_LAST", "COMPLEX_MEAN"])
    merged["unscored_reason"] = merged["service_route"].where(~merged["is_scored"], pd.NA)
    if merged.loc[merged["is_scored"], "pred_price_per_m2"].isna().any():
        raise AssertionError("서비스 cell route의 예측값이 비어 있습니다.")
    return merged.drop(columns="price_source")
