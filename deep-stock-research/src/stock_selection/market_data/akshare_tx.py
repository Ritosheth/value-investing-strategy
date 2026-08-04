"""Tencent daily-history adapter used by the A-share history fallback chain."""

from __future__ import annotations

from collections.abc import Mapping

import pandas as pd

from ..utils import market_for_code, normalize_code


def tx_symbol(code: str) -> str:
    """Return Tencent's market-prefixed symbol, e.g. ``sz000001``."""

    return f"{market_for_code(code)}{normalize_code(code)}"


def fetch_daily_prices_tx(
    code: str,
    *,
    start: str,
    end: str,
    adjust: str = "qfq",
    timeout: float | None = None,
) -> pd.DataFrame:
    """Fetch and normalize Tencent daily bars through AkShare."""

    try:
        import akshare as ak
    except ImportError as exc:
        raise RuntimeError("Missing dependency: akshare is required for Tencent history.") from exc

    frame = ak.stock_zh_a_hist_tx(
        symbol=tx_symbol(code),
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust=adjust,
        timeout=timeout,
    )
    if frame is None or frame.empty:
        return pd.DataFrame()

    columns: Mapping[str, str] = {
        "date": "trade_date",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "amount": "turnover",
    }
    missing = [source for source in ("date", "open", "high", "low", "close") if source not in frame.columns]
    if missing:
        raise ValueError(f"Unexpected Tencent history schema; missing columns: {', '.join(missing)}")

    out = pd.DataFrame({target: frame[source] for source, target in columns.items() if source in frame.columns})
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out["code"] = normalize_code(code)
    for name in ("open", "high", "low", "close", "turnover"):
        if name in out:
            out[name] = pd.to_numeric(out[name], errors="coerce")
    out["volume"] = pd.NA
    out["source"] = "akshare_stock_zh_a_hist_tx_tencent"
    out["adjust"] = adjust
    return out.dropna(subset=["trade_date", "close"]).drop_duplicates(
        ["code", "trade_date"]
    ).sort_values(["code", "trade_date"]).reset_index(drop=True)
