#!/usr/bin/env python3
"""Collect read-only Futu/OpenD and local-model evidence for deep research.

The script deliberately keeps the evidence layer separate from the scorer. It
never places orders and it never turns unavailable data into a neutral score.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import re
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
RUNTIME = WORKSPACE_ROOT / "stock_investment_system" / "env.sh"


def resolve_futu_scripts() -> Path:
    """Resolve the skill outside runtimes that intentionally override HOME."""
    explicit = os.environ.get("FUTU_SKILL_ROOT")
    if explicit:
        return Path(explicit).expanduser() / "quote"
    candidates = [
        Path.home() / ".codex" / "skills" / "futuapi" / "scripts" / "quote",
        Path(pwd.getpwuid(os.getuid()).pw_dir) / ".codex" / "skills" / "futuapi" / "scripts" / "quote",
    ]
    return next((path for path in candidates if path.is_dir()), candidates[-1])


FUTU_SCRIPTS = resolve_futu_scripts()
MODEL_MODULE = "stock_investment_system.run_models"


def normalize_ticker(value: str) -> dict[str, str]:
    original = value.strip()
    code = original.upper().replace("_", ".")
    code = re.sub(r"\s+", "", code)
    if re.fullmatch(r"\d{6}", code):
        suffix = "SH" if code.startswith("6") else "BJ" if code.startswith(("4", "8")) else "SZ"
        return {"input": original, "code": f"{code}.{suffix}", "market": "A", "futu_code": f"{suffix}.{code}"}
    if re.fullmatch(r"\d{1,5}\.HK", code):
        number = code.split(".")[0].zfill(5)
        return {"input": original, "code": f"{number}.HK", "market": "HK", "futu_code": f"HK.{number}"}
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
        number, suffix = code.split(".")
        return {"input": original, "code": code, "market": "A", "futu_code": f"{suffix}.{number}"}
    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}(?:\.US)?", code):
        symbol = code.removesuffix(".US")
        return {"input": original, "code": f"{symbol}.US", "market": "US", "futu_code": f"US.{symbol}"}
    raise ValueError(f"Unsupported ticker format: {original}")


def run_json(command: list[str], warnings: list[str], label: str) -> Any:
    try:
        completed = subprocess.run(command, cwd=str(WORKSPACE_ROOT), text=True, capture_output=True, timeout=180, check=False)
    except Exception as exc:
        warnings.append(f"{label} failed to start: {exc}")
        return {}
    stdout = completed.stdout.strip()
    if completed.returncode != 0:
        detail = (completed.stderr or stdout or "unknown error").strip().splitlines()[-1]
        warnings.append(f"{label} unavailable: {detail}")
        return {}
    if not stdout:
        return {}
    parsed = parse_json_output(stdout)
    if parsed is not None:
        return parsed
    else:
        warnings.append(f"{label} returned non-JSON output")
        return {}


def parse_json_output(stdout: str) -> Any:
    """Extract one JSON value even when SDK logs surround a multi-line body."""
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for index, character in enumerate(stdout):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(stdout[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)):
            return value
    return None


def futu_json(script_name: str, futu_code: str, warnings: list[str], *args: str) -> Any:
    script = FUTU_SCRIPTS / script_name
    if not script.exists():
        warnings.append(f"Futu script missing: {script}")
        return {}
    return run_json([str(RUNTIME), str(script), futu_code, *args, "--json"], warnings, script_name)


def direct_daily_flow(futu_code: str, warnings: list[str]) -> Any:
    """Use the SDK enum string because the bundled helper's integer flag is incompatible with OpenD 10.08."""
    try:
        from futu import OpenQuoteContext

        ctx = OpenQuoteContext(
            host=os.environ.get("FUTU_OPEND_HOST", "127.0.0.1"),
            port=int(os.environ.get("FUTU_OPEND_PORT", "11111")),
        )
        try:
            ret, data = ctx.get_capital_flow(futu_code, period_type="DAY")
            if ret != 0 or data is None:
                warnings.append(f"capital_flow unavailable: {data}")
                return {}
            records = data.to_dict(orient="records") if hasattr(data, "to_dict") else data
            return {"code": futu_code, "period_type": "DAY", "data": records}
        finally:
            ctx.close()
    except Exception as exc:
        warnings.append(f"capital_flow unavailable: {exc}")
        return {}


def model_evidence(warnings: list[str], codes: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not RUNTIME.exists():
        warnings.append(f"Project runtime missing: {RUNTIME}")
        return {}
    result = run_json(
        [
            str(RUNTIME), "-m", MODEL_MODULE, "--model", "all",
            "--max-watchlist", str(max(20, len(codes))), "--format", "json",
            "--refresh-quotes", "--codes", ",".join(codes),
            "--min-turnover", "0", "--min-float-market-cap", "0",
        ],
        warnings,
        "local model output",
    )
    by_code: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(result, list):
        return by_code
    for model in result:
        if not isinstance(model, dict):
            continue
        for row in model.get("watchlist", []) or []:
            if isinstance(row, dict) and row.get("code"):
                by_code.setdefault(str(row["code"]), []).append({"model_name": model.get("model_name"), **row})
    return by_code


def load_model_rows(path: Path, warnings: list[str]) -> dict[str, list[dict[str, Any]]]:
    """Reuse the watchlist rows that triggered research instead of rerunning models."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"model rows file unavailable: {exc}")
        return {}
    if not isinstance(data, dict):
        warnings.append("model rows file must contain an object keyed by six-digit code")
        return {}
    result: dict[str, list[dict[str, Any]]] = {}
    for key, rows in data.items():
        code = str(key).split(".")[0]
        if re.fullmatch(r"\d{6}", code) and isinstance(rows, list):
            result[code] = [row for row in rows if isinstance(row, dict)]
    return result


def numeric(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def average(values: list[float | None]) -> float | None:
    usable = [value for value in values if value is not None]
    return round(sum(usable) / len(usable), 2) if usable else None


def clamp(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def selected_model_rows(rows: list[dict[str, Any]], code: str) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("code")) == code.split(".")[0]]


def compact_model_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = [
        "model_name", "code", "name", "total_score", "fundamental_quality_score", "growth_quality_score",
        "valuation_score", "price_volume_score", "capital_flow_score", "flow_net_10d", "flow_net_20d",
        "event_score", "expectation_score", "catalyst_score", "industry", "research_posture",
        "entry_action", "selection_reason", "price_source",
    ]
    return [{key: row[key] for key in keep if key in row} for row in rows]


def derive_financial_quality_score(financials: Any, model_rows: list[dict[str, Any]]) -> tuple[float | None, str, dict[str, float]]:
    data = financials.get("data", financials) if isinstance(financials, dict) else {}
    reports = data.get("report_list", []) if isinstance(data, dict) else []
    reports = [report for report in reports if isinstance(report, dict)]
    if reports:
        latest = max(reports, key=lambda report: str(report.get("date_time_str") or report.get("period_text") or ""))
        metrics: dict[str, float] = {}
        for item in latest.get("item_list", []) if isinstance(latest.get("item_list"), list) else []:
            if not isinstance(item, dict):
                continue
            field_id = item.get("field_id")
            value = numeric(item, "data")
            yoy = numeric(item, "yoy")
            if value is None:
                continue
            if field_id in {3015, 3016}:
                metrics.setdefault("roe", value)
            elif field_id == 3019:
                metrics["net_margin"] = value
                if yoy is not None:
                    metrics["net_margin_yoy"] = yoy
            elif field_id == 3048:
                metrics["revenue_cagr_3y"] = value
            elif field_id == 3049:
                metrics["profit_cagr_3y"] = value
            elif field_id == 3054:
                metrics["profit_cash_content"] = value
                if yoy is not None:
                    metrics["profit_cash_content_yoy"] = yoy
            elif field_id == 3064:
                metrics["debt_to_assets"] = value

        parts: list[tuple[float, float]] = []
        if "roe" in metrics:
            parts.append((clamp(50 + (metrics["roe"] - 10) * 2), 0.25))
        if "net_margin" in metrics:
            parts.append((clamp(50 + (metrics["net_margin"] - 10) * 1.5 + metrics.get("net_margin_yoy", 0) * 0.25), 0.20))
        if "profit_cash_content" in metrics:
            parts.append((clamp(metrics["profit_cash_content"] * 0.5 + metrics.get("profit_cash_content_yoy", 0) * 0.20), 0.20))
        growth_values = [metrics[key] for key in ("revenue_cagr_3y", "profit_cagr_3y") if key in metrics]
        if growth_values:
            parts.append((clamp(50 + sum(growth_values) / len(growth_values) * 1.5), 0.25))
        if "debt_to_assets" in metrics:
            parts.append((clamp(100 - metrics["debt_to_assets"]), 0.10))
        if parts:
            weight = sum(item[1] for item in parts)
            score = sum(value * item_weight for value, item_weight in parts) / weight
            return clamp(score), "富途最新财务指标：ROE、净利率、现金含量、3年增长及资产负债率", metrics

    fallback = average([numeric(row, "fundamental_quality_score") for row in model_rows])
    return fallback, "本地模型基本面分（财务报表指标不足时降级）", {}


def derive_valuation_score(valuation: Any, model_rows: list[dict[str, Any]]) -> tuple[float | None, str]:
    data = valuation.get("data", valuation) if isinstance(valuation, dict) else {}
    trend = data.get("trend", {}) if isinstance(data, dict) else {}
    percentile = numeric(trend, "valuation_percentile") if isinstance(trend, dict) else None
    if percentile is not None and 0 <= percentile <= 100:
        return clamp(100 - percentile), "富途当前历史估值分位的反向得分"
    return average([numeric(row, "valuation_score") for row in model_rows]), "本地模型横截面估值分（历史分位不可用时降级）"


def profile_has_data(profile: Any) -> bool:
    if isinstance(profile, dict):
        data = profile.get("data")
        return isinstance(data, list) and bool(data)
    return False


def derive_governance_score(profile: Any, executives: Any, buybacks: Any, rows: list[dict[str, Any]]) -> tuple[float | None, str]:
    profile_ok = profile_has_data(profile)
    executives_ok = isinstance(executives, dict) and bool(executives.get("data"))
    buyback_data = buybacks.get("data") if isinstance(buybacks, dict) else None
    buyback_ok = bool(buyback_data)
    if not profile_ok and not executives_ok and not buyback_ok:
        return None, "公司概况、高管和回购数据均不可用"
    # This is a transparent evidence-availability triage score, not a claim
    # that the company's governance quality has been proven.
    # Cap this proxy below a clean bill of health: availability of records is
    # not the same thing as verified governance quality.
    governance_risk_hit = any(
        str(row.get("name") or "").upper().startswith(("*ST", "ST"))
        or "退" in str(row.get("name") or "")
        or any(keyword in str(row.get("risk_flags") or "").upper() for keyword in ("*ST", "ST", "退"))
        for row in rows
    )
    if governance_risk_hit:
        return 0.0, "名称或模型风险字段触发 ST/退市硬风险；需以交易所公告复核"
    score = 45.0 + (5.0 if profile_ok else 0.0) + (5.0 if executives_ok else 0.0) + (5.0 if buyback_ok else 0.0)
    return clamp(score), "仅为治理证据覆盖度筛查分（上限60），不代表治理质量已获证实；需结合公告人工复核"


def source_has_payload(value: Any) -> bool:
    if value in (None, "", [], {}):
        return False
    if isinstance(value, dict):
        if value.get("error") or value.get("isError"):
            return False
        if "data" in value:
            return source_has_payload(value.get("data"))
        return any(source_has_payload(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(source_has_payload(item) for item in value)
    return True


def collect_one(ticker: dict[str, str], model_rows: list[dict[str, Any]]) -> dict[str, Any]:
    warnings: list[str] = []
    futu_code = ticker["futu_code"]
    snapshot = futu_json("get_snapshot.py", futu_code, warnings)
    financials = futu_json("get_financials_statements.py", futu_code, warnings, "--statement-type", "4", "--financial-type", "10", "--num", "6")
    # DCF uses annual statements only. Keeping them separate from the mixed
    # quarterly/main-index response prevents accidental TTM/FY mixing and makes
    # every model input traceable to a dated report.
    annual_statements = {
        "income": futu_json("get_financials_statements.py", futu_code, warnings, "--statement-type", "1", "--financial-type", "7", "--num", "8"),
        "balance": futu_json("get_financials_statements.py", futu_code, warnings, "--statement-type", "2", "--financial-type", "7", "--num", "8"),
        "cashflow": futu_json("get_financials_statements.py", futu_code, warnings, "--statement-type", "3", "--financial-type", "7", "--num", "8"),
        "main_index": futu_json("get_financials_statements.py", futu_code, warnings, "--statement-type", "4", "--financial-type", "7", "--num", "8"),
    }
    valuation = futu_json("get_valuation_detail.py", futu_code, warnings, "--interval-type", "6")
    profile = futu_json("get_company_profile.py", futu_code, warnings)
    executives = futu_json("get_company_executives.py", futu_code, warnings)
    start = (date.today() - timedelta(days=150)).isoformat()
    kline = futu_json("get_kline.py", futu_code, warnings, "--ktype", "1d", "--start", start, "--end", date.today().isoformat(), "--max-page", "1", "--num", "60")
    rows = selected_model_rows(model_rows, ticker["code"])
    model_flow_rows = [
        {key: row[key] for key in ["code", "flow_net", "flow_net_5d", "flow_net_10d", "flow_net_20d", "large_order_net_20d", "flow_positive_ratio", "flow_positive_ratio_20d", "flow_days"] if key in row}
        for row in rows
        if any(key in row for key in ["flow_net", "flow_net_10d", "flow_net_20d"])
    ]
    # The local model already queries daily flow. Reusing it avoids a second
    # request inside the same job and respects OpenD's rate limits.
    flow = {"source": "local model output", "data": model_flow_rows} if model_flow_rows else direct_daily_flow(futu_code, warnings)
    buybacks = futu_json("get_corporate_actions_buybacks.py", futu_code, warnings, "--num", "20")

    model_score = average([numeric(row, "total_score") for row in rows])
    financial_score, financial_basis, financial_metrics = derive_financial_quality_score(financials, rows)
    valuation_score, valuation_basis = derive_valuation_score(valuation, rows)
    catalyst_score = average([numeric(row, "catalyst_score") for row in rows])
    technical_score = average([
        average([numeric(row, "price_volume_score") for row in rows]),
        average([numeric(row, "capital_flow_score") for row in rows]),
    ])
    governance_score, governance_basis = derive_governance_score(profile, executives, buybacks, rows)
    source_values = [snapshot, financials, *annual_statements.values(), valuation, profile, executives, kline, flow, buybacks, rows]
    successful_sources = sum(source_has_payload(value) for value in source_values)
    data_confidence = clamp(successful_sources / len(source_values) * 100.0)

    sources = [
        {"publisher": "Futu OpenD", "endpoint": endpoint, "retrieved_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        for endpoint, value in [
            ("market_snapshot", snapshot), ("financials_statements", financials),
            ("annual_income_statement", annual_statements["income"]),
            ("annual_balance_sheet", annual_statements["balance"]),
            ("annual_cashflow_statement", annual_statements["cashflow"]),
            ("annual_main_index", annual_statements["main_index"]),
            ("valuation_detail", valuation),
            ("company_profile", profile), ("company_executives", executives), ("daily_kline", kline),
            ("capital_flow", flow), ("buybacks", buybacks), ("local_model_output", rows),
        ] if source_has_payload(value)
    ]
    evidence: dict[str, Any] = {
        "model": {"score": model_score, "rows": compact_model_rows(rows)} if model_score is not None else None,
        "financial_quality": {
            "score": financial_score,
            "source": financial_basis,
            "derived_metrics": financial_metrics,
            "financials": financials,
            "annual_statements": annual_statements,
        } if financials or any(annual_statements.values()) else None,
        "valuation": {"score": valuation_score, "source": valuation_basis, "valuation_detail": valuation} if valuation else None,
        "catalyst": {"score": catalyst_score, "source": "local model catalyst_score", "rows": compact_model_rows(rows)} if catalyst_score is not None else None,
        "technical_flow": {"score": technical_score, "source": "local model price/flow scores", "kline": kline, "capital_flow": flow} if technical_score is not None else None,
        "governance_risk": {"score": governance_score, "basis": governance_basis, "profile": profile, "executives": executives, "buybacks": buybacks} if governance_score is not None else None,
        "data_confidence": {"score": data_confidence, "successful_sources": successful_sources, "source_count": len(source_values)},
        "snapshot": snapshot,
        "ticker": ticker,
        "sources": sources,
        "warnings": warnings,
        "evidence_tags": {"quantitative": "Observed/Derived", "governance_score": "Derived; manual verification required"},
    }
    return {key: value for key, value in evidence.items() if value is not None}


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect Futu/OpenD evidence for deep stock research")
    parser.add_argument("codes", nargs="+", help="A-share, Hong Kong, or US tickers")
    parser.add_argument("--output", type=Path, required=True, help="Evidence JSON output path")
    parser.add_argument("--model-rows-json", type=Path, help="Reuse the stock-system rows that triggered this research run")
    args = parser.parse_args()
    warnings: list[str] = []
    normalized = [normalize_ticker(code) for code in args.codes]
    model_map = load_model_rows(args.model_rows_json, warnings) if args.model_rows_json else model_evidence(
        warnings, [ticker["futu_code"] for ticker in normalized]
    )
    output: dict[str, Any] = {}
    for ticker in normalized:
        rows = model_map.get(ticker["code"].split(".")[0], [])
        output[ticker["code"]] = collect_one(ticker, rows)
    if warnings:
        for evidence in output.values():
            evidence.setdefault("warnings", []).extend(warnings)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "codes": list(output), "warnings": warnings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
