# ============================================================================
# 30.spike_hedonic.py
# ============================================================================
# Purpose: 물리·입지 지표의 아파트 단위면적 거래가 설명력과 out-of-time 예측력을 검증한다.
# Notes:
#   - split 이전에는 명백한 오류(가격/면적 <= 0)만 제거한다.
#   - 가격 outlier cutoff와 모든 대체값은 train 거래만으로 적합한다.
#   - 검증 거래는 가격을 근거로 절대 제거하지 않는다.
# ============================================================================

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.outliers_influence import variance_inflation_factor

from _features import (
    I_MODEL_FEATURES,
    M2_FEATURES,
    PHYSICAL_FEATURES,
    REDEVELOP_FEATURES,
    add_location_fe_columns,
    bool_to_float,
    build_design,
    location_category_columns,
    make_full_bjd_key,
    normalize_administrative_code,
    transformed_feature,
)
from _floor_band import assign_floor_band, load_actual_max_levels


work_dir = Path(__file__).resolve().parents[2]
output_dir = work_dir / "output"

TRADES_PATH = output_dir / "11.1.trades_sale.txt"
COMPLEX_PATH = output_dir / "23.1.complex.txt"
METRICS_PATH = output_dir / "23.2.complex_metrics.txt"
PROFILE_PATH = output_dir / "23.3.horizon_profile.txt"
COMPLEX_FINAL_PATH = output_dir / "15.1.complex_final.txt"
GEOCODED_PATH = output_dir / "14.1.geocoded_master.txt"
COMPARISON_PATH = output_dir / "30.1.spike_model_comparison.txt"
COEFFICIENTS_PATH = output_dir / "30.2.spike_coefficients.txt"
BASELINE_PATH = output_dir / "30.3.baseline_breakdown.txt"

BOOTSTRAP_REPS = 1_000
BOOTSTRAP_SEED = 20260911
MIN_BJD_TRAIN_ROWS = 30

# A--F는 완전히 같은 train/test 표본으로 비교한다. B는 값이 아니라 profile 매칭 성공만 넣는다.
ABLATION_FEATURES = {
    "A_M2": M2_FEATURES,
    "B_profile_availability": M2_FEATURES | {"physical_profile_available": ("physical_profile_available", False)},
    "C_river_view": M2_FEATURES | {"river_view": PHYSICAL_FEATURES["river_view"]},
    "D_sun_hours_winter": M2_FEATURES | {"sun_hours_winter": PHYSICAL_FEATURES["sun_hours_winter"]},
    "E_openness_3": M2_FEATURES
    | {name: PHYSICAL_FEATURES[name] for name in ["open_angle_mean", "view_block_pct", "open_span_max"]},
    "F_M3_all_physical": M2_FEATURES | PHYSICAL_FEATURES,
    "H_M2_redevelop": M2_FEATURES | REDEVELOP_FEATURES,
    "I_M3_redevelop": I_MODEL_FEATURES,
}
ABLATION_LABELS = {
    "A_M2": "A. M2 (기준)",
    "B_profile_availability": "B. M2 + physical profile availability만",
    "C_river_view": "C. M2 + river_view만",
    "D_sun_hours_winter": "D. M2 + sun_hours_winter만",
    "E_openness_3": "E. M2 + 개방도 3종만",
    "F_M3_all_physical": "F. M3 (물리 feature 전체)",
    "G_M2_complete_case": "G-1. complete-case M2",
    "G_M3_complete_case": "G-2. complete-case M3",
    "H_M2_redevelop": "H. M2 + 정비사업 신호만",
    "I_M3_redevelop": "I. M3 + 정비사업 신호 (= 전체)",
}


def ensure_unique(frame: pd.DataFrame, key: str, label: str) -> pd.DataFrame:
    """merge 증식을 막기 위해 단지 key의 유일성을 검증한다."""
    if frame[key].duplicated().any():
        raise ValueError(f"{label}: {key} 중복으로 merge가 불가능합니다.")
    return frame


def modal_code(values: pd.Series):
    """오입력 행 하나가 전체 단지의 자치구를 바꾸지 않도록 최빈 code를 선택한다."""
    valid = normalize_administrative_code(values).dropna()
    return valid.value_counts().index[0] if not valid.empty else pd.NA


def add_canonical_sgg(complex_df: pd.DataFrame, trades: pd.DataFrame) -> pd.DataFrame:
    """14.1의 gu와 11.1의 sggCd를 결합해 23.1 단지별 자치구 code를 보강한다."""
    geocoded = ensure_unique(pd.read_csv(GEOCODED_PATH, sep="\t", usecols=["aptSeq", "gu"]), "aptSeq", "14.1")
    geocoded = geocoded.rename(columns={"aptSeq": "apt_seq"})
    sale_codes = trades[["aptSeq", "sggCd", "gu"]].copy()
    sale_codes["sgg_code"] = normalize_administrative_code(sale_codes["sggCd"])
    gu_to_sgg = sale_codes.dropna(subset=["gu", "sgg_code"]).groupby("gu")["sgg_code"].agg(modal_code)
    apt_to_sgg = sale_codes.groupby("aptSeq")["sgg_code"].agg(modal_code)
    result = complex_df.merge(geocoded, on="apt_seq", how="left", validate="one_to_one")
    result["sgg_code"] = result["gu"].map(gu_to_sgg).fillna(result["apt_seq"].map(apt_to_sgg))
    result["sgg_code"] = result["sgg_code"].fillna(result["apt_seq"].astype("string").str.slice(0, 5))
    result["sgg_code"] = normalize_administrative_code(result["sgg_code"])
    if result["sgg_code"].isna().any():
        raise ValueError("23.1 단지의 자치구 code를 보강하지 못했습니다.")
    return result.drop(columns="gu")


def location_key_audit(frame: pd.DataFrame) -> dict:
    """완전 법정동 키의 범위와 자치구 혼입 여부를 검증한다."""
    valid = frame.dropna(subset=["bjd_full_key"])
    mixed = int(valid.groupby("bjd_full_key")["sgg_code"].nunique().gt(1).sum())
    return {
        "full_key_unique": int(valid["bjd_full_key"].nunique()),
        "full_key_rows": len(valid),
        "bjd_missing_rows": int(frame["bjd_full_key"].isna().sum()),
        "mixed_sgg_keys": mixed,
    }


def dense_bjd_keys(train: pd.DataFrame, minimum_rows: int = MIN_BJD_TRAIN_ROWS) -> set[str]:
    counts = train["bjd_full_key"].dropna().value_counts()
    return set(counts[counts.ge(minimum_rows)].index.astype(str))


def complex_holdout(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    hashes = pd.util.hash_pandas_object(frame["apt_seq"].astype(str), index=False).to_numpy(dtype=np.uint64) % 5
    return frame.loc[hashes != 0].copy(), frame.loc[hashes == 0].copy()


def load_pre_split_sample() -> tuple[pd.DataFrame, pd.Period, pd.Period, pd.Period]:
    """최근 24개월 거래를 결합한다. 이 단계에서는 가격 outlier를 제거하지 않는다."""
    trades = pd.read_csv(TRADES_PATH, sep="\t", low_memory=False)
    complex_df = ensure_unique(pd.read_csv(COMPLEX_PATH, sep="\t"), "apt_seq", "complex")
    complex_df = add_canonical_sgg(complex_df, trades)
    metrics = ensure_unique(pd.read_csv(METRICS_PATH, sep="\t"), "apt_seq", "complex_metrics")
    profile = pd.read_csv(PROFILE_PATH, sep="\t")
    complex_final = pd.read_csv(COMPLEX_FINAL_PATH, sep="\t", low_memory=False).rename(columns={"aptSeq": "apt_seq"})
    if profile.duplicated(["apt_seq", "floor_band"]).any():
        raise ValueError("horizon_profile의 apt_seq×floor_band가 유일하지 않습니다.")

    trades["deal_period"] = pd.PeriodIndex(trades["deal_ym"].astype(str), freq="M")
    latest = trades["deal_period"].max()
    start = latest - 23
    train_end = start + 17
    is_not_cancelled = trades["is_cancelled"].astype("string").str.lower().ne("true")
    # split 전에 허용되는 정제는 명백한 입력 오류뿐이다.
    trades = trades.loc[
        is_not_cancelled
        & trades["deal_period"].between(start, latest)
        & pd.to_numeric(trades["deal_amount_manwon"], errors="coerce").gt(0)
        & pd.to_numeric(trades["excluUseAr"], errors="coerce").gt(0)
    ].copy()
    if trades.empty:
        raise ValueError("최근 24개월의 유효 매매 거래가 없습니다.")

    trades = trades.rename(columns={"aptSeq": "apt_seq"})
    trades["area_type"] = np.round(pd.to_numeric(trades["excluUseAr"], errors="coerce") / 3) * 3
    trades["cell_key"] = trades["apt_seq"].astype(str) + "|" + trades["area_type"].astype(str)
    trades["deal_date"] = pd.to_datetime(
        dict(
            year=pd.to_numeric(trades["dealYear"], errors="coerce"),
            month=pd.to_numeric(trades["dealMonth"], errors="coerce"),
            day=pd.to_numeric(trades["dealDay"], errors="coerce"),
        ),
        errors="coerce",
    ).fillna(trades["deal_period"].dt.to_timestamp())

    # 15.1의 실제 최고층으로 삼등분한다. 23.3 HIGH repr_floor는 5/6 대표
    # 관측층이므로 최고층 proxy로 사용하지 않는다.
    trades["floor_band"] = assign_floor_band(trades, load_actual_max_levels(complex_final))

    keep_complex = ["apt_seq", "bjd_code", "sgg_code", "built_year", "total_households", "far", "bcr", "parking_per_hh",
                    "redevelop_type", "redevelop_stage"]
    trades = trades.merge(complex_df[keep_complex], on="apt_seq", how="left", validate="many_to_one")
    trades = trades.merge(metrics, on="apt_seq", how="left", validate="many_to_one")
    physical = profile.drop(columns=["repr_floor", "obs_height", "sun_hours_spring"], errors="ignore")
    trades = trades.merge(
        physical,
        on=["apt_seq", "floor_band"],
        how="left",
        validate="many_to_one",
        indicator="_profile_merge",
    )
    trades["physical_profile_available"] = trades["_profile_merge"].eq("both").astype(float)
    trades = trades.drop(columns="_profile_merge")

    for column in ["river_view", "park_view", "mountain_view"]:
        trades[column] = bool_to_float(trades[column])
    for column in ["far", "bcr", "parking_per_hh"]:
        trades[column] = pd.to_numeric(trades[column], errors="coerce")
    # 23.1의 유형·단계 결측은 비매칭이며, 비매칭 사유(구역 아님/대표 지번 차이)는
    # 구분할 수 없다. 회귀 signal은 지번 완전일치 재건축 추진 단지의 조건부 지표다.
    trades["is_redevelop"] = trades["redevelop_type"].notna().astype(float)
    trades["redevelop_stage_advanced"] = trades["redevelop_stage"].isin(["관리처분", "착공"]).astype(float)
    if not trades.loc[trades["is_redevelop"].eq(1), "redevelop_stage"].notna().all():
        raise ValueError("정비사업 매칭 거래에 추진단계 결측이 있습니다.")
    if not trades.loc[trades["is_redevelop"].eq(0), ["redevelop_type", "redevelop_stage"]].isna().all().all():
        raise ValueError("정비사업 미매칭 단지의 유형 또는 단계가 결측이 아닙니다.")
    trades["bjd_full_key"] = make_full_bjd_key(trades["bjd_code"], trades["sgg_code"])
    location_audit = location_key_audit(trades)
    if location_audit["full_key_unique"] < 300:
        raise AssertionError(f"완전 법정동 키 고유값 부족: {location_audit['full_key_unique']}")
    if location_audit["mixed_sgg_keys"]:
        raise AssertionError(f"완전 법정동 키의 자치구 혼입: {location_audit['mixed_sgg_keys']}")
    trades["bjd_code"] = trades["bjd_code"].astype("string").fillna("MISSING")
    trades["price_per_m2"] = pd.to_numeric(trades["deal_amount_manwon"], errors="coerce") / pd.to_numeric(
        trades["excluUseAr"], errors="coerce"
    )
    trades["y"] = np.log(trades["price_per_m2"])
    trades.attrs["location_audit"] = location_audit
    return trades, start, train_end, latest


def apply_train_outlier_rule(train_raw: pd.DataFrame, test_raw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """train 셀의 1/99 분위만으로 학습 표본을 정제하고, test에는 cutoff를 적용만 한다."""
    # 보간 분위수를 작은 셀에 그대로 쓰면 n=2에서 양 끝 두 거래가 모두 사라진다.
    # quantile(0.01)이 최솟값보다 크고 quantile(0.99)가 최댓값보다 작기 때문이다.
    # 100건 미만 셀은 무조건 최소·최대를 잃어 실제 제거율이 1%를 크게 넘는다.
    # 32.build_price_cells.py 와 동일하게 각 tail 제거 수를 floor(n×1%)로 정의한다.
    group_size = train_raw.groupby("cell_key")["price_per_m2"].transform("size")
    trim_count = np.floor(group_size * 0.01).astype(int)
    ascending_rank = train_raw.groupby("cell_key")["price_per_m2"].rank(method="first")
    descending_rank = train_raw.groupby("cell_key")["price_per_m2"].rank(method="first", ascending=False)
    train = train_raw.loc[(ascending_rank > trim_count) & (descending_rank > trim_count)].copy()

    # test 행을 가격으로 제거하지 않되, 기록용 cutoff 는 계속 남긴다.
    cutoffs = train_raw.groupby("cell_key")["price_per_m2"].agg(
        lower="min", upper="max", n="size"
    )
    global_lower = float(train_raw["price_per_m2"].quantile(0.01))
    global_upper = float(train_raw["price_per_m2"].quantile(0.99))

    # test 셀에 train cutoff가 없으면 train 전체 분위수를 기록용 fallback으로 쓴다.
    # 중요한 점은 이 범위 밖인 test 행도 제거하지 않는다는 것이다.
    test_bounds = test_raw.join(cutoffs[["lower", "upper"]], on="cell_key")
    test_bounds["cutoff_fallback_train_global"] = test_bounds["lower"].isna()
    test_bounds["lower"] = test_bounds["lower"].fillna(global_lower)
    test_bounds["upper"] = test_bounds["upper"].fillna(global_upper)
    test_bounds["outside_train_price_cutoff"] = ~test_bounds["price_per_m2"].between(
        test_bounds["lower"], test_bounds["upper"], inclusive="both"
    )
    test = test_bounds.drop(columns=["lower", "upper"])
    audit = {
        "train_raw_n": len(train_raw), "train_clean_n": len(train),
        "train_outlier_removed_n": len(train_raw) - len(train),
        "train_global_lower": global_lower, "train_global_upper": global_upper,
        "test_n": len(test), "test_price_removed_n": 0,
        "test_outside_train_cutoff_n": int(test["outside_train_price_cutoff"].sum()),
        "test_fallback_rows": int(test["cutoff_fallback_train_global"].sum()),
        "test_fallback_cells": int(test.loc[test["cutoff_fallback_train_global"], "cell_key"].nunique()),
        "test_cells": int(test["cell_key"].nunique()),
    }
    return train, test, audit


def fit_ols(
    frame: pd.DataFrame,
    features: dict[str, tuple[str, bool]],
    context: dict | None = None,
    location_columns: list[str] | None = None,
    cluster_robust: bool = True,
):
    """지역 fixed effect와 단지 cluster-robust SE를 사용한 OLS를 적합한다."""
    design, context = build_design(frame, features, context, location_columns)
    model = sm.OLS(frame["y"].astype(float), design)
    result = model.fit(cov_type="cluster", cov_kwds={"groups": frame["apt_seq"].astype(str), "use_correction": True}) if cluster_robust else model.fit()
    return result, design, context


def original_scale_metrics(actual: pd.Series, predicted: np.ndarray) -> dict[str, float]:
    """원 스케일 가격/m2의 MAE, MAPE, R2를 계산한다."""
    actual_values = actual.to_numpy(dtype=float)
    predicted_values = np.asarray(predicted, dtype=float)
    return {"mae": float(np.mean(np.abs(actual_values - predicted_values))),
            "mape": float(np.mean(np.abs(actual_values - predicted_values) / actual_values) * 100),
            "r2": float(1 - np.sum((actual_values - predicted_values) ** 2) / np.sum((actual_values - actual_values.mean()) ** 2))}


def fit_and_score(
    train: pd.DataFrame, test: pd.DataFrame, features: dict[str, tuple[str, bool]], location_columns: list[str], cluster_robust: bool = True,
) -> dict:
    """한 feature 조합을 train에만 적합하고 동일한 test에서 평가한다."""
    result, _, context = fit_ols(train, features, location_columns=location_columns, cluster_robust=cluster_robust)
    test_design, _ = build_design(test, features, context)
    prediction = np.exp(result.predict(test_design))
    return {"result": result, "context": context, "prediction": prediction,
            "oot": original_scale_metrics(test["price_per_m2"], prediction)}


def evaluate_location_controls(train: pd.DataFrame, test: pd.DataFrame) -> tuple[dict, list[dict]]:
    """완전 법정동과 sparse 법정동을 자치구에 흡수한 계층 FE를 같은 split에서 비교한다."""
    hold_train, hold_test = complex_holdout(train)
    candidates = []
    for scheme in ["full_bjd", "hierarchical"]:
        fit_dense = dense_bjd_keys(hold_train) if scheme == "hierarchical" else set()
        hold_fit = add_location_fe_columns(hold_train, scheme, fit_dense)
        hold_eval = add_location_fe_columns(hold_test, scheme, fit_dense)
        hold = fit_and_score(hold_fit, hold_eval, M2_FEATURES, location_category_columns(scheme), cluster_robust=False)["oot"]["mape"]
        oot_dense = dense_bjd_keys(train) if scheme == "hierarchical" else set()
        oot_fit = add_location_fe_columns(train, scheme, oot_dense)
        oot_eval = add_location_fe_columns(test, scheme, oot_dense)
        oot = fit_and_score(oot_fit, oot_eval, M2_FEATURES, location_category_columns(scheme), cluster_robust=False)["oot"]["mape"]
        candidates.append({
            "scheme": scheme,
            "complex_holdout_mape_pct": hold,
            "out_of_time_mape_pct": oot,
            "dense_bjd_keys": len(oot_dense) if scheme == "hierarchical" else int(train["bjd_full_key"].nunique()),
            "sparse_min_train_rows": MIN_BJD_TRAIN_ROWS if scheme == "hierarchical" else 0,
        })
    # 단지 holdout을 우선으로 하고, 동률이면 OOT를 쓴다. 두 split 모두 사전에 고정돼 있다.
    chosen = min(candidates, key=lambda item: (item["complex_holdout_mape_pct"], item["out_of_time_mape_pct"]))
    return chosen, candidates


def joint_wald_test(result, terms: list[str]) -> dict[str, float]:
    """cluster-robust covariance로 지정한 계수들이 동시에 0인지 Wald F test를 한다."""
    included = [term for term in terms if term in result.params.index]
    if not included:
        raise ValueError("joint Wald test에 포함할 물리항이 없습니다.")
    restriction = np.zeros((len(included), len(result.params)))
    for row, term in enumerate(included):
        restriction[row, result.params.index.get_loc(term)] = 1.0
    test = result.wald_test(restriction, use_f=True, scalar=True)
    return {"n_terms": len(included), "f_stat": float(test.statistic), "p_value": float(test.pvalue),
            "df_num": float(test.df_num), "df_denom": float(test.df_denom), "terms": ", ".join(included)}


def paired_block_bootstrap_mape(test: pd.DataFrame, prediction_m2: np.ndarray, prediction_m3: np.ndarray) -> dict[str, float]:
    """단지를 복원추출하는 paired block bootstrap으로 M2-M3 MAPE 차이 CI를 계산한다."""
    errors = pd.DataFrame({"apt_seq": test["apt_seq"].astype(str).to_numpy(),
                           "m2": np.abs(test["price_per_m2"].to_numpy() - prediction_m2) / test["price_per_m2"].to_numpy() * 100,
                           "m3": np.abs(test["price_per_m2"].to_numpy() - prediction_m3) / test["price_per_m2"].to_numpy() * 100})
    blocks = [block[["m2", "m3"]].to_numpy() for _, block in errors.groupby("apt_seq", sort=False)]
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    differences = np.empty(BOOTSTRAP_REPS)
    for rep in range(BOOTSTRAP_REPS):
        picked = rng.integers(0, len(blocks), size=len(blocks))
        sampled = np.concatenate([blocks[index] for index in picked], axis=0)
        differences[rep] = sampled[:, 0].mean() - sampled[:, 1].mean()
    observed = float(errors["m2"].mean() - errors["m3"].mean())
    lower, upper = np.quantile(differences, [0.025, 0.975])
    return {"observed_m2_minus_m3_mape_pp": observed, "ci_lower": float(lower), "ci_upper": float(upper),
            "n_complexes": len(blocks), "reps": BOOTSTRAP_REPS}


def physical_diagnostics(train: pd.DataFrame, context: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """물리항과 유지된 결측지시자만으로 correlation, VIF, rank, condition number를 계산한다."""
    values = pd.DataFrame({name: transformed_feature(train, source, use_log).fillna(context["means"][name])
                           for name, (source, use_log) in PHYSICAL_FEATURES.items()}, index=train.index)
    for name in context["indicators"]:
        if name in PHYSICAL_FEATURES:
            source, use_log = PHYSICAL_FEATURES[name]
            indicator = f"miss_{name}"
            if indicator not in context.get("dropped_collinear_columns", []):
                values[indicator] = transformed_feature(train, source, use_log).isna().astype(float)
    nonconstant = values.loc[:, values.nunique(dropna=False).gt(1)]
    vif_input = sm.add_constant(nonconstant, has_constant="add").to_numpy(dtype=float)
    vif = [variance_inflation_factor(vif_input, idx + 1) for idx in range(nonconstant.shape[1])]
    vif_table = pd.DataFrame({"feature": nonconstant.columns, "vif": vif}).sort_values("vif", ascending=False)
    matrix = sm.add_constant(values, has_constant="add").to_numpy(dtype=float)
    dropped = "; ".join(f"miss_{old} (miss_{kept}와 동일)" for old, kept in context["dropped_duplicate_indicators"] if old in PHYSICAL_FEATURES) or "없음"
    diagnostics = {"n_columns_including_constant": matrix.shape[1], "rank": int(np.linalg.matrix_rank(matrix)),
                   "rank_deficient": bool(np.linalg.matrix_rank(matrix) < matrix.shape[1]), "condition_number": float(np.linalg.cond(matrix)),
                   "constant_columns_excluded_from_vif": ", ".join(values.columns[values.nunique(dropna=False).le(1)]) or "없음",
                   "dropped_duplicate_physical_indicators": dropped,
                   "dropped_collinear_physical_columns": ", ".join(
                       column for column in context.get("dropped_collinear_columns", [])
                       if column.startswith("miss_")
                   ) or "없음"}
    return values.corr(), vif_table, diagnostics


def baseline_predictions(train: pd.DataFrame, test: pd.DataFrame) -> tuple[dict[str, pd.Series], pd.DataFrame]:
    """train 거래만으로 baseline과 B2 provenance를 만든다. test target은 전혀 사용하지 않는다."""
    global_mean = train["price_per_m2"].mean()
    complex_mean = train.groupby("apt_seq")["price_per_m2"].mean()
    cell_mean = train.groupby("cell_key")["price_per_m2"].mean()
    ordered_train = train.sort_values(["deal_date", "deal_ym"])
    last_cell = ordered_train.groupby("cell_key")["price_per_m2"].last()
    last_cell_date = ordered_train.groupby("cell_key")["deal_date"].last()
    cell_n = train.groupby("cell_key").size()
    bjd_area_median = train.groupby(["bjd_full_key", "area_type"])["price_per_m2"].median()
    bjd_median = train.groupby("bjd_full_key")["price_per_m2"].median()
    b1 = test["cell_key"].map(cell_mean).fillna(test["apt_seq"].map(complex_mean)).fillna(global_mean)
    exact_last = test["cell_key"].map(last_cell)
    complex_fallback = test["apt_seq"].map(complex_mean)
    b2 = exact_last.fillna(complex_fallback).fillna(global_mean)
    b3_keys = pd.MultiIndex.from_frame(test[["bjd_full_key", "area_type"]])
    b3 = pd.Series(bjd_area_median.reindex(b3_keys).to_numpy(), index=test.index)
    b3 = b3.fillna(test["bjd_full_key"].map(bjd_median)).fillna(global_mean)
    detail = pd.DataFrame(index=test.index)
    detail["b2_source"] = np.select([exact_last.notna(), complex_fallback.notna()], ["exact_cell_last_trade", "complex_mean_fallback"], default="global_mean_fallback")
    detail["train_cell_n"] = test["cell_key"].map(cell_n).fillna(0).astype(int)
    detail["last_cell_date"] = test["cell_key"].map(last_cell_date)
    detail["elapsed_months"] = (test["deal_date"] - detail["last_cell_date"]).dt.days / 30.4375
    detail["elapsed_bin"] = pd.cut(detail["elapsed_months"], bins=[-np.inf, 3, 6, 12, np.inf],
                                    labels=["0-3개월", "3-6개월", "6-12개월", "12개월+"]).astype("string").fillna("직전 동일 셀 거래 없음")
    detail["cell_n_bin"] = pd.cut(detail["train_cell_n"], bins=[-1, 0, 1, 4, np.inf],
                                   labels=["0건 (cold-start)", "1건", "2-4건", "5건+"]).astype("string")
    return {"B1_cell_mean": b1, "B2_last_train_trade": b2, "B3_bjd_area_median": b3}, detail


def grouped_baseline_comparison(test: pd.DataFrame, detail: pd.DataFrame, b2: pd.Series, m3_prediction: np.ndarray, column: str) -> pd.DataFrame:
    """B2의 subgroup별 표본 비중과 B2/M3 MAPE를 같은 행에서 비교한다."""
    frame = pd.DataFrame({"group": detail[column].astype(str), "actual": test["price_per_m2"].to_numpy(),
                          "b2": b2.to_numpy(), "m3": m3_prediction}, index=test.index)
    rows = []
    for group, values in frame.groupby("group", sort=False):
        b2_mape = float((np.abs(values["actual"] - values["b2"]) / values["actual"]).mean() * 100)
        m3_mape = float((np.abs(values["actual"] - values["m3"]) / values["actual"]).mean() * 100)
        rows.append({"subgroup": group, "n": len(values), "sample_share_pct": len(values) / len(frame) * 100,
                     "B2_MAPE_pct": b2_mape, "M3_MAPE_pct": m3_mape, "B2_minus_M3_MAPE_pp": b2_mape - m3_mape})
    return pd.DataFrame(rows)


def coefficient_table(result, model_name: str) -> pd.DataFrame:
    """log-price 계수를 가격 변화율과 cluster-robust 95% CI로 함께 변환한다."""
    rows = []
    for term in result.params.index:
        beta, se = float(result.params[term]), float(result.bse[term])
        ci_low, ci_high = beta - 1.96 * se, beta + 1.96 * se
        rows.append({"model": model_name, "term": term, "coefficient_log_price": beta,
                     "cluster_robust_se": se, "coefficient_ci95_low": ci_low, "coefficient_ci95_high": ci_high,
                     "p_value": float(result.pvalues[term]), "price_change_pct_per_unit": (np.exp(beta) - 1) * 100,
                     "price_change_pct_ci95_low": (np.exp(ci_low) - 1) * 100,
                     "price_change_pct_ci95_high": (np.exp(ci_high) - 1) * 100,
                     "physical_feature": term in PHYSICAL_FEATURES,
                     "interpretation": "1단위 증가(이진 변수는 0→1) 시 가격 변화율"})
    return pd.DataFrame(rows)


def main() -> None:
    raw, start, train_end, latest = load_pre_split_sample()
    complex_redevelop = ensure_unique(
        pd.read_csv(COMPLEX_PATH, sep="\t", usecols=["apt_seq", "redevelop_type", "redevelop_stage"]),
        "apt_seq", "complex 정비사업",
    )
    n_complexes_total = len(complex_redevelop)
    n_redevelop_complexes = int(complex_redevelop["redevelop_type"].notna().sum())
    n_redevelop_advanced = int(complex_redevelop["redevelop_stage"].isin(["관리처분", "착공"]).sum())
    if not complex_redevelop.loc[complex_redevelop["redevelop_type"].isna(), "redevelop_stage"].isna().all():
        raise ValueError("23.1 정비사업 미매칭 단지의 추진단계가 결측이 아닙니다.")
    train_raw = raw.loc[raw["deal_period"].le(train_end)].copy()
    test_raw = raw.loc[raw["deal_period"].gt(train_end)].copy()
    if train_raw.empty or test_raw.empty:
        raise ValueError("out-of-time 학습 또는 검증 표본이 비어 있습니다.")
    if train_raw["deal_period"].max() >= test_raw["deal_period"].min():
        raise AssertionError("학습/검증 기간이 겹칩니다.")
    train, test, outlier_audit = apply_train_outlier_rule(train_raw, test_raw)
    if train.empty:
        raise ValueError("train outlier 정제 후 거래가 없습니다.")

    location_audit = raw.attrs["location_audit"]
    selected_location, location_candidates = evaluate_location_controls(train, test)
    location_scheme = selected_location["scheme"]
    dense_keys = dense_bjd_keys(train) if location_scheme == "hierarchical" else set()
    train = add_location_fe_columns(train, location_scheme, dense_keys)
    test = add_location_fe_columns(test, location_scheme, dense_keys)
    location_columns = location_category_columns(location_scheme)

    # A--F: 같은 정제 train/test 표본, 같은 지역 FE·층대·계약월 통제.
    fitted: dict[str, dict] = {}
    comparison_rows: list[dict] = []
    for key, features in ABLATION_FEATURES.items():
        fitted[key] = fit_and_score(train, test, features, location_columns)
        result, oot = fitted[key]["result"], fitted[key]["oot"]
        comparison_rows.append({"model": ABLATION_LABELS[key], "train_n": int(result.nobs), "test_n": len(test),
                                "train_R2": result.rsquared, "train_adj_R2": result.rsquared_adj,
                                "out_of_time_MAPE_pct": oot["mape"], "out_of_time_MAE_manwon_per_m2": oot["mae"],
                                "out_of_time_R2": oot["r2"]})

    # G: 물리 값이 실제로 모두 존재하는 행만 남긴 complete-case 비교다.
    physical_present = pd.DataFrame({name: transformed_feature(raw, source, use_log).notna()
                                     for name, (source, use_log) in PHYSICAL_FEATURES.items()}, index=raw.index).all(axis=1)
    train_cc = train.loc[physical_present.reindex(train.index, fill_value=False)].copy()
    test_cc = test.loc[physical_present.reindex(test.index, fill_value=False)].copy()
    if train_cc.empty or test_cc.empty:
        raise ValueError("complete-case train 또는 test 표본이 비어 있습니다.")
    for key, features in [("G_M2_complete_case", M2_FEATURES), ("G_M3_complete_case", M2_FEATURES | PHYSICAL_FEATURES)]:
        fitted[key] = fit_and_score(train_cc, test_cc, features, location_columns)
        result, oot = fitted[key]["result"], fitted[key]["oot"]
        comparison_rows.append({"model": ABLATION_LABELS[key], "train_n": int(result.nobs), "test_n": len(test_cc),
                                "train_R2": result.rsquared, "train_adj_R2": result.rsquared_adj,
                                "out_of_time_MAPE_pct": oot["mape"], "out_of_time_MAE_manwon_per_m2": oot["mae"],
                                "out_of_time_R2": oot["r2"]})
    comparison = pd.DataFrame(comparison_rows)
    baseline_row = comparison.loc[comparison["model"].eq(ABLATION_LABELS["A_M2"])].iloc[0]
    comparison["increment_reference"] = "A. M2 (기준)"
    comparison["delta_R2_vs_M2_reference"] = comparison["train_R2"] - baseline_row["train_R2"]
    comparison["delta_adj_R2_vs_M2_reference"] = comparison["train_adj_R2"] - baseline_row["train_adj_R2"]
    comparison["M2_reference_minus_model_MAPE_pp"] = baseline_row["out_of_time_MAPE_pct"] - comparison["out_of_time_MAPE_pct"]
    # complete-case 두 행은 같은 축소 표본 내 M2를 기준으로 다시 증분을 계산한다.
    complete_mask = comparison["model"].isin([ABLATION_LABELS["G_M2_complete_case"], ABLATION_LABELS["G_M3_complete_case"]])
    complete_base = comparison.loc[comparison["model"].eq(ABLATION_LABELS["G_M2_complete_case"])].iloc[0]
    comparison.loc[complete_mask, "increment_reference"] = "G-1. complete-case M2"
    comparison.loc[complete_mask, "delta_R2_vs_M2_reference"] = comparison.loc[complete_mask, "train_R2"] - complete_base["train_R2"]
    comparison.loc[complete_mask, "delta_adj_R2_vs_M2_reference"] = comparison.loc[complete_mask, "train_adj_R2"] - complete_base["train_adj_R2"]
    comparison.loc[complete_mask, "M2_reference_minus_model_MAPE_pp"] = complete_base["out_of_time_MAPE_pct"] - comparison.loc[complete_mask, "out_of_time_MAPE_pct"]

    m3_fit, m2_fit = fitted["F_M3_all_physical"], fitted["A_M2"]
    physical_terms = list(PHYSICAL_FEATURES) + [f"miss_{name}" for name in PHYSICAL_FEATURES]
    wald = joint_wald_test(m3_fit["result"], physical_terms)
    bootstrap = paired_block_bootstrap_mape(test, m2_fit["prediction"], m3_fit["prediction"])
    correlation, vif_table, diag = physical_diagnostics(train, m3_fit["context"])
    coefficients_f = coefficient_table(m3_fit["result"], "F_M3_all_physical (train only)")
    redevelop_coefficients = coefficient_table(
        fitted["I_M3_redevelop"]["result"], "I_M3_redevelop (train only)"
    ).query("term in @REDEVELOP_FEATURES")
    # 30.2에는 기존 F 전체 계수와 새 I의 정비사업 signal 계수를 함께 남긴다.
    coefficients = pd.concat([coefficients_f, redevelop_coefficients], ignore_index=True)
    coefficients.to_csv(COEFFICIENTS_PATH, sep="\t", index=False, float_format="%.8g")

    baselines, b2_detail = baseline_predictions(train, test)
    baseline_table = pd.DataFrame([{"baseline": name, **original_scale_metrics(test["price_per_m2"], prediction)} for name, prediction in baselines.items()])
    source_table = grouped_baseline_comparison(test, b2_detail, baselines["B2_last_train_trade"], m3_fit["prediction"], "b2_source")
    elapsed_table = grouped_baseline_comparison(test, b2_detail, baselines["B2_last_train_trade"], m3_fit["prediction"], "elapsed_bin")
    cell_n_table = grouped_baseline_comparison(test, b2_detail, baselines["B2_last_train_trade"], m3_fit["prediction"], "cell_n_bin")
    physical_coefficients = coefficients_f.loc[coefficients_f["physical_feature"]].copy()

    report = [
        "# 물리·입지 hedonic spike 결과 (누수 방지 재검증)", "", "## 표본·split·이상치 규칙",
        f"- 분석 기간: {start} ~ {latest} (최근 24개월, 계약년월 기준)",
        f"- split: train {start} ~ {train_end}; test {train_end + 1} ~ {latest}",
        f"- 명백한 오류만 사전 제거한 표본: train {len(train_raw):,}건 / test {len(test_raw):,}건",
        f"- train 가격 outlier 제거: 단지×면적타입별 train 1/99 분위로 {outlier_audit['train_outlier_removed_n']:,}건 제거, 최종 train {len(train):,}건",
        f"- train 전체 fallback cutoff: {outlier_audit['train_global_lower']:.2f} ~ {outlier_audit['train_global_upper']:.2f} 만원/m²",
        f"- test 셀 중 train에 없는 셀: {outlier_audit['test_fallback_cells']:,}/{outlier_audit['test_cells']:,}개; 해당 test 행 {outlier_audit['test_fallback_rows']:,}/{len(test):,}건 ({outlier_audit['test_fallback_rows'] / len(test) * 100:.2f}%). 이 행에는 train 전체 cutoff를 부여했지만 제거하지 않았습니다.",
        f"- train에서 적합한 cutoff 밖에 있는 test 행은 {outlier_audit['test_outside_train_cutoff_n']:,}건으로 flag만 남기고 모두 유지했습니다.",
        "- 명시적 자체 검증: 이상치 cutoff 계산에는 test 기간 가격 데이터가 쓰이지 않았습니다. test는 가격 기준으로 0건 제거했으며, 가격<=0·면적<=0 같은 명백한 오류만 split 전에 제거했습니다.",
        "- 모든 회귀는 train에만 적합했으며, 표의 R²/adj. R²는 train in-sample 값, MAPE는 out-of-time test 값입니다. 모든 모델은 선택된 지역 fixed effect, 층대, 계약월, 단지 cluster-robust SE를 사용합니다.", "",
        "## 법정동 완전 키 및 지역 통제 선택",
        f"- 10자리 완전 법정동 키(sggCd 5자리 + bjd_code 5자리 zero-pad) 고유값: {location_audit['full_key_unique']:,}; 키 보유 거래 {location_audit['full_key_rows']:,}건, bjd_code 결측 거래 {location_audit['bjd_missing_rows']:,}건.",
        f"- 한 완전 키에 자치구가 2개 이상 섞인 경우: {location_audit['mixed_sgg_keys']}건.",
        "- (a) 완전 법정동 더미와 (b) 자치구 더미 + 법정동 더미를 단지 hash 80/20 holdout 및 OOT에서 비교했습니다. (b)는 train 거래 30건 미만 법정동을 자치구에 흡수합니다. 30건은 더미 한 개당 최소 관측치가 지나치게 적은 법정동을 분리 추정하지 않기 위한 사전 기준입니다.",
        pd.DataFrame(location_candidates).to_csv(sep="\t", index=False, float_format="%.6f").rstrip(),
        f"- 채택: {location_scheme}; 선택 우선순위는 단지 holdout MAPE, 동률 시 OOT MAPE입니다. 계층 사양의 최종 dense 법정동 수는 {len(dense_keys):,}개입니다.", "",
        "## Ablation (A--F, H--I는 완전히 동일한 정제 train/test 표본)", comparison.to_csv(sep="\t", index=False, float_format="%.6f").rstrip(),
        "- M2_reference_minus_model_MAPE_pp가 양수이면 해당 모델의 out-of-time MAPE가 해당 행의 M2 기준보다 낮습니다. 이 값의 유의성을 주장하지 않습니다.",
        f"- G complete-case 표본: train {len(train_cc):,}/{len(train):,}건 ({len(train_cc)/len(train)*100:.2f}%), test {len(test_cc):,}/{len(test):,}건 ({len(test_cc)/len(test)*100:.2f}%). 물리 feature가 모두 실제 존재하는 행만 남겼으므로 G의 물리항에는 평균대체가 없습니다.", "",
        "## 정비사업 signal의 정의·해석 한계",
        "- is_redevelop=1은 31.1에서 정비사업 구역 대표 지번과 단지 지번이 완전일치한 경우입니다. 0은 비매칭이며, 재건축 구역이 아님과 대표 지번 차이로 인한 매칭 실패를 구분하지 못합니다.",
        "- redevelop_stage_advanced=1은 관리처분·착공, 그 외 매칭된 추진단계는 0입니다. 미매칭도 0이나 is_redevelop과 함께 넣었으므로, 이 항의 해석은 is_redevelop=1인 단지 안에서의 단계 차이라는 조건부 해석입니다.",
        f"- 지번 완전일치 단지는 {n_redevelop_complexes:,}/{n_complexes_total:,} ({n_redevelop_complexes / n_complexes_total * 100:.2f}%)이며, advanced 단계는 {n_redevelop_advanced:,}단지입니다.",
        "- 31의 자체 검증 기준 재개발 구역 매칭은 5/302 (1.7%)에 그쳤습니다. 따라서 is_redevelop 계수는 '정비사업 구역' 일반의 효과가 아니라 사실상 '재건축 추진 아파트 단지'의 효과입니다.",
        "- 매칭 단지가 150개뿐이므로 cluster-robust CI의 폭을 함께 보고하며, 넓은 CI는 정밀한 효과 추정을 뒷받침하지 못합니다.", "",
        "## 정비사업 signal 계수 (I 모델, train; cluster-robust 95% CI)",
        redevelop_coefficients[["term", "coefficient_log_price", "cluster_robust_se", "p_value", "price_change_pct_per_unit", "price_change_pct_ci95_low", "price_change_pct_ci95_high", "interpretation"]].to_csv(sep="\t", index=False, float_format="%.8g").rstrip(),
        "- 가격 변화율은 exp(계수)-1로 변환했습니다. is_redevelop은 0→1, redevelop_stage_advanced는 매칭 단지 내 non-advanced→advanced의 조건부 비교입니다.", "",
        "## 물리 feature 전체의 cluster-robust joint Wald test (F 모델, train)",
        f"- H0: 물리항 및 유지된 물리 결측지시자의 계수가 모두 0; F({wald['df_num']:.0f}, {wald['df_denom']:.0f}) = {wald['f_stat']:.6f}, p = {wald['p_value']:.6g}; 항 수 = {wald['n_terms']}",
        f"- 검정 포함 항: {wald['terms']}", "",
        "## M2 대 M3 out-of-time paired block bootstrap (단지 복원추출, 1,000회)",
        f"- 차이 정의: M2 MAPE - M3 MAPE. 관측 차이 = {bootstrap['observed_m2_minus_m3_mape_pp']:.6f} percentage points; 95% CI = [{bootstrap['ci_lower']:.6f}, {bootstrap['ci_upper']:.6f}]; bootstrap 단지 수 = {bootstrap['n_complexes']:,}.",
        "- CI가 0을 포함하면 MAPE 차이에 대한 명확한 방향 주장을 하지 않습니다.", "",
        "## 물리 feature 공선성 진단 (F 모델의 train 변환값·유지된 결측지시자만 사용)",
        f"- design matrix rank = {diag['rank']}/{diag['n_columns_including_constant']}; rank deficient = {diag['rank_deficient']}; condition number = {diag['condition_number']:.6g}",
        f"- VIF에서 제외된 상수열: {diag['constant_columns_excluded_from_vif']}",
        f"- 중복으로 제거한 물리 결측지시자: {diag['dropped_duplicate_physical_indicators']}",
        f"- floor_band_UNKNOWN과 완전 공선성으로 회귀 및 joint test에서 제외한 물리 결측지시자: {diag['dropped_collinear_physical_columns']}",
        "- B는 15.1 실제 최고층의 삼등분 층대만 통제한 A에 profile availability를 추가한 모델입니다. 따라서 B의 증분은 profile 매칭 성공이라는 선택 효과 자체를 나타냅니다.",
        "", "### VIF",
        vif_table.to_csv(sep="\t", index=False, float_format="%.6f").rstrip(), "", "### Correlation matrix",
        correlation.to_csv(sep="\t", float_format="%.6f").rstrip(), "", "## 물리 계수의 실질 크기 (F 모델, train; cluster-robust 95% CI)",
        physical_coefficients[["term", "coefficient_log_price", "cluster_robust_se", "p_value", "price_change_pct_per_unit", "price_change_pct_ci95_low", "price_change_pct_ci95_high"]].to_csv(sep="\t", index=False, float_format="%.8g").rstrip(),
    ]
    COMPARISON_PATH.write_text("\n".join(report) + "\n", encoding="utf-8")
    baseline_report = [
        "# B2 baseline 세부 분해 및 M3 비교", "", "## 공통 규칙",
        f"- train {start} ~ {train_end} 거래만으로 B2의 직전 거래가·단지 평균 fallback·global fallback을 만들었습니다. test target은 어떤 집계에도 쓰지 않았습니다.",
        "- B2_minus_M3_MAPE_pp가 양수이면 해당 subgroup에서 M3 MAPE가 B2보다 낮습니다. 표본이 작은 subgroup의 차이는 불확실할 수 있으므로 유의성 주장을 하지 않습니다.", "", "## 전체 baseline 성능",
        baseline_table.to_csv(sep="\t", index=False, float_format="%.6f").rstrip(), "", "## B2 출처별: exact cell 직전 거래가 대 fallback",
        source_table.to_csv(sep="\t", index=False, float_format="%.6f").rstrip(), "", "## B2 직전 동일 셀 거래 이후 경과기간별",
        elapsed_table.to_csv(sep="\t", index=False, float_format="%.6f").rstrip(), "", "## train 기간 동일 단지×면적타입 셀 거래 수별",
        cell_n_table.to_csv(sep="\t", index=False, float_format="%.6f").rstrip(),
    ]
    BASELINE_PATH.write_text("\n".join(baseline_report) + "\n", encoding="utf-8")
    print(f"작성 완료: {COMPARISON_PATH}")
    print(f"작성 완료: {COEFFICIENTS_PATH}")
    print(f"작성 완료: {BASELINE_PATH}")
    print(f"최종 표본: train {len(train):,}건 | test {len(test):,}건")


if __name__ == "__main__":
    main()
