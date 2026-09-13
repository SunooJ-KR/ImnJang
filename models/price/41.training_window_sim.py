# ============================================================================
# 41.training_window_sim.py   (가번호 — PR 직전에 확정)
# ============================================================================
# Author:      jieun
# Purpose:     학습 기간 설정이 지정 이후 구간의 예측 오차·예측구간 coverage에 주는 영향을 측정한다
# Description: docs/analysis-plan-regulation-break.md 절차 04. 후보·지표·선택 규칙은
#              03 결과를 보기 전에 정한 그대로 동결했다(계획서 '04 사양 동결').
#
#              모델: LightGBM, 팀 서비스 사양(33)과 같은 정의를 독립 구현
#                feature  기본 3(log 면적·준공연도·log 세대수) + 입지 17 + 정비사업 2
#                         (전세 feature 없음 → 팀원 33.2의 J와 같은 사양)
#                더미     층대(LOW 기준) · 거래월(학습 마지막 달 기준, 학습에 없는 미래
#                         달은 기준 달로 처리) · 자치구 + 학습 30건 이상 법정동
#                결측     학습 평균 대체 + 결측 지시자(결측 패턴이 같은 지시자는 하나만)
#                하이퍼파라미터 33과 동일, 300 rounds, 모든 창 동일
#                이상치   각 학습 표본 안에서만 단지×면적타입별 floor(n×1%) 양쪽 절단
#
#              학습 기간 후보 (모두 2026-02-28까지, 선지정 9개 동·확정 중복 제외 표본)
#                A 2024-09~2026-02 (현행)
#                D A + 지정 이후 지시변수(자치구별 시작일 기준)   ← 사전 지정 주 후보
#                C 자치구별 지정 이후 거래만 (4개 구 2025-03-24~, 21개 구 2025-10-20~)
#                E A + 최근 가중 (반감기 6개월, 각 적합의 마지막 학습일 기준)
#
#              평가 (단지 해시 80/20 × 기간 분리)
#                학습은 해시 80% 단지만. 주 평가 표본은 해시 20% 단지의 지정 이후 거래
#                  선택 구간 2026-03~05 / 확인 구간 2026-06~07 (2026-08은 전부 제외)
#                MAPE·MdAPE를 전체·집단(4개 구/21개 구)·월별로
#                보조 표본: 해시 80% 단지의 같은 기간 거래 — 전체 / 학습에 같은
#                  단지×면적타입 셀이 있는 거래 (33의 OOT처럼 '본 단지'를 맞히는 경우)
#                80% 예측구간: 창마다 마지막 3개월(2025-12~2026-02)을 보정 구간으로
#                  split conformal. 보정 모델은 그 이전·해시 80% 단지로 적합하고
#                  해시 20% 단지의 보정 구간 잔차 10/90 분위로 구간을 만든다(33과 같은 구조)
#                차이 CI: 같은 평가 표본에 대한 단지 블록 paired bootstrap(집단 층화,
#                  2,000회). 모델 재학습은 하지 않으므로 학습 변동을 뺀 조건부 CI다
#
#              판단 기준 (계획서, 사전 지정)
#                D가 선택·확인 두 구간 모두에서
#                  (1) A 대비 MAPE 0.5%p 이상 낮음
#                  (2) 차이의 bootstrap 95% CI가 0을 제외
#                  (3) 4개 구·21개 구 어느 쪽도 0.5%p 넘게 나빠지지 않음
#                  (4) 80% coverage가 A보다 80%에서 더 멀어지지 않음
#                모두 충족할 때만 "33에서 재검증할 가설"로 제안. C·E는 탐색 결과로만
#
#              외부 검토(codex) 반영:
#                - 보정 표본을 절단하지 않고, 보정 모델 학습 표본 안에서만 다시 절단한다
#                - 구간 분위는 유한표본 conformal 순위를 쓴다
#                - 판단 기준 (2)는 개선 방향(CI 상한 < 0)으로 명시한다
#                - 주 구간은 33과 같은 구조라 보정 모델 ≠ 최종 모델, 보정 단지 = 평가 단지다.
#                  엄밀한 split conformal이 아니므로 단지 분리·같은 모델 구간을 사후 민감도로 병기한다
#                - 사후 진단(선택 규칙과 무관): 부호 있는 오차, log(D/A) 예측 이동폭,
#                  시점 proxy 대조 T1(서울 공통 2025-10-20 이후) · T2(2025-12-01 이후)
#
#              민감도: 정비사업 feature 2개를 뺀 사양(단지 속성이 2026-09 시점 값이라
#                가장 시변적인 feature의 미래 정보 누수 가능성)
#              충실도: 현행 A를 33의 J 조건(23.1 단지·300㎡ 이하·선지정 동/중복 포함·
#                2026-08 포함, 해시 분리 없는 OOT / 학습 기간 안 해시 holdout)으로
#                재현해 J(OOT 13.33% / holdout 12.65%)와 대조. 지역 키가 팀원은
#                법정동 코드, 여기는 자치구+법정동명이라 완전히 같지는 않다
#
#              통과 기준(구현 점검):
#                (1) 평가 단지(해시 20%)가 어느 학습·보정 모델 적합에도 없음
#                (2) 2026-08 거래가 학습·보정·평가 어디에도 없음
#                (3) 평가 표본은 절단되지 않음 (주 표본 행 수와 일치)
#                (4) 모든 창의 보정 표본 500행 이상
#                (5) D의 평가 거래 지정 이후 지시변수가 전부 1
#                (6) 예측값 결측·비유한 0건
#                (7) 주 표본이 전부 신고 완결 월
#                (8) 보정 표본 미절단
#                (9) 단지 분리 민감도에서 보정 단지와 평가 단지가 겹치지 않음
# ============================================================================

# ============================================================================
# 0. 환경 설정
# ============================================================================

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.font_manager as fm
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from lightgbm import Dataset, train as lgb_train

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

INPUT_PATH = output_dir / "38.1.trades_regulation.txt"
COMPLEX_PATH = output_dir / "23.1.complex.txt"
RESULT_PATH = output_dir / "41.1.window_comparison.txt"
FIGURE_PATH = output_dir / "41.2.window_comparison.png"

TRAIN_START, TRAIN_END = pd.Timestamp("2024-09-01"), pd.Timestamp("2026-02-28")
CAL_START = pd.Timestamp("2025-12-01")            # 창마다 마지막 3개월
PERIODS = {"select": (202603, 202605), "confirm": (202606, 202607)}
PERIOD_LABEL = {"select": "선택 2026-03~05", "confirm": "확인 2026-06~07"}
J_REFERENCE = {"oot": 13.327491, "holdout": 12.653804}   # 팀원 33.2 (전세 없음 J)

BASE_FEATURES = {"log_area_m2": ("excluUseAr", True), "built_year": ("built_year", False),
                 "log_total_households": ("total_households", True)}
LOCATION_FEATURES = {
    "log_station_dist_m": ("station_dist_m", True),
    "log_station_ridership_daily": ("station_ridership_daily", True),
    "log_elem_school_m": ("elem_school_m", True), "log_mid_school_m": ("mid_school_m", True),
    "log_general_hosp_m": ("general_hosp_m", True), "log_park_m": ("park_m", True),
    "log_park_area_m2": ("park_area_m2", True), "cvs_500m": ("cvs_500m", False),
    "restaurant_500m": ("restaurant_500m", False), "nightlife_300m": ("nightlife_300m", False),
    "log_dept_store_m": ("dept_store_m", True), "log_mart_m": ("mart_m", True),
    "log_road_arterial_dist_m": ("road_arterial_dist_m", True),
    "log_rail_centerline_m": ("rail_centerline_m", True),
    "far": ("far", False), "bcr": ("bcr", False), "parking_per_hh": ("parking_per_hh", False),
}
REDEVELOP_FEATURES = {"is_redevelop": ("is_redevelop", False),
                      "redevelop_stage_advanced": ("redevelop_stage_advanced", False)}
SERVICE_FEATURES = BASE_FEATURES | LOCATION_FEATURES | REDEVELOP_FEATURES
NO_REDEVELOP_FEATURES = BASE_FEATURES | LOCATION_FEATURES

LGB_PARAMS = {"objective": "regression", "learning_rate": .05, "num_leaves": 31,
              "min_data_in_leaf": 40, "feature_fraction": .8, "bagging_fraction": .8,
              "bagging_freq": 1, "lambda_l2": 1, "seed": 20260911, "verbosity": -1,
              "num_threads": 8}
NUM_ROUNDS = 300
MIN_DENSE_REGION_ROWS = 30
MIN_CAL_ROWS = 500
HALF_LIFE_MONTHS = 6
N_BOOT = 2000
BOOT_SEED = 20260914
THRESHOLD_PP = 0.5

WINDOWS = ["A", "D", "C", "E"]
WINDOW_LABEL = {"A": "A 현행", "D": "D 지정 지시변수", "C": "C 지정 이후만", "E": "E 최근 가중"}
GROUP_LABEL = {"early4": "4개 구", "late21": "21개 구"}

font_path = Path.home() / ".fonts" / "NotoSansKR-Regular.ttf"
if font_path.exists():
    fm.fontManager.addfont(str(font_path))
    plt.rcParams["font.family"] = fm.FontProperties(fname=str(font_path)).get_name()
plt.rcParams["axes.unicode_minus"] = False

# ============================================================================
# 1. 입력
# ============================================================================

print("===== 1. 입력 =====")
raw = pd.read_csv(INPUT_PATH, sep="\t", low_memory=False,
                  dtype={"aptSeq": str, "sggCd": str, "floor_band": str})
raw["deal_date"] = pd.to_datetime(raw["deal_date"])
raw["treat_start"] = pd.to_datetime(raw["treat_start"])
for column in ["post", "in_main_sample", "pre_designated_dong", "is_duplicate", "eval_complete"]:
    raw[column] = raw[column].astype(str).str.lower().eq("true")
raw["floor_band"] = raw["floor_band"].fillna("UNKNOWN")
raw["holdout"] = (pd.util.hash_pandas_object(raw["aptSeq"], index=False)
                  .to_numpy(dtype=np.uint64) % 5 == 0)
raw["hash5"] = (pd.util.hash_pandas_object(raw["aptSeq"], index=False)
                .to_numpy(dtype=np.uint64) % 5).astype(int)
raw["fe_key"] = raw["aptSeq"] + "|" + raw["area_type"].astype(str)
print(f"  38.1 {len(raw):,}행, 주 표본 {int(raw['in_main_sample'].sum()):,}행, "
      f"해시 20% 단지 {raw.loc[raw['holdout'], 'aptSeq'].nunique():,}개")

main = raw[raw["in_main_sample"]].copy()

# ============================================================================
# 2. 설계행렬 · 적합 · 예측구간
# ============================================================================

def transformed(frame, source, use_log):
    values = pd.to_numeric(frame[source], errors="coerce").astype(float)
    return np.log1p(values.clip(lower=0)) if use_log else values


def make_context(train, features, extra_numeric):
    """학습 표본만으로 평균·결측 지시자·범주 수준·지역 기준을 확정한다."""
    means, indicators, seen = {}, [], set()
    for name, (source, use_log) in features.items():
        values = transformed(train, source, use_log)
        means[name] = values.mean()
        signature = values.isna().to_numpy().tobytes()
        if values.isna().any() and signature not in seen:
            indicators.append(name)
            seen.add(signature)
    counts = train["region_key"].value_counts()
    dense = set(counts[counts >= MIN_DENSE_REGION_ROWS].index)
    reference = {}
    for key in sorted(dense):
        reference.setdefault(key.split("_")[0], key)   # 자치구마다 첫 법정동을 기준으로
    months = sorted(train["deal_ym"].unique(), reverse=True)
    context = {
        "features": features, "extra": extra_numeric, "means": means, "indicators": indicators,
        "dense": dense, "reference": reference,
        "levels": {
            "floor_band": ["LOW"] + sorted(set(train["floor_band"]) - {"LOW"}),
            "deal_ym": [str(m) for m in months],
            "sgg": sorted(train["sggCd"].unique()),
        },
        "unseen_months": 0,
    }
    context["levels"]["region"] = ["00000_REFERENCE"] + sorted(dense - set(reference.values()))
    design = build_design(train, context)
    # 학습 표본에서 서로 완전히 같은 이진 열은 뒤에 생긴 열을 뺀다(33 _features.build_design과
    # 같은 규칙). 예측 대상에서 두 열이 달라지면 정보가 사라지므로 fit_predict에서 따로 점검한다
    dropped, twins, signatures = [], {}, {}
    for column in design.columns:
        values = design[column].to_numpy()
        if np.isin(values, [0.0, 1.0]).all():
            signature = values.astype(np.uint8).tobytes()
            if signature in signatures:
                dropped.append(column)
                twins[column] = signatures[signature]
            else:
                signatures[signature] = column
    context["dropped"], context["twins"] = dropped, twins
    return context


def build_design(frame, context):
    columns = {}
    for name, (source, use_log) in context["features"].items():
        values = transformed(frame, source, use_log)
        columns[name] = values.fillna(context["means"][name])
        if name in context["indicators"]:
            columns[f"miss_{name}"] = values.isna().astype(float)
    for name in context["extra"]:
        columns[name] = frame[name].astype(float)
    region = frame["region_key"].where(frame["region_key"].isin(context["dense"])
                                       & ~frame["region_key"].isin(context["reference"].values()),
                                       "00000_REFERENCE")
    categorical = {"floor_band": frame["floor_band"], "deal_ym": frame["deal_ym"].astype(str),
                   "sgg": frame["sggCd"], "region": region}
    for category, values in categorical.items():
        levels = context["levels"][category]
        values = values.where(values.isin(levels), levels[0])
        dummies = pd.get_dummies(pd.Categorical(values, categories=levels), prefix=category,
                                 drop_first=True, dtype=float)
        dummies.index = frame.index
        columns.update({column: dummies[column] for column in dummies.columns})
    design = pd.DataFrame(columns, index=frame.index)
    design = design.drop(columns=context.get("dropped", []), errors="ignore")
    if not np.isfinite(design.to_numpy()).all():
        raise SystemExit("설계행렬에 결측·비유한 값이 있다")
    return design


def trim_train(frame):
    """단지×면적타입별 양 끝 floor(n×1%)건 절단 — 학습 표본 안에서만 쓴다."""
    group = frame.groupby("fe_key").price_per_m2
    cut = np.floor(group.transform("size") * .01).astype(int)
    keep = ((group.rank(method="first") > cut)
            & (group.rank(method="first", ascending=False) > cut))
    return frame[keep].copy()


def recency_weight(frame, end_date):
    age_months = (end_date - frame["deal_date"]).dt.days / 30.4375
    return np.power(0.5, age_months / HALF_LIFE_MONTHS).to_numpy()


FIT_LOG = []


def fit_predict(train, targets, features, extra, weighted, label):
    """학습 표본으로 적합하고 targets(dict of frame)의 ㎡당 가격 예측값을 돌려준다."""
    context = make_context(train, features, extra)
    x_train = build_design(train, context)
    weight = recency_weight(train, train["deal_date"].max()) if weighted else None
    data = Dataset(x_train, label=train["y_log_ppm2"].astype(float), weight=weight,
                   feature_name=list(x_train.columns), free_raw_data=False)
    model = lgb_train(LGB_PARAMS, data, num_boost_round=NUM_ROUNDS)
    predictions = {}
    for name, frame in targets.items():
        if frame.empty:
            predictions[name] = np.array([])
            continue
        unseen = int((~frame["deal_ym"].astype(str).isin(context["levels"]["deal_ym"])).sum())
        full = build_design(frame, {**context, "dropped": []})
        twin_mismatch = {column: int((full[column] != full[twin]).sum())
                         for column, twin in context["twins"].items()}
        predictions[name] = np.exp(model.predict(full.drop(columns=context["dropped"])))
        FIT_LOG.append({"fit": label, "target": name, "n_train": len(train),
                        "train_apts": train["aptSeq"].nunique(),
                        "train_holdout_apts": int(train.loc[train["holdout"], "aptSeq"].nunique()),
                        "train_max_ym": int(train["deal_ym"].max()), "n_target": len(frame),
                        "target_unseen_month_rows": unseen, "n_columns": x_train.shape[1],
                        "dropped_twins": ";".join(f"{c}={t}" for c, t in context["twins"].items()) or "-",
                        "twin_mismatch_rows": int(sum(twin_mismatch.values())),
                        "pred_finite": bool(np.isfinite(predictions[name]).all())})
    return predictions


def window_raw(window):
    frame = main[main["deal_date"].between(TRAIN_START, TRAIN_END)]
    return frame[frame["post"]] if window == "C" else frame


def window_sample(window):
    return trim_train(window_raw(window))


def conformal_bounds(residual, level=.80):
    """유한표본 split conformal 순위 — 하한 floor((n+1)α/2)번째, 상한 ceil((n+1)(1-α/2))번째."""
    ordered = np.sort(residual)
    n, tail = len(ordered), (1 - level) / 2
    k_lo = max(int(np.floor((n + 1) * tail)), 1)
    k_hi = min(int(np.ceil((n + 1) * (1 - tail))), n)
    return float(ordered[k_lo - 1]), float(ordered[k_hi - 1])


def window_spec(window, features):
    extra = ["post_indicator"] if window == "D" else []
    return features, extra, window == "E"


main["post_indicator"] = main["post"].astype(float)
main["seoul_post"] = (main["deal_date"] >= pd.Timestamp("2025-10-20")).astype(float)   # 사후 진단 T1
main["recent3"] = (main["deal_date"] >= CAL_START).astype(float)                       # 사후 진단 T2
evaluation = main[main["holdout"] & main["deal_ym"].between(202603, 202607)].copy()
seen_eval = main[~main["holdout"] & main["deal_ym"].between(202603, 202607)].copy()
position = pd.Series(np.arange(len(evaluation)), index=evaluation.index)
evaluation["period"] = np.where(evaluation["deal_ym"] <= 202605, "select", "confirm")
seen_eval["period"] = np.where(seen_eval["deal_ym"] <= 202605, "select", "confirm")
print(f"  평가 표본(해시 20% 단지) {len(evaluation):,}행 / 보조(해시 80% 단지) {len(seen_eval):,}행")

print("===== 2. 창별 적합 =====")
pred, intervals, cal_sizes, window_sizes, fe_cells = {}, {}, {}, {}, {}
pred_noredev, cal_bias, sep_cover, sep_sizes = {}, {}, {}, {}
for window in WINDOWS:
    sample = window_sample(window)
    fit_sample = sample[~sample["holdout"]]
    window_sizes[window] = {"trimmed_rows": len(sample), "fit_rows": len(fit_sample),
                            "fit_apts": fit_sample["aptSeq"].nunique(),
                            "first_date": str(fit_sample["deal_date"].min().date())}
    fe_cells[window] = set(fit_sample["fe_key"])
    features, extra, weighted = window_spec(window, SERVICE_FEATURES)
    out = fit_predict(fit_sample, {"eval": evaluation, "seen": seen_eval}, features, extra,
                      weighted, f"{window}")
    pred[window], pred[f"{window}_seen"] = out["eval"], out["seen"]

    # 33과 같은 구조: 보정 이전·해시 80% 단지로 적합한 보정 모델의 잔차 분위를 최종 모델에 붙인다.
    # 절단은 보정 모델의 학습 표본 안에서만 다시 하고, 보정 표본은 절단하지 않는다(외부 검토 반영)
    unit = window_raw(window)
    inner_fit = trim_train(unit[~unit["holdout"] & (unit["deal_date"] < CAL_START)])
    inner_cal = unit[unit["holdout"] & (unit["deal_date"] >= CAL_START)]
    cal_sizes[window] = {"inner_fit": len(inner_fit), "inner_cal": len(inner_cal)}
    cal_pred = fit_predict(inner_fit, {"cal": inner_cal}, features, extra, weighted,
                           f"{window}_inner")["cal"]
    residual = np.log(inner_cal["price_per_m2"].to_numpy()) - np.log(cal_pred)
    if not np.isfinite(residual).all():
        raise SystemExit(f"{window} 보정 잔차에 비유한 값")
    lo, hi = conformal_bounds(residual)
    intervals[window] = (lo, hi)
    cal_bias[window] = float(np.median(residual) * 100)

    # 민감도(사후): 단지 분리·같은 모델 split conformal. 해시 버킷 1 단지를 보정 전용으로 두고
    # 버킷 2~4 단지의 보정 이전 거래로 한 모델만 적합해 보정 잔차와 평가 예측을 모두 만든다
    sep_fit = trim_train(unit[(unit["hash5"] >= 2) & (unit["deal_date"] < CAL_START)])
    sep_cal = unit[(unit["hash5"] == 1) & (unit["deal_date"] >= CAL_START)]
    sep_out = fit_predict(sep_fit, {"cal": sep_cal, "eval": evaluation}, features, extra,
                          weighted, f"{window}_sep")
    sep_residual = np.log(sep_cal["price_per_m2"].to_numpy()) - np.log(sep_out["cal"])
    sep_lo, sep_hi = conformal_bounds(sep_residual)
    sep_cover[window] = ((evaluation["price_per_m2"] >= sep_out["eval"] * np.exp(sep_lo))
                         & (evaluation["price_per_m2"] <= sep_out["eval"] * np.exp(sep_hi)))
    fit_apts, cal_apts, eval_apts = set(sep_fit["aptSeq"]), set(sep_cal["aptSeq"]), set(evaluation["aptSeq"])
    sep_sizes[window] = {"fit": len(sep_fit), "cal": len(sep_cal),
                         "apt_overlap": len(fit_apts & cal_apts) + len(fit_apts & eval_apts)
                                        + len(cal_apts & eval_apts),
                         "cal_untrimmed": len(sep_cal) == int(((unit["hash5"] == 1)
                                                               & (unit["deal_date"] >= CAL_START)).sum()),
                         "residual_finite": bool(np.isfinite(sep_residual).all()),
                         "lo": sep_lo, "hi": sep_hi}

    features_nr, extra_nr, _ = window_spec(window, NO_REDEVELOP_FEATURES)
    pred_noredev[window] = fit_predict(fit_sample, {"eval": evaluation}, features_nr, extra_nr,
                                       weighted, f"{window}_noredev")["eval"]
    print(f"  {window}: 적합 {len(fit_sample):,}행 / 보정 {len(inner_cal):,}행 / "
          f"구간 log[{lo:+.3f}, {hi:+.3f}]")

# ============================================================================
# 3. 충실도 — 현행 A를 33의 J 조건으로 재현
# ============================================================================

print("===== 3. J 조건 재현 =====")
complex_ids = set(pd.read_csv(COMPLEX_PATH, sep="\t", usecols=["apt_seq"])["apt_seq"].astype(str))
j_frame = raw[raw["aptSeq"].isin(complex_ids) & (raw["excluUseAr"] <= 300)].copy()
j_frame["post_indicator"] = j_frame["post"].astype(float)
j_train = trim_train(j_frame[j_frame["deal_date"].between(TRAIN_START, TRAIN_END)])
j_test = j_frame[j_frame["deal_ym"].between(202603, 202608)]
j_oot = fit_predict(j_train, {"test": j_test}, SERVICE_FEATURES, [], False, "J_oot")["test"]
j_hold = fit_predict(j_train[~j_train["holdout"]], {"hold": j_train[j_train["holdout"]]},
                     SERVICE_FEATURES, [], False, "J_holdout")["hold"]


def ape(actual, predicted):
    actual = np.asarray(actual, dtype=float)
    return np.abs(actual - predicted) / actual * 100


fidelity = {
    "oot": float(ape(j_test["price_per_m2"], j_oot).mean()),
    "holdout": float(ape(j_train.loc[j_train["holdout"], "price_per_m2"], j_hold).mean()),
    "n_train": len(j_train), "n_test": len(j_test),
}
print(f"  재현 OOT {fidelity['oot']:.2f}% (J {J_REFERENCE['oot']:.2f}) / "
      f"holdout {fidelity['holdout']:.2f}% (J {J_REFERENCE['holdout']:.2f})")

# 사후 진단(선택 규칙과 무관, 04 결과를 본 뒤 추가): D의 개선이 자치구별 지정 시점 때문인지
# 최근 가격 수준 표현 때문인지 가른다. 평가 거래에서는 세 변수가 모두 1이다
#   T1 A + 서울 공통 2025-10-20 이후 지시변수 (D와 4개 구의 2025-03-24~10-19만 다름)
#   T2 A + 최근 3개월(2025-12-01 이후) 지시변수 (규제와 무관한 순수 시점 proxy)
print("===== 3b. 시점 proxy 진단 =====")
a_fit = window_sample("A")
a_fit = a_fit[~a_fit["holdout"]]
for name, column in [("T1", "seoul_post"), ("T2", "recent3")]:
    pred[name] = fit_predict(a_fit, {"eval": evaluation}, SERVICE_FEATURES, [column], False,
                             name)["eval"]

# ============================================================================
# 4. 지표 · bootstrap
# ============================================================================

print("===== 4. 지표 =====")
for window in WINDOWS:
    evaluation[f"ape_{window}"] = ape(evaluation["price_per_m2"], pred[window])
    lo, hi = intervals[window]
    evaluation[f"cover_{window}"] = ((evaluation["price_per_m2"] >= pred[window] * np.exp(lo))
                                     & (evaluation["price_per_m2"] <= pred[window] * np.exp(hi)))
    evaluation[f"ape_nr_{window}"] = ape(evaluation["price_per_m2"], pred_noredev[window])
    seen_eval[f"ape_{window}"] = ape(seen_eval["price_per_m2"], pred[f"{window}_seen"])
    seen_eval[f"cell_seen_{window}"] = seen_eval["fe_key"].isin(fe_cells[window])
    evaluation[f"sepcover_{window}"] = sep_cover[window].to_numpy()
for name in ["T1", "T2"]:
    evaluation[f"ape_{name}"] = ape(evaluation["price_per_m2"], pred[name])


def summary_rows(frame, by, prefix="ape_", cover=True):
    rows = []
    for keys, part in frame.groupby(by):
        keys = keys if isinstance(keys, tuple) else (keys,)
        for window in WINDOWS:
            row = dict(zip(by, keys))
            row.update({"window": window, "n": len(part), "apts": part["aptSeq"].nunique(),
                        "MAPE": part[f"{prefix}{window}"].mean(),
                        "MdAPE": part[f"{prefix}{window}"].median()})
            if cover:
                row["coverage80"] = part[f"cover_{window}"].mean() * 100
            rows.append(row)
    return pd.DataFrame(rows)


overall = pd.concat([summary_rows(evaluation, ["period"]).assign(group="all"),
                     summary_rows(evaluation, ["period", "group"])], ignore_index=True)
monthly = summary_rows(evaluation, ["deal_ym", "group"])
monthly_all = summary_rows(evaluation, ["deal_ym"])
noredev = summary_rows(evaluation, ["period"], prefix="ape_nr_", cover=False)
seen_rows = []
for period, part in seen_eval.groupby("period"):
    for window in WINDOWS:
        seen_cell = part[f"cell_seen_{window}"]
        seen_rows.append({"period": period, "window": window,
                          "MAPE_all": part[f"ape_{window}"].mean(), "n_all": len(part),
                          "MAPE_cell_in_train": part.loc[seen_cell, f"ape_{window}"].mean(),
                          "n_cell_in_train": int(seen_cell.sum())})
seen_table = pd.DataFrame(seen_rows)

# 보조 진단(선택 규칙과 무관): 부호 있는 오차. log(실제/예측)의 중앙값·평균 ×100, +면 과소예측
bias_rows = []
for (period, group), part in pd.concat([evaluation.assign(group="all"), evaluation]).groupby(["period", "group"]):
    for window in WINDOWS + ["T1", "T2"]:
        log_ratio = np.log(part["price_per_m2"].to_numpy() / pred[window][part.index.map(position)])
        bias_rows.append({"period": period, "group": group, "window": window,
                          "MAPE": part[f"ape_{window}"].mean(),
                          "median_log_ratio_x100": float(np.median(log_ratio) * 100),
                          "mean_log_ratio_x100": float(np.mean(log_ratio) * 100),
                          "cal_all_median_log_ratio_x100": cal_bias.get(window, np.nan)})
bias_table = pd.DataFrame(bias_rows)

# 사후 진단: D가 A 예측을 얼마나 평행 이동시켰나 — log(예측 D / 예측 A)×100, 월×집단
evaluation["log_pred_D_over_A"] = np.log(pred["D"] / pred["A"]) * 100
shift_table = (evaluation.groupby(["deal_ym", "group"])["log_pred_D_over_A"]
               .agg(["size", "median", "mean", "std"]).reset_index())

# 민감도(사후): 단지 분리·같은 모델 conformal coverage
sep_rows = []
for (period, group), part in pd.concat([evaluation.assign(group="all"), evaluation]).groupby(["period", "group"]):
    for window in WINDOWS:
        sep_rows.append({"period": period, "group": group, "window": window, "n": len(part),
                         "coverage80_main": part[f"cover_{window}"].mean() * 100,
                         "coverage80_separated": part[f"sepcover_{window}"].mean() * 100})
sep_table = pd.DataFrame(sep_rows)

# 단지 블록 paired bootstrap — 집단 안에서 단지를 복원 추출
rng = np.random.default_rng(BOOT_SEED)
boot_rows = []
for period in PERIODS:
    part = evaluation[evaluation["period"] == period]
    per_apt = part.groupby(["group", "aptSeq"]).agg(
        n=("price_per_m2", "size"), **{f"s_{w}": (f"ape_{w}", "sum") for w in WINDOWS})
    draws = {}
    for group, block in per_apt.groupby(level="group"):
        index = rng.integers(0, len(block), size=(N_BOOT, len(block)))
        draws[group] = {column: block[column].to_numpy()[index].sum(axis=1)
                        for column in block.columns}
    for scope in ["all", "early4", "late21"]:
        groups = list(draws) if scope == "all" else [scope]
        n = sum(draws[g]["n"] for g in groups)
        point_n = per_apt.loc[groups, "n"].sum()
        mape_a_boot = sum(draws[g]["s_A"] for g in groups) / n
        point_a = per_apt.loc[groups, "s_A"].sum() / point_n
        for window in ["D", "C", "E"]:
            diff = sum(draws[g][f"s_{window}"] for g in groups) / n - mape_a_boot
            point = per_apt.loc[groups, f"s_{window}"].sum() / point_n - point_a
            low, high = np.percentile(diff, [2.5, 97.5])
            boot_rows.append({"period": period, "group": scope, "contrast": f"{window}-A",
                              "diff_pp": point, "ci_low": low, "ci_high": high})
boot = pd.DataFrame(boot_rows)

# ============================================================================
# 5. 판단 기준 (사전 지정)
# ============================================================================

print("===== 5. 판단 =====")


def metric(period, group, window, column):
    row = overall[(overall["period"] == period) & (overall["group"] == group)
                  & (overall["window"] == window)]
    return float(row[column].iloc[0])


def contrast(period, group, window):
    return boot[(boot["period"] == period) & (boot["group"] == group)
                & (boot["contrast"] == f"{window}-A")].iloc[0]


criteria = []
for period in PERIODS:
    overall_diff = contrast(period, "all", "D")
    group_diffs = {g: float(contrast(period, g, "D")["diff_pp"]) for g in ["early4", "late21"]}
    cover_a, cover_d = metric(period, "all", "A", "coverage80"), metric(period, "all", "D", "coverage80")
    criteria += [
        (period, "(1) D MAPE가 A보다 0.5%p 이상 낮음", overall_diff["diff_pp"] <= -THRESHOLD_PP,
         f"D-A {overall_diff['diff_pp']:+.2f}%p"),
        (period, "(2) 차이 95% CI가 0 제외(개선 방향)", overall_diff["ci_high"] < 0,
         f"[{overall_diff['ci_low']:+.2f}, {overall_diff['ci_high']:+.2f}]"),
        (period, "(3) 어느 집단도 0.5%p 넘게 악화 안 함",
         all(v <= THRESHOLD_PP for v in group_diffs.values()),
         " / ".join(f"{GROUP_LABEL[g]} {v:+.2f}%p" for g, v in group_diffs.items())),
        (period, "(4) coverage가 80%에서 더 멀어지지 않음", abs(cover_d - 80) <= abs(cover_a - 80),
         f"A {cover_a:.1f}% / D {cover_d:.1f}%"),
    ]
adopt = all(ok for _, _, ok, _ in criteria)
verdict = ("D를 '33에서 재검증할 가설'로 제안" if adopt else
           "현 자료로는 학습 기간 조정 근거가 부족 — 운영상 현행 유지")
print(f"  {verdict}")

# ============================================================================
# 6. 구현 점검
# ============================================================================

fit_log = pd.DataFrame(FIT_LOG)
model_fits = fit_log[~fit_log["fit"].str.startswith("J_")]
checks = [
    ("평가 단지(해시 20%)가 학습·보정 적합에 없음", int(model_fits["train_holdout_apts"].sum()) == 0,
     f"적합 {model_fits['fit'].nunique()}개의 해시 20% 단지 합 {int(model_fits['train_holdout_apts'].sum())}"),
    ("2026-08 거래가 학습·보정·평가에 없음",
     int(model_fits["train_max_ym"].max()) <= 202602 and int(evaluation["deal_ym"].max()) <= 202607,
     f"학습 최대 {int(model_fits['train_max_ym'].max())} / 평가 최대 {int(evaluation['deal_ym'].max())}"),
    ("평가 표본 미절단",
     len(evaluation) == int((main["holdout"] & main["deal_ym"].between(202603, 202607)).sum()),
     f"{len(evaluation):,}행"),
    ("보정 표본 500행 이상", min(v["inner_cal"] for v in cal_sizes.values()) >= MIN_CAL_ROWS,
     " / ".join(f"{w} {v['inner_cal']:,}" for w, v in cal_sizes.items())),
    ("D 평가 거래 지정 이후 지시변수 전부 1", bool(evaluation["post"].all()),
     f"{int(evaluation['post'].sum()):,}/{len(evaluation):,}"),
    ("예측값 결측·비유한 0건", bool(fit_log["pred_finite"].all()), f"{len(fit_log)}개 예측"),
    ("주 표본 전부 신고 완결 월(eval_complete)", bool(main["eval_complete"].all()),
     f"{int(main['eval_complete'].sum()):,}/{len(main):,}"),
    ("보정 표본 미절단", all(cal_sizes[w]["inner_cal"] == int((window_raw(w)["holdout"]
                                                          & (window_raw(w)["deal_date"] >= CAL_START)).sum())
                            for w in WINDOWS), "보정 표본 = 절단 전 해당 행 수"),
    ("단지 분리 민감도: 적합·보정·평가 단지 서로 겹침 0, 보정 미절단·500행 이상·잔차 유한",
     all(v["apt_overlap"] == 0 and v["cal_untrimmed"] and v["cal"] >= MIN_CAL_ROWS
         and v["residual_finite"] for v in sep_sizes.values()),
     " / ".join(f"{w} 겹침 {v['apt_overlap']}·보정 {v['cal']:,}" for w, v in sep_sizes.items())),
    ("평가 표본 인덱스 유일", bool(evaluation.index.is_unique), f"{len(evaluation):,}행"),
    ("학습에서 제거한 동일 이진 열이 예측 대상에서도 동일",
     int(fit_log["twin_mismatch_rows"].sum()) == 0,
     f"불일치 {int(fit_log['twin_mismatch_rows'].sum())}행 / 제거 사례 "
     f"{sorted(set(x for v in fit_log['dropped_twins'] for x in v.split(';') if x != '-'))}"),
]

# ============================================================================
# 7. 출력
# ============================================================================

def table(frame, float_format="{:.3f}"):
    lines = ["\t".join(frame.columns)]
    for row in frame.itertuples(index=False):
        lines.append("\t".join(float_format.format(v) if isinstance(v, float) else str(v)
                               for v in row))
    return lines


lines = ["# 41.1 학습 기간 후보별 지정 이후 예측 오차·coverage", "",
         "## 판단 (사전 지정 기준)", f"- 결론: **{verdict}**",
         "- 의미: 사전 지정된 D 채택 규칙(4개 기준 × 두 구간)이 충족되지 않아 이 분석만으로 33 변경을 권고하지 않는다. "
         "D의 MAPE 개선이 없었다는 뜻이 아니다 — (1)~(3)은 두 구간 모두 통과했고 (4) coverage만 불통과다.",
         "- 04 사양은 03 결과를 보기 전 계획서대로 동결했다. 다만 03에서 확인 구간(2026-06~07)의 "
         "집계 가격 추이를 이미 봤으므로 완전한 맹검 검증은 아니다.",
         "period\tcriterion\tpass\tdetail",
         *[f"{p}\t{c}\t{'PASS' if ok else 'FAIL'}\t{d}" for p, c, ok, d in criteria], "",
         "## 창 구성",
         "window\ttrimmed_rows\tfit_rows(해시80%)\tfit_apts\tfirst_date\tinner_fit\tinner_cal\tlog_lo\tlog_hi\twidth_pct_of_pred",
         *[f"{w}\t{window_sizes[w]['trimmed_rows']}\t{window_sizes[w]['fit_rows']}\t"
           f"{window_sizes[w]['fit_apts']}\t{window_sizes[w]['first_date']}\t"
           f"{cal_sizes[w]['inner_fit']}\t{cal_sizes[w]['inner_cal']}\t"
           f"{intervals[w][0]:.4f}\t{intervals[w][1]:.4f}\t"
           f"{(np.exp(intervals[w][1]) - np.exp(intervals[w][0])) * 100:.2f}" for w in WINDOWS], "",
         "## 주 평가 — 해시 20% 단지, 구간·집단별", *table(overall), "",
         "## D·C·E − A 차이 (%p, 단지 블록 paired bootstrap 95% CI, 학습 변동 제외 조건부)",
         *table(boot), "",
         "## 월별 (전체)", *table(monthly_all), "",
         "## 월별 × 집단", *table(monthly), "",
         "## 사후 진단 — 부호 있는 오차와 시점 proxy (+면 과소예측, 선택 규칙과 무관)",
         "- T1 = A + 서울 공통 2025-10-20 이후 지시변수, T2 = A + 2025-12-01 이후 지시변수(규제와 무관한 시점 proxy). "
         "04 결과를 본 뒤 외부 검토 제안으로 추가했다.",
         *table(bias_table), "",
         "## 사후 진단 — D가 A 예측을 옮긴 폭 log(D/A)×100, 월×집단", *table(shift_table), "",
         "## 민감도(사후) — 단지 분리·같은 모델 split conformal coverage",
         "- 주 구간은 33과 같은 구조(보정 모델 ≠ 최종 모델, 보정 단지 = 평가 단지)라 엄밀한 split conformal이 아니다. "
         "아래 대안은 적합 단지(80%→60%)·점예측 모델·보정 단지를 함께 바꾼 파이프라인이라 '구간 구성만 바꾼' 비교가 아니며, "
         "보정·평가 기간이 달라 명목 coverage 보장도 없다. "
         "여기서는 해시 버킷 1 단지를 보정 전용으로, 버킷 2~4 단지의 2025-11까지 거래로 한 모델만 적합해 "
         "보정 잔차와 평가 예측을 같은 모델로 만든다.",
         "window\tfit_rows\tcal_rows\tlog_lo\tlog_hi",
         *[f"{w}\t{v['fit']}\t{v['cal']}\t{v['lo']:.4f}\t{v['hi']:.4f}" for w, v in sep_sizes.items()],
         *table(sep_table), "",
         "## 보조 — 해시 80% 단지(학습에 있던 단지)의 같은 기간 거래", *table(seen_table), "",
         "## 민감도 — 정비사업 feature 2개 제외", *table(noredev), "",
         "## 충실도 — 현행 A를 33의 J 조건으로 재현",
         "metric\t재현\tJ(33.2)\t차이",
         f"OOT MAPE (학습 전체 적합 → 2026-03~08 전체)\t{fidelity['oot']:.3f}\t{J_REFERENCE['oot']:.3f}\t{fidelity['oot'] - J_REFERENCE['oot']:+.3f}",
         f"holdout MAPE (학습 기간 안 해시 20%)\t{fidelity['holdout']:.3f}\t{J_REFERENCE['holdout']:.3f}\t{fidelity['holdout'] - J_REFERENCE['holdout']:+.3f}",
         f"- 재현 학습 {fidelity['n_train']:,}행 / 검증 {fidelity['n_test']:,}행. 지역 키(법정동 코드 대 자치구+법정동명)와 "
         "38의 입력 정리(지하층·거래금액 0 제외) 차이가 있다.", "",
         "## 적합 기록", *table(fit_log), "",
         "## 구현 점검", *[f"[{'PASS' if ok else 'FAIL'}]\t{label}\t{detail}" for label, ok, detail in checks]]
RESULT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(f"  저장: {RESULT_PATH.relative_to(work_dir)}")

colors = {"A": "#444444", "D": "#1f77b4", "C": "#d62728", "E": "#2ca02c"}
fig, axes = plt.subplots(1, 3, figsize=(17, 5.2))
months = sorted(monthly_all["deal_ym"].unique())
labels = [f"{str(m)[2:4]}-{str(m)[4:]}" for m in months]
for window in WINDOWS:
    part = monthly_all[monthly_all["window"] == window].set_index("deal_ym").loc[months]
    axes[0].plot(labels, part["MAPE"], marker="o", color=colors[window], label=WINDOW_LABEL[window])
    axes[2].plot(labels, part["coverage80"], marker="o", color=colors[window], label=WINDOW_LABEL[window])
for ax in (axes[0], axes[2]):
    ax.axvspan(2.5, 4.5, color="#f0f0f0", zorder=0)
axes[0].set_title("월별 MAPE (해시 20% 단지)")
axes[0].set_ylabel("MAPE (%)")
axes[0].legend(fontsize=8)
axes[2].axhline(80, color="black", lw=.8, ls="--")
axes[2].set_title("월별 80% 예측구간 coverage")
axes[2].set_ylabel("coverage (%)")

positions, tick_labels = [], []
for i, period in enumerate(PERIODS):
    for j, scope in enumerate(["all", "late21", "early4"]):
        for k, window in enumerate(["D", "C", "E"]):
            row = contrast(period, scope, window)
            x = i * 4 + j + (k - 1) * .22
            axes[1].errorbar(x, row["diff_pp"], yerr=[[row["diff_pp"] - row["ci_low"]],
                                                      [row["ci_high"] - row["diff_pp"]]],
                             fmt="o", color=colors[window], capsize=2,
                             label=WINDOW_LABEL[window] if (i, j) == (0, 0) else None)
        positions.append(i * 4 + j)
        tick_labels.append(f"{'선택' if period == 'select' else '확인'}\n"
                           f"{ {'all': '전체', 'late21': '21개 구', 'early4': '4개 구'}[scope] }")
axes[1].axhline(0, color="black", lw=.8)
axes[1].axhspan(-THRESHOLD_PP, THRESHOLD_PP, color="#f5f5f5", zorder=0)
axes[1].set_xticks(positions, tick_labels, fontsize=8)
axes[1].set_title("A 대비 MAPE 차이 (%p, 95% CI)")
axes[1].legend(fontsize=8)
fig.suptitle("학습 기간 후보별 지정 이후 예측 성능 — 음영: 확인 구간(좌·우) / ±0.5%p(중)", fontsize=11)
fig.tight_layout()
fig.savefig(FIGURE_PATH, dpi=150)
print(f"  저장: {FIGURE_PATH.relative_to(work_dir)}")

print("===== 구현 점검 =====")
for label, ok, detail in checks:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
if not all(ok for _, ok, _ in checks):
    raise SystemExit("구현 점검 실패")
