from __future__ import annotations

from collections.abc import Callable, Iterable

import pandas as pd

from ..utils import normalize_code
from .akshare_tx import fetch_daily_prices_tx, tx_symbol


HistoryFetcher = Callable[..., pd.DataFrame]
DEFAULT_HISTORY_SOURCES = ("tencent", "sina", "eastmoney")


def fetch_a_share_history(
    code: str,
    *,
    start: str,
    end: str,
    adjust: str = "qfq",
    timeout: float | None = None,
    sources: Iterable[str] = DEFAULT_HISTORY_SOURCES,
) -> tuple[pd.DataFrame, list[str]]:
    """Fetch A-share daily bars without consuming Futu history-kline quota.

    Providers are attempted in order. Exceptions and empty responses are retained
    as warnings so research output never silently changes data source.
    """
    warnings: list[str] = []
    for source in sources:
        try:
            frame = _fetch_source(
                source,
                code=code,
                start=start,
                end=end,
                adjust=adjust,
                timeout=timeout,
            )
        except Exception as exc:
            warnings.append(f"history.{source}: {type(exc).__name__}: {exc}")
            continue
        if frame.empty:
            warnings.append(f"history.{source}: returned no rows")
            continue
        return frame, warnings
    return pd.DataFrame(), warnings


def _fetch_source(
    source: str,
    *,
    code: str,
    start: str,
    end: str,
    adjust: str,
    timeout: float | None,
) -> pd.DataFrame:
    source_name = source.strip().lower()
    if source_name == "tencent":
        return fetch_daily_prices_tx(code, start=start, end=end, adjust=adjust, timeout=timeout)

    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("Missing dependency: akshare is required for A-share history.") from exc

    if source_name == "eastmoney":
        frame = ak.stock_zh_a_hist(
            symbol=normalize_code(code),
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=adjust,
            timeout=timeout,
        )
        return normalize_eastmoney_history(frame, code=code, adjust=adjust)
    if source_name == "sina":
        frame = ak.stock_zh_a_daily(
            symbol=tx_symbol(code),
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust=adjust,
        )
        return normalize_sina_history(frame, code=code, adjust=adjust)
    raise ValueError(f"Unsupported A-share history source: {source}")


def normalize_eastmoney_history(frame: pd.DataFrame, *, code: str, adjust: str) -> pd.DataFrame:
    return _normalize_history(
        frame,
        code=code,
        adjust=adjust,
        source="akshare_stock_zh_a_hist_eastmoney",
        columns={
            "trade_date": "日期",
            "open": "开盘",
            "high": "最高",
            "low": "最低",
            "close": "收盘",
            "volume": "成交量",
            "turnover": "成交额",
        },
    )


def normalize_sina_history(frame: pd.DataFrame, *, code: str, adjust: str) -> pd.DataFrame:
    return _normalize_history(
        frame,
        code=code,
        adjust=adjust,
        source="akshare_stock_zh_a_daily_sina",
        columns={
            "trade_date": "date",
            "open": "open",
            "high": "high",
            "low": "low",
            "close": "close",
            "volume": "volume",
            "turnover": "amount",
        },
    )


def _normalize_history(
    frame: pd.DataFrame,
    *,
    code: str,
    adjust: str,
    source: str,
    columns: dict[str, str],
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame()
    required = ("trade_date", "open", "high", "low", "close")
    missing = [columns[name] for name in required if columns[name] not in frame.columns]
    if missing:
        raise ValueError(f"Unexpected {source} schema; missing columns: {', '.join(missing)}")

    out = pd.DataFrame({"trade_date": pd.to_datetime(frame[columns["trade_date"]], errors="coerce")})
    out["code"] = normalize_code(code)
    for name in ("open", "high", "low", "close", "volume", "turnover"):
        provider_column = columns[name]
        out[name] = pd.to_numeric(frame[provider_column], errors="coerce") if provider_column in frame else pd.NA
    out["source"] = source
    out["adjust"] = adjust
    out = out.dropna(subset=["trade_date", "close"])
    out["trade_date"] = out["trade_date"].dt.date.astype(str)
    return out.drop_duplicates(["code", "trade_date"]).sort_values(["code", "trade_date"]).reset_index(drop=True)
