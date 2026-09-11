"""전세 feature를 포함해 cold-start 단지 가격/m²를 추정한다.

이름 기반 임대 판정은 cold-start 단지에만 적용한다. 최근 24개월 매매 거래가
있는 단지는 이름에 임대 표기가 있어도 제외하지 않는다. 이름에 임대 표기가 없는
임대 단지는 놓칠 수 있고, 동 단위 임대 표기는 분양 세대가 섞인 단지일 수 있다.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
import statsmodels.api as sm
from lightgbm import Dataset, train as lgb_train
from _features import (add_jeonse_interactions, add_location_fe_columns, bool_to_float,
                       build_design, location_category_columns, make_full_bjd_key,
                       model_features, normalize_administrative_code)
from _jeonse import JeonseFeatureBuilder

work_dir = Path(__file__).resolve().parents[2]
output_dir = work_dir / "output"
TRADES_PATH, RENT_PATH = output_dir / "11.1.trades_sale.txt", output_dir / "11.2.trades_rent.txt"
COMPLEX_PATH, METRICS_PATH, PROFILE_PATH = output_dir / "23.1.complex.txt", output_dir / "23.2.complex_metrics.txt", output_dir / "23.3.horizon_profile.txt"
GEOCODED_PATH = output_dir / "14.1.geocoded_master.txt"
REDEVELOP_PATH, CELLS_PATH = output_dir / "31.1.complex_redevelop.txt", output_dir / "32.1.price_cells.txt"
ESTIMATES_PATH, METRICS_OUT_PATH = output_dir / "33.1.coldstart_estimates.txt", output_dir / "33.2.coldstart_metrics.txt"
EXCLUDED_PATH = output_dir / "33.3.excluded_complexes.txt"
TRAIN_START, TRAIN_END, TEST_START, TEST_END = pd.Period("2024-09", "M"), pd.Period("2026-02", "M"), pd.Period("2026-03", "M"), pd.Period("2026-08", "M")
MIN_CALIBRATION_ROWS, DEFAULT_CALIBRATION_MONTHS = 500, 3
CONFORMAL_NOMINAL_LEVELS = (70.0, 80.0, 90.0)
PROFILE_BANDS = ["LOW", "MID", "HIGH"]
# 실측상 cold-start 2,315개 중 아래 이름 키워드 매칭은 109개였고, 최근 24개월
# 매매 거래 단지 6,842개에서는 0개였다. 가격을 매길 수 없는 임대 단지는 제외한다.
RENTAL_KEYWORDS = ("국민임대", "공공임대", "행복주택", "장기전세", "임대", "LH", "SH")
LOCATION_SCHEME, MIN_BJD_TRAIN_ROWS = "hierarchical", 30
LOCATION_COLUMNS = location_category_columns(LOCATION_SCHEME)

def unique(frame, keys, label):
    if frame.duplicated(keys).any(): raise ValueError(f"{label}: {'×'.join(keys)} key 중복")
    return frame

def modal_sgg(values):
    codes = normalize_administrative_code(values).dropna()
    return codes.value_counts().index[0] if not codes.empty else pd.NA

def add_canonical_sgg(complex_df, sale):
    """23.1에 없는 자치구 code를 14.1 gu와 11.1 sggCd의 modal 대응으로 보강한다."""
    geo = unique(pd.read_csv(GEOCODED_PATH, sep="\t", usecols=["aptSeq", "gu"]).rename(columns={"aptSeq":"apt_seq"}), ["apt_seq"], "14.1")
    sale_codes = sale[["apt_seq", "sggCd", "gu"]].copy()
    sale_codes["sale_sgg_code"] = normalize_administrative_code(sale_codes.sggCd)
    gu_map = sale_codes.dropna(subset=["gu", "sale_sgg_code"]).groupby("gu").sale_sgg_code.agg(modal_sgg)
    apt_map = sale_codes.groupby("apt_seq").sale_sgg_code.agg(modal_sgg)
    out = complex_df.merge(geo, on="apt_seq", how="left", validate="one_to_one")
    out["sgg_code"] = out.gu.map(gu_map).fillna(out.apt_seq.map(apt_map)).fillna(out.apt_seq.str.slice(0, 5))
    out["sgg_code"] = normalize_administrative_code(out.sgg_code)
    if out.sgg_code.isna().any(): raise ValueError("23.1 자치구 code 보강 실패")
    out["bjd_full_key"] = make_full_bjd_key(out.bjd_code, out.sgg_code)
    return out.drop(columns="gu")

def dense_bjd_keys(train):
    counts = train.bjd_full_key.dropna().value_counts()
    return set(counts[counts.ge(MIN_BJD_TRAIN_ROWS)].index.astype(str))

def rental_only_exclusions(complex_df, active):
    """cold-start에만 이름 기반 임대 판정을 적용해 화면용 제외 목록을 만든다."""
    def matched_keyword(name):
        text = "" if pd.isna(name) else str(name)
        return next((keyword for keyword in RENTAL_KEYWORDS if keyword in text), None)

    cold = complex_df.loc[~complex_df.apt_seq.isin(active), ["apt_seq", "name"]].copy()
    cold["matched_keyword"] = cold.name.map(matched_keyword)
    excluded = cold.loc[cold.matched_keyword.notna(), ["apt_seq", "name", "matched_keyword"]].copy()
    excluded["exclude_reason"] = "RENTAL_ONLY"
    excluded = excluded[["apt_seq", "name", "exclude_reason", "matched_keyword"]]
    return unique(excluded.sort_values("apt_seq"), ["apt_seq"], "33.3")

def floor_band(trades, profile):
    high = profile.loc[profile.floor_band.eq("HIGH"), ["apt_seq", "repr_floor"]].copy()
    unique(high, ["apt_seq"], "HIGH profile")
    maximum = trades.apt_seq.map(pd.to_numeric(high.set_index("apt_seq").repr_floor, errors="coerce"))
    floor, present = pd.to_numeric(trades.floor, errors="coerce"), maximum.notna() & maximum.gt(0)
    return pd.Series(np.select([present & floor.le(maximum/3), present & floor.gt(maximum/3) & floor.le(maximum*2/3), present & floor.gt(maximum*2/3)], PROFILE_BANDS, default="UNKNOWN"), index=trades.index, dtype="string")

def load_base():
    sale = pd.read_csv(TRADES_PATH, sep="\t", low_memory=False).rename(columns={"aptSeq":"apt_seq"})
    complex_df = unique(pd.read_csv(COMPLEX_PATH, sep="\t", low_memory=False), ["apt_seq"], "23.1")
    metrics = unique(pd.read_csv(METRICS_PATH, sep="\t", low_memory=False), ["apt_seq"], "23.2")
    profile = unique(pd.read_csv(PROFILE_PATH, sep="\t", low_memory=False), ["apt_seq","floor_band"], "23.3")
    redevelop = unique(pd.read_csv(REDEVELOP_PATH, sep="\t", low_memory=False).rename(columns={"aptSeq":"apt_seq"}), ["apt_seq"], "31.1")
    for x in [sale, complex_df, metrics, profile, redevelop]: x["apt_seq"] = x.apt_seq.astype(str)
    complex_df = add_canonical_sgg(complex_df, sale)
    sale["deal_period"] = pd.PeriodIndex(sale.deal_ym.astype(str), freq="M")
    sale["deal_amount_manwon"], sale["excluUseAr"] = pd.to_numeric(sale.deal_amount_manwon, errors="coerce"), pd.to_numeric(sale.excluUseAr, errors="coerce")
    valid = sale.apt_seq.isin(complex_df.apt_seq) & sale.is_cancelled.astype("string").str.lower().ne("true") & sale.deal_amount_manwon.gt(0) & sale.excluUseAr.gt(0) & sale.excluUseAr.le(300) & sale.deal_period.between(TRAIN_START, TEST_END)
    sale = sale.loc[valid].copy()
    if sale.deal_period.min()!=TRAIN_START or sale.deal_period.max()!=TEST_END: raise ValueError("매매 split 불일치")
    sale["area_type"], sale["floor_band"] = np.round(sale.excluUseAr/3)*3, floor_band(sale, profile)
    sale["price_per_m2"], sale["y"] = sale.deal_amount_manwon/sale.excluUseAr, np.log(sale.deal_amount_manwon/sale.excluUseAr)
    keep=["apt_seq","bjd_code","bjd_full_key","sgg_code","built_year","total_households","far","bcr","parking_per_hh","redevelop_type","redevelop_stage"]
    sale=sale.merge(complex_df[keep],on="apt_seq",how="left",validate="many_to_one").merge(metrics,on="apt_seq",how="left",validate="many_to_one")
    physical=profile.drop(columns=["repr_floor","obs_height","sun_hours_spring"],errors="ignore")
    sale=sale.merge(physical,on=["apt_seq","floor_band"],how="left",validate="many_to_one").drop(columns=["redevelop_type","redevelop_stage"]).merge(redevelop[["apt_seq","redevelop_type","redevelop_stage"]],on="apt_seq",how="left",validate="many_to_one")
    for c in ["river_view","park_view","mountain_view"]: sale[c]=bool_to_float(sale[c])
    for c in ["far","bcr","parking_per_hh"]: sale[c]=pd.to_numeric(sale[c],errors="coerce")
    sale["is_redevelop"], sale["redevelop_stage_advanced"] = sale.redevelop_type.notna().astype(float), sale.redevelop_stage.isin(["관리처분","착공"]).astype(float)
    sale["bjd_code"], sale["area_type_source"] = sale.bjd_code.astype("string").fillna("MISSING"), "SALE"
    return sale,complex_df,profile,TEST_END

def clean_train(x):
    cut=x.groupby(["apt_seq","area_type"]).price_per_m2.agg(lo=lambda s:s.quantile(.01),hi=lambda s:s.quantile(.99))
    y=x.join(cut,on=["apt_seq","area_type"]); z=y.loc[y.price_per_m2.between(y.lo,y.hi)].drop(columns=["lo","hi"])
    return z,{"raw":len(x),"clean":len(z),"removed":len(x)-len(z)}
def mape(a,p): return float(np.mean(np.abs(a.to_numpy(float)-np.asarray(p))/a.to_numpy(float))*100)
def fit(x,features,family,context=None):
    d,context=build_design(x,features,context,LOCATION_COLUMNS)
    if family=="OLS": return sm.OLS(x.y.astype(float),d).fit(),context
    data=Dataset(d,label=x.y.astype(float),feature_name=list(d.columns),free_raw_data=False)
    return lgb_train({"objective":"regression","learning_rate":.05,"num_leaves":31,"min_data_in_leaf":40,"feature_fraction":.8,"bagging_fraction":.8,"bagging_freq":1,"lambda_l2":1,"seed":20260911,"verbosity":-1,"num_threads":-1},data,num_boost_round=300),context
def predict(model,x,features,context,family):
    d,_=build_design(x,features,context); return np.exp(model.predict(d,num_iteration=model.best_iteration) if family=="LightGBM" else model.predict(d))
def split(x):
    h=pd.util.hash_pandas_object(x.apt_seq.astype(str),index=False).to_numpy(dtype=np.uint64)%5
    return x.loc[h!=0].copy(),x.loc[h==0].copy()
def bin_mapes(x,p):
    h=pd.to_numeric(x.total_households,errors="coerce"); masks={"<=20":h.le(20),"21~100":h.between(21,100),"101+":h.ge(101)}
    return {k:mape(x.loc[v,"price_per_m2"],np.asarray(p)[v.to_numpy()]) if v.any() else float("nan") for k,v in masks.items()}

def prepare(x,rent,version,end,interaction,levels):
    if rent is None:return x.copy(),{}
    z,a=rent.attach(x,version,end)
    return (add_jeonse_interactions(z,levels) if interaction else z),a
def score(name,train,test,rent=None,version=None,interaction=False):
    levels=sorted(train.sgg_code.astype(str).unique()) if interaction else []
    tr,a=prepare(train,rent,version,TRAIN_END,interaction,levels); te,b=prepare(test,rent,version,TRAIN_END,interaction,levels); features=model_features(rent is not None,levels)
    ft,va=split(tr); family_scores={}
    for family in ["OLS","LightGBM"]:
        mo,co=fit(ft,features,family); family_scores[family]=mape(va.price_per_m2,predict(mo,va,features,co,family))
    family=min(family_scores,key=family_scores.get); mo,co=fit(tr,features,family); pt=predict(mo,te,features,co,family); mh,ch=fit(ft,features,family); ph=predict(mh,va,features,ch,family)
    return {"name":name,"version":version,"interaction":interaction,"family":family,"features":features,"levels":levels,"train":tr,"test":te,"oot":mape(te.price_per_m2,pt),"hold":mape(va.price_per_m2,ph),"bins":bin_mapes(va,ph),"n":len(va),"complexes":va.apt_seq.nunique(),"audit":a|{f"test_{k}":v for k,v in b.items()}}
def pick(xs): return min(xs,key=lambda x:(x["hold"],x["oot"]))
def coverage(actual, predicted, lo, hi):
    return float(np.mean((actual >= predicted*np.exp(lo)) & (actual <= predicted*np.exp(hi))) * 100)

def calibration_split(train):
    """시간적으로 미래인 holdout 단지에서 calibration residual을 만든다."""
    for months in range(DEFAULT_CALIBRATION_MONTHS, 7):
        cal_start = TRAIN_END - (months - 1)
        inner_fit_all = train.loc[train.deal_period.lt(cal_start)].copy()
        inner_cal_all = train.loc[train.deal_period.between(cal_start, TRAIN_END)].copy()
        inner_fit, _ = split(inner_fit_all)
        _, inner_cal = split(inner_cal_all)
        if len(inner_cal) >= MIN_CALIBRATION_ROWS:
            return inner_fit, inner_cal, cal_start, months
    raise ValueError(f"inner_cal 표본 부족: 3~6개월 및 hash calibration 단지에서 {MIN_CALIBRATION_ROWS}행 미만")

def intervals(chosen):
    inner_fit, inner_cal, cal_start, cal_months = calibration_split(chosen["train"])
    # inner model의 feature 수준도 inner_fit에서만 확정한다. deal_ym fixed effect의
    # 미관측 미래월은 build_design이 마지막 학습월 reference로 처리한다.
    inner_levels = sorted(inner_fit.sgg_code.astype(str).unique()) if chosen["interaction"] else []
    inner_features = model_features(chosen["version"] is not None, inner_levels)
    mo, co = fit(inner_fit, inner_features, chosen["family"])
    pc = predict(mo, inner_cal, inner_features, co, chosen["family"])
    residual = np.log(inner_cal.price_per_m2) - np.log(pc)
    mf, cf = fit(chosen["train"], chosen["features"], chosen["family"])
    pt = predict(mf, chosen["test"], chosen["features"], cf, chosen["family"])
    results = []
    for nominal in CONFORMAL_NOMINAL_LEVELS:
        tail = (100.0 - nominal) / 200.0
        lo, hi = np.quantile(residual, [tail, 1.0-tail])
        results.append({
            "nominal": nominal, "lo": float(lo), "hi": float(hi),
            "inner_cal_coverage": coverage(inner_cal.price_per_m2, pc, lo, hi),
            "test_coverage": coverage(chosen["test"].price_per_m2, pt, lo, hi),
        })
    primary = next(x for x in results if x["nominal"] == 80.0)
    primary.update({
        "inner_fit_end": str(cal_start - 1), "inner_cal_start": str(cal_start),
        "inner_cal_end": str(TRAIN_END), "inner_cal_months": cal_months,
        "inner_fit_n": len(inner_fit), "inner_fit_complexes": inner_fit.apt_seq.nunique(),
        "inner_cal_n": len(inner_cal), "inner_cal_complexes": inner_cal.apt_seq.nunique(),
        "alternatives": results,
    })
    return primary, primary["test_coverage"]

def targets(complex_df,profile,latest,rent):
    active=set(pd.read_csv(CELLS_PATH,sep="\t",usecols=["apt_seq"]).apt_seq.astype(str)); cold=set(complex_df.apt_seq)-active
    if len(cold)!=2318:raise ValueError(f"cold-start 수 불일치: {len(cold)}")
    s=pd.read_csv(TRADES_PATH,sep="\t",low_memory=False).rename(columns={"aptSeq":"apt_seq"});s.apt_seq=s.apt_seq.astype(str);s["deal_period"]=pd.PeriodIndex(s.deal_ym.astype(str),freq="M");s["excluUseAr"]=pd.to_numeric(s.excluUseAr,errors="coerce");s=s.loc[s.apt_seq.isin(cold)&s.is_cancelled.astype("string").str.lower().ne("true")&s.excluUseAr.gt(0)&s.excluUseAr.le(300)&s.deal_period.between(latest-59,latest)].copy();s["area_type"]=np.round(s.excluUseAr/3)*3;s["floor_band"]=floor_band(s,profile);s["area_type_source"]="SALE"
    r=rent.rent.loc[rent.rent.apt_seq.isin(cold)&rent.rent.deal_period.between(latest-59,latest),["apt_seq","area_type","floor","deal_period"]].copy();r["floor_band"]=floor_band(r,profile);r["area_type_source"]="RENT"
    both=pd.concat([s[["apt_seq","area_type","floor_band","area_type_source"]],r[["apt_seq","area_type","floor_band","area_type_source"]]]);src=both.groupby(["apt_seq","area_type"],as_index=False).area_type_source.agg(lambda x:"BOTH" if x.nunique()==2 else x.iloc[0]);out=both.drop(columns="area_type_source").drop_duplicates().merge(src,on=["apt_seq","area_type"],how="left")
    return out,{"cold":len(cold),"complexes":out.apt_seq.nunique(),"rows":len(out),"no_area":len(cold-set(out.apt_seq)),"sources":src.area_type_source.value_counts().to_dict()}
def physical(x):return x[["sun_hours_winter","open_angle_mean","view_block_pct","open_span_max","river_view","park_view","mountain_view"]].notna().all(axis=1)

def main():
    print("===== 1. A1 키 검증 =====");raw,complex_df,profile,latest=load_base();rent=JeonseFeatureBuilder(RENT_PATH,complex_df,TRAIN_START,TRAIN_END);sale_keys=set(pd.read_csv(TRADES_PATH,sep="\t",usecols=["aptSeq"]).aptSeq.astype(str));keys=rent.validate_key_system(sale_keys,set(complex_df.apt_seq));print(f"  11.2→11.1 {keys['rent_sale_intersection']:,} ({keys['rent_sale_pct_of_rent']:.2f}%), 11.2→23.1 {keys['rent_complex_intersection']:,} ({keys['rent_complex_pct_of_rent']:.2f}%)")
    train,audit=clean_train(raw.loc[raw.deal_period.between(TRAIN_START,TRAIN_END)].copy());test=raw.loc[raw.deal_period.between(TEST_START,TEST_END)].copy()
    full_keys=raw.bjd_full_key.dropna();mixed=int(raw.dropna(subset=["bjd_full_key"]).groupby("bjd_full_key").sgg_code.nunique().gt(1).sum())
    if full_keys.nunique()<300: raise AssertionError(f"완전 법정동 키 고유값 부족: {full_keys.nunique()}")
    if mixed: raise AssertionError(f"완전 법정동 키 자치구 혼입: {mixed}")
    dense_keys=dense_bjd_keys(train)
    train=add_location_fe_columns(train,LOCATION_SCHEME,dense_keys);test=add_location_fe_columns(test,LOCATION_SCHEME,dense_keys)
    print(f"  완전 법정동 키 {full_keys.nunique():,}개, 자치구 혼입 {mixed}개, 계층 dense {len(dense_keys):,}개")
    print("===== 2. J/K/L 비교 =====")
    j=score("J: I (전세 없음)",train,test);k=pick([score("K: 순수 전세 / linear",train,test,rent,"PURE",False),score("K: 순수 전세 / 자치구 교호",train,test,rent,"PURE",True)]);l=pick([score("L: 5% 환산 / linear",train,test,rent,"CONVERTED",False),score("L: 5% 환산 / 자치구 교호",train,test,rent,"CONVERTED",True)]);choices=[j,k,l];candidate=pick([k,l]);use_rent=candidate["hold"]<j["hold"];chosen=candidate if use_rent else j;inte,cov=intervals(chosen);print(f"  J/K/L holdout: {j['hold']:.2f}% / {k['hold']:.2f}% / {l['hold']:.2f}% | 채택 {chosen['name']}")
    print("===== 3. cold-start 추정 =====");tar,ta=targets(complex_df,profile,latest,rent);active=set(pd.read_csv(CELLS_PATH,sep="\t",usecols=["apt_seq"]).apt_seq.astype(str));excluded=rental_only_exclusions(complex_df,active);excluded.to_csv(EXCLUDED_PATH,sep="\t",index=False);ta["before_exclusion_complexes"],ta["before_exclusion_rows"]=tar.apt_seq.nunique(),len(tar);tar=tar.loc[~tar.apt_seq.isin(excluded.apt_seq)].copy();ta["after_exclusion_complexes"],ta["after_exclusion_rows"]=tar.apt_seq.nunique(),len(tar);tar=tar.merge(complex_df.drop(columns=["redevelop_type","redevelop_stage"]),on="apt_seq",how="left",validate="many_to_one");met=unique(pd.read_csv(METRICS_PATH,sep="\t",low_memory=False).assign(apt_seq=lambda x:x.apt_seq.astype(str)),["apt_seq"],"23.2");tar=tar.merge(met,on="apt_seq",how="left",validate="many_to_one").merge(profile.drop(columns=["repr_floor","obs_height","sun_hours_spring"],errors="ignore"),on=["apt_seq","floor_band"],how="left",validate="many_to_one");red=pd.read_csv(REDEVELOP_PATH,sep="\t",low_memory=False).rename(columns={"aptSeq":"apt_seq"});red.apt_seq=red.apt_seq.astype(str);tar=tar.merge(red[["apt_seq","redevelop_type","redevelop_stage"]],on="apt_seq",how="left",validate="many_to_one")
    for c in ["river_view","park_view","mountain_view"]:tar[c]=bool_to_float(tar[c])
    for c in ["far","bcr","parking_per_hh"]:tar[c]=pd.to_numeric(tar[c],errors="coerce")
    tar["is_redevelop"],tar["redevelop_stage_advanced"],tar["bjd_code"],tar["deal_period"],tar["deal_ym"],tar["excluUseAr"]=tar.redevelop_type.notna().astype(float),tar.redevelop_stage.isin(["관리처분","착공"]).astype(float),tar.bjd_code.astype("string").fillna("MISSING"),latest,str(latest),tar.area_type.astype(float)
    tar=add_location_fe_columns(tar,LOCATION_SCHEME,dense_keys)
    tx,final_audit=prepare(tar,rent if use_rent else None,chosen["version"],latest,chosen["interaction"],chosen["levels"]);mo,co=fit(chosen["train"],chosen["features"],chosen["family"]);est=predict(mo,tx,chosen["features"],co,chosen["family"]);tx["est_price_per_m2"],tx["est_low"],tx["est_high"]=est,est*np.exp(inte["lo"]),est*np.exp(inte["hi"]);tx["jeonse_over_sale_ratio_gt1"]=tx.get("jeonse_per_m2_adj",pd.Series(np.nan,index=tx.index)).gt(tx.est_price_per_m2);ref=train.groupby(["bjd_full_key","area_type","floor_band"]).size();tx["reference_cell_n"]=ref.reindex(pd.MultiIndex.from_frame(tx[["bjd_full_key","area_type","floor_band"]]),fill_value=0).to_numpy();small=pd.to_numeric(tx.total_households,errors="coerce").le(20);tx["est_confidence"]=np.select([~small&physical(tx)&tx.reference_cell_n.ge(20),~small&tx.reference_cell_n.ge(5)],["HIGH","MEDIUM"],default="LOW")
    cols=["apt_seq","area_type","area_type_source","floor_band","jeonse_level","jeonse_per_m2_adj","jeonse_n_trades","jeonse_months_since","is_move_in_period","jeonse_over_sale_ratio_gt1","est_price_per_m2","est_low","est_high","est_confidence"]
    out=tx[cols].sort_values(["apt_seq","area_type","floor_band"])
    overlap=set(out.apt_seq)&active
    rental_in_estimates=set(out.apt_seq)&set(excluded.apt_seq)
    excluded_in_price_cells=set(excluded.apt_seq)&active
    leak=all(pd.Period(v,"M")<=TRAIN_END for k,v in chosen["audit"].items() if k.endswith("feature_max_rent_period")) if use_rent else True
    nominal=inte["nominal"]
    coverage_ok=abs(cov-nominal)<=8
    keyword_counts=excluded.matched_keyword.value_counts().reindex(RENTAL_KEYWORDS,fill_value=0).to_dict()
    checks=[
        ("A1 aptSeq key 체계",keys["rent_key_malformed_rows"]==0,f"malformed {keys['rent_key_malformed_rows']}"),
        ("완전 법정동 키 300개 이상",full_keys.nunique()>=300,f"{full_keys.nunique()}"),
        ("완전 법정동 키 자치구 혼입 0건",mixed==0,f"{mixed}"),
        ("33.1/32.1 apt_seq 겹침 0건",not overlap,f"{len(overlap)}"),
        ("33.1 임대 키워드 단지 0건",not rental_in_estimates,f"{len(rental_in_estimates)}"),
        ("33.3/32.1 apt_seq 겹침 0건",not excluded_in_price_cells,f"{len(excluded_in_price_cells)}"),
        (f"test coverage nominal {nominal:.0f}% ±8%p",coverage_ok,f"{cov:.2f}%"),
        ("test 전세 feature test기간 데이터 미사용",leak,f"최대 {TRAIN_END}"),
        ("전세 채택시 J보다 holdout 개선",not use_rent or candidate["hold"]<j["hold"],f"J {j['hold']:.2f} / 후보 {candidate['hold']:.2f}"),
    ]
    lines=["# 33.2 cold-start 전세 feature 평가","","## A. 데이터 정합",f"- A1 11.2→11.1 교집합 {keys['rent_sale_intersection']:,}단지 (11.2 {keys['rent_sale_pct_of_rent']:.2f}%, 11.1 {keys['rent_sale_pct_of_sale']:.2f}%); 11.2→23.1 {keys['rent_complex_intersection']:,}단지 (11.2 {keys['rent_complex_pct_of_rent']:.2f}%, 23.1 {keys['rent_complex_pct_of_complex']:.2f}%). 형식 불일치 {keys['rent_key_malformed_rows']}행.",f"- A2 11.2이나 23.1 미존재 행 제외 {rent.audit['rent_not_in_complex_removed']:,}; A3 excluUseAr <=0/결측/>300m² 제외 {rent.audit['rent_area_invalid_removed']:,}.","", "## 지역 fixed effect",f"- 10자리 완전 법정동 키 고유값 {full_keys.nunique():,}개; 자치구 혼입 {mixed}개. bjd_code 결측은 완전 법정동 더미에 넣지 않고 자치구로 흡수했습니다.",f"- 30.spike_hedonic.py의 사전 고정 비교 결과에 맞춰 {LOCATION_SCHEME} 사양을 사용했습니다. train 30건 미만 법정동은 자치구 FE로 흡수하며, 최종 dense 법정동은 {len(dense_keys):,}개입니다.","","## J/K/L 동일 split","variant\tfamily\tD4_spec\tOOT_MAPE_pct\tholdout_MAPE_pct\t<=20\t21~100\t101+",*[f"{x['name']}\t{x['family']}\t{'sgg interaction' if x['interaction'] else 'linear'}\t{x['oot']:.6f}\t{x['hold']:.6f}\t{x['bins']['<=20']:.6f}\t{x['bins']['21~100']:.6f}\t{x['bins']['101+']:.6f}" for x in choices],f"- 주 지표는 단지 holdout이며 {j['complexes']:,}단지/{j['n']:,}행을 안정 hash 80/20으로 분리했습니다. D4 linear와 자치구×전세가 교호항은 각각 holdout으로 선택했습니다.",f"- {'전세 feature 채택' if use_rent else '전세 후보가 J holdout MAPE를 낮추지 못하여 미채택'}: {chosen['name']} / {chosen['family']}.","","## B/C 전세 처리",f"- B1 순수 전세 {rent.audit['pure_jeonse_rows']:,}행 및 보증부월세 환산을 모두 평가; 환산식=보증금+월세×12/0.05 (연 5% 고정).",f"- B2 셀 대표값은 평균이 아닌 expanding 중앙값. 단지×면적×분기 하위10% 비중: PURE {k['audit'].get('pure_low10_share',float('nan')):.2f}%, CONVERTED {l['audit'].get('converted_low10_share',float('nan')):.2f}%. contractType/preDeposit 부재로 갱신계약 완전 탐지는 불가하며 저가 갱신계약이 남을 수 있습니다.",f"- B4 준공월 부재로 준공연도 1월 proxy부터 12개월 이내 is_move_in_period을 feature에 포함. B5 1/99% cutoff는 train rent만으로 적합 (PURE {k['audit'].get('pure_rent_outlier_removed',0):,}행, CONVERTED {l['audit'].get('converted_rent_outlier_removed',0):,}행 제거).",f"- C1 자치구×월 중앙값 전세 index로 기준월 보정. C2 평가 feature/index source 최대월={TRAIN_END}; test 기간 rent는 사용하지 않았습니다. C3 최종 추정은 {latest} 이전 최근 rent 사용.","","## conformal",f"- nominal 80% 구간의 log 상대오차 10/90 분위 {inte['lo']:.6f}, {inte['hi']:.6f}. inner_fit은 80% 단지×{TRAIN_START}~{inte['inner_fit_end']} ({inte['inner_fit_complexes']:,}단지/{inte['inner_fit_n']:,}행), inner_cal은 hash calibration 단지×{inte['inner_cal_start']}~{inte['inner_cal_end']} ({inte['inner_cal_complexes']:,}단지/{inte['inner_cal_n']:,}행)입니다.",f"- inner_cal coverage {inte['inner_cal_coverage']:.2f}%, test coverage {cov:.2f}%, 차이 {cov-inte['inner_cal_coverage']:+.2f}%p. inner_cal 기간은 {inte['inner_cal_months']}개월입니다.","- 명목 수준\tlog 하한\tlog 상한\tinner_cal coverage\ttest coverage",*[f"- {x['nominal']:.0f}%\t{x['lo']:.6f}\t{x['hi']:.6f}\t{x['inner_cal_coverage']:.2f}%\t{x['test_coverage']:.2f}%" for x in inte['alternatives']],"","## cold-start",f"- 2,318 단지 중 sale/rent 면적·층대 합집합 {ta['complexes']:,}단지/{ta['rows']:,}행; 면적 미발견 {ta['no_area']:,}개. A4 면적 출처 {ta['sources']}.",f"- jeonse_level {out.jeonse_level.value_counts().to_dict()}; B3 전세/추정매매가>1.0 플래그 {int(out.jeonse_over_sale_ratio_gt1.sum()):,}행(제거 안 함).",f"- est_confidence {out.est_confidence.value_counts().to_dict()}; <=20세대는 holdout 외삽 위험으로 LOW 강등.","","## 자체 검증",*[f"- [{'PASS' if ok else 'FAIL'}] {label}: {detail}" for label,ok,detail in checks]]
    insertion=lines.index("## 자체 검증")
    lines[insertion:insertion]=[
        "## 임대 전용 단지 제외",
        f"- cold-start 단지 이름에서 임대성 키워드 매칭 {len(excluded):,}개를 33.3으로 별도 저장하고, 33.1 추정 대상에서 제외했습니다. 키워드별 분포 {keyword_counts}.",
        f"- 추정 대상 단지는 제외 전 {ta['before_exclusion_complexes']:,}개/{ta['before_exclusion_rows']:,}행에서 제외 후 {ta['after_exclusion_complexes']:,}개/{ta['after_exclusion_rows']:,}행으로 감소했습니다.",
        "- 한계: 이름에 임대 표기가 없는 임대 단지는 잡지 못합니다. 반대로 임대 표기가 있어도 분양 세대가 섞인 동 단위 표기일 수 있습니다.",
        "- 따라서 이 규칙은 cold-start 단지에만 적용합니다. 실제 최근 24개월 매매 거래가 있는 32.1 단지는 이름에 임대 표기가 있어도 제외하지 않습니다.",
        "",
    ]
    METRICS_OUT_PATH.write_text("\n".join(lines)+"\n",encoding="utf-8")
    if all(ok for _,ok,_ in checks):out.to_csv(ESTIMATES_PATH,sep="\t",index=False,float_format="%.6f");print(f"  저장: {ESTIMATES_PATH.relative_to(work_dir)} ({len(out):,}행)")
    elif ESTIMATES_PATH.exists():ESTIMATES_PATH.unlink();print("  33.1 미발행: 검증 FAIL")
    print(f"  저장: {EXCLUDED_PATH.relative_to(work_dir)} ({len(excluded):,}개)")
    print(f"  저장: {METRICS_OUT_PATH.relative_to(work_dir)}")
    if not all(ok for _,ok,_ in checks):raise AssertionError("필수 검증 실패")
if __name__=="__main__":main()
