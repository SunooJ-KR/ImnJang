# ============================================================================
# 39.composition_shift.py   (가번호 — PR 직전에 확정)
# ============================================================================
# Author:      jieun
# Purpose:     토지거래허가 지정 전후로 '어떤 집이 거래됐는지'가 바뀌었는지 기술한다
# Description: docs/analysis-plan-regulation-break.md 절차 02.
#              허가제는 실거주 매수만 허용하므로 거래 표본 자체가 달라질 수 있다.
#              구성이 바뀌면 같은 기간의 가격 중앙값 비교나 모델 학습 표본이 그
#              변화를 가격 변화로 착각한다. 03·04를 해석하기 전에 먼저 본다.
#
#              사건 두 개를 같은 방식으로 본다. 처치군의 전후 변화를 같은 달력
#              구간의 비교군 변화와 나란히 둔다(비교군도 같은 시기의 시장 흐름을
#              겪으므로, 처치군만 보면 계절·금리 변화를 제도 탓으로 읽게 된다).
#                A. 2025-03-24 — 처치 4개 구 / 비교 21개 구
#                   전 2024-09-01~2025-03-23, 후 2025-03-24~2025-10-19
#                B. 2025-10-20 — 처치 21개 구 / 비교 4개 구(이미 지정)
#                   전 2025-04-01~2025-10-19, 후 2025-10-20~2026-07-31
#                   (전 구간을 4월부터 잡아 비교군의 03-24 전환기를 피한다)
#
#              표준화 평균차(SMD)로 크기를 잰다. |SMD| >= 0.1을 '눈에 띄는 차이'로
#              표시하지만 판정 기준이 아니라 읽기 보조다. 인과 해석은 하지 않는다.
#
#              입력은 38.1의 in_main_sample 행만 쓴다(선지정 9개 동·2026-08·같은 동
#              확정 중복 제외).
#
#              외부 검토(codex) 반영:
#                - ㎡당 가격은 구성 변수가 아니라 결과 변수다. 구성표에서 분리해
#                  '관측 거래가격 분포의 무조건부 변화'로만 따로 적는다
#                - 처치군 SMD − 비교군 SMD는 분모가 달라 표준화된 이중차분이 아니다.
#                  원단위 이중차분과, 처치군 지정 전 SD로 나눈 값을 쓴다
#                - 표본이 비거나 분산이 0이면 SMD를 0이 아니라 NaN으로 둔다
#                - 공통 지지는 03의 고정효과 단위(단지×면적타입)로도 잰다
#                - 거래량 많은 달이 평균을 끌므로 월별 동일가중 SMD와, 사건 B의
#                  길이를 맞춘 대칭 창을 민감도로 함께 낸다
#
#              통과 기준:
#                (1) 사건·집단·전후 모든 구간(대칭 창 포함 12개) 거래 1,000건 이상
#                (2) 모든 변수가 구간마다 유효표본 1,000건 이상이고 SMD가 유한값
#                (3) 공통 지지 비율이 0~1 범위
#                (4) 면적 구간·층대 더미의 합이 행마다 1
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

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

INPUT_PATH = output_dir / "38.1.trades_regulation.txt"
TABLE_PATH = output_dir / "39.1.composition_table.txt"
FIGURE_PATH = output_dir / "39.2.composition_shift.png"

EVENTS = {
    "A_2025-03-24": {
        "treated": "early4", "comparison": "late21",
        "pre": ("2024-09-01", "2025-03-23"), "post": ("2025-03-24", "2025-10-19"),
    },
    "B_2025-10-20": {
        "treated": "late21", "comparison": "early4",
        "pre": ("2025-04-01", "2025-10-19"), "post": ("2025-10-20", "2026-07-31"),
    },
    # 민감도 — 사건 B 후 구간을 전 구간과 같은 길이(202일)로 자른 대칭 창
    "Bsym_2025-10-20": {
        "treated": "late21", "comparison": "early4",
        "pre": ("2025-04-01", "2025-10-19"), "post": ("2025-10-20", "2026-05-09"),
    },
}
MAIN_EVENTS = ["A_2025-03-24", "B_2025-10-20"]
GROUP_LABEL = {"early4": "4개 구", "late21": "21개 구"}
SMD_NOTABLE = 0.1
MIN_SEGMENT_N = 1000

# 전용면적 구간 — 국민주택규모(85㎡) 기준의 통상 구분
AREA_BINS = [0, 60, 85, 102, np.inf]
AREA_LABELS = ["area_le60", "area_60_85", "area_85_102", "area_gt102"]

KFONT = Path("/home/jieun/.fonts/NotoSansKR-Regular.ttf")
if KFONT.exists():
    fm.fontManager.addfont(str(KFONT))
    plt.rcParams["font.family"] = fm.FontProperties(fname=str(KFONT)).get_name()
plt.rcParams.update({"axes.unicode_minus": False, "figure.dpi": 150,
                     "savefig.bbox": "tight", "savefig.facecolor": "white"})


# ============================================================================
# 1. 로드
# ============================================================================

print("===== 1. 로드 =====")
df = pd.read_csv(INPUT_PATH, sep="\t", dtype={"aptSeq": str, "sggCd": str},
                 parse_dates=["deal_date"], low_memory=False)
for col in ["in_main_sample", "bulk_trade", "possible_duplicate", "floor_above_max",
            "gross_price_flag"]:
    df[col] = df[col].astype(str).eq("True")
df = df[df["in_main_sample"]].copy()
print(f"  주 분석 표본 {len(df):,}행 / 단지 {df['aptSeq'].nunique():,}")

df["log_area"] = np.log(df["excluUseAr"])
df["log_households"] = np.log(df["total_households"])
df["log_station_m"] = np.log1p(df["station_dist_m"])
area_bin = pd.cut(df["excluUseAr"], AREA_BINS, labels=AREA_LABELS, right=True)
for label in AREA_LABELS:
    df[label] = (area_bin == label).astype(float)
for band in ["LOW", "MID", "HIGH", "UNKNOWN"]:
    df[f"band_{band}"] = (df["floor_band"] == band).astype(float)
for flag in ["bulk_trade", "possible_duplicate", "floor_above_max"]:
    df[flag] = df[flag].astype(float)
df["cell_key"] = (df["aptSeq"] + "|" + df["area_type"].astype(str) + "|"
                  + df["floor_band"])
df["fe_key"] = df["aptSeq"] + "|" + df["area_type"].astype(str)   # 03 고정효과 단위

OUTCOME = {"log_price_per_m2": "y_log_ppm2"}   # 결과 변수 — 구성 변수와 분리
CONTINUOUS = {
    "log_전용면적": "log_area",
    "준공연도": "built_year", "log_세대수": "log_households",
    "log_역거리": "log_station_m",
}
BINARY = {
    "전용 60㎡ 이하": "area_le60", "전용 60~85㎡": "area_60_85",
    "전용 85~102㎡": "area_85_102", "전용 102㎡ 초과": "area_gt102",
    "층대 LOW": "band_LOW", "층대 MID": "band_MID", "층대 HIGH": "band_HIGH",
    "층대 UNKNOWN": "band_UNKNOWN", "정비사업 단지": "is_redevelop",
    "일괄 매각 가능": "bulk_trade", "중복 판별 불가": "possible_duplicate",
}


# ============================================================================
# 2. SMD와 이중차분
# ============================================================================

def weighted_stats(x, w):
    """가중 평균·분산. w가 None이면 비가중."""
    x = np.asarray(x, dtype=float)
    if w is None:
        return (x.mean(), x.var(ddof=1)) if len(x) > 1 else (np.nan, np.nan)
    w = np.asarray(w, dtype=float)
    if len(x) < 2 or w.sum() <= 0:
        return np.nan, np.nan
    mean = np.average(x, weights=w)
    return mean, np.average((x - mean) ** 2, weights=w)


def smd(pre_x, post_x, pre_w=None, post_w=None):
    """연속형·이항형 공통. 이항형 0/1은 분산이 p(1-p)라 같은 식이 된다.
    표본이 없거나 분산이 0이면 NaN — 0으로 위장하지 않는다."""
    m0, v0 = weighted_stats(pre_x, pre_w)
    m1, v1 = weighted_stats(post_x, post_w)
    pooled = np.sqrt((v0 + v1) / 2) if np.isfinite(v0) and np.isfinite(v1) else np.nan
    if not np.isfinite(pooled) or pooled <= 0:
        return np.nan, m0, m1
    return (m1 - m0) / pooled, m0, m1


def month_weights(frame):
    """월별 동일가중 — 거래 수가 많은 달이 평균을 끌지 않게 각 달의 합을 1로."""
    return 1.0 / frame.groupby("deal_ym")["deal_ym"].transform("size")


def segment(frame, group, window):
    start, end = pd.Timestamp(window[0]), pd.Timestamp(window[1])
    return frame[(frame["group"] == group) & frame["deal_date"].between(start, end)]


def window_months(window):
    return ((pd.Timestamp(window[1]) - pd.Timestamp(window[0])).days + 1) / 30.4375


print("\n===== 2. 사건별 구성 비교 =====")
variables = {**OUTCOME, **CONTINUOUS, **BINARY}
kind_of = {**{k: "outcome" for k in OUTCOME}, **{k: "continuous" for k in CONTINUOUS},
           **{k: "share" for k in BINARY}}

rows, seg_counts, support_rows = [], [], []
segments = {}
for event, spec in EVENTS.items():
    for role in ["treated", "comparison"]:
        group = spec[role]
        pre, post = segment(df, group, spec["pre"]), segment(df, group, spec["post"])
        segments[(event, role)] = (pre, post)
        seg_counts.append({"event": event, "role": role, "group": group,
                           "n_pre": len(pre), "n_post": len(post),
                           "per_month_pre": round(len(pre) / window_months(spec["pre"]), 1),
                           "per_month_post": round(len(post) / window_months(spec["post"]), 1)})
        wpre, wpost = month_weights(pre), month_weights(post)
        for name, col in variables.items():
            a_ok, b_ok = pre[col].notna(), post[col].notna()
            val, m0, m1 = smd(pre.loc[a_ok, col], post.loc[b_ok, col])
            val_w, _, _ = smd(pre.loc[a_ok, col], post.loc[b_ok, col],
                              wpre[a_ok], wpost[b_ok])
            rows.append({"event": event, "role": role, "group": group, "variable": name,
                         "kind": kind_of[name], "pre_mean": m0, "post_mean": m1,
                         "smd": val, "smd_month_weighted": val_w,
                         "n_valid_pre": int(a_ok.sum()), "n_valid_post": int(b_ok.sum()),
                         "missing_pct_pre": 100 * (1 - a_ok.mean()),
                         "missing_pct_post": 100 * (1 - b_ok.mean())})

        # 공통 지지 — 03 고정효과 단위(단지×면적타입)와 층대까지 포함한 셀 두 가지
        pre_fe, post_fe = set(pre["fe_key"]), set(post["fe_key"])
        pre_cell = set(pre["cell_key"])
        support_rows.append({
            "event": event, "role": role, "group": group,
            "post_trades_in_pre_fe": post["fe_key"].isin(pre_fe).mean(),
            "pre_trades_in_post_fe": pre["fe_key"].isin(post_fe).mean(),
            "pre_fe_survival": len(pre_fe & post_fe) / len(pre_fe) if pre_fe else np.nan,
            "fe_jaccard": len(pre_fe & post_fe) / len(pre_fe | post_fe) if pre_fe | post_fe else np.nan,
            "post_trades_in_pre_cell": post["cell_key"].isin(pre_cell).mean(),
        })

table = pd.DataFrame(rows)
counts = pd.DataFrame(seg_counts)
support = pd.DataFrame(support_rows)
table["notable"] = table["smd"].abs() >= SMD_NOTABLE

# 원단위 이중차분 — (처치 후-전) − (비교 후-전). 처치군 지정 전 SD로 나눈 값도 함께
did_rows = []
for event, spec in EVENTS.items():
    t_pre, t_post = segments[(event, "treated")]
    for name, col in variables.items():
        t = table[(table["event"] == event) & (table["variable"] == name)].set_index("role")
        did = ((t.loc["treated", "post_mean"] - t.loc["treated", "pre_mean"])
               - (t.loc["comparison", "post_mean"] - t.loc["comparison", "pre_mean"]))
        pre_sd = t_pre[col].dropna().std(ddof=1)
        did_rows.append({"event": event, "variable": name, "kind": kind_of[name],
                         "treated_change": t.loc["treated", "post_mean"] - t.loc["treated", "pre_mean"],
                         "comparison_change": t.loc["comparison", "post_mean"] - t.loc["comparison", "pre_mean"],
                         "did_raw": did,
                         "did_in_treated_pre_sd": did / pre_sd if pre_sd > 0 else np.nan})
did = pd.DataFrame(did_rows)

print(counts.to_string(index=False))
order = list(CONTINUOUS) + list(BINARY)
for event in EVENTS:
    print(f"\n  [{event}] 구성 SMD 처치/비교 (월동일가중 처치) | 이중차분(처치 전 SD 단위)")
    for name in order:
        t = table[(table["event"] == event) & (table["variable"] == name)].set_index("role")
        d = did[(did["event"] == event) & (did["variable"] == name)].iloc[0]
        mark = "*" if abs(t.loc["treated", "smd"]) >= SMD_NOTABLE else " "
        print(f"    {name:14s} {t.loc['treated','smd']:+.3f}{mark} / {t.loc['comparison','smd']:+.3f} "
              f"({t.loc['treated','smd_month_weighted']:+.3f}) | {d['did_in_treated_pre_sd']:+.3f}")
    o = table[(table["event"] == event) & (table["kind"] == "outcome")].set_index("role")
    print(f"    [결과변수·참고] log_price_per_m2 무조건부 SMD 처치 {o.loc['treated','smd']:+.3f} / "
          f"비교 {o.loc['comparison','smd']:+.3f}")
print("\n  공통 지지")
print(support.round(3).to_string(index=False))


# ============================================================================
# 3. 그림 — 구성 변수만 (가격 제외)
# ============================================================================

fig, axes = plt.subplots(1, 2, figsize=(13.5, 6.8), sharey=True)
ys = np.arange(len(order))[::-1]
wide = table.pivot_table(index=["event", "variable"], columns="role", values="smd").reset_index()
for ax, event in zip(axes, MAIN_EVENTS):
    sub = wide[wide["event"] == event].set_index("variable").reindex(order)
    spec = EVENTS[event]
    ax.axvspan(-SMD_NOTABLE, SMD_NOTABLE, color="#E8EEF5", zorder=0)
    ax.axvline(0, color="#12305A", lw=1)
    ax.scatter(sub["comparison"], ys, color="#8A93A0", s=46, zorder=3,
               label=f"비교군 {GROUP_LABEL[spec['comparison']]}")
    ax.scatter(sub["treated"], ys, color="#C0504D", s=56, zorder=4,
               label=f"처치군 {GROUP_LABEL[spec['treated']]}")
    for y, (t, c) in zip(ys, zip(sub["treated"], sub["comparison"])):
        ax.plot([c, t], [y, y], color="#C0504D", alpha=0.35, lw=1.5, zorder=2)
    ax.set_yticks(ys, order)
    ax.set_xlabel("지정 전 대비 후 표준화 평균차(SMD) — 집단별 분모")
    ax.set_title(f"사건 {event[0]} — {event[2:]} 지정\n"
                 f"전 {spec['pre'][0]}~{spec['pre'][1]} / 후 {spec['post'][0]}~{spec['post'][1]}",
                 fontsize=10.5)
    ax.legend(loc="lower right", fontsize=9)
    ax.grid(axis="x", alpha=0.3)
fig.suptitle("토지거래허가 지정 전후 거래 구성 변화 — 기술 통계 (음영 = |SMD| < 0.1, 가격은 결과변수라 제외)",
             fontsize=12, y=1.0)
fig.tight_layout()
fig.savefig(FIGURE_PATH)
plt.close(fig)


# ============================================================================
# 4. 판정 및 저장
# ============================================================================

print("\n===== 4. 판정 =====")
min_n = int(counts[["n_pre", "n_post"]].min().min())
min_valid = int(table[["n_valid_pre", "n_valid_post"]].min().min())
support_vals = support.drop(columns=["event", "role", "group"]).to_numpy()
area_sum_ok = bool((df[AREA_LABELS].sum(axis=1) == 1).all())
band_sum_ok = bool((df[[f"band_{b}" for b in ["LOW", "MID", "HIGH", "UNKNOWN"]]].sum(axis=1) == 1).all())
checks = [
    ("구간 거래 1,000건 이상", min_n >= MIN_SEGMENT_N, f"최소 {min_n:,}건"),
    ("변수 유효표본 1,000건 이상 · SMD 유한",
     min_valid >= MIN_SEGMENT_N and bool(np.isfinite(table["smd"]).all()),
     f"최소 유효 {min_valid:,}건 / NaN {int(table['smd'].isna().sum())}개"),
    ("공통 지지 비율 0~1", bool(((support_vals >= 0) & (support_vals <= 1)).all()), "범위 확인"),
    ("면적·층대 더미 합 = 1", area_sum_ok and band_sum_ok, f"면적 {area_sum_ok} / 층대 {band_sum_ok}"),
]
all_passed = True
for label, passed, observed in checks:
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:30s} {observed}")

with TABLE_PATH.open("w", encoding="utf-8") as fh:
    fh.write("# 39.1 토지거래허가 지정 전후 거래 구성 — 기술 통계\n"
             "# 인과 해석 금지. |SMD| >= 0.1 은 읽기 보조 표시\n"
             "# log_price_per_m2는 결과변수: '관측 거래가격 분포의 무조건부 변화'로만 읽는다\n"
             "# Bsym_은 사건 B 후 구간을 전 구간 길이에 맞춘 민감도 창\n\n## 구간별 거래 수\n")
    counts.to_csv(fh, sep="\t", index=False, lineterminator="\n")
    fh.write("\n## 변수별 전후 평균·SMD(비가중, 월별 동일가중)·결측\n")
    table.round(5).to_csv(fh, sep="\t", index=False, lineterminator="\n")
    fh.write("\n## 원단위 이중차분 (처치 변화 − 비교 변화), 처치군 지정 전 SD 단위\n")
    did.round(5).to_csv(fh, sep="\t", index=False, lineterminator="\n")
    fh.write("\n## 공통 지지 (fe = 단지x면적타입, cell = +층대)\n")
    support.round(5).to_csv(fh, sep="\t", index=False, lineterminator="\n")
    fh.write("\n## 판정\n")
    for label, passed, observed in checks:
        fh.write(f"[{'PASS' if passed else 'FAIL'}]\t{label}\t{observed}\n")

print(f"\n표: {TABLE_PATH}\n그림: {FIGURE_PATH}")
print(f"\n===== 39 거래 구성 비교 {'통과' if all_passed else '미통과'} =====")
