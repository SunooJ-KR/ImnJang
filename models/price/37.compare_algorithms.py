# ============================================================================
# Author:      yjkim
# Purpose:     cold-start 가격/m² 예측 알고리즘을 동일한 L feature와 split에서 비교한다.
# Description: OLS, Ridge, RandomForest, LightGBM, XGBoost 및 CatBoost를 비교한다.
#              CatBoost만 완전 법정동 등 다섯 범주형 변수를 native categorical로
#              처리하며, 나머지 모델은 _features의 기존 dummy design을 사용한다.
# ============================================================================

from __future__ import annotations

import importlib.util
import json
import re
import time
from pathlib import Path
import sys

import numpy as np
import pandas as pd
import statsmodels.api as sm
from catboost import CatBoostRegressor
from lightgbm import LGBMRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GroupKFold, ParameterGrid
from xgboost import XGBRegressor

work_dir = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _features import (  # noqa: E402
    add_jeonse_interactions,
    add_location_fe_columns,
    build_design,
    location_category_columns,
    model_features,
    service_model_features,
    transformed_feature,
)
from _jeonse import JeonseFeatureBuilder  # noqa: E402

OUTPUT_PATH = work_dir / "output" / "37.1.algorithm_comparison.txt"
METRICS_PATH = work_dir / "output" / "33.2.coldstart_metrics.txt"
SOURCE_PATH = Path(__file__).with_name("33.train_coldstart.py")
SEED = 20260911
LOCATION_SCHEME = "hierarchical"
MIN_BJD_TRAIN_ROWS = 30
CAT_COLUMNS = ["bjd_full_key", "sgg_code", "floor_band", "jeonse_level", "deal_ym"]


def parse_adopted_jeonse_spec() -> dict[str, object]:
    """33.2의 채택 선언과 J/K/L 표에서 서비스 전세 사양을 읽는다."""
    lines = METRICS_PATH.read_text(encoding="utf-8").splitlines()
    adopted_line = next(
        (line for line in lines if line.startswith("- 전세 feature 채택:")), None
    )
    if adopted_line is None:
        raise ValueError(f"33.2에서 전세 feature 채택 선언을 찾지 못했습니다: {METRICS_PATH}")
    match = re.search(r"전세 feature 채택:\s*([^:]+):", adopted_line)
    if match is None:
        raise ValueError(f"33.2 채택 선언의 variant 형식을 읽지 못했습니다: {adopted_line}")
    adopted_variant = match.group(1).strip()

    candidate_line = next(
        (line for line in lines if line.startswith(f"{adopted_variant}:") and "\t" in line), None
    )
    if candidate_line is None:
        raise ValueError(f"33.2에서 채택 variant {adopted_variant!r}의 표 행을 찾지 못했습니다.")
    fields = candidate_line.split("\t")
    if len(fields) < 5:
        raise ValueError(f"33.2 채택 표 행의 열 수가 부족합니다: {candidate_line}")

    variant_label, family, d4_spec, _, holdout_mape = fields[:5]
    if family != "LightGBM":
        raise ValueError(f"37의 기준 model은 LightGBM이어야 합니다: {candidate_line}")
    if "순수 전세" in variant_label:
        jeonse_variant = "PURE"
    elif "5% 환산" in variant_label:
        jeonse_variant = "CONVERTED"
    else:
        raise ValueError(f"37이 처리할 수 없는 33.2 전세 variant입니다: {variant_label}")

    d4_spec_normalized = d4_spec.strip().lower()
    if d4_spec_normalized == "linear":
        use_sgg_interactions = False
    elif d4_spec_normalized == "sgg interaction":
        use_sgg_interactions = True
    else:
        raise ValueError(f"37이 처리할 수 없는 33.2 D4 사양입니다: {d4_spec}")
    return {
        "jeonse_variant": jeonse_variant,
        "use_sgg_interactions": use_sgg_interactions,
        "holdout_mape": float(holdout_mape),
        "adopted_line": adopted_line,
        "candidate_line": candidate_line,
    }


def load_coldstart_module():
    """33번의 데이터 정제·전세 feature 구현을 복사하지 않고 직접 불러온다."""
    spec = importlib.util.spec_from_file_location("coldstart33", SOURCE_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"33번 스크립트를 불러올 수 없습니다: {SOURCE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mape(actual: pd.Series, predicted: np.ndarray) -> float:
    values = actual.to_numpy(dtype=float)
    return float(np.mean(np.abs(values - np.asarray(predicted)) / values) * 100)


def household_mapes(frame: pd.DataFrame, predicted: np.ndarray) -> dict[str, float]:
    households = pd.to_numeric(frame["total_households"], errors="coerce")
    masks = {
        "le20": households.le(20),
        "21_100": households.between(21, 100),
        "101plus": households.ge(101),
    }
    return {
        name: mape(frame.loc[mask, "price_per_m2"], np.asarray(predicted)[mask.to_numpy()])
        if mask.any() else float("nan")
        for name, mask in masks.items()
    }


def make_cat_input(frame: pd.DataFrame, features: dict[str, tuple[str, bool]]) -> pd.DataFrame:
    """L 수치 feature와 CatBoost native categorical 열을 결합한다."""
    values = {
        name: transformed_feature(frame, source, use_log)
        for name, (source, use_log) in features.items()
    }
    result = pd.DataFrame(values, index=frame.index)
    for column in CAT_COLUMNS:
        result[column] = frame[column].astype("string").fillna("MISSING").astype(str)
    return result


def make_model(name: str, params: dict):
    """모든 model이 log(price/m²)를 예측하도록 deterministic estimator를 만든다."""
    if name == "OLS":
        return None
    if name == "Ridge":
        return Ridge(**params)
    if name == "RandomForest":
        return RandomForestRegressor(random_state=SEED, n_jobs=-1, **params)
    if name == "LightGBM":
        return LGBMRegressor(
            objective="regression", random_state=SEED, n_jobs=-1, verbosity=-1,
            n_estimators=300, learning_rate=0.05, subsample_freq=1, **params,
        )
    if name == "XGBoost":
        return XGBRegressor(
            objective="reg:squarederror", random_state=SEED, n_jobs=-1,
            n_estimators=300, learning_rate=0.05, tree_method="hist", verbosity=0,
            **params,
        )
    if name == "CatBoost":
        return CatBoostRegressor(
            loss_function="RMSE", random_seed=SEED, thread_count=-1,
            verbose=False, allow_writing_files=False, iterations=300,
            learning_rate=0.05, **params,
        )
    raise ValueError(f"알 수 없는 model: {name}")


def fit_predict(name: str, params: dict, x_train: pd.DataFrame, y_train: pd.Series,
                x_predict: pd.DataFrame) -> np.ndarray:
    """입력 종류에 맞춰 model을 적합하고 원 단위 price/m² 예측값을 반환한다."""
    if name == "OLS":
        model = sm.OLS(y_train.astype(float), x_train).fit()
        log_prediction = model.predict(x_predict)
    elif name == "CatBoost":
        model = make_model(name, params)
        model.fit(x_train, y_train.astype(float), cat_features=CAT_COLUMNS)
        log_prediction = model.predict(x_predict)
    else:
        model = make_model(name, params)
        model.fit(x_train, y_train.astype(float))
        log_prediction = model.predict(x_predict)
    return np.exp(np.asarray(log_prediction, dtype=float))


def standard_fold_inputs(fit_frame: pd.DataFrame, valid_frame: pd.DataFrame,
                         features: dict[str, tuple[str, bool]], location_columns: list[str]):
    """기존 dummy 방식은 fold fit의 결측 대체·범주 수준만 validation에 적용한다."""
    x_fit, context = build_design(fit_frame, features, None, location_columns)
    x_valid, _ = build_design(valid_frame, features, context, location_columns)
    return x_fit, x_valid


def choose_params(name: str, fit_frame: pd.DataFrame, features: dict[str, tuple[str, bool]],
                  location_columns: list[str], grid: dict) -> tuple[dict, float]:
    """단지 GroupKFold 3분할로 train 내부에서만 단 한 번의 grid를 탐색한다."""
    groups = fit_frame["apt_seq"].astype(str)
    splitter = GroupKFold(n_splits=3)
    folds = []
    for train_idx, valid_idx in splitter.split(fit_frame, groups=groups):
        fold_train, fold_valid = fit_frame.iloc[train_idx], fit_frame.iloc[valid_idx]
        if name == "CatBoost":
            x_train, x_valid = make_cat_input(fold_train, features), make_cat_input(fold_valid, features)
        else:
            x_train, x_valid = standard_fold_inputs(fold_train, fold_valid, features, location_columns)
        folds.append((fold_train, fold_valid, x_train, x_valid))

    best_params, best_score = None, float("inf")
    for params in ParameterGrid(grid):
        scores = []
        for fold_train, fold_valid, x_train, x_valid in folds:
            prediction = fit_predict(name, params, x_train, fold_train.y, x_valid)
            scores.append(mape(fold_valid.price_per_m2, prediction))
        score = float(np.mean(scores))
        if score < best_score:
            best_params, best_score = params, score
    if best_params is None:
        raise AssertionError(f"{name} tuning 결과가 없습니다.")
    return best_params, best_score


def inputs_for_evaluation(name: str, train_frame: pd.DataFrame, predict_frame: pd.DataFrame,
                          features: dict[str, tuple[str, bool]], location_columns: list[str]):
    if name == "CatBoost":
        return make_cat_input(train_frame, features), make_cat_input(predict_frame, features)
    return standard_fold_inputs(train_frame, predict_frame, features, location_columns)


def main():
    source = load_coldstart_module()
    adopted_spec = parse_adopted_jeonse_spec()
    jeonse_variant = str(adopted_spec["jeonse_variant"])
    use_sgg_interactions = bool(adopted_spec["use_sgg_interactions"])
    print("===== 1. 동일 L 데이터 준비 =====")
    print(
        f"  33.2 채택 사양: {jeonse_variant}, "
        f"자치구 교호항={use_sgg_interactions}",
        flush=True,
    )
    # 33.load_base 가 층대용 max_levels 를 추가로 반환하도록 바뀌었다
    raw, complex_df, *_ = source.load_base()
    print(f"  기본 매매·단지 data 로드: {len(raw):,}행", flush=True)
    rent = JeonseFeatureBuilder(source.RENT_PATH, complex_df, source.TRAIN_START, source.TRAIN_END)
    print("  전세 feature builder 초기화 완료", flush=True)
    train_raw = raw.loc[raw.deal_period.between(source.TRAIN_START, source.TRAIN_END)].copy()
    train, outlier_audit = source.clean_train(train_raw)
    test = raw.loc[raw.deal_period.between(source.TEST_START, source.TEST_END)].copy()
    dense_keys = source.dense_bjd_keys(train)
    train = add_location_fe_columns(train, LOCATION_SCHEME, dense_keys)
    test = add_location_fe_columns(test, LOCATION_SCHEME, dense_keys)
    print("  train 기간 이상치 cutoff 및 완전 법정동 FE 완료", flush=True)

    # 33.2의 실제 채택 variant와 D4 사양을 매 실행마다 파싱해 사용한다.
    sgg_levels = sorted(train.sgg_code.astype(str).unique())
    train, train_rent_audit = source.prepare(
        train, rent, jeonse_variant, source.TRAIN_END, use_sgg_interactions, sgg_levels,
    )
    print(f"  train 전세 {jeonse_variant} feature 결합 완료", flush=True)
    test, test_rent_audit = source.prepare(
        test, rent, jeonse_variant, source.TRAIN_END, use_sgg_interactions, sgg_levels,
    )
    print(f"  test 전세 {jeonse_variant} feature 결합 완료", flush=True)
    # 33 이 실제로 배포에 쓰는 사양과 같아야 비교가 의미 있다.
    # 물리 feature 는 예측 기여가 0으로 측정돼 서비스 모델에서 제외됐다.
    # 첫 인자는 전세 본항 포함 여부이고, 교호항 여부가 아니다. 채택 variant가
    # 있으므로 linear 사양이어도 전세 본항은 반드시 포함한다.
    interaction_levels = sgg_levels if use_sgg_interactions else []
    features = service_model_features(True, interaction_levels)
    location_columns = location_category_columns(LOCATION_SCHEME)
    fit_frame, holdout_frame = source.split(train)

    grids = {
        "OLS": {},
        "Ridge": {"alpha": [0.1, 1.0, 10.0, 100.0]},
        "RandomForest": {"n_estimators": [150], "max_depth": [16, None], "min_samples_leaf": [2, 8], "max_features": [0.8]},
        "LightGBM": {"num_leaves": [31], "min_child_samples": [40], "subsample": [0.8], "colsample_bytree": [0.8], "reg_lambda": [1.0]},
        "XGBoost": {"max_depth": [4, 7], "min_child_weight": [5, 20], "subsample": [0.8], "colsample_bytree": [0.8], "reg_lambda": [1.0]},
        "CatBoost": {"depth": [6, 8], "l2_leaf_reg": [3.0, 10.0], "random_strength": [1.0]},
    }
    assert all(len(list(ParameterGrid(grid))) <= 12 for grid in grids.values())
    assert set(fit_frame.apt_seq).isdisjoint(set(holdout_frame.apt_seq))
    assert train.deal_period.max() == source.TRAIN_END
    assert test.deal_period.min() == source.TEST_START

    print(f"  train {len(train):,}행 / fit {len(fit_frame):,}행 / holdout {len(holdout_frame):,}행 / test {len(test):,}행", flush=True)
    print("===== 2. train 내부 CV tuning 및 평가 =====", flush=True)
    results = []
    row_count_checks = []
    for name, grid in grids.items():
        # OLS에는 조정할 hyperparameter가 없으므로 CV 탐색을 수행하지 않는다.
        if name == "OLS":
            params, cv_mape = {}, float("nan")
        else:
            params, cv_mape = choose_params(name, fit_frame, features, location_columns, grid)

        # 주 지표는 33번과 같은 stable hash 단지 holdout이며 tuning에는 쓰지 않는다.
        x_fit, x_holdout = inputs_for_evaluation(name, fit_frame, holdout_frame, features, location_columns)
        holdout_prediction = fit_predict(name, params, x_fit, fit_frame.y, x_holdout)
        assert len(holdout_prediction) == len(holdout_frame), f"{name} holdout 예측 행 수 불일치"
        holdout_mape = mape(holdout_frame.price_per_m2, holdout_prediction)
        bins = household_mapes(holdout_frame, holdout_prediction)

        # 운영 재빌드 비용은 전체 train 적합 및 미래 6개월 test 예측 시간으로 측정한다.
        x_train, x_test = inputs_for_evaluation(name, train, test, features, location_columns)
        fit_start = time.perf_counter()
        if name == "OLS":
            full_model = sm.OLS(train.y.astype(float), x_train).fit()
        elif name == "CatBoost":
            full_model = make_model(name, params)
            full_model.fit(x_train, train.y.astype(float), cat_features=CAT_COLUMNS)
        else:
            full_model = make_model(name, params)
            full_model.fit(x_train, train.y.astype(float))
        fit_seconds = time.perf_counter() - fit_start
        predict_start = time.perf_counter()
        oot_prediction = np.exp(np.asarray(full_model.predict(x_test), dtype=float))
        predict_seconds = time.perf_counter() - predict_start
        assert len(oot_prediction) == len(test), f"{name} OOT 예측 행 수 불일치"
        row_count_checks.append((name, len(holdout_prediction), len(oot_prediction)))
        oot_mape = mape(test.price_per_m2, oot_prediction)
        results.append({
            "model": name, "holdout_MAPE_pct": holdout_mape, "oot_MAPE_pct": oot_mape,
            "holdout_MAPE_le20": bins["le20"], "holdout_MAPE_21_100": bins["21_100"],
            "holdout_MAPE_101plus": bins["101plus"], "fit_seconds": fit_seconds,
            "predict_seconds": predict_seconds, "cv_MAPE_pct": cv_mape, "hyperparams": params,
        })
        print(f"  {name}: holdout {holdout_mape:.3f}%, OOT {oot_mape:.3f}% (CV {cv_mape:.3f}%)", flush=True)

    result_frame = pd.DataFrame(results).sort_values("holdout_MAPE_pct").reset_index(drop=True)
    lightgbm_holdout = float(result_frame.loc[result_frame.model.eq("LightGBM"), "holdout_MAPE_pct"].iloc[0])
    source_lightgbm_holdout = float(adopted_spec["holdout_mape"])
    lightgbm_holdout_delta = lightgbm_holdout - source_lightgbm_holdout
    best = result_frame.iloc[0]
    improvement = lightgbm_holdout - float(best.holdout_MAPE_pct)
    catboost_oot = float(result_frame.loc[result_frame.model.eq("CatBoost"), "oot_MAPE_pct"].iloc[0])
    lightgbm_oot = float(result_frame.loc[result_frame.model.eq("LightGBM"), "oot_MAPE_pct"].iloc[0])
    if best.model == "LightGBM" or improvement < 0.1:
        recommendation = (
            "LightGBM 유지: 최저 holdout MAPE와의 차이가 0.1%p 미만이므로 model 교체의 "
            "실무적 의미가 작습니다. 재학습 시간과 기존 운영 경로를 고려했습니다."
        )
    else:
        recommendation = (
            f"{best.model} 검토 권고: LightGBM 대비 holdout MAPE가 {improvement:.3f}%p 낮습니다. "
            f"다만 CatBoost의 OOT MAPE는 LightGBM보다 {catboost_oot - lightgbm_oot:+.3f}%p 높아 "
            "시간 일반화 원인은 추가 점검이 필요합니다. 이는 33번의 채택 model을 변경하지 않으며 "
            "별도 승인 전까지 LightGBM을 유지합니다."
        )

    checks = [
        ("모든 model의 holdout/test 행 수 일치", all(n_holdout == len(holdout_frame) and n_oot == len(test) for _, n_holdout, n_oot in row_count_checks),
         f"holdout={len(holdout_frame):,}, test={len(test):,}; " + ", ".join(f"{name}={n_holdout}/{n_oot}" for name, n_holdout, n_oot in row_count_checks)),
        ("test 기간 데이터가 학습·tuning에 미사용", train.deal_period.max() == source.TRAIN_END and test.deal_period.min() == source.TEST_START,
         f"train 최대={train.deal_period.max()}, test 최소={test.deal_period.min()}"),
        ("단지 holdout fit/validation 단지 비중첩", set(fit_frame.apt_seq).isdisjoint(set(holdout_frame.apt_seq)),
         f"교집합={len(set(fit_frame.apt_seq) & set(holdout_frame.apt_seq))}"),
        ("완전 법정동 키 사용 및 자치구 혼입 없음", raw.bjd_full_key.nunique() >= 300 and raw.dropna(subset=["bjd_full_key"]).groupby("bjd_full_key").sgg_code.nunique().le(1).all(),
         f"완전 키={raw.bjd_full_key.nunique():,}, dense={len(dense_keys):,}"),
        ("모든 grid 후보 조합 12개 이하", all(len(list(ParameterGrid(grid))) <= 12 for grid in grids.values()),
         ", ".join(f"{name}={len(list(ParameterGrid(grid)))}" for name, grid in grids.items())),
    ]
    table_columns = [
        "model", "holdout_MAPE_pct", "oot_MAPE_pct", "holdout_MAPE_le20",
        "holdout_MAPE_21_100", "holdout_MAPE_101plus", "fit_seconds", "predict_seconds", "hyperparams",
    ]
    table = result_frame[table_columns].copy()
    table["hyperparams"] = table["hyperparams"].map(lambda value: json.dumps(value, sort_keys=True))
    lines = [
        "# 37.1 cold-start 알고리즘 비교",
        "",
        "## 33.2 채택 사양 (실행 시 파싱)",
        f"- 사용 사양: 전세 variant={jeonse_variant}, 자치구 교호항={use_sgg_interactions}.",
        f"- 33.2 채택 선언 근거: `{adopted_spec['adopted_line']}`",
        f"- 33.2 채택 표 행 근거: `{adopted_spec['candidate_line']}`",
        f"- LightGBM holdout MAPE 대조: 33.2={source_lightgbm_holdout:.6f}%, 37={lightgbm_holdout:.6f}%, 차이={lightgbm_holdout_delta:+.6f}%p.",
        "",
        "## 결과",
        table.to_csv(sep="\t", index=False, float_format="%.6f").rstrip(),
        "",
        "## 비교 조건",
        f"- random seed: {SEED}. 최근 24개월 중 train={source.TRAIN_START}~{source.TRAIN_END}, test={source.TEST_START}~{source.TEST_END}입니다.",
        f"- L feature: 33.2에서 파싱한 전세 {jeonse_variant}, 자치구 교호항={use_sgg_interactions} 사양을 사용했습니다. train 이상치 cutoff만 적합하여 {outlier_audit['removed']:,}행을 제거했습니다.",
        "- OLS/Ridge/RandomForest/LightGBM/XGBoost는 `_features.build_design()`의 기존 dummy 방식(층대·월·지역 FE·전세 수준)을 사용했습니다.",
        "- CatBoost는 dummy를 만들지 않고 완전 법정동 키(`bjd_full_key`), 자치구(`sgg_code`), `floor_band`, `jeonse_level`, `deal_ym`을 native `cat_features`로 넘겼습니다. 수치 L feature는 동일하게 사용했습니다.",
        "- tuning은 stable hash holdout을 제외한 fit 단지의 GroupKFold 3분할에서만 실시했습니다. model당 grid는 1회, 최대 4개 후보입니다.",
        "- fit_seconds는 전체 train 재적합 시간, predict_seconds는 전체 OOT test 예측 시간입니다. 환경의 CPU 부하에 따라 달라질 수 있습니다.",
        "",
        "## 최종 hyperparameter 및 LightGBM 대비",
    ]
    for row in result_frame.itertuples(index=False):
        delta = lightgbm_holdout - row.holdout_MAPE_pct
        lines.append(f"- {row.model}: {json.dumps(row.hyperparams, sort_keys=True)}; LightGBM 대비 holdout {delta:+.3f}%p")
    lines += ["", "## 결론", f"- {recommendation}", "", "===== 3. 자체 검증 ====="]
    if abs(lightgbm_holdout_delta) > 0.1:
        lines.append(
            "- [WARN] LightGBM holdout MAPE가 33.2와 0.1%p를 초과해 차이납니다. "
            "같은 사양·split 여부 외에 33과 37의 estimator hyperparameter 또는 tuning 절차 차이를 점검해야 합니다."
        )
    lines += [f"- [{'PASS' if passed else 'FAIL'}] {label}: {detail}" for label, passed, detail in checks]
    OUTPUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  저장: {OUTPUT_PATH.relative_to(work_dir)}")
    assert all(passed for _, passed, _ in checks), "필수 자체 검증 실패"


if __name__ == "__main__":
    main()
