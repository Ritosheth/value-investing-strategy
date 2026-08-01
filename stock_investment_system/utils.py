from __future__ import annotations

import pandas as pd


def safe_merge(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    on: str | list[str],
    how: str = "left",
    suffix: str = "_right",
) -> pd.DataFrame:
    """Merge without failing when the enrichment frame is empty or incomplete."""

    if right is None or right.empty:
        return left.copy()
    keys = [on] if isinstance(on, str) else list(on)
    if any(key not in right.columns for key in keys):
        return left.copy()
    return left.merge(right, on=on, how=how, suffixes=("", suffix))


def clip_score(value: pd.Series | float, lower: float = 0.0, upper: float = 100.0):
    return pd.to_numeric(value, errors="coerce").fillna(0.0).clip(lower, upper)

