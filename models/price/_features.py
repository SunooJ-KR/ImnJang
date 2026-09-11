# ============================================================================
# _features.py
# ============================================================================
# Author:      yjkim
# Purpose:     가격 hedonic 모델의 공통 feature와 design matrix를 한 곳에서 관리한다.
# Description: 30.spike_hedonic.py의 I 모델과 33.train_coldstart.py가 동일한
#              M3+정비사업 signal, 결측 처리, 행정동 fixed effect를 사용한다.
# ============================================================================

from __future__ import annotations

import numpy as np
import pandas as pd
import statsmodels.api as sm


BASE_FEATURES = {
    "log_area_m2": ("excluUseAr", True),
    "built_year": ("built_year", False),
    "log_total_households": ("total_households", True),
}
LOCATION_FEATURES = {
    "log_station_dist_m": ("station_dist_m", True),
    "log_station_ridership_daily": ("station_ridership_daily", True),
    "log_elem_school_m": ("elem_school_m", True),
    "log_mid_school_m": ("mid_school_m", True),
    "log_general_hosp_m": ("general_hosp_m", True),
    "log_park_m": ("park_m", True),
    "log_park_area_m2": ("park_area_m2", True),
    "cvs_500m": ("cvs_500m", False),
    "restaurant_500m": ("restaurant_500m", False),
    "nightlife_300m": ("nightlife_300m", False),
    "log_dept_store_m": ("dept_store_m", True),
    "log_mart_m": ("mart_m", True),
    "log_road_arterial_dist_m": ("road_arterial_dist_m", True),
    "log_rail_centerline_m": ("rail_centerline_m", True),
    "far": ("far", False),
    "bcr": ("bcr", False),
    "parking_per_hh": ("parking_per_hh", False),
}
PHYSICAL_FEATURES = {
    "sun_hours_winter": ("sun_hours_winter", False),
    "open_angle_mean": ("open_angle_mean", False),
    "view_block_pct": ("view_block_pct", False),
    "open_span_max": ("open_span_max", False),
    "river_view": ("river_view", False),
    "park_view": ("park_view", False),
    "mountain_view": ("mountain_view", False),
}
REDEVELOP_FEATURES = {
    "is_redevelop": ("is_redevelop", False),
    "redevelop_stage_advanced": ("redevelop_stage_advanced", False),
}
M2_FEATURES = BASE_FEATURES | LOCATION_FEATURES
I_MODEL_FEATURES = M2_FEATURES | PHYSICAL_FEATURES | REDEVELOP_FEATURES

# 전세 feature는 30/33에서 같은 design-matrix 규칙(학습 평균 대체와 결측
# 지시자)을 쓰도록 여기에 둔다. 금액 자체가 아니라 만원/m²를 사용한다.
JEONSE_FEATURES = {
    "jeonse_per_m2_adj": ("jeonse_per_m2_adj", False),
    "log_jeonse_n_trades": ("jeonse_n_trades", True),
    "jeonse_months_since": ("jeonse_months_since", False),
    "is_move_in_period": ("is_move_in_period", False),
}


def model_features(include_jeonse: bool = False, sgg_interaction_levels: list[str] | None = None) -> dict[str, tuple[str, bool]]:
    """I 모델 또는 전세 확장 모델의 수치 feature 명세를 반환한다.

    `sgg_interaction_levels`는 train에서 확정한 자치구 수준만 받는다. 따라서
    test/cold-start의 새 자치구가 design matrix의 열을 늘리거나 누수를 만들지 않는다.
    """
    features = I_MODEL_FEATURES.copy()
    if include_jeonse:
        features |= JEONSE_FEATURES
        for level in sgg_interaction_levels or []:
            name = f"jeonse_x_sgg_{level}"
            features[name] = (name, False)
    return features


def add_jeonse_interactions(frame: pd.DataFrame, sgg_levels: list[str]) -> pd.DataFrame:
    """전세 단가의 자치구별 선형 기울기 열을 만든다.

    결측 전세 단가는 0으로 만들지 않는다. 본항은 build_design의 결측지시자와
    train 평균 대체를 유지하고, 교호항만 0으로 두어 해당 기울기를 적용하지 않는다.
    """
    result = frame.copy()
    value = pd.to_numeric(result["jeonse_per_m2_adj"], errors="coerce")
    sgg = result["sgg_code"].astype("string")
    for level in sgg_levels:
        result[f"jeonse_x_sgg_{level}"] = value.where(sgg.eq(level), 0.0).fillna(0.0)
    return result


def bool_to_float(series: pd.Series) -> pd.Series:
    """True/False 문자열과 Boolean을 1/0/결측으로 변환한다."""
    text = series.astype("string").str.strip().str.lower()
    return text.map({"true": 1.0, "false": 0.0, "1": 1.0, "0": 0.0}).astype(float)


def transformed_feature(frame: pd.DataFrame, source: str, use_log: bool) -> pd.Series:
    """수치형 입력을 안전하게 변환한다. 거리·카운트 log 변수는 log1p를 쓴다."""
    values = pd.to_numeric(frame[source], errors="coerce")
    if use_log:
        values = np.log1p(values.clip(lower=0))
    return values.astype(float)


def build_design(
    frame: pd.DataFrame, features: dict[str, tuple[str, bool]], context: dict | None = None,
) -> tuple[pd.DataFrame, dict]:
    """train context로 평균대체, 결측지시자, 층대·월·행정동 fixed effect를 고정한다."""
    if context is None:
        transformed = {name: transformed_feature(frame, source, use_log) for name, (source, use_log) in features.items()}
        means = {name: values.mean() for name, values in transformed.items()}
        indicators: list[str] = []
        dropped_duplicate_indicators: list[tuple[str, str]] = []
        seen_masks: dict[bytes, str] = {}
        for name, values in transformed.items():
            missing = values.isna()
            if not missing.any():
                continue
            signature = missing.to_numpy(dtype=np.uint8).tobytes()
            if signature in seen_masks:
                dropped_duplicate_indicators.append((name, seen_masks[signature]))
            else:
                indicators.append(name)
                seen_masks[signature] = name
        floor_levels = ["LOW"] + [x for x in sorted(frame["floor_band"].dropna().unique()) if x != "LOW"]
        month_values = sorted(frame["deal_ym"].astype(str).unique(), reverse=True)
        category_columns = ["floor_band", "deal_ym", "bjd_code"]
        if "jeonse_level" in frame.columns:
            category_columns.append("jeonse_level")
        context = {
            "means": means,
            "indicators": indicators,
            "levels": {
                "floor_band": floor_levels,
                "deal_ym": [month_values[0]] + month_values[1:],
                "bjd_code": sorted(frame["bjd_code"].astype(str).fillna("MISSING").unique()),
                **({"jeonse_level": ["NONE", "SALE_CELL", "RENT_COMPLEX", "RENT_CELL"]}
                   if "jeonse_level" in category_columns else {}),
            },
            "category_columns": category_columns,
            "dropped_duplicate_indicators": dropped_duplicate_indicators,
        }
    columns: dict[str, pd.Series] = {}
    for name, (source, use_log) in features.items():
        values = transformed_feature(frame, source, use_log)
        columns[name] = values.fillna(context["means"][name])
        if name in context["indicators"]:
            columns[f"miss_{name}"] = values.isna().astype(float)
    for category in context.get("category_columns", ["floor_band", "deal_ym", "bjd_code"]):
        levels = context["levels"][category]
        values = frame[category].astype(str).where(frame[category].astype(str).isin(levels), levels[0])
        dummies = pd.get_dummies(pd.Categorical(values, categories=levels), prefix=category, drop_first=True, dtype=float)
        dummies.index = frame.index
        columns.update({column: dummies[column] for column in dummies.columns})
    design = sm.add_constant(pd.DataFrame(columns, index=frame.index), has_constant="add").astype(float)
    if context.get("dropped_collinear_columns") is None:
        dropped_collinear_columns: list[str] = []
        unknown_column = "floor_band_UNKNOWN"
        if unknown_column in design:
            if "physical_profile_available" in design and np.array_equal(
                (design["physical_profile_available"] + design[unknown_column]).to_numpy(), np.ones(len(design))
            ):
                dropped_collinear_columns.append("physical_profile_available")
            for name in context["indicators"]:
                indicator = f"miss_{name}"
                if indicator in design and np.array_equal(design[indicator].to_numpy(), design[unknown_column].to_numpy()):
                    dropped_collinear_columns.append(indicator)
        context["dropped_collinear_columns"] = dropped_collinear_columns
    design = design.drop(columns=context["dropped_collinear_columns"], errors="ignore")
    if design.isna().any().any() or not np.isfinite(design.to_numpy()).all():
        raise ValueError("design matrix에 결측 또는 비유한 값이 남았습니다.")
    return design, context
