# ============================================================================
# 34.build_comparables.py
# ============================================================================
# Author:      yjkim
# Purpose:     실제 매매 거래를 환경이 유사한 인근 단지의 비교사례로 제시한다.
# Description: 가격을 예측하지 않는다. 가격 셀과 cold-start 추정의 단지×면적타입마다
#              최근 24개월 거래가 있는 다른 단지를 면적·연식·규모·거리 순으로 제한한 뒤,
#              환경 feature 유사도가 높은 실제 거래를 최대 10건 제시한다.
#              각 거래는 해당 자치구의 월별 실거래 중앙값 index로 최신월까지
#              보정하며, 보정폭이 20%를 넘는 거래는 화면에 쓰지 않는다.
# ============================================================================

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree


work_dir = Path(__file__).resolve().parents[2]
output_dir = work_dir / "output"

TRADES_PATH = output_dir / "11.1.trades_sale.txt"
COMPLEX_PATH = output_dir / "23.1.complex.txt"
METRICS_PATH = output_dir / "23.2.complex_metrics.txt"
CELLS_PATH = output_dir / "32.1.price_cells.txt"
COLDSTART_PATH = output_dir / "33.1.coldstart_estimates.txt"
RESULT_PATH = output_dir / "34.1.comparables.txt"

RESULT_COLUMNS = [
    "apt_seq", "area_type", "target_source", "rank", "comp_apt_seq", "comp_name", "comp_deal_ym",
    "comp_price_manwon", "comp_price_per_m2", "adj_price_per_m2", "adj_reason", "dist_m",
]
FEATURE_GROUPS = {
    "교통": ["station_dist_m"],
    "교육": ["elem_school_m"],
    "공원": ["park_m"],
    "상권": ["cvs_500m", "restaurant_500m"],
    "일조·조망": ["sun_hours_avg", "view_open_avg", "river_view_ratio"],
}
RADIUS_M = 3_000.0
MAX_COMPARABLES = 10
MAX_ADJUSTMENT = 0.20


def require_unique(frame: pd.DataFrame, keys: list[str], label: str) -> pd.DataFrame:
    """입력 key가 유일한지 확인해 조인으로 행이 불어나지 않게 한다."""
    if frame.duplicated(keys).any():
        raise ValueError(f"{label}: {'×'.join(keys)} key가 유일하지 않습니다.")
    return frame


def month_period(values: pd.Series) -> pd.PeriodIndex:
    """CSV의 정수·실수형 YYYYMM을 월 Period로 안전하게 변환한다."""
    numeric = pd.to_numeric(values, errors="coerce").astype("Int64")
    return pd.PeriodIndex(numeric.astype("string"), freq="M")


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.Index]:
    """단지·환경·가격 셀·cold-start 셀을 읽고 비교 대상 단위로 정리한다."""
    complex_df = pd.read_csv(COMPLEX_PATH, sep="\t", low_memory=False)
    metrics = pd.read_csv(METRICS_PATH, sep="\t", low_memory=False)
    price_cells = pd.read_csv(
        CELLS_PATH, sep="\t", usecols=["apt_seq", "area_type"], low_memory=False,
    )
    coldstart_cells = pd.read_csv(
        COLDSTART_PATH, sep="\t", usecols=["apt_seq", "area_type"], low_memory=False,
    )
    require_unique(complex_df, ["apt_seq"], "complex")
    require_unique(metrics, ["apt_seq"], "complex_metrics")

    complex_df["apt_seq"] = complex_df["apt_seq"].astype(str)
    metrics["apt_seq"] = metrics["apt_seq"].astype(str)
    for target_cells, source in ((price_cells, "CELL"), (coldstart_cells, "COLDSTART")):
        target_cells["apt_seq"] = target_cells["apt_seq"].astype(str)
        target_cells["area_type"] = pd.to_numeric(target_cells["area_type"], errors="coerce")
        target_cells.dropna(subset=["area_type"], inplace=True)
        target_cells.drop_duplicates(["apt_seq", "area_type"], inplace=True)
        target_cells["target_source"] = source

    overlapping_apts = set(price_cells["apt_seq"]).intersection(coldstart_cells["apt_seq"])
    if overlapping_apts:
        raise ValueError(f"32.1과 33.1의 대상 단지가 겹칩니다: {len(overlapping_apts):,}개")
    targets = pd.concat([price_cells, coldstart_cells], ignore_index=True)

    numeric_complex = ["lat", "lng", "built_year", "total_households"]
    for column in numeric_complex:
        complex_df[column] = pd.to_numeric(complex_df[column], errors="coerce")
    for features in FEATURE_GROUPS.values():
        for column in features:
            metrics[column] = pd.to_numeric(metrics[column], errors="coerce")

    complexes = complex_df.merge(metrics[["apt_seq", *sum(FEATURE_GROUPS.values(), [])]],
                                on="apt_seq", how="left", validate="one_to_one")
    known = set(complexes["apt_seq"])
    missing_targets = sorted(set(targets["apt_seq"]) - known)
    if missing_targets:
        raise ValueError(f"비교 대상 중 단지 기본 테이블에 없는 apt_seq가 {len(missing_targets):,}개 있습니다.")
    if targets.empty:
        raise ValueError("가격 셀·cold-start 셀과 단지 기본 테이블에 공통 apt_seq가 없습니다.")
    return complexes, targets, pd.Index(price_cells["apt_seq"].unique())


def load_recent_trades(known_complexes: set[str]) -> tuple[pd.DataFrame, pd.Period]:
    """취소·비정상 거래를 제외하고 최신월 기준 최근 24개월 실제 매매만 남긴다."""
    trades = pd.read_csv(TRADES_PATH, sep="\t", low_memory=False).rename(columns={"aptSeq": "apt_seq"})
    trades["apt_seq"] = trades["apt_seq"].astype(str)
    trades["deal_period"] = month_period(trades["deal_ym"])
    for column in ["excluUseAr", "deal_amount_manwon"]:
        trades[column] = pd.to_numeric(trades[column], errors="coerce")
    not_cancelled = trades["is_cancelled"].astype("string").str.strip().str.lower().ne("true")
    trades = trades.loc[
        trades["apt_seq"].isin(known_complexes)
        & not_cancelled
        & trades["deal_period"].notna()
        & trades["excluUseAr"].gt(0)
        & trades["deal_amount_manwon"].gt(0)
        & trades["gu"].notna()
    ].copy()
    if trades.empty:
        raise ValueError("유효한 매매 거래가 없습니다.")

    latest_month = trades["deal_period"].max()
    trades = trades.loc[trades["deal_period"].between(latest_month - 23, latest_month)].copy()
    trades["area_type"] = np.round(trades["excluUseAr"] / 3) * 3
    trades["price_per_m2"] = trades["deal_amount_manwon"] / trades["excluUseAr"]
    trades["deal_date"] = pd.to_datetime(
        dict(
            year=pd.to_numeric(trades["dealYear"], errors="coerce"),
            month=pd.to_numeric(trades["dealMonth"], errors="coerce"),
            day=pd.to_numeric(trades["dealDay"], errors="coerce"),
        ),
        errors="coerce",
    ).fillna(trades["deal_period"].dt.to_timestamp())
    trades["source_order"] = np.arange(len(trades))
    if trades.empty:
        raise ValueError("최근 24개월의 유효한 매매 거래가 없습니다.")
    return trades, latest_month


def attach_district_index(trades: pd.DataFrame, latest_month: pd.Period) -> pd.DataFrame:
    """자치구×월 중앙 거래단가 index로 각 실제 거래를 기준월 가격으로 환산한다."""
    monthly = (
        trades.groupby(["gu", "deal_period"], observed=True)["price_per_m2"]
        .median()
        .rename("district_month_price_per_m2")
        .reset_index()
    )
    base = monthly.loc[monthly["deal_period"].eq(latest_month), ["gu", "district_month_price_per_m2"]]
    base = base.rename(columns={"district_month_price_per_m2": "district_base_price_per_m2"})
    if base["gu"].duplicated().any():
        raise ValueError("기준월 자치구 index가 유일하지 않습니다.")
    indexed = trades.merge(monthly, on=["gu", "deal_period"], how="left", validate="many_to_one")
    indexed = indexed.merge(base, on="gu", how="left", validate="many_to_one")
    indexed["adjustment_factor"] = (
        indexed["district_base_price_per_m2"] / indexed["district_month_price_per_m2"]
    )
    missing_index = indexed["adjustment_factor"].isna().sum()
    if missing_index:
        raise ValueError(f"자치구×월 index를 만들 수 없는 거래가 {missing_index:,}건입니다.")
    return indexed


def make_coordinate_tree(complexes: pd.DataFrame) -> tuple[cKDTree, np.ndarray]:
    """EPSG:5179 좌표의 cKDTree를 만들어 반경 후보를 먼저 빠르게 찾는다."""
    if complexes[["lat", "lng"]].isna().any().any():
        raise ValueError("좌표 결측 단지가 있어 3km 반경 후보를 만들 수 없습니다.")
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)
    x, y = transformer.transform(complexes["lng"].to_numpy(), complexes["lat"].to_numpy())
    coordinates = np.column_stack([x, y])
    return cKDTree(coordinates), coordinates


def standard_deviations(complexes: pd.DataFrame) -> dict[str, float]:
    """전체 단지 분포의 표준편차를 반환한다. 결측은 표준화에 쓰지 않는다."""
    result = {}
    for feature in sum(FEATURE_GROUPS.values(), []):
        std = float(complexes[feature].std(ddof=0, skipna=True))
        if not np.isfinite(std) or std <= 0:
            raise ValueError(f"환경 feature {feature}의 표준편차가 유효하지 않습니다.")
        result[feature] = std
    return result


def environmental_similarity(target_index: int, candidate_indices: np.ndarray,
                             feature_values: np.ndarray, feature_stds: np.ndarray,
                             group_positions: list[list[int]]) -> np.ndarray:
    """feature별 표준화 절대차를 군별 평균한 뒤, 군을 같은 가중치로 평균한다."""
    group_scores = []
    for positions in group_positions:
        differences = np.abs(
            feature_values[candidate_indices][:, positions] - feature_values[target_index, positions]
        ) / feature_stds[positions]
        differences = np.atleast_2d(differences)
        available = np.isfinite(differences)
        counts = available.sum(axis=1)
        scores = np.full(len(candidate_indices), np.nan)
        valid = counts > 0
        scores[valid] = np.nansum(differences[valid], axis=1) / counts[valid]
        group_scores.append(scores)

    stacked = np.column_stack(group_scores)
    available_groups = np.isfinite(stacked)
    counts = available_groups.sum(axis=1)
    similarity = np.full(len(candidate_indices), np.nan)
    valid = counts > 0
    # 결측 군을 0점으로 채우지 않고, 실제로 함께 관측된 군의 동일 가중 평균만 쓴다.
    similarity[valid] = np.nansum(stacked[valid], axis=1) / counts[valid]
    return similarity


def build_adjusted_trade_choices(ordered_trades: pd.DataFrame,
                                 target_areas: np.ndarray) -> dict[float, dict[str, object]]:
    """각 목표 면적·비교 단지마다 20% 보정 제한 안의 가장 최근 거래를 미리 고른다."""
    target_area_set = {float(value) for value in target_areas}
    choices = {area_type: {} for area_type in target_area_set}
    usable = ordered_trades.loc[ordered_trades["adjustment_factor"].sub(1).abs().le(MAX_ADJUSTMENT)]
    for trade in usable.itertuples(index=False):
        # area_type은 3m bin이므로 동일·인접 세 bin만 확인하면 ±3m 조건과 같다.
        for target_area in (float(trade.area_type) - 3, float(trade.area_type), float(trade.area_type) + 3):
            if target_area in target_area_set and trade.apt_seq not in choices[target_area]:
                choices[target_area][trade.apt_seq] = trade
    return choices


def build_comparables(complexes: pd.DataFrame, targets: pd.DataFrame, trades: pd.DataFrame,
                      latest_month: pd.Period) -> tuple[pd.DataFrame, dict[str, int]]:
    """명시된 다섯 단계로 후보를 줄이고 환경 유사도 순의 실제 거래를 반환한다."""
    complexes = complexes.reset_index(drop=True).copy()
    complex_index = pd.Series(complexes.index.to_numpy(), index=complexes["apt_seq"])
    tree, coordinates = make_coordinate_tree(complexes)
    feature_names = sum(FEATURE_GROUPS.values(), [])
    feature_stds_by_name = standard_deviations(complexes)
    feature_stds = np.asarray([feature_stds_by_name[name] for name in feature_names])
    feature_values = complexes[feature_names].to_numpy(dtype=float)
    group_positions = []
    position = 0
    for features in FEATURE_GROUPS.values():
        group_positions.append(list(range(position, position + len(features))))
        position += len(features)
    built_years = complexes["built_year"].to_numpy(dtype=float)
    households = complexes["total_households"].to_numpy(dtype=float)
    apt_seqs = complexes["apt_seq"].to_numpy()
    names = complexes["name"].to_numpy()

    # 1단계의 면적 후보는 실제 거래가 하나라도 있는 단지로만 만든다.
    ordered_trades = trades.sort_values(
        ["apt_seq", "deal_date", "source_order"],
        ascending=[True, False, False],
        kind="stable",
    )
    adjusted_trade_choices = build_adjusted_trade_choices(
        ordered_trades, targets["area_type"].unique(),
    )
    candidate_indices_by_area: dict[float, np.ndarray] = {}
    traded_areas = trades[["apt_seq", "area_type"]].drop_duplicates()
    for area_type in targets["area_type"].unique():
        matching_apts = traded_areas.loc[traded_areas["area_type"].between(area_type - 3, area_type + 3), "apt_seq"]
        candidate_indices_by_area[float(area_type)] = complex_index.reindex(matching_apts.unique()).dropna().astype(int).to_numpy()

    stage_counts = defaultdict(int)
    result_rows: list[dict[str, object]] = []
    target_indices = complex_index.reindex(targets["apt_seq"]).astype(int).to_numpy()
    target_coordinates = coordinates[target_indices]
    nearby_indices = tree.query_ball_point(target_coordinates, r=RADIUS_M)

    for row_position, target in enumerate(targets.itertuples(index=False)):
        target_index = int(target_indices[row_position])
        target_apt_seq = target.apt_seq
        area_type = float(target.area_type)
        stage1 = candidate_indices_by_area[area_type]
        stage1 = stage1[stage1 != target_index]
        stage_counts["1_area"] += len(stage1)

        target_year = built_years[target_index]
        if not np.isfinite(target_year):
            continue
        stage2 = stage1[np.abs(built_years[stage1] - target_year) <= 5]
        stage_counts["2_year"] += len(stage2)

        target_households = households[target_index]
        if not np.isfinite(target_households) or target_households <= 0:
            continue
        candidate_households = households[stage2]
        stage3 = stage2[
            np.isfinite(candidate_households)
            & (candidate_households >= target_households * 0.5)
            & (candidate_households <= target_households * 2.0)
        ]
        stage_counts["3_households"] += len(stage3)

        nearby = np.asarray(nearby_indices[row_position], dtype=int)
        stage4 = nearby[np.isin(nearby, stage3, assume_unique=False)]
        stage_counts["4_radius"] += len(stage4)
        if len(stage4) == 0:
            continue

        similarity = environmental_similarity(
            target_index, stage4, feature_values, feature_stds, group_positions,
        )
        valid_similarity = np.isfinite(similarity)
        stage5 = stage4[valid_similarity]
        similarity = similarity[valid_similarity]
        stage_counts["5_environment"] += len(stage5)
        if len(stage5) == 0:
            continue

        distances = np.linalg.norm(coordinates[stage5] - target_coordinates[row_position], axis=1)
        order = np.lexsort((apt_seqs[stage5], distances, similarity))

        rank = 0
        for candidate_index in stage5[order]:
            comp_apt_seq = apt_seqs[candidate_index]
            trade = adjusted_trade_choices[area_type].get(comp_apt_seq)
            if trade is None:
                continue
            rank += 1
            factor = float(trade.adjustment_factor)
            result_rows.append({
                "apt_seq": target_apt_seq,
                "area_type": area_type,
                "target_source": target.target_source,
                "rank": rank,
                "comp_apt_seq": comp_apt_seq,
                "comp_name": names[candidate_index],
                "comp_deal_ym": str(trade.deal_period),
                "comp_price_manwon": float(trade.deal_amount_manwon),
                "comp_price_per_m2": float(trade.price_per_m2),
                "adj_price_per_m2": float(trade.price_per_m2 * factor),
                "adj_reason": f"{latest_month.strftime('%Y-%m')} 기준 보정 ({trade.gu} 자치구 지수 x{factor:.2f})",
                "dist_m": float(np.linalg.norm(coordinates[candidate_index] - target_coordinates[row_position])),
            })
            if rank == MAX_COMPARABLES:
                break

    output = pd.DataFrame(result_rows, columns=RESULT_COLUMNS)
    if not output.empty:
        output = output.sort_values(["apt_seq", "area_type", "rank"], kind="stable").reset_index(drop=True)
    return output, dict(stage_counts)


def print_validation(output: pd.DataFrame, targets: pd.DataFrame, price_cell_apts: pd.Index,
                     stage_counts: dict[str, int]) -> None:
    """산출물 계약과 비교사례 제한을 자체 검증하고 0곳 현황을 보고한다."""
    print("\n===== 4. 자체 검증 =====")
    self_excluded = output.empty or output["comp_apt_seq"].ne(output["apt_seq"]).all()
    adjustment_ok = output.empty or (
        output["adj_price_per_m2"].sub(output["comp_price_per_m2"]).abs()
        .div(output["comp_price_per_m2"]).le(MAX_ADJUSTMENT + 1e-12).all()
    )
    expected_rank = output.groupby(["apt_seq", "area_type"], observed=True).cumcount().add(1)
    rank_continuous = output.empty or output["rank"].eq(expected_rank).all()
    per_target = output.groupby(["apt_seq", "area_type"], observed=True).size()
    max_ten = per_target.le(MAX_COMPARABLES).all()
    comp_from_price_cells = output.empty or output["comp_apt_seq"].isin(price_cell_apts).all()
    coldstart_targets = targets.loc[targets["target_source"].eq("COLDSTART")]
    coldstart_apts = pd.Index(coldstart_targets["apt_seq"].unique())
    coldstart_compared_apts = pd.Index(
        output.loc[output["target_source"].eq("COLDSTART"), "apt_seq"].unique(),
    )
    coldstart_coverage = coldstart_apts.isin(coldstart_compared_apts).mean()
    coldstart_has_comparable = bool(coldstart_coverage > 0)

    checks = [
        ("comp_apt_seq != apt_seq 100%", self_excluded,
         f"위반 {(~output['comp_apt_seq'].ne(output['apt_seq'])).sum() if not output.empty else 0:,}건"),
        ("보정폭 20% 이내", adjustment_ok,
         f"최대 {((output['adj_price_per_m2'].sub(output['comp_price_per_m2']).abs().div(output['comp_price_per_m2']).max() * 100) if not output.empty else 0):.2f}%"),
        ("apt_seq×area_type 안 rank 1부터 연속", rank_continuous, f"{len(output):,}행"),
        ("비교사례가 10곳을 넘는 조합 0건", max_ten,
         f"초과 {(per_target.gt(MAX_COMPARABLES).sum() if not per_target.empty else 0):,}조합"),
        ("comp_apt_seq는 모두 32.1 가격 셀 단지", comp_from_price_cells,
         f"위반 {(~output['comp_apt_seq'].isin(price_cell_apts)).sum() if not output.empty else 0:,}건"),
        ("33.1 cold-start 단지 중 비교사례 1곳 이상 비율 > 0%", coldstart_has_comparable,
         f"{coldstart_apts.isin(coldstart_compared_apts).sum():,} / {len(coldstart_apts):,} ({coldstart_coverage * 100:.2f}%)"),
    ]
    for label, passed, detail in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}: {detail}")

    target_keys = pd.MultiIndex.from_frame(targets[["apt_seq", "area_type"]])
    per_case = per_target.reindex(target_keys, fill_value=0)
    zero_case = int(per_case.eq(0).sum())
    target_apts = pd.Index(targets["apt_seq"].unique())
    compared_apts = pd.Index(output["apt_seq"].unique())
    zero_apts = int((~target_apts.isin(compared_apts)).sum())
    median_count = float(per_case.median())
    print(f"\n  참고: 비교사례 0곳 단지: {zero_apts:,} / {len(target_apts):,} ({zero_apts / len(target_apts) * 100:.2f}%)")
    print(f"  참고: 비교사례 0곳 단지×면적타입: {zero_case:,} / {len(per_case):,} ({zero_case / len(per_case) * 100:.2f}%)")
    print(f"  참고: 사례 수 분포 중앙값: {median_count:.1f}곳 (0곳 포함)")
    print("  참고: target_source별 현황 (단지 기준 0곳 비율, 단지×면적타입 기준 중앙값)")
    target_case_counts = targets[["apt_seq", "area_type", "target_source"]].copy()
    target_case_counts["n_comparables"] = per_case.to_numpy()
    for source in ("CELL", "COLDSTART"):
        source_cases = target_case_counts.loc[target_case_counts["target_source"].eq(source)]
        source_apts = pd.Index(source_cases["apt_seq"].unique())
        source_compared_apts = pd.Index(
            output.loc[output["target_source"].eq(source), "apt_seq"].unique(),
        )
        source_zero_apts = (~source_apts.isin(source_compared_apts)).sum()
        print(
            f"    {source}: 단지 {len(source_apts):,}개 / 사례 수 중앙값 "
            f"{source_cases['n_comparables'].median():.1f}곳 / 비교사례 0곳 단지 "
            f"{source_zero_apts:,}개 ({source_zero_apts / len(source_apts) * 100:.2f}%)"
        )
    print("  참고: 단계별 후보 수 (단지×면적타입 후보 관계, 보정 20% 제외 전)")
    print("    1 면적(±3m) -> 2 준공(±5년) -> 3 세대수(0.5~2배) -> "
          "4 반경(3km) -> 5 환경 유사도")
    print("    " + " -> ".join(f"{stage_counts.get(key, 0):,}" for key in [
        "1_area", "2_year", "3_households", "4_radius", "5_environment",
    ]))
    print(f"  참고: 보정 20% 제한과 최대 {MAX_COMPARABLES}곳 적용 후: {len(output):,}건")
    print("  참고: 환경 가중치: 교통 20%, 교육 20%, 공원 20%, 상권 20% "
          "(cvs·restaurant 표준화 차이 평균), 일조·조망 20% "
          "(sun_hours·view_open·river_view 표준화 차이 평균)")

    assert all(passed for _, passed, _ in checks), "자체 검증 실패: 위 [FAIL] 항목을 확인하십시오."


def main() -> None:
    print("===== 1. 입력 및 기준월 =====")
    complexes, targets, price_cell_apts = load_inputs()
    trades, latest_month = load_recent_trades(set(complexes["apt_seq"]))
    trades = attach_district_index(trades, latest_month)
    print(f"  기준월: {latest_month} / 최근 24개월 거래: {len(trades):,}건")
    print(f"  비교 대상: {len(targets):,} 단지×면적타입 / {targets['apt_seq'].nunique():,}단지")

    print("\n===== 2. 비교사례 retrieval =====")
    output, stage_counts = build_comparables(complexes, targets, trades, latest_month)
    output.to_csv(RESULT_PATH, sep="\t", index=False)
    print(f"  저장: {RESULT_PATH.relative_to(work_dir)} ({len(output):,}행)")

    print_validation(output, targets, price_cell_apts, stage_counts)


if __name__ == "__main__":
    main()
