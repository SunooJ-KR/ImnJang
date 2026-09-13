# ============================================================================
# 40.event_study.py   (가번호 — PR 직전에 확정)
# ============================================================================
# Author:      jieun
# Purpose:     토지거래허가 지정 전후 가격 추이를 같은 단지·면적 안에서 비교군 대비로 기술한다
# Description: docs/analysis-plan-regulation-break.md 절차 03.
#              **인과 추정이 아니다.** 깨끗한 비교군이 없고(4개 구는 선지정, 신속통합
#              기획·모아타운 등 구역 지정이 겹침) 허가제가 거래 자체를 선택하므로,
#              계수는 "비교군 대비 처치군의 상대 가격 추이"로만 읽는다.
#
#              사양 (사건별로 처치·비교군을 명시하고 처치군×상대월만 넣는다):
#                log(㎡당 가격) = 단지×면적타입 FE + 달력월 FE + 층대 더미
#                                 + Σ_k β_k · 처치군 · 1[상대월 = k]
#                기준 상대월 k = -1 (지정 전월). 상대월 0(지정일이 속한 달, 전후 혼재)의
#                처치군 거래는 뺀다. 비교군은 상호작용을 두지 않는다 — 두 집단 모두
#                상대월 상호작용을 넣으면 달력월 FE와 완전 공선이다(외부 검토 지적)
#
#                A 2025-03-24  처치 4개 구 / 비교 21개 구     창 2024-09-01~2025-10-19
#                B 2025-10-20  처치 21개 구 / 비교 4개 구(이미 지정) 창 2025-04-01~2026-07-31
#                  B의 계수는 '이미 지정된 지역 대비 상대 변화'다
#
#              추정:
#                - 단지×면적타입 FE는 셀 안 평균을 빼서 흡수하고(Frisch-Waugh-Lovell),
#                  나머지 더미는 명시적으로 넣는다. 관측 1건뿐인 셀은 정보가 없어 뺀다
#                - 표준오차 ① 단지 클러스터 해석식 ② 자치구 wild cluster bootstrap
#                  (Webb 6점 가중, 999회). 지정은 자치구 단위라 ②가 더 보수적이다.
#                  A는 처치 자치구가 4개뿐이라 ②도 불안정하다 — 결과에 표기한다
#                - 사전 추세: 선행 계수 전체의 joint Wald (단지 클러스터 해석식 +
#                  자치구 WCR bootstrap)
#                - 요약 수치: 같은 사양의 정적 이중차분(처치군 × 지정 후) 계수
#
#              착수 전 식별 점검(외부 검토 요구): FE 흡수 후 설계행렬이 완전 랭크인지,
#              상대월 계수가 몇 개 추정 가능한지 먼저 확인하고 실패하면 멈춘다.
#
#              민감도(정적 이중차분 + joint 사전추세만 보고):
#                common_support  전후 모두 거래된 단지×면적타입 셀만
#                drop_jun_jul    2025-06~07 거래 제외 (거래량 급변기)
#                incl_predesig   선지정 9개 동 포함 (전후 라벨이 틀린 표본 — 비교용)
#                placebo_0720    B의 지정 전 구간만으로 가짜 지정일 2025-07-20 적용
#
#              외부 검토(codex) 2차 반영:
#                - 월별 계수에도 자치구 WCR p값을 붙인다(그림 음영은 단지 클러스터 CI)
#                - 공통지지는 처치군 상대월 0을 뺀 뒤, 지정월 이전·이후 거래가 모두 있는
#                  셀로 정의한다
#                - bootstrap p = (1 + 초과 횟수) / (반복 + 1)
#                - A는 처치, B는 비교 쪽이 자치구 4개뿐 — 두 사건 모두 자치구 추론 불안정
#                - 가짜 지정일 검정이 단지 클러스터로는 유의(p<0.001), 자치구 WCR로는
#                  p≈0.10이었다. 단지 클러스터는 이 문제에서 과도하게 낙관적이다
#
#              통과 기준:
#                (1) 모든 적합(주 사양·민감도·placebo)의 설계행렬 완전 랭크
#                (2) 주 사양에서 흡수 후 남은 선행 계수 5개·후행 계수 6개 이상
#                (3) 모든 wild bootstrap이 설정 횟수만큼 반복됨
#                (4) 단지(aptSeq)가 자치구 하나에만 속함 (클러스터 중첩 전제)
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
from scipy import stats

work_dir = Path(__file__).resolve().parents[2]   # 저장소 루트
output_dir = work_dir / "output"

INPUT_PATH = output_dir / "38.1.trades_regulation.txt"
COEF_PATH = output_dir / "40.1.event_coefficients.txt"
FIGURE_PATH = output_dir / "40.2.event_study.png"

EVENTS = {
    "A": {"date": "2025-03-24", "treated": "early4", "comparison": "late21",
          "window": ("2024-09-01", "2025-10-19")},
    "B": {"date": "2025-10-20", "treated": "late21", "comparison": "early4",
          "window": ("2025-04-01", "2026-07-31")},
}
BASE_REL = -1
N_BOOT = 999
SEED = 20260913
MIN_LEADS, MIN_LAGS = 5, 6

KFONT = Path("/home/jieun/.fonts/NotoSansKR-Regular.ttf")
if KFONT.exists():
    fm.fontManager.addfont(str(KFONT))
    plt.rcParams["font.family"] = fm.FontProperties(fname=str(KFONT)).get_name()
plt.rcParams.update({"axes.unicode_minus": False, "figure.dpi": 150,
                     "savefig.bbox": "tight", "savefig.facecolor": "white"})
rng = np.random.default_rng(SEED)


# ============================================================================
# 1. 로드
# ============================================================================

print("===== 1. 로드 =====")
raw = pd.read_csv(INPUT_PATH, sep="\t", dtype={"aptSeq": str, "sggCd": str},
                  parse_dates=["deal_date"], low_memory=False)
for col in ["in_main_sample", "pre_designated_dong", "eval_complete", "is_duplicate"]:
    raw[col] = raw[col].astype(str).eq("True")
raw["fe_key"] = raw["aptSeq"] + "|" + raw["area_type"].astype(str)
raw["cal_month"] = raw["deal_date"].dt.to_period("M")
print(f"  38.1 {len(raw):,}행 / 주 표본 {int(raw['in_main_sample'].sum()):,}행")
nested_ok = bool((raw.groupby("aptSeq")["sggCd"].nunique() == 1).all())
print(f"  단지 → 자치구 중첩: {nested_ok}")


# ============================================================================
# 2. 추정 도구
# ============================================================================

def build_design(frame, event_month, treated, rel_min=None, rel_max=None):
    """처치군×상대월 더미 + 달력월 더미 + 층대 더미. 기준: rel=-1, 첫 달력월, 층대 MID."""
    f = frame.copy()
    em = pd.Period(event_month, "M")
    f["rel"] = (f["cal_month"] - em).apply(lambda d: d.n)
    f["is_treated"] = f["group"].eq(treated)
    # 상대월 0 처치군 거래는 전후 혼재라 제외
    f = f[~(f["is_treated"] & f["rel"].eq(0))]
    rels = sorted(r for r in f.loc[f["is_treated"], "rel"].unique() if r not in (BASE_REL, 0))
    if rel_min is not None:
        rels = [r for r in rels if r >= rel_min]
    if rel_max is not None:
        rels = [r for r in rels if r <= rel_max]
    cols = {}
    for r in rels:
        cols[f"ev_{r:+d}"] = (f["is_treated"] & f["rel"].eq(r)).astype(float)
    months = sorted(f["cal_month"].unique())
    for m in months[1:]:
        cols[f"m_{m}"] = f["cal_month"].eq(m).astype(float)
    for band in ["LOW", "HIGH", "UNKNOWN"]:
        cols[f"band_{band}"] = f["floor_band"].eq(band).astype(float)
    X = pd.DataFrame(cols, index=f.index)
    return f, X, rels


def absorb(frame, X, y_col="y_log_ppm2"):
    """단지×면적타입 셀 평균을 빼서 FE를 흡수. 관측 1건 셀은 제외."""
    size = frame.groupby("fe_key")["fe_key"].transform("size")
    keep = size > 1
    f, Xk = frame[keep], X[keep]
    y = f[y_col] - f.groupby("fe_key")[y_col].transform("mean")
    Xd = Xk - Xk.groupby(f["fe_key"]).transform("mean")
    # 흡수 후 전부 0인 열(셀 안에서 변동 없음)은 식별 불가 — 기록 후 제거
    varying = Xd.abs().sum(axis=0) > 1e-10
    dropped = list(Xd.columns[~varying])
    return f, Xd.loc[:, varying], y, dropped, int((~keep).sum())


def cluster_vcov(X, u, groups, n_absorbed=0):
    """클러스터 강건 분산. FE가 클러스터 안에 중첩되면 흡수 자유도는 세지 않는다."""
    Xv = X.to_numpy()
    XtX_inv = np.linalg.inv(Xv.T @ Xv)
    scores = pd.DataFrame(Xv * u[:, None]).groupby(groups.to_numpy()).sum().to_numpy()
    meat = scores.T @ scores
    n, k = Xv.shape
    g = scores.shape[0]
    c = g / (g - 1) * (n - 1) / (n - k - n_absorbed)
    return c * XtX_inv @ meat @ XtX_inv, g


def fit(frame, X, cluster_col="aptSeq"):
    f, Xd, y, dropped, n_singleton = absorb(frame, X)
    Xv, yv = Xd.to_numpy(), y.to_numpy()
    rank = np.linalg.matrix_rank(Xv)
    FIT_RANK_OK.append(rank == Xv.shape[1])
    if rank < Xv.shape[1]:
        return {"rank_ok": False, "rank": rank, "k": Xv.shape[1], "dropped": dropped}
    beta = np.linalg.solve(Xv.T @ Xv, Xv.T @ yv)
    u = yv - Xv @ beta
    V, g = cluster_vcov(Xd, u, f[cluster_col])
    se = np.sqrt(np.diag(V))
    return {"rank_ok": True, "rank": rank, "k": Xv.shape[1], "dropped": dropped,
            "n": len(yv), "n_singleton_dropped": n_singleton, "n_clusters": g,
            "beta": pd.Series(beta, Xd.columns), "se": pd.Series(se, Xd.columns),
            "V": pd.DataFrame(V, Xd.columns, Xd.columns), "u": u,
            "Xd": Xd, "y": yv, "frame": f,
            "cond": float(np.linalg.cond(Xv.T @ Xv))}


def joint_wald(res, names):
    b = res["beta"][names].to_numpy()
    V = res["V"].loc[names, names].to_numpy()
    w = float(b @ np.linalg.pinv(V) @ b)
    return w, float(stats.chi2.sf(w, len(names)))


BOOT_COUNTS = []
FIT_RANK_OK = []
WEBB = np.array([-np.sqrt(1.5), -1.0, -np.sqrt(0.5), np.sqrt(0.5), 1.0, np.sqrt(1.5)])


def wild_cluster(res, restrict, cluster_col="sggCd", n_boot=N_BOOT):
    """자치구 wild cluster restricted bootstrap(WCR).
    restrict 열들을 0으로 제한한 모형의 잔차를 자치구 단위 Webb 가중으로 뒤집어
    검정 통계량 분포를 만든다. 1개 열이면 |t|, 여러 열이면 Wald.
    자치구 단위로 X_g'X_g, X_g'u, X_g'yhat를 미리 합산해 반복마다 KxK 연산만 한다."""
    Xd, y, f = res["Xd"], res["y"], res["frame"]
    X = Xd.to_numpy()
    idx = [Xd.columns.get_loc(c) for c in restrict]
    free = [i for i in range(X.shape[1]) if i not in idx]
    # 제한 모형
    Xr = X[:, free]
    br = np.linalg.solve(Xr.T @ Xr, Xr.T @ y)
    yhat_r = Xr @ br
    u_r = y - yhat_r
    groups = f[cluster_col].to_numpy()
    labels, gid = np.unique(groups, return_inverse=True)
    G, K = len(labels), X.shape[1]
    XtX_inv = np.linalg.inv(X.T @ X)
    H = np.zeros((G, K, K)); A = np.zeros((G, K)); B = np.zeros((G, K))
    for g in range(G):
        m = gid == g
        Xg = X[m]
        H[g] = Xg.T @ Xg
        A[g] = Xg.T @ u_r[m]
        B[g] = Xg.T @ yhat_r[m]
    n = len(y)
    c = G / (G - 1) * (n - 1) / (n - K)

    def statistic(beta, scores):
        V = c * XtX_inv @ (scores.T @ scores) @ XtX_inv
        b = beta[idx]
        Vs = V[np.ix_(idx, idx)]
        if len(idx) == 1:
            return abs(b[0]) / np.sqrt(Vs[0, 0])
        return float(b @ np.linalg.pinv(Vs) @ b)

    # 관측 통계량 — 자치구 클러스터 분산으로
    beta_hat = XtX_inv @ (X.T @ y)
    u_hat = y - X @ beta_hat
    scores_hat = np.array([X[gid == g].T @ u_hat[gid == g] for g in range(G)])
    t_obs = statistic(beta_hat, scores_hat)

    Xty_r = B.sum(axis=0)
    draws = []
    for _ in range(n_boot):
        w = rng.choice(WEBB, size=G)
        Xty = Xty_r + (w[:, None] * A).sum(axis=0)
        beta = XtX_inv @ Xty
        # X_g'u*_g = X_g'(yhat_r + u_r w_g) − X_g'X_g beta
        scores = B + w[:, None] * A - H @ beta
        draws.append(statistic(beta, scores))
    draws = np.array(draws)
    p = (1 + int((draws >= t_obs).sum())) / (len(draws) + 1)
    BOOT_COUNTS.append(len(draws))
    return t_obs, float(p), G


def static_design(frame, event_date, treated):
    f = frame.copy()
    f["is_treated"] = f["group"].eq(treated)
    em = pd.Period(event_date[:7], "M")
    f = f[~(f["is_treated"] & f["cal_month"].eq(em))]
    post = f["deal_date"] >= pd.Timestamp(event_date)
    cols = {"did_post": (f["is_treated"] & post).astype(float)}
    for m in sorted(f["cal_month"].unique())[1:]:
        cols[f"m_{m}"] = f["cal_month"].eq(m).astype(float)
    for band in ["LOW", "HIGH", "UNKNOWN"]:
        cols[f"band_{band}"] = f["floor_band"].eq(band).astype(float)
    return f, pd.DataFrame(cols, index=f.index)


def event_sample(event, spec_name):
    ev = EVENTS[event]
    base = raw[raw["eval_complete"] & ~raw["is_duplicate"]]
    if spec_name != "incl_predesig":
        base = base[~base["pre_designated_dong"]]
    start, end = map(pd.Timestamp, ev["window"])
    s = base[base["deal_date"].between(start, end)]
    if spec_name == "drop_jun_jul":
        s = s[~s["cal_month"].isin([pd.Period("2025-06", "M"), pd.Period("2025-07", "M")])]
    if spec_name == "common_support":
        # 추정 표본과 같은 규칙으로 처치군 지정월을 먼저 뺀 뒤, 지정월 이전·이후가
        # 모두 있는 셀만 남긴다 (지정월 거래로만 '후'를 채운 셀이 섞이지 않게)
        em = pd.Period(ev["date"][:7], "M")
        s = s[~(s["group"].eq(ev["treated"]) & s["cal_month"].eq(em))]
        side = np.sign((s["cal_month"] - em).apply(lambda d: d.n))
        both = side.groupby(s["fe_key"]).agg(lambda x: (x < 0).any() and (x > 0).any())
        s = s[s["fe_key"].isin(both[both].index)]
    return s


# ============================================================================
# 3. 식별 점검 (착수 전)
# ============================================================================

print("\n===== 3. 식별 점검 =====")
ident_rows = []
for event, ev in EVENTS.items():
    s = event_sample(event, "main")
    f, X, rels = build_design(s, ev["date"][:7], ev["treated"])
    fa, Xd, y, dropped, n_single = absorb(f, X)
    rank = np.linalg.matrix_rank(Xd.to_numpy())
    leads = [r for r in rels if r < 0]
    lags = [r for r in rels if r > 0]
    ident_rows.append({"event": event, "rows": len(fa), "cols_before": X.shape[1],
                       "cols_after_absorb": Xd.shape[1], "rank": rank,
                       "dropped_constant": ";".join(dropped), "leads": len(leads),
                       "lags": len(lags), "singleton_cells_dropped": n_single})
    print(f"  [{event}] 행 {len(fa):,} / 열 {X.shape[1]} → 흡수 후 {Xd.shape[1]} / 랭크 {rank} "
          f"/ 선행 {len(leads)}개({min(leads)}~{max(leads)}) 후행 {len(lags)}개({min(lags)}~{max(lags)}) "
          f"/ 단일 관측 셀 제외 {n_single:,}행 / 흡수로 사라진 열 {dropped or '없음'}")
ident = pd.DataFrame(ident_rows)
ident_ok = bool((ident["rank"] == ident["cols_after_absorb"]).all())
if not ident_ok:
    print(ident.to_string(index=False))
    raise SystemExit("식별 점검 실패 — 설계행렬이 완전 랭크가 아니다")
print("  → 두 사건 모두 완전 랭크. 추정 진행")


# ============================================================================
# 4. 주 사양 event study
# ============================================================================

print("\n===== 4. 주 사양 event study =====")
coef_rows, summary_rows, fits = [], [], {}
for event, ev in EVENTS.items():
    s = event_sample(event, "main")
    f, X, rels = build_design(s, ev["date"][:7], ev["treated"])
    res = fit(f, X)
    fits[event] = (res, rels)
    ev_cols = [f"ev_{r:+d}" for r in rels]
    leads = [c for c, r in zip(ev_cols, rels) if r < 0 and c in res["beta"].index]
    z = stats.norm.ppf(0.975)
    surviving = [c for c in ev_cols if c in res["beta"].index]
    for c, r in zip(ev_cols, rels):
        if c not in res["beta"].index:
            continue
        b, se = res["beta"][c], res["se"][c]
        _, p_wild_k, _ = wild_cluster(res, [c])
        coef_rows.append({"event": event, "spec": "main", "rel_month": r, "p_wild_gu": p_wild_k,
                          "cal_month": str(pd.Period(ev["date"][:7], "M") + r),
                          "coef": b, "se_cluster_apt": se, "ci_low": b - z * se,
                          "ci_high": b + z * se, "pct": 100 * (np.exp(b) - 1),
                          "p_cluster_apt": 2 * stats.norm.sf(abs(b / se))})
    w_apt, p_apt = joint_wald(res, leads)
    _, p_wild_pre, g = wild_cluster(res, leads)
    print(f"  [{event}] n={res['n']:,} 단지 클러스터 {res['n_clusters']:,} / 자치구 {g}")
    print(f"      사전추세 joint Wald: 단지클러스터 p={p_apt:.3f} / 자치구 WCR p={p_wild_pre:.3f}")
    for row in [r for r in coef_rows if r["event"] == event]:
        print(f"      rel {row['rel_month']:+d} ({row['cal_month']})  {row['pct']:+6.2f}%  "
              f"CI단지[{100 * (np.exp(row['ci_low']) - 1):+6.2f}, {100 * (np.exp(row['ci_high']) - 1):+6.2f}]  "
              f"p자치구WCR={row['p_wild_gu']:.3f}")
    summary_rows.append({"event": event, "spec": "main", "n": res["n"],
                         "pretrend_wald_apt": w_apt, "pretrend_p_apt": p_apt,
                         "pretrend_p_wild_gu": p_wild_pre})


# ============================================================================
# 5. 정적 이중차분 — 주 사양 + 민감도
# ============================================================================

print("\n===== 5. 정적 이중차분 (처치군 × 지정 후) =====")
SPECS = ["main", "common_support", "drop_jun_jul", "incl_predesig"]
static_rows = []
for event, ev in EVENTS.items():
    for spec in SPECS:
        s = event_sample(event, spec)
        f, X = static_design(s, ev["date"], ev["treated"])
        res = fit(f, X)
        if not res["rank_ok"]:
            static_rows.append({"event": event, "spec": spec, "rank_ok": False})
            continue
        b, se = res["beta"]["did_post"], res["se"]["did_post"]
        _, p_wild, g = wild_cluster(res, ["did_post"])
        # 같은 표본의 사전추세(event study 사양)도 함께
        fe, Xe, rels = build_design(s, ev["date"][:7], ev["treated"])
        rese = fit(fe, Xe)
        leads = [f"ev_{r:+d}" for r in rels if r < 0]
        _, p_pre_apt = joint_wald(rese, leads) if rese["rank_ok"] else (np.nan, np.nan)
        static_rows.append({"event": event, "spec": spec, "rank_ok": True, "n": res["n"],
                            "did_coef": b, "did_pct": 100 * (np.exp(b) - 1),
                            "ci_low_pct": 100 * (np.exp(b - 1.96 * se) - 1),
                            "ci_high_pct": 100 * (np.exp(b + 1.96 * se) - 1),
                            "p_cluster_apt": 2 * stats.norm.sf(abs(b / se)),
                            "p_wild_gu": p_wild, "pretrend_p_apt": p_pre_apt})
        print(f"  [{event}] {spec:15s} n={res['n']:>7,}  {100 * (np.exp(b) - 1):+6.2f}% "
              f"CI[{100 * (np.exp(b - 1.96 * se) - 1):+6.2f}, {100 * (np.exp(b + 1.96 * se) - 1):+6.2f}] "
              f"p(단지)={2 * stats.norm.sf(abs(b / se)):.3f} p(자치구WCR)={p_wild:.3f} "
              f"사전추세 p={p_pre_apt:.3f}")

# 가짜 지정일 — B의 지정 전 구간만으로 2025-07-20 적용
print("\n  [placebo] B 지정 전 구간(2025-04-01~2025-10-19), 가짜 지정일 2025-07-20")
pl = raw[raw["eval_complete"] & ~raw["is_duplicate"] & ~raw["pre_designated_dong"]
         & raw["deal_date"].between(pd.Timestamp("2025-04-01"), pd.Timestamp("2025-10-19"))]
f, X = static_design(pl, "2025-07-20", "late21")
res = fit(f, X)
b, se = res["beta"]["did_post"], res["se"]["did_post"]
_, p_wild, _ = wild_cluster(res, ["did_post"])
static_rows.append({"event": "B", "spec": "placebo_0720", "rank_ok": True, "n": res["n"],
                    "did_coef": b, "did_pct": 100 * (np.exp(b) - 1),
                    "ci_low_pct": 100 * (np.exp(b - 1.96 * se) - 1),
                    "ci_high_pct": 100 * (np.exp(b + 1.96 * se) - 1),
                    "p_cluster_apt": 2 * stats.norm.sf(abs(b / se)), "p_wild_gu": p_wild,
                    "pretrend_p_apt": np.nan})
print(f"      n={res['n']:,}  {100 * (np.exp(b) - 1):+6.2f}% "
      f"CI[{100 * (np.exp(b - 1.96 * se) - 1):+6.2f}, {100 * (np.exp(b + 1.96 * se) - 1):+6.2f}] "
      f"p(단지)={2 * stats.norm.sf(abs(b / se)):.3f} p(자치구WCR)={p_wild:.3f}")
static = pd.DataFrame(static_rows)


# ============================================================================
# 6. 그림
# ============================================================================

coefs = pd.DataFrame(coef_rows)
fig, axes = plt.subplots(1, 2, figsize=(13.5, 4.8))
for ax, event in zip(axes, EVENTS):
    ev = EVENTS[event]
    c = coefs[coefs["event"] == event].sort_values("rel_month")
    x = list(c["rel_month"]) + [BASE_REL]
    yv = list(c["pct"]) + [0.0]
    lo = list(100 * (np.exp(c["ci_low"]) - 1)) + [0.0]
    hi = list(100 * (np.exp(c["ci_high"]) - 1)) + [0.0]
    order = np.argsort(x)
    x, yv, lo, hi = (np.array(v)[order] for v in (x, yv, lo, hi))
    ax.axhline(0, color="#12305A", lw=1)
    ax.axvline(-0.5, color="#C0504D", ls="--", lw=1.2)
    ax.fill_between(x, lo, hi, color="#1F8A8C", alpha=0.18)
    ax.plot(x, yv, "-", color="#1F8A8C", lw=1.5)
    sig = c.set_index("rel_month")["p_wild_gu"] < 0.05
    for xi, yi in zip(x, yv):
        if xi == BASE_REL:
            continue
        filled = bool(sig.get(xi, False))
        ax.plot(xi, yi, "o", color="#1F8A8C", ms=6, mfc="#1F8A8C" if filled else "white", mew=1.5)
    ax.scatter([BASE_REL], [0], color="#12305A", zorder=5, s=30)
    st = static[(static["event"] == event) & (static["spec"] == "main")].iloc[0]
    sm = [r for r in summary_rows if r["event"] == event][0]
    ax.set_title(f"사건 {event} {ev['date']} — 처치 {'4개 구' if ev['treated'] == 'early4' else '21개 구'} "
                 f"대 비교 {'21개 구' if ev['comparison'] == 'late21' else '4개 구(이미 지정)'}\n"
                 f"정적 DiD {st['did_pct']:+.2f}% (p 단지 {st['p_cluster_apt']:.3f} / 자치구 {st['p_wild_gu']:.3f}) · "
                 f"사전추세 p 단지 {sm['pretrend_p_apt']:.3f} / 자치구 {sm['pretrend_p_wild_gu']:.3f}", fontsize=10)
    ax.set_xlabel("지정 기준 상대월 (0월 처치군 제외, -1 = 기준)")
    ax.set_ylabel("비교군 대비 ㎡당 가격 차이 (%)")
    ax.grid(alpha=0.3)
fig.suptitle("토지거래허가 지정 전후 상대 가격 추이 — 기술 분석, 인과 해석 금지\n"
             "음영 = 단지 클러스터 95% CI(낙관적) · 채운 점 = 자치구 WCR p<0.05 · 두 사건 모두 한쪽 집단 자치구 4개",
             fontsize=12, y=1.02)
fig.tight_layout()
fig.savefig(FIGURE_PATH)
plt.close(fig)


# ============================================================================
# 7. 판정 및 저장
# ============================================================================

print("\n===== 7. 판정 =====")
def surviving_counts(e):
    res, rels = fits[e]
    names = set(res["beta"].index)
    return (sum(1 for r in rels if r < 0 and f"ev_{r:+d}" in names),
            sum(1 for r in rels if r > 0 and f"ev_{r:+d}" in names))

counts_ok = all(surviving_counts(e)[0] >= MIN_LEADS and surviving_counts(e)[1] >= MIN_LAGS
                for e in EVENTS)
checks = [
    ("모든 적합 설계행렬 완전 랭크", ident_ok and all(FIT_RANK_OK), f"{sum(FIT_RANK_OK)}/{len(FIT_RANK_OK)}개"),
    (f"흡수 후 선행 {MIN_LEADS}·후행 {MIN_LAGS}개 이상", counts_ok,
     " / ".join(f"{e}: 선행 {surviving_counts(e)[0]} 후행 {surviving_counts(e)[1]}" for e in EVENTS)),
    (f"wild bootstrap 전부 {N_BOOT}회", bool(BOOT_COUNTS) and all(n == N_BOOT for n in BOOT_COUNTS),
     f"{len(BOOT_COUNTS)}개 검정"),
    ("단지 → 자치구 중첩", nested_ok, str(nested_ok)),
]
all_passed = True
for label, passed, observed in checks:
    all_passed &= passed
    print(f"  [{'PASS' if passed else 'FAIL'}] {label:28s} {observed}")

with COEF_PATH.open("w", encoding="utf-8") as fh:
    fh.write("# 40.1 토지거래허가 지정 전후 상대 가격 추이 — 기술 분석, 인과 해석 금지\n"
             "# 계수 = 비교군 대비 처치군의 log(㎡당 가격) 차이 (기준 상대월 -1)\n"
             "# 사건 A는 처치, B는 비교 쪽이 자치구 4개뿐 — 자치구 wild bootstrap도 불안정\n"
             "# 단지 클러스터 CI는 가짜 지정일 검정을 유의로 잡을 만큼 낙관적 — p_wild_gu를 우선\n"
             "# 사건 B 비교군(4개 구)은 이미 지정된 지역 — '이미 지정된 곳 대비 상대 변화'\n\n"
             "## 식별 점검\n")
    ident.to_csv(fh, sep="\t", index=False, lineterminator="\n")
    fh.write("\n## event study 계수 (주 사양)\n")
    coefs.round(6).to_csv(fh, sep="\t", index=False, lineterminator="\n")
    fh.write("\n## 사전추세 joint 검정 (주 사양)\n")
    pd.DataFrame(summary_rows).round(6).to_csv(fh, sep="\t", index=False, lineterminator="\n")
    fh.write("\n## 정적 이중차분 — 주 사양·민감도·placebo\n")
    static.round(6).to_csv(fh, sep="\t", index=False, lineterminator="\n")
    fh.write("\n## 판정\n")
    for label, passed, observed in checks:
        fh.write(f"[{'PASS' if passed else 'FAIL'}]\t{label}\t{observed}\n")

print(f"\n계수: {COEF_PATH}\n그림: {FIGURE_PATH}")
print(f"\n===== 40 event study {'통과' if all_passed else '미통과'} =====")
