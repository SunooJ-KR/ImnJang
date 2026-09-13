"""전세 feature의 시간제약·index 보정·as-of 집계를 공통으로 제공한다."""

from __future__ import annotations

import numpy as np
import pandas as pd


AREA_UPPER_M2 = 300.0


class JeonseFeatureBuilder:
    """Rent records를 target 월 이전 정보만으로 cell/complex feature로 바꾼다."""

    def __init__(self, rent_path, complex_df: pd.DataFrame, train_start: pd.Period, train_end: pd.Period):
        rent = pd.read_csv(rent_path, sep="\t", low_memory=False).rename(columns={"aptSeq": "apt_seq"})
        rent["apt_seq"] = rent["apt_seq"].astype(str)
        complex_keys = set(complex_df["apt_seq"].astype(str))
        rent_keys = set(rent["apt_seq"])
        sale_placeholder = None
        area = pd.to_numeric(rent["excluUseAr"], errors="coerce")
        invalid_area = area.isna() | area.le(0) | area.gt(AREA_UPPER_M2)
        outside_complex = ~rent["apt_seq"].isin(complex_keys)
        self.audit = {
            "rent_rows_raw": int(len(rent)),
            "rent_complexes_raw": int(len(rent_keys)),
            "rent_area_invalid_removed": int(invalid_area.sum()),
            "rent_not_in_complex_removed": int((~invalid_area & outside_complex).sum()),
        }
        rent = rent.loc[~invalid_area & ~outside_complex].copy()
        rent["excluUseAr"] = area.loc[rent.index]
        rent["deal_period"] = pd.PeriodIndex(rent["deal_ym"].astype(str), freq="M")
        rent["period_n"] = rent["deal_period"].astype("int64")
        rent["area_type"] = np.round(rent["excluUseAr"] / 3) * 3
        rent["sgg_code"] = rent["sggCd"].astype("string").fillna("MISSING")
        rent["deposit_manwon"] = pd.to_numeric(rent["deposit_manwon"], errors="coerce")
        rent["monthly_rent_manwon"] = pd.to_numeric(rent["monthly_rent_manwon"], errors="coerce").fillna(0)
        rent["is_jeonse_bool"] = rent["is_jeonse"].astype("string").str.lower().eq("true")
        self.rent = rent
        self.train_start, self.train_end = train_start, train_end
        self.audit["pure_jeonse_rows"] = int(rent["is_jeonse_bool"].sum())
        self.audit["monthly_or_semijeonse_rows"] = int((~rent["is_jeonse_bool"]).sum())
        self.audit["conversion_rate_annual"] = 0.05

    def validate_key_system(self, sale_keys: set[str], complex_keys: set[str]) -> dict:
        """키 문자열 형식과 교집합을 명시적으로 기록한다; A2는 별도 제외한다."""
        rent_keys = set(self.rent["apt_seq"])
        pattern = r"^\d{5}-\d+$"
        malformed = int((~self.rent["apt_seq"].str.match(pattern)).sum())
        metrics = {
            "rent_sale_intersection": len(rent_keys & sale_keys),
            "rent_sale_pct_of_rent": 100 * len(rent_keys & sale_keys) / len(rent_keys),
            "rent_sale_pct_of_sale": 100 * len(rent_keys & sale_keys) / len(sale_keys),
            "rent_complex_intersection": len(rent_keys & complex_keys),
            "rent_complex_pct_of_rent": 100 * len(rent_keys & complex_keys) / len(rent_keys),
            "rent_complex_pct_of_complex": 100 * len(rent_keys & complex_keys) / len(complex_keys),
            "rent_key_malformed_rows": malformed,
        }
        if malformed:
            raise ValueError("11.2 aptSeq 문자열 형식이 11.1/23.1 key 체계와 다릅니다.")
        return metrics

    def _amount(self, version: str) -> pd.Series:
        if version == "PURE":
            return self.rent["deposit_manwon"].where(self.rent["is_jeonse_bool"])
        if version == "CONVERTED":
            # 보증부월세 보증금 + 월세×12/0.05. 단위는 모두 만원이다.
            return self.rent["deposit_manwon"] + self.rent["monthly_rent_manwon"] * 12 / 0.05
        raise ValueError(f"알 수 없는 전세 버전: {version}")

    def _prepared_rent(self, version: str, source_end: pd.Period) -> tuple[pd.DataFrame, dict]:
        data = self.rent.loc[self.rent["deal_period"].le(source_end)].copy()
        data["jeonse_amount"] = self._amount(version).loc[data.index]
        data = data.loc[data["jeonse_amount"].gt(0)].copy()
        data["rent_per_m2"] = data["jeonse_amount"] / data["excluUseAr"]
        cell = ["apt_seq", "area_type"]
        train_mask = data["deal_period"].between(self.train_start, self.train_end)
        train_data = data.loc[train_mask].copy()
        cutoffs = train_data.groupby(cell)["rent_per_m2"].agg(lo=lambda s: s.quantile(.01), hi=lambda s: s.quantile(.99))
        global_lo, global_hi = train_data["rent_per_m2"].quantile([.01, .99])
        bounded = data.join(cutoffs, on=cell)
        bounded["lo"] = bounded["lo"].fillna(global_lo)
        bounded["hi"] = bounded["hi"].fillna(global_hi)
        kept = bounded.loc[bounded["rent_per_m2"].between(bounded["lo"], bounded["hi"], inclusive="both")].drop(columns=["lo", "hi"])

        # 자치구×월 index. source_end보다 미래 관측을 포함하지 않으며, 각 자치구
        # 중앙값 대비 상대 index로 만든다. 누락 월은 as-of forward-fill한다.
        month = kept.groupby(["sgg_code", "period_n"])["rent_per_m2"].median().rename("monthly_median").reset_index()
        base = month.groupby("sgg_code")["monthly_median"].median().rename("base_median")
        month = month.join(base, on="sgg_code")
        month["jeonse_index"] = month["monthly_median"] / month["base_median"]
        index_map = month.set_index(["sgg_code", "period_n"])["jeonse_index"]
        kept["record_index"] = pd.MultiIndex.from_frame(kept[["sgg_code", "period_n"]]).map(index_map).astype(float)
        kept["record_index"] = kept["record_index"].replace([np.inf, -np.inf], np.nan).fillna(1.0)
        kept["base_per_m2"] = kept["rent_per_m2"] / kept["record_index"]
        audit = {
            f"{version.lower()}_rent_positive_rows": int(len(data)),
            f"{version.lower()}_rent_outlier_removed": int(len(data) - len(kept)),
            f"{version.lower()}_index_months": int(len(month)),
            f"{version.lower()}_index_max_period": str(source_end),
            f"{version.lower()}_low10_share": self._low10_share(kept),
        }
        return kept, {"month": month, "audit": audit}

    @staticmethod
    def _low10_share(data: pd.DataFrame) -> float:
        if data.empty:
            return float("nan")
        quarter = data["deal_period"].dt.asfreq("Q")
        keys = [data["apt_seq"], data["area_type"], quarter]
        q10 = data.groupby(keys, observed=True)["rent_per_m2"].transform(lambda x: x.quantile(.10))
        return float(data["rent_per_m2"].le(q10).mean() * 100)

    @staticmethod
    def _timeline(data: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
        """cell/complex별 월말 시점까지의 중앙값과 누적 거래 수를 만든다."""
        if data.empty:
            return pd.DataFrame(columns=keys + ["period_n", "median_base", "n_trades", "last_period_n"])
        monthly = data.groupby(keys + ["period_n"], as_index=False).agg(base_per_m2=("base_per_m2", "median"), n=("base_per_m2", "size"))
        monthly = monthly.sort_values(keys + ["period_n"], kind="stable")
        monthly["n_trades"] = monthly.groupby(keys, observed=True)["n"].cumsum()
        # 월 대표값의 expanding median: 갱신계약의 낮은 꼬리에 평균보다 견고하다.
        monthly["median_base"] = monthly.groupby(keys, observed=True)["base_per_m2"].transform(lambda x: x.expanding().median())
        monthly["last_period_n"] = monthly["period_n"]
        return monthly[keys + ["period_n", "median_base", "n_trades", "last_period_n"]]

    @staticmethod
    def _normalize_keys(frame: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
        """merge_asof는 by 키의 dtype이 정확히 같아야 한다. StringDtype의 na_value가
        한쪽은 nan, 한쪽은 pd.NA면 MergeError가 난다. 양쪽을 plain object str로 맞춘다."""
        out = frame.copy()
        for key in keys:
            if key in out.columns:
                out[key] = out[key].astype(object).where(out[key].notna(), "MISSING").astype(str)
        return out

    @staticmethod
    def _asof(left: pd.DataFrame, timeline: pd.DataFrame, keys: list[str], prefix: str) -> pd.DataFrame:
        base = left.reset_index(names="_target_index").copy()
        if timeline.empty:
            for col in ["median_base", "n_trades", "last_period_n"]:
                base[f"{prefix}_{col}"] = np.nan
            return base.set_index("_target_index")
        base = JeonseFeatureBuilder._normalize_keys(base, keys)
        timeline = JeonseFeatureBuilder._normalize_keys(timeline, keys)
        left_sort = base.sort_values(["cutoff_period_n"] + keys, kind="stable")
        right_sort = timeline.sort_values(["period_n"] + keys, kind="stable")
        joined = pd.merge_asof(left_sort, right_sort, left_on="cutoff_period_n", right_on="period_n", by=keys, direction="backward")
        joined = joined.drop(columns=["period_n"], errors="ignore").rename(columns={
            "median_base": f"{prefix}_median_base", "n_trades": f"{prefix}_n_trades", "last_period_n": f"{prefix}_last_period_n",
        })
        return joined.set_index("_target_index").reindex(left.index)

    @staticmethod
    def _target_index(targets: pd.DataFrame, month: pd.DataFrame) -> pd.Series:
        left = JeonseFeatureBuilder._normalize_keys(
            targets[["sgg_code", "cutoff_period_n"]].reset_index(names="_target_index"), ["sgg_code"]
        ).sort_values(["cutoff_period_n", "sgg_code"])
        right = JeonseFeatureBuilder._normalize_keys(
            month[["sgg_code", "period_n", "jeonse_index"]], ["sgg_code"]
        ).sort_values(["period_n", "sgg_code"])
        if right.empty:
            return pd.Series(1.0, index=targets.index)
        joined = pd.merge_asof(left, right, left_on="cutoff_period_n", right_on="period_n", by="sgg_code", direction="backward")
        return joined.set_index("_target_index")["jeonse_index"].reindex(targets.index).fillna(1.0)

    def attach(self, targets: pd.DataFrame, version: str, source_end: pd.Period) -> tuple[pd.DataFrame, dict]:
        """각 target의 기준월 이전 rent만 사용하여 requested feature를 붙인다."""
        if not {"apt_seq", "area_type", "sgg_code", "deal_period", "built_year"}.issubset(targets.columns):
            raise ValueError("전세 feature target에 필수 열이 없습니다.")
        result = targets.copy()
        result["cutoff_period_n"] = np.minimum(result["deal_period"].astype("int64"), int(source_end.ordinal))
        data, state = self._prepared_rent(version, source_end)
        cell = self._asof(result, self._timeline(data, ["apt_seq", "area_type"]), ["apt_seq", "area_type"], "cell")
        complex_ = self._asof(result, self._timeline(data, ["apt_seq"]), ["apt_seq"], "complex")
        result = result.join(cell[[c for c in cell if c.startswith("cell_")]])
        result = result.join(complex_[[c for c in complex_ if c.startswith("complex_")]])
        factor = self._target_index(result, state["month"])
        cell_value = result["cell_median_base"] * factor
        complex_value = result["complex_median_base"] * factor
        has_cell, has_complex = cell_value.notna(), complex_value.notna()
        result["jeonse_per_m2_adj"] = cell_value.where(has_cell, complex_value)
        result["jeonse_n_trades"] = result["cell_n_trades"].where(has_cell, result["complex_n_trades"])
        last = result["cell_last_period_n"].where(has_cell, result["complex_last_period_n"])
        result["jeonse_months_since"] = (result["cutoff_period_n"] - last).where(last.notna())
        source = result.get("area_type_source", pd.Series("SALE", index=result.index)).astype("string")
        result["jeonse_level"] = np.select(
            [has_cell, ~has_cell & has_complex, ~has_cell & ~has_complex & source.str.contains("SALE", na=False)],
            ["RENT_CELL", "RENT_COMPLEX", "SALE_CELL"], default="NONE",
        )
        built = pd.to_numeric(result["built_year"], errors="coerce")
        built_n = ((built - 1970) * 12).astype("Float64")   # pd.Period(freq="M") ordinal은 1970-01 기준 경과 월수다
        result["is_move_in_period"] = ((result["cutoff_period_n"] - built_n).ge(0) & (result["cutoff_period_n"] - built_n).lt(12)).astype(float)
        state["audit"][f"{version.lower()}_feature_max_rent_period"] = str(data["deal_period"].max()) if not data.empty else "NONE"
        state["audit"][f"{version.lower()}_feature_level_counts"] = result["jeonse_level"].value_counts().to_dict()
        return result.drop(columns=[c for c in result if c.startswith(("cell_", "complex_"))] + ["cutoff_period_n"]), state["audit"]
