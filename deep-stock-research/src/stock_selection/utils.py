from __future__ import annotations

import datetime as dt
import math
from typing import Iterable, Sequence

import pandas as pd


def today_yyyymmdd() -> str:
    return dt.date.today().strftime("%Y%m%d")


def report_date_candidates(today: dt.date | None = None) -> list[str]:
    today = today or dt.date.today()
    year = today.year
    candidates: list[str] = []

    if today.month >= 11:
        candidates.append(f"{year}0930")
    if today.month >= 9:
        candidates.append(f"{year}0630")
    if today.month >= 5:
        candidates.append(f"{year}0331")

    for y in range(year - 1, year - 4, -1):
        candidates.extend([f"{y}1231", f"{y}0930", f"{y}0630", f"{y}0331"])

    return list(dict.fromkeys(candidates))


def recent_trading_date_candidates(days: int = 30, today: dt.date | None = None) -> list[str]:
    today = today or dt.date.today()
    result: list[str] = []
    cursor = today
    while len(result) < days:
        if cursor.weekday() < 5:
            result.append(cursor.strftime("%Y%m%d"))
        cursor -= dt.timedelta(days=1)
    return result


def market_for_code(code: str) -> str:
    text = str(code).zfill(6)
    if text.startswith(("6", "9")):
        return "sh"
    if text.startswith(("4", "8")):
        return "bj"
    return "sz"


def prefixed_code(code: str) -> str:
    text = str(code).zfill(6)
    if text.startswith(("6", "9")):
        return f"SH{text}"
    if text.startswith(("4", "8")):
        return f"BJ{text}"
    return f"SZ{text}"


def normalize_code(value: object) -> str:
    if pd.isna(value):
        return ""
    text = str(value).strip()
    if "." in text and text.split(".", 1)[0].upper() in {"SH", "SZ", "BJ", "HK"}:
        text = text.split(".", 1)[1]
    if "." in text and text.replace(".", "", 1).isdigit():
        text = text.split(".", 1)[0]
    text = (
        text.replace("SH", "")
        .replace("SZ", "")
        .replace("BJ", "")
        .replace("sh", "")
        .replace("sz", "")
        .replace("bj", "")
    )
    return text.zfill(6)


def find_column(df: pd.DataFrame, names: Sequence[str]) -> str | None:
    for name in names:
        if name in df.columns:
            return name
    return None


def require_column(df: pd.DataFrame, names: Sequence[str], label: str) -> str:
    column = find_column(df, names)
    if column is None:
        raise KeyError(f"Missing {label}; expected one of {', '.join(names)}")
    return column


def first_matching_column(df: pd.DataFrame, contains: Iterable[str]) -> str | None:
    tokens = list(contains)
    for column in df.columns:
        text = str(column)
        if all(token in text for token in tokens):
            return column
    return None


def numeric(series: pd.Series, default: float = 0.0) -> pd.Series:
    if series is None:
        return pd.Series(dtype=float)
    cleaned = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("%", "", regex=False)
        .str.replace("--", "", regex=False)
        .str.strip()
    )
    return pd.to_numeric(cleaned, errors="coerce").fillna(default)


def normalize_score(
    series: pd.Series,
    *,
    higher_is_better: bool = True,
    lower: float | None = None,
    upper: float | None = None,
) -> pd.Series:
    values = numeric(series, default=math.nan)
    if lower is None:
        lower = values.quantile(0.05)
    if upper is None:
        upper = values.quantile(0.95)
    if pd.isna(lower) or pd.isna(upper) or upper == lower:
        return pd.Series(50.0, index=series.index)
    clipped = values.clip(lower=lower, upper=upper)
    scaled = (clipped - lower) / (upper - lower) * 100.0
    if not higher_is_better:
        scaled = 100.0 - scaled
    return scaled.fillna(50.0)


def valuation_score(pe: pd.Series, pb: pd.Series | None = None) -> pd.Series:
    pe_values = numeric(pe, default=math.nan)
    pe_component = pd.Series(45.0, index=pe.index)
    valid = pe_values > 0
    pe_component.loc[valid] = 100.0 - (pe_values.loc[valid] - 8.0).abs().clip(0, 60) / 60.0 * 100.0
    pe_component = pe_component.clip(0, 100)

    if pb is None:
        return pe_component

    pb_values = numeric(pb, default=math.nan)
    pb_component = pd.Series(50.0, index=pb.index)
    valid_pb = pb_values > 0
    pb_component.loc[valid_pb] = 100.0 - (pb_values.loc[valid_pb] - 1.5).abs().clip(0, 8) / 8.0 * 100.0
    return (pe_component * 0.65 + pb_component.clip(0, 100) * 0.35).fillna(45.0)


def bucket_for_score(score: float, preferred_bucket: str = "core") -> str:
    if score >= 80:
        return "core" if preferred_bucket == "core" else preferred_bucket
    if score >= 65:
        return "watchlist"
    if score >= 50:
        return "timing/event"
    return "risk-watch"


def add_reason(existing: object, reason: str) -> str:
    if pd.isna(existing) or not str(existing).strip():
        return reason
    return f"{existing}; {reason}"


def safe_merge(left: pd.DataFrame, right: pd.DataFrame, on: str, suffix: str) -> pd.DataFrame:
    if right.empty or on not in right.columns:
        return left
    return left.merge(right, on=on, how="left", suffixes=("", suffix))
