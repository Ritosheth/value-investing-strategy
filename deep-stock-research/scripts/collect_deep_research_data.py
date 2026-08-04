"""Collect reproducible stock-research data and build a deterministic brief.

The script is intentionally separate from the daily stock-selection pipeline.
It collects only explicitly requested stocks and stores raw evidence, derived
metrics, warnings, and a Chinese report brief for later LLM-assisted research.
"""

from __future__ import annotations

if __name__ == "__main__":
    from _stockselection_env import ensure_stockselection_venv

    ensure_stockselection_venv()

import argparse
import datetime as dt
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_selection.config import DEFAULT_DB_PATH
from stock_selection.futu_client import FutuClient, FutuUnavailable, payload_frame
from stock_selection.futu_runtime import prepare_futu_runtime
from stock_selection.market_data.a_share_history import fetch_a_share_history
from stock_selection.utils import normalize_code


ASHARE_ENDPOINTS: dict[str, tuple[str, str | None]] = {
    "stock": ("stock_list", None),
    "market_daily": ("market_daily", "20240101"),
    "fundamentals": ("fundamentals", "20240101"),
    "financial_indicators": ("financial_indicators", "20230101"),
    "income": ("income", "20230101"),
    "balance_sheet": ("balance_sheet", "20230101"),
    "cash_flow": ("cash_flow", "20230101"),
    "forecast": ("forecast", "20240101"),
    "express": ("express", "20240101"),
    "audit": ("audit", "20220101"),
    "main_business": ("main_business", "20230101"),
    "disclosure_date": ("disclosure_date", "20250101"),
    "analyst_reports": ("analyst_reports", "20250101"),
    "shareholders": ("shareholders", "20230101"),
    "holder_trade": ("holder_trade", "20230101"),
    "margin": ("margin", "20250101"),
    "block_trade": ("block_trade", "20250101"),
    "top_list": ("top_list", "20250101"),
    "top_inst": ("top_inst", "20250101"),
    "moneyflow": ("moneyflow", "20250101"),
    "northbound_holdings": ("northbound_holdings", "20240101"),
    "technical_factors": ("technical_factors", "20250101"),
    "chip_distribution": ("chip_distribution", "20250101"),
    "dividend": ("dividend", "20200101"),
}

ASHARE_CORE_ENDPOINTS = (
    "stock",
    "fundamentals",
    "financial_indicators",
    "income",
    "balance_sheet",
    "cash_flow",
    "disclosure_date",
    "shareholders",
)
ASHARE_DAILY_LIMIT = 100
ASHARE_DEFAULT_RESERVE = 20
ASHARE_DEFAULT_RUN_BUDGET = 80


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect Futu, AShareHub, and saved-model evidence for on-demand deep stock research."
    )
    parser.add_argument("codes", nargs="+", help="Stock codes, e.g. 603444, SH.603444, 00700, US.AAPL.")
    parser.add_argument("--as-of", default=dt.date.today().isoformat(), help="Research cutoff date (YYYY-MM-DD).")
    parser.add_argument("--history-start", default=None, help="Price/flow start date; defaults to 550 calendar days ago.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Date-level output root; each stock is saved below its stock-name directory.",
    )
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="Prediction-ledger SQLite path.")
    parser.add_argument("--futu-host", default="127.0.0.1")
    parser.add_argument("--futu-port", type=int, default=11111)
    parser.add_argument("--skip-futu", action="store_true")
    parser.add_argument("--skip-asharehub", action="store_true")
    parser.add_argument(
        "--asharehub-profile",
        choices=("core", "full"),
        default="core",
        help="Core uses 8 high-value endpoints so a 10-stock watchlist fits the reserved daily budget; full uses all 24.",
    )
    parser.add_argument(
        "--asharehub-budget",
        type=int,
        default=ASHARE_DEFAULT_RUN_BUDGET,
        help="Maximum AShareHub calls for this invocation. Default: 80.",
    )
    parser.add_argument(
        "--asharehub-daily-reserve",
        type=int,
        default=ASHARE_DEFAULT_RESERVE,
        help="Keep this many calls unused from the documented daily quota. Default: 20.",
    )
    parser.add_argument(
        "--refresh-asharehub",
        action="store_true",
        help="Ignore same-cutoff AShareHub cache and spend fresh API calls.",
    )
    parser.add_argument(
        "--language",
        choices=("zh-CN", "en"),
        default="zh-CN",
        help="Language for the deterministic report brief; Chinese is the default.",
    )
    return parser.parse_args()


def normalize_security(raw: str) -> tuple[str, str, bool]:
    value = str(raw).strip().upper()
    if value.startswith(("SH.", "SZ.")):
        digits = value.split(".", 1)[1]
        return digits, value, True
    if value.startswith("US.") or value.startswith("HK."):
        return value.split(".", 1)[1], value, False
    if len(value) == 5 and value.isdigit():
        return value, f"HK.{value}", False
    digits = normalize_code(value)
    if len(digits) == 6 and digits.isdigit():
        market = "SH" if digits.startswith(("5", "6", "9")) else "SZ"
        return digits, f"{market}.{digits}", True
    return value, f"US.{value}", False


def dataframe_records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    if frame is None or frame.empty:
        return []
    return json.loads(frame.to_json(orient="records", force_ascii=False, date_format="iso"))


def safe_call(name: str, call: Callable[[], Any], warnings: list[str]) -> Any:
    try:
        return call()
    except Exception as exc:  # Endpoint coverage varies by market and account.
        warnings.append(f"{name}: {type(exc).__name__}: {exc}")
        return None


def ashare_usage_path(today: dt.date | None = None) -> Path:
    usage_date = today or dt.date.today()
    return ROOT / ".runtime" / "asharehub_usage" / f"{usage_date:%Y%m%d}.json"


def load_ashare_usage(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return max(int(payload.get("attempted_calls", 0)), 0)
    except (FileNotFoundError, ValueError, TypeError, json.JSONDecodeError):
        return 0


def record_ashare_call(path: Path) -> int:
    used = load_ashare_usage(path) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "date": path.stem,
                "attempted_calls": used,
                "documented_daily_limit": ASHARE_DAILY_LIMIT,
                "updated_at": dt.datetime.now().astimezone().isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    temporary.replace(path)
    return used


def ashare_cache_path(symbol: str, as_of: str, profile: str) -> Path:
    safe_symbol = symbol.replace(".", "_")
    return ROOT / ".runtime" / "deep_research_cache" / as_of.replace("-", "") / f"{safe_symbol}_{profile}.json"


def collect_futu(
    futu_code: str,
    *,
    host: str,
    port: int,
    start: str,
    end: str,
) -> tuple[dict[str, Any], list[str]]:
    warnings: list[str] = []
    prepare_futu_runtime(ROOT)
    client: FutuClient | None = None
    output: dict[str, Any] = {}
    try:
        client = FutuClient(host=host, port=port, max_rate_limit_retries=1)
        snapshot = safe_call("futu.market_snapshot", lambda: client.market_snapshot([futu_code]), warnings)
        output["market_snapshot"] = dataframe_records(snapshot)
        output["capital_flow_summary"] = safe_call(
            "futu.capital_flow_summary",
            lambda: client.capital_flow_summary(futu_code, start=start, end=end),
            warnings,
        )
        output["capital_distribution"] = dataframe_records(
            payload_frame(
                safe_call(
                    "futu.get_capital_distribution",
                    lambda: client._call("get_capital_distribution", futu_code, required=False),
                    warnings,
                )
            )
        )
        output["valuation_detail"] = safe_call(
            "futu.valuation_detail", lambda: client.valuation_detail(futu_code), warnings
        )
        output["owner_plate"] = dataframe_records(
            safe_call("futu.owner_plate", lambda: client.owner_plate([futu_code]), warnings)
        )
        output["financial_statements"] = dataframe_records(
            payload_frame(
                safe_call(
                    "futu.get_financials_statements",
                    lambda: client._call("get_financials_statements", futu_code, num=10, required=False),
                    warnings,
                )
            )
        )
        output["revenue_breakdown"] = dataframe_records(
            payload_frame(
                safe_call(
                    "futu.get_financials_revenue_breakdown",
                    lambda: client._call("get_financials_revenue_breakdown", futu_code, required=False),
                    warnings,
                )
            )
        )
        output["company_profile"] = dataframe_records(
            payload_frame(
                safe_call(
                    "futu.get_company_profile",
                    lambda: client._call("get_company_profile", futu_code, required=False),
                    warnings,
                )
            )
        )
        output["corporate_actions"] = safe_call(
            "futu.corporate_action_flags", lambda: client.corporate_action_flags(futu_code), warnings
        )
        output["event_context"] = safe_call(
            "futu.event_context", lambda: client.event_context(futu_code), warnings
        )
        warnings.extend(client.warnings)
    except FutuUnavailable as exc:
        warnings.append(f"futu.connection: {exc}")
    except Exception as exc:
        warnings.append(f"futu.connection: {type(exc).__name__}: {exc}")
    finally:
        if client is not None:
            client.close()
    return output, list(dict.fromkeys(warnings))


def collect_asharehub(
    symbol: str,
    *,
    as_of: str,
    profile: str,
    max_calls: int,
    usage_path: Path,
    refresh: bool = False,
) -> tuple[dict[str, Any], list[str], int]:
    warnings: list[str] = []
    cache_path = ashare_cache_path(symbol, as_of, profile)
    if not refresh and cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            return cached["data"], ["asharehub: reused same-cutoff cache; API calls=0"], 0
        except (KeyError, TypeError, json.JSONDecodeError):
            warnings.append("asharehub: ignored invalid local cache")
    key = os.environ.get("ASHAREHUB_API_KEY")
    if not key:
        machine_key = None
        if os.name == "nt":
            try:
                import winreg

                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as reg:
                    machine_key, _ = winreg.QueryValueEx(reg, "ASHAREHUB_API_KEY")
            except OSError:
                machine_key = None
        key = machine_key
    if not key:
        return {}, ["asharehub: ASHAREHUB_API_KEY is unavailable"], 0

    from asharehub import AShareHub

    client = AShareHub(api_key=key)
    output: dict[str, Any] = {}
    cutoff = as_of.replace("-", "")
    endpoint_names = ASHARE_CORE_ENDPOINTS if profile == "core" else tuple(ASHARE_ENDPOINTS)
    endpoint_items = [(name, ASHARE_ENDPOINTS[name]) for name in endpoint_names]
    calls = 0
    for index, (label, (method_name, start_date)) in enumerate(endpoint_items):
        if calls >= max_calls:
            for remaining_label, _ in endpoint_items[index:]:
                output[remaining_label] = []
            warnings.append(f"asharehub: invocation/daily budget reached after {calls} calls; remaining endpoints skipped")
            break
        status_code = None
        method = getattr(client, method_name)
        kwargs: dict[str, Any] = {"symbol": symbol}
        if start_date:
            kwargs["start_date"] = start_date
        try:
            record_ashare_call(usage_path)
            calls += 1
            frame = method(**kwargs)
        except Exception as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            warnings.append(f"asharehub.{method_name}: {type(exc).__name__}: {exc}")
            frame = None
            if status_code == 429:
                for remaining_label, _ in endpoint_items[index + 1 :]:
                    output[remaining_label] = []
                warnings.append("asharehub: quota exhausted; remaining endpoints were not called")
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            for date_field in ("ann_date", "f_ann_date", "trade_date"):
                if date_field in frame.columns:
                    values = frame[date_field].astype(str).str.replace("-", "", regex=False).str.slice(0, 8)
                    frame = frame.loc[values <= cutoff].copy()
                    break
        if frame is None:
            output[label] = []
            if not any(item.startswith(f"asharehub.{method_name}:") for item in warnings):
                warnings.append(f"asharehub.{method_name}: returned no data object")
        else:
            output[label] = dataframe_records(frame) if isinstance(frame, pd.DataFrame) else frame
        if status_code == 429:
            break
    cacheable = len(output) == len(endpoint_items) and not any(
        item.startswith("asharehub.") or "quota" in item or "budget" in item for item in warnings
    )
    if cacheable:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        write_json(
            cache_path,
            {
                "symbol": symbol,
                "as_of": as_of,
                "profile": profile,
                "retrieved_at": dt.datetime.now().astimezone().isoformat(),
                "data": output,
            },
        )
    return output, warnings, calls


def _model_json_candidates(as_of: str) -> list[Path]:
    cutoff = as_of.replace("-", "")[:8]
    output_root = ROOT.parent / "outputs" / "deep_research"
    if not output_root.is_dir():
        return []
    date_dirs = sorted(
        (
            path
            for path in output_root.iterdir()
            if path.is_dir() and re.fullmatch(r"\d{8}", path.name) and path.name <= cutoff
        ),
        key=lambda path: path.name,
        reverse=True,
    )
    candidates: list[Path] = []
    for date_dir in date_dirs:
        dated: list[tuple[bool, float, Path]] = []
        for path in date_dir.glob("model_rows_*.json"):
            production_name = bool(
                re.fullmatch(rf"model_rows_{date_dir.name}_\d{{6}}\.json", path.name)
            )
            try:
                modified = path.stat().st_mtime
            except OSError:
                modified = 0.0
            dated.append((production_name, modified, path))
        candidates.extend(
            item[2]
            for item in sorted(dated, key=lambda item: (item[0], item[1], item[2].name), reverse=True)
        )
    return candidates


def _load_model_json(code: str, *, as_of: str) -> tuple[list[dict[str, Any]], Path | None]:
    normalized_code = normalize_code(code)
    for path in _model_json_candidates(as_of):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        source_rows = payload.get(normalized_code)
        if not isinstance(source_rows, list) or not source_rows:
            continue

        output: list[dict[str, Any]] = []
        for source_row in source_rows:
            if not isinstance(source_row, dict):
                continue
            row = dict(source_row)
            model_name = str(row.get("model_name") or row.get("model_slug") or "").strip()
            peers = [
                peer
                for values in payload.values()
                if isinstance(values, list)
                for peer in values
                if isinstance(peer, dict)
                and str(peer.get("model_name") or peer.get("model_slug") or "").strip() == model_name
            ]
            peers.sort(
                key=lambda peer: float(peer.get("total_score") or float("-inf")),
                reverse=True,
            )
            rank = next(
                (
                    index
                    for index, peer in enumerate(peers, 1)
                    if normalize_code(peer.get("code")) == normalized_code
                ),
                None,
            )
            row.setdefault("model_slug", model_name)
            row.setdefault("prediction_date", f"{path.parent.name[:4]}-{path.parent.name[4:6]}-{path.parent.name[6:8]}")
            row["rank"] = rank
            row["universe_size"] = len(peers)
            row["model_source_file"] = str(path)
            output.append(row)
        if output:
            return output, path
    return [], None


def load_model_evidence(code: str, db_path: Path, *, as_of: str) -> tuple[list[dict[str, Any]], list[str]]:
    sqlite_error: str | None = None
    if not db_path.exists():
        sqlite_error = f"database not found: {db_path}"
    else:
        query = """
            SELECT prediction_date, model_slug, name, rank, bucket, total_score,
                   rotation_state, research_posture, selection_reason, risk_flags,
                   feature_json, run_id
            FROM model_predictions
            WHERE code = ? AND prediction_date <= ?
            ORDER BY prediction_date DESC, model_slug, rank
        """
        try:
            with sqlite3.connect(db_path) as conn:
                frame = pd.read_sql_query(query, conn, params=(code, as_of))
            rows = dataframe_records(frame)
            if rows:
                return rows, []
            sqlite_error = f"no SQLite rows for {code} through {as_of}"
        except Exception as exc:
            sqlite_error = f"{type(exc).__name__}: {exc}"

    fallback_rows, fallback_path = _load_model_json(code, as_of=as_of)
    if fallback_rows:
        return fallback_rows, []
    return [], [
        f"model_ledger: {sqlite_error}; no model_rows JSON fallback found for {code} through {as_of}"
    ]


def price_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    frame = pd.DataFrame(rows)
    close_column = "close" if "close" in frame else "close_qfq" if "close_qfq" in frame else None
    date_column = "trade_date" if "trade_date" in frame else "time_key" if "time_key" in frame else None
    if close_column is None:
        return {}
    frame[close_column] = pd.to_numeric(frame[close_column], errors="coerce")
    frame = frame.dropna(subset=[close_column])
    if date_column:
        frame = frame.sort_values(date_column)
    if frame.empty:
        return {}
    close = frame[close_column].astype(float)
    result: dict[str, Any] = {"close": float(close.iloc[-1])}
    if date_column:
        result["date"] = str(frame.iloc[-1][date_column])[:10]
    for days in (5, 10, 20, 60):
        if len(close) > days:
            result[f"return_{days}d_pct"] = (float(close.iloc[-1] / close.iloc[-days - 1]) - 1.0) * 100.0
    returns = close.pct_change()
    for days in (20, 60):
        sample = returns.tail(days).dropna()
        if len(sample) >= max(5, days // 2):
            result[f"annualized_vol_{days}d_pct"] = float(sample.std(ddof=1) * math.sqrt(252) * 100.0)
    tail = close.tail(60)
    if len(tail) >= 2:
        drawdown = tail / tail.cummax() - 1.0
        result["max_drawdown_60d_pct"] = float(drawdown.min() * 100.0)
    return result


def latest_row(rows: list[dict[str, Any]], date_field: str) -> dict[str, Any]:
    valid = [row for row in rows if row.get(date_field) is not None]
    return max(valid, key=lambda row: str(row[date_field])) if valid else {}


def resolve_stock_name(
    code: str,
    futu_data: dict[str, Any],
    ashare_data: dict[str, Any],
    model_rows: list[dict[str, Any]],
) -> str:
    candidates = [
        ((futu_data.get("market_snapshot") or [{}])[0]).get("name"),
        ((ashare_data.get("stock") or [{}])[0]).get("name"),
        ((ashare_data.get("stock") or [{}])[0]).get("stock_name"),
        (model_rows[0] if model_rows else {}).get("name"),
    ]
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value and value.lower() not in {"nan", "none"}:
            return value
    return code


def safe_directory_name(value: str, fallback: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip().rstrip(".")
    return cleaned or fallback


def positive_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def percentile(values: list[float], quantile: float) -> float:
    return float(pd.Series(values, dtype=float).quantile(quantile, interpolation="linear"))


def derive_relative_valuation(
    raw: dict[str, Any],
    *,
    price: dict[str, Any],
    fundamentals: dict[str, Any],
    indicators: dict[str, Any],
    futu_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Estimate a traceable value range from historical PE/PB percentiles.

    The method deliberately makes no forward earnings forecast. It converts
    same-date valuation multiples back into per-share fundamentals, then
    applies the available historical 25th/50th/75th percentile multiples.
    """
    ashare = raw.get("asharehub", {})
    rows = ashare.get("fundamentals", []) or []
    current_price = positive_number(price.get("close")) or positive_number(fundamentals.get("close"))
    if current_price is None:
        current_price = positive_number(futu_snapshot.get("latest_price"))
    if current_price is None:
        return {"available": False, "reason": "缺少同一截止日的当前股价"}

    methods: dict[str, dict[str, Any]] = {}
    current_pe = positive_number(fundamentals.get("pe_ttm")) or positive_number(futu_snapshot.get("pe_dynamic"))
    pe_values = [
        number
        for row in rows
        if (number := positive_number(row.get("pe_ttm"))) is not None and 2.0 <= number <= 120.0
    ]
    if current_pe is not None and len(pe_values) >= 60:
        eps_ttm = current_price / current_pe
        pe_multiples = {
            "bear": percentile(pe_values, 0.25),
            "base": percentile(pe_values, 0.50),
            "bull": percentile(pe_values, 0.75),
        }
        methods["pe"] = {
            "label": "PE TTM 历史分位",
            "basis_label": "隐含 TTM EPS",
            "basis_value": eps_ttm,
            "basis_derivation": "当前股价 ÷ 同日 PE TTM",
            "current_multiple": current_pe,
            "sample_size": len(pe_values),
            "multiples": pe_multiples,
            "values": {key: eps_ttm * multiple for key, multiple in pe_multiples.items()},
        }

    current_pb = positive_number(fundamentals.get("pb")) or positive_number(futu_snapshot.get("pb"))
    pb_values = [
        number
        for row in rows
        if (number := positive_number(row.get("pb"))) is not None and 0.1 <= number <= 20.0
    ]
    bps = positive_number(indicators.get("bps"))
    if current_pb is not None:
        implied_bps = current_price / current_pb
        if bps is None or abs(bps - implied_bps) / implied_bps > 0.25:
            bps = implied_bps
            bps_derivation = "当前股价 ÷ 同日 PB"
        else:
            bps_derivation = f"最新财务指标 BPS（报告期 {indicators.get('end_date', '不可用')}）"
    else:
        bps_derivation = "不可用"
    if current_pb is not None and bps is not None and len(pb_values) >= 60:
        pb_multiples = {
            "bear": percentile(pb_values, 0.25),
            "base": percentile(pb_values, 0.50),
            "bull": percentile(pb_values, 0.75),
        }
        methods["pb"] = {
            "label": "PB 历史分位",
            "basis_label": "每股净资产",
            "basis_value": bps,
            "basis_derivation": bps_derivation,
            "current_multiple": current_pb,
            "sample_size": len(pb_values),
            "multiples": pb_multiples,
            "values": {key: bps * multiple for key, multiple in pb_multiples.items()},
        }

    if not methods:
        return {
            "available": False,
            "reason": "有效 PE/PB、每股财务基准或历史估值样本不足（至少需要 60 个交易日）",
        }

    weight = 1.0 / len(methods)
    for method in methods.values():
        method["weight"] = weight
    scenarios: dict[str, dict[str, Any]] = {}
    for key, label in (("bear", "保守"), ("base", "基准"), ("bull", "乐观")):
        value = sum(method["values"][key] * method["weight"] for method in methods.values())
        scenarios[key] = {
            "label": label,
            "value": round(value, 2),
            "upside_pct": round((value / current_price - 1.0) * 100.0, 1),
        }

    trade_dates = sorted(str(row.get("trade_date")) for row in rows if row.get("trade_date"))
    metadata = raw.get("metadata", {})
    return {
        "available": True,
        "label": "分析估算价值",
        "method": "PE TTM 与 PB 历史分位等权综合" if len(methods) == 2 else next(iter(methods.values()))["label"],
        "valuation_date": metadata.get("as_of") or price.get("date"),
        "currency": "CNY" if str(metadata.get("futu_code", "")).startswith(("SH.", "SZ.")) else "",
        "current_price": round(current_price, 2),
        "reasonable_value_range": [scenarios["bear"]["value"], scenarios["bull"]["value"]],
        "scenarios": scenarios,
        "methods": methods,
        "sample_period": {
            "start": trade_dates[0] if trade_dates else None,
            "end": trade_dates[-1] if trade_dates else None,
        },
        "confidence": "中等" if len(methods) == 2 else "偏低",
        "warnings": [
            "这是基于历史估值分位的分析估算价值，不是券商一致目标价或收益承诺。",
            "未加入未来盈利增长预测；周期股还需结合商品价格与利润周期判断。",
        ],
    }


def derive(raw: dict[str, Any]) -> dict[str, Any]:
    futu = raw.get("futu", {})
    ashare = raw.get("asharehub", {})
    prices = raw.get("market_history", []) or ashare.get("technical_factors") or ashare.get("market_daily") or []
    model_rows = raw.get("model_evidence", [])
    price = price_metrics(prices)
    futu_snapshot = (futu.get("market_snapshot") or [{}])[0]
    fundamentals = latest_row(ashare.get("fundamentals", []), "trade_date")
    indicators = latest_row(ashare.get("financial_indicators", []), "end_date")
    result = {
        "price": price,
        "latest_futu_snapshot": futu_snapshot,
        "futu_flow": futu.get("capital_flow_summary") or {},
        "futu_valuation": futu.get("valuation_detail") or {},
        "latest_ashare_fundamentals": fundamentals,
        "latest_financial_indicators": indicators,
        "latest_disclosure_plan": latest_row(ashare.get("disclosure_date", []), "end_date"),
        "latest_margin": latest_row(ashare.get("margin", []), "trade_date"),
        "latest_chips": latest_row(ashare.get("chip_distribution", []), "trade_date"),
        "latest_model_selection": model_rows[0] if model_rows else {},
        "model_selection_count": len(model_rows),
    }
    result["fair_value"] = derive_relative_valuation(
        raw,
        price=price,
        fundamentals=fundamentals,
        indicators=indicators,
        futu_snapshot=futu_snapshot,
    )
    return result


def fmt(value: Any, digits: int = 2) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "不可用"
    if not math.isfinite(number):
        return "不可用"
    return f"{number:,.{digits}f}"


def render_brief(code: str, futu_code: str, as_of: str, derived: dict[str, Any], warnings: list[str], language: str) -> str:
    price = derived.get("price", {})
    fundamentals = derived.get("latest_ashare_fundamentals", {})
    futu_snapshot = derived.get("latest_futu_snapshot", {})
    pe_ttm = fundamentals.get("pe_ttm", futu_snapshot.get("pe_dynamic"))
    pb = fundamentals.get("pb", futu_snapshot.get("pb"))
    indicator = derived.get("latest_financial_indicators", {})
    model = derived.get("latest_model_selection", {})
    plan = derived.get("latest_disclosure_plan", {})
    model_name = model.get("model_slug") or model.get("model_name") or "不可用"
    rank = model.get("rank")
    universe_size = model.get("universe_size")
    rank_text = f"{rank}/{universe_size}" if rank is not None and universe_size else rank or "不可用"
    fair_value = derived.get("fair_value", {})
    if language == "en":
        fair_value_text = (
            f"- Analysis-estimated value (bear/base/bull): {fmt(fair_value['scenarios']['bear']['value'])} / "
            f"{fmt(fair_value['scenarios']['base']['value'])} / {fmt(fair_value['scenarios']['bull']['value'])}\n"
            if fair_value.get("available")
            else f"- Analysis-estimated value: Unavailable ({fair_value.get('reason', 'insufficient inputs')})\n"
        )
        return (
            f"# Deep-research data brief: {futu_code}\n\n"
            f"Data cutoff: {as_of}\n\n"
            f"- Close: {fmt(price.get('close'))}\n"
            f"- 20-day return: {fmt(price.get('return_20d_pct'))}%\n"
            f"- PE TTM / PB: {fmt(pe_ttm)} / {fmt(pb)}\n"
            + fair_value_text
            +
            f"- Latest model: {model_name}, rank {rank_text}, score {model.get('total_score', 'unavailable')}\n"
            f"- Next planned disclosure: {plan.get('pre_date', 'unavailable')}\n\n"
            "## Collection warnings\n\n" + "\n".join(f"- {item}" for item in warnings or ["None"])
        )
    if fair_value.get("available"):
        scenarios = fair_value["scenarios"]
        methods = fair_value.get("methods", {})
        method_lines = []
        for method in methods.values():
            method_lines.append(
                f"- {method['label']}：{method['basis_label']} {fmt(method['basis_value'], 4)} × "
                f"历史25/50/75分位倍数 {fmt(method['multiples']['bear'])} / {fmt(method['multiples']['base'])} / {fmt(method['multiples']['bull'])}，"
                f"样本 {method['sample_size']} 个交易日，权重 {method['weight'] * 100:.0f}%"
            )
        fair_value_text = (
            "## 分析估算价值（历史相对估值）\n\n"
            f"- 估值日期：{fair_value.get('valuation_date', as_of)}\n"
            f"- 当前股价：{fmt(fair_value.get('current_price'))} 元\n"
            f"- 合理价值区间：{fmt(fair_value['reasonable_value_range'][0])}–{fmt(fair_value['reasonable_value_range'][1])} 元\n"
            f"- 保守情景：{fmt(scenarios['bear']['value'])} 元，较现价 {scenarios['bear']['upside_pct']:+.1f}%\n"
            f"- 基准情景：{fmt(scenarios['base']['value'])} 元，较现价 {scenarios['base']['upside_pct']:+.1f}%\n"
            f"- 乐观情景：{fmt(scenarios['bull']['value'])} 元，较现价 {scenarios['bull']['upside_pct']:+.1f}%\n"
            f"- 方法：{fair_value['method']}；置信度：{fair_value['confidence']}\n"
            + "\n".join(method_lines)
            + "\n- 说明：这是历史估值分位推导的分析估算价值，未加入未来盈利增长，不等同于保证目标价。\n\n"
        )
    else:
        fair_value_text = (
            "## 分析估算价值（历史相对估值）\n\n"
            f"- Unavailable：{fair_value.get('reason', '缺少估值输入')}\n\n"
        )
    return (
        f"# 深度研究数据简报：{futu_code}\n\n"
        f"**数据截止：** {as_of}  \n"
        "**说明：** 本文件由脚本确定性生成，是后续深度研究的证据底稿，不等同于投资结论。\n\n"
        "## 行情与估值\n\n"
        f"- 收盘价：{fmt(price.get('close'))}\n"
        f"- 5/20/60日收益：{fmt(price.get('return_5d_pct'))}% / {fmt(price.get('return_20d_pct'))}% / {fmt(price.get('return_60d_pct'))}%\n"
        f"- 20日年化波动率：{fmt(price.get('annualized_vol_20d_pct'))}%\n"
        f"- 近60日最大回撤：{fmt(price.get('max_drawdown_60d_pct'))}%\n"
        f"- PE TTM / PB / 股息率TTM：{fmt(pe_ttm)} / {fmt(pb)} / {fmt(fundamentals.get('dv_ttm'))}%\n\n"
        + fair_value_text
        +
        "## 最新财务指标\n\n"
        f"- 报告期：{indicator.get('end_date', '不可用')}\n"
        f"- EPS：{fmt(indicator.get('eps'))}\n"
        f"- ROE：{fmt(indicator.get('roe'))}%\n"
        f"- 毛利率：{fmt(indicator.get('grossprofit_margin'))}%\n"
        f"- 归母净利润同比：{fmt(indicator.get('netprofit_yoy'))}%\n"
        f"- 资产负债率：{fmt(indicator.get('debt_to_assets'))}%\n\n"
        "## 模型与催化剂\n\n"
        f"- 最近模型：{model_name}，排名 {rank_text}，得分 {model.get('total_score', '不可用')}\n"
        f"- 研究姿态：{model.get('research_posture', '不可用')}\n"
        f"- 建议动作：{model.get('entry_action', '不可用')}\n"
        f"- 选股理由：{model.get('selection_reason', '不可用')}\n"
        f"- 预约披露日期：{plan.get('pre_date', '不可用')}\n\n"
        "## 数据警告\n\n" + "\n".join(f"- {item}" for item in warnings or ["无"])
    )


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> int:
    args = parse_args()
    as_of_date = dt.date.fromisoformat(args.as_of)
    start = args.history_start or (as_of_date - dt.timedelta(days=550)).isoformat()
    output_root = Path(args.output_dir) if args.output_dir else ROOT / "outputs" / "deep_research" / as_of_date.strftime("%Y%m%d")
    output_root.mkdir(parents=True, exist_ok=True)
    usage_file = ashare_usage_path()
    used_before = load_ashare_usage(usage_file)
    reserve = min(max(args.asharehub_daily_reserve, 0), ASHARE_DAILY_LIMIT)
    daily_available = max(ASHARE_DAILY_LIMIT - reserve - used_before, 0)
    run_remaining = min(max(args.asharehub_budget, 0), daily_available)
    failures = 0

    for raw_code in args.codes:
        code, futu_code, is_a_share = normalize_security(raw_code)
        warnings: list[str] = []
        futu_data: dict[str, Any] = {}
        ashare_data: dict[str, Any] = {}
        market_history: list[dict[str, Any]] = []
        ashare_calls = 0
        # Collect HTTP data before starting Futu's socket/background-thread
        # runtime. Some Windows SDK builds are unstable when lengthy HTTP work
        # follows quote-context shutdown in the same process.
        if is_a_share and not args.skip_asharehub:
            exchange = futu_code.split(".", 1)[0]
            ashare_data, ashare_warnings, ashare_calls = collect_asharehub(
                f"{code}.{exchange}",
                as_of=args.as_of,
                profile=args.asharehub_profile,
                max_calls=run_remaining,
                usage_path=usage_file,
                refresh=args.refresh_asharehub,
            )
            run_remaining = max(run_remaining - ashare_calls, 0)
            warnings.extend(ashare_warnings)
        elif not is_a_share and not args.skip_asharehub:
            warnings.append("asharehub: skipped because the security is not an A-share")
        if is_a_share:
            history_frame, history_warnings = fetch_a_share_history(
                code,
                start=start,
                end=args.as_of,
                adjust="qfq",
            )
            market_history = dataframe_records(history_frame)
            warnings.extend(history_warnings)
            if not market_history:
                warnings.append("market_history: all non-Futu A-share history sources failed")
        else:
            warnings.append("market_history: non-Futu history fallback is currently available for A-shares only")
        if not args.skip_futu:
            futu_data, futu_warnings = collect_futu(
                futu_code,
                host=args.futu_host,
                port=args.futu_port,
                start=start,
                end=args.as_of,
            )
            warnings.extend(futu_warnings)
        model_rows, model_warnings = load_model_evidence(code, Path(args.db_path), as_of=args.as_of)
        warnings.extend(model_warnings)
        stock_name = resolve_stock_name(code, futu_data, ashare_data, model_rows)
        stock_dir = output_root / safe_directory_name(stock_name, code)
        stock_dir.mkdir(parents=True, exist_ok=True)

        raw = {
            "metadata": {
                "code": code,
                "futu_code": futu_code,
                "as_of": args.as_of,
                "history_start": start,
                "retrieved_at": dt.datetime.now().astimezone().isoformat(),
                "language": args.language,
                "stock_name": stock_name,
                "asharehub_profile": args.asharehub_profile,
                "asharehub_calls_this_stock": ashare_calls,
                "asharehub_usage_ledger": str(usage_file),
            },
            "futu": futu_data,
            "market_history": market_history,
            "asharehub": ashare_data,
            "model_evidence": model_rows,
            "warnings": list(dict.fromkeys(warnings)),
        }
        derived = derive(raw)
        raw_path = stock_dir / "research_raw.json"
        derived_path = stock_dir / "research_derived.json"
        brief_path = stock_dir / "research_brief.md"
        write_json(raw_path, raw)
        write_json(derived_path, derived)
        brief_path.write_text(
            render_brief(code, futu_code, args.as_of, derived, raw["warnings"], args.language),
            encoding="utf-8",
        )
        print(f"[{futu_code}] raw={raw_path}")
        print(f"[{futu_code}] derived={derived_path}")
        print(f"[{futu_code}] brief={brief_path}")
        if not futu_data and not ashare_data:
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
