"""가격 파이프라인의 공통 층대 규칙을 제공한다."""

from __future__ import annotations

import numpy as np
import pandas as pd


FLOOR_BANDS = ["LOW", "MID", "HIGH"]


def load_actual_max_levels(complex_final: pd.DataFrame) -> pd.Series:
    """15.1의 단지별 실제 최고층을 apt_seq index Series로 반환한다."""
    required = {"apt_seq", "max_levels"}
    missing = required - set(complex_final.columns)
    if missing:
        raise ValueError(f"15.1.complex_final에 필수 열이 없습니다: {sorted(missing)}")
    if complex_final["apt_seq"].duplicated().any():
        raise ValueError("15.1.complex_final의 apt_seq가 유일하지 않습니다.")
    maximum = pd.to_numeric(complex_final["max_levels"], errors="coerce")
    return pd.Series(maximum.to_numpy(), index=complex_final["apt_seq"].astype(str), name="max_levels")


def assign_floor_band(trades: pd.DataFrame, max_levels: pd.Series) -> pd.Series:
    """실제 최고층의 1/3·2/3 경계로 LOW/MID/HIGH를 공통 배정한다."""
    if not {"apt_seq", "floor"}.issubset(trades.columns):
        raise ValueError("층대 배정 거래에 apt_seq 또는 floor 열이 없습니다.")
    floor = pd.to_numeric(trades["floor"], errors="coerce")
    maximum = trades["apt_seq"].astype(str).map(max_levels)
    present = maximum.notna() & maximum.gt(0)
    return pd.Series(
        np.select(
            [
                present & floor.le(maximum / 3),
                present & floor.gt(maximum / 3) & floor.le(maximum * 2 / 3),
                present & floor.gt(maximum * 2 / 3),
            ],
            FLOOR_BANDS,
            default="UNKNOWN",
        ),
        index=trades.index,
        dtype="string",
    )
