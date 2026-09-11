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


def normalize_administrative_code(values: pd.Series, width: int = 5) -> pd.Series:
    """Float로 읽힌 행정코드를 정수 문자열로 바꾸고 zero-pad한다."""
    numeric = pd.to_numeric(values, errors="coerce")
    valid = numeric.notna() & np.isfinite(numeric) & numeric.mod(1).eq(0) & numeric.ge(0)
    result = pd.Series(pd.NA, index=values.index, dtype="string")
    result.loc[valid] = numeric.loc[valid].astype("int64").astype(str).str.zfill(width)
    return result


def make_full_bjd_key(bjd_code: pd.Series, sgg_code: pd.Series) -> pd.Series:
    """시군구 5자리와 법정동 뒤 5자리를 결합한 10자리 법정동 키를 만든다."""
    bjd = normalize_administrative_code(bjd_code)
    sgg = normalize_administrative_code(sgg_code)
    return (sgg + bjd).where(sgg.notna() & bjd.notna(), pd.NA).astype("string")


def add_location_fe_columns(
    frame: pd.DataFrame, scheme: str, dense_bjd_keys: set[str] | None = None,
) -> pd.DataFrame:
    """선택한 지역 통제 사양에 필요한 범주형 열을 추가한다.

    ``full_bjd``는 완전 법정동 더미만 사용한다. ``hierarchical``는 자치구 더미와
    충분한 train 표본을 가진 법정동 더미를 함께 사용하며, 나머지는 자치구에 흡수한다.
    """
    result = frame.copy()
    if scheme == "full_bjd":
        result["location_bjd"] = result["bjd_full_key"].astype("string").fillna("MISSING")
    elif scheme == "hierarchical":
        if dense_bjd_keys is None:
            raise ValueError("hierarchical 지역 통제에는 train에서 확정한 dense 법정동 키가 필요합니다.")
        result["location_sgg"] = result["sgg_code"].astype("string").fillna("MISSING")
        bjd = result["bjd_full_key"].astype("string")
        # 법정동은 자치구에 nested되어 있으므로 각 자치구의 한 법정동(또는 sparse
        # 묶음)을 reference로 빼야 자치구 더미와 완전 공선성이 생기지 않는다.
        reference_by_sgg = {
            sgg: min(key for key in dense_bjd_keys if key.startswith(sgg))
            for sgg in {key[:5] for key in dense_bjd_keys}
        }
        is_reference = bjd.eq(result["location_sgg"].map(reference_by_sgg))
        # build_design은 범주를 정렬한 뒤 첫 수준을 버리므로 reference가 반드시
        # 첫 수준이 되도록 0-prefix를 둔다.
        result["location_bjd"] = bjd.where(
            bjd.isin(dense_bjd_keys) & ~is_reference, "00000_REFERENCE"
        ).fillna("00000_REFERENCE")
    else:
        raise ValueError(f"알 수 없는 지역 통제 사양: {scheme}")
    return result


def location_category_columns(scheme: str) -> list[str]:
    """지역 통제 사양의 design-matrix 범주형 열 이름을 반환한다."""
    if scheme == "full_bjd":
        return ["location_bjd"]
    if scheme == "hierarchical":
        return ["location_sgg", "location_bjd"]
    raise ValueError(f"알 수 없는 지역 통제 사양: {scheme}")


def build_design(
    frame: pd.DataFrame,
    features: dict[str, tuple[str, bool]],
    context: dict | None = None,
    location_columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict]:
    """train context로 평균대체, 결측지시자, 층대·월·지역 fixed effect를 고정한다."""
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
        category_columns = ["floor_band", "deal_ym"] + (location_columns or ["bjd_code"])
        if "jeonse_level" in frame.columns:
            category_columns.append("jeonse_level")
        context = {
            "means": means,
            "indicators": indicators,
            "levels": {
                "floor_band": floor_levels,
                "deal_ym": [month_values[0]] + month_values[1:],
                **{column: sorted(frame[column].astype(str).fillna("MISSING").unique()) for column in category_columns
                   if column not in {"floor_band", "deal_ym", "jeonse_level"}},
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
        # 결측 indicator와 지역 FE의 MISSING 더미처럼 서로 완전히 같은 이진 열은
        # 어느 범주 조합에서도 공선성을 만들 수 있다. 앞서 만든 수치 feature/결측
        # indicator를 우선 보존하고, 뒤에 생성된 dummy만 제외한다.
        seen_binary_columns: dict[bytes, str] = {}
        for column in design.columns:
            raw_values = design[column].to_numpy(dtype=float)
            if not np.isin(raw_values, [0.0, 1.0]).all():
                continue
            values = raw_values.astype(np.uint8)
            signature = values.tobytes()
            if signature in seen_binary_columns:
                dropped_collinear_columns.append(column)
            else:
                seen_binary_columns[signature] = column
        context["dropped_collinear_columns"] = dropped_collinear_columns
    design = design.drop(columns=context["dropped_collinear_columns"], errors="ignore")
    if design.isna().any().any() or not np.isfinite(design.to_numpy()).all():
        raise ValueError("design matrix에 결측 또는 비유한 값이 남았습니다.")
    return design, context
