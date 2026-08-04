#!/usr/bin/env python3
"""Deterministic, auditable FCFF DCF valuation from saved Futu evidence.

The engine intentionally fails closed. It does not manufacture a DCF when the
issuer is a financial institution, annual FCFF history is insufficient, or the
capital-structure bridge cannot be reproduced from the evidence bundle.
"""

from __future__ import annotations

from statistics import median
from typing import Any


FINANCIAL_INDUSTRY_KEYWORDS = (
    "银行",
    "保险",
    "证券",
    "券商",
    "多元金融",
    "信托",
    "金融服务",
    "bank",
    "insurance",
    "broker",
)

MARKET_ASSUMPTIONS = {
    "A": {"risk_free_rate": 0.025, "equity_risk_premium": 0.060, "terminal_growth": 0.025, "currency": "CNY"},
    "HK": {"risk_free_rate": 0.030, "equity_risk_premium": 0.060, "terminal_growth": 0.025, "currency": "HKD"},
    "US": {"risk_free_rate": 0.040, "equity_risk_premium": 0.055, "terminal_growth": 0.025, "currency": "USD"},
}


def derive_dcf_valuation(evidence: dict[str, Any], horizon: str = "MEDIUM") -> dict[str, Any]:
    """Return a three-scenario per-share FCFF DCF or an explicit gap reason."""
    forecast_years = {"SHORT": 3, "MEDIUM": 5, "LONG": 7}.get(str(horizon).upper(), 5)
    market = _market(evidence)
    defaults = MARKET_ASSUMPTIONS.get(market, MARKET_ASSUMPTIONS["A"])
    current_price = _current_price(evidence)
    if current_price is None or current_price <= 0:
        return _unavailable("缺少有效当前股价，无法计算 WACC 权重和目标价")

    industry = _industry_text(evidence)
    if any(keyword.lower() in industry.lower() for keyword in FINANCIAL_INDUSTRY_KEYWORDS):
        return _unavailable("金融行业不适用普通 FCFF DCF；应改用 PB、股利折现或剩余收益模型", model="financial-sector-exclusion")

    annual = _annual_statements(evidence)
    main_reports = _annual_reports(annual.get("main_index"))
    if not main_reports:
        main_reports = _annual_reports(_financial_quality(evidence).get("financials"))
    income_reports = _annual_reports(annual.get("income"))
    series = _fcff_series(main_reports, income_reports)
    usable = [row for row in series if row["fcff_per_share"] is not None and row["revenue_per_share"] not in (None, 0)]
    if len(usable) < 3:
        return _unavailable("至少需要 3 个年度的每股企业自由现金流和每股收入；当前证据不足")

    usable = sorted(usable, key=lambda row: row["year"])[-5:]
    ratios = [row["fcff_per_share"] / row["revenue_per_share"] for row in usable if row["revenue_per_share"] > 0]
    positive_ratios = [value for value in ratios if value > 0]
    if len(positive_ratios) < 2:
        return _unavailable("历史 FCFF 大多为负或不可用，普通永续增长 DCF 缺乏可靠现金流起点")

    normalized_margin = median(ratios)
    if normalized_margin <= 0:
        return _unavailable("历史 FCFF 利润率中位数不为正，普通永续增长 DCF 不适用")
    normalized_margin = min(normalized_margin, 0.60)
    latest = usable[-1]
    latest_revenue = latest["revenue_per_share"]
    if latest_revenue is None or latest_revenue <= 0:
        return _unavailable("最新年度每股收入无效")

    balance_reports = _annual_reports(annual.get("balance"))
    if not balance_reports or not income_reports:
        return _unavailable("缺少年度资产负债表或利润表，无法完成净债务和 WACC 桥接")
    latest_balance = max(balance_reports, key=_report_year)
    latest_income = max(income_reports, key=_report_year)

    shares, share_basis = _infer_shares(latest, latest_income)
    if shares is None or shares <= 0:
        return _unavailable("无法由 EBIT/股或净利润/EPS 反推股数，不能把净债务换算为每股价值")
    cash = _item_value(latest_balance, ids=(3003,), names=("货币资金", "现金及现金等价物"))
    if cash is None:
        return _unavailable("资产负债表缺少货币资金，无法完成企业价值到股权价值的桥接")
    debt, debt_items = _interest_bearing_debt(latest_balance)
    net_debt = debt - cash
    net_debt_per_share = net_debt / shares

    pretax = _item_value(latest_income, ids=(3038,), names=("利润总额", "税前利润"))
    tax_expense = _item_value(latest_income, ids=(3039,), names=("所得税费用",))
    effective_tax = 0.25
    if pretax is not None and pretax > 0 and tax_expense is not None:
        effective_tax = _clamp(tax_expense / pretax, 0.0, 0.35)
    interest_expense = _item_value(latest_income, ids=(3017,), names=("利息费用",)) or 0.0

    beta = 1.0
    cost_of_equity = defaults["risk_free_rate"] + beta * defaults["equity_risk_premium"]
    observed_cost_of_debt = abs(interest_expense) / debt if debt > 0 else None
    cost_of_debt = _clamp(observed_cost_of_debt, 0.02, 0.10) if observed_cost_of_debt is not None else defaults["risk_free_rate"] + 0.015
    equity_market_value = current_price * shares
    capital = equity_market_value + debt
    if capital <= 0:
        return _unavailable("权益和债务资本之和无效，无法计算 WACC")
    wacc = (
        equity_market_value / capital * cost_of_equity
        + debt / capital * cost_of_debt * (1.0 - effective_tax)
    )
    wacc = _clamp(wacc, 0.055, 0.16)

    revenue_growth = _absolute_revenue_cagr(income_reports)
    growth_basis = "利润表绝对营业收入近3年 CAGR"
    if revenue_growth is None:
        revenue_growth = _revenue_cagr(usable)
        growth_basis = "每股收入 CAGR（绝对收入不可用时降级）"
    if revenue_growth is None:
        return _unavailable("年度营业收入序列不足，无法建立显式增长假设")
    base_growth = _clamp(revenue_growth, -0.05, 0.20)
    terminal_growth = min(defaults["terminal_growth"], wacc - 0.015)
    scenario_inputs = {
        "bear": {
            "label": "保守",
            "revenue_growth": _clamp(base_growth - 0.03, -0.08, 0.15),
            "fcff_margin": max(0.01, normalized_margin * 0.80),
            "wacc": min(0.18, wacc + 0.01),
            "terminal_growth": max(0.005, terminal_growth - 0.01),
        },
        "base": {
            "label": "基准",
            "revenue_growth": base_growth,
            "fcff_margin": normalized_margin,
            "wacc": wacc,
            "terminal_growth": terminal_growth,
        },
        "bull": {
            "label": "乐观",
            "revenue_growth": _clamp(base_growth + 0.03, 0.0, 0.25),
            "fcff_margin": min(0.60, normalized_margin * 1.15),
            "wacc": max(0.05, wacc - 0.01),
            "terminal_growth": min(0.04, terminal_growth + 0.005, wacc - 0.01),
        },
    }

    scenarios: dict[str, dict[str, Any]] = {}
    for key, assumptions in scenario_inputs.items():
        result = _dcf_value(
            latest_revenue,
            assumptions["revenue_growth"],
            assumptions["fcff_margin"],
            assumptions["wacc"],
            assumptions["terminal_growth"],
            net_debt_per_share,
            forecast_years,
        )
        scenarios[key] = {
            "label": assumptions["label"],
            "value": round(max(0.0, result["equity_value_per_share"]), 2),
            "upside_pct": round((max(0.0, result["equity_value_per_share"]) / current_price - 1.0) * 100.0, 1),
            "revenue_growth_pct": round(assumptions["revenue_growth"] * 100.0, 2),
            "fcff_margin_pct": round(assumptions["fcff_margin"] * 100.0, 2),
            "wacc_pct": round(assumptions["wacc"] * 100.0, 2),
            "terminal_growth_pct": round(assumptions["terminal_growth"] * 100.0, 2),
            "pv_explicit_fcff_per_share": round(result["pv_explicit"], 2),
            "pv_terminal_value_per_share": round(result["pv_terminal"], 2),
            "net_debt_per_share": round(net_debt_per_share, 2),
        }

    sensitivity_wacc = [max(0.05, wacc - 0.01), wacc, min(0.18, wacc + 0.01)]
    sensitivity_growth = [max(0.005, terminal_growth - 0.01), terminal_growth, min(0.04, terminal_growth + 0.005)]
    sensitivity = []
    for test_wacc in sensitivity_wacc:
        row = {"wacc_pct": round(test_wacc * 100.0, 2), "values": []}
        for test_growth in sensitivity_growth:
            safe_growth = min(test_growth, test_wacc - 0.01)
            result = _dcf_value(latest_revenue, base_growth, normalized_margin, test_wacc, safe_growth, net_debt_per_share, forecast_years)
            row["values"].append({
                "terminal_growth_pct": round(safe_growth * 100.0, 2),
                "value": round(max(0.0, result["equity_value_per_share"]), 2),
            })
        sensitivity.append(row)

    implied_growth = _solve_implied_growth(
        target_price=current_price,
        revenue_per_share=latest_revenue,
        fcff_margin=normalized_margin,
        wacc=wacc,
        terminal_growth=terminal_growth,
        net_debt_per_share=net_debt_per_share,
        forecast_years=forecast_years,
    )
    mad = median([abs(value - normalized_margin) for value in ratios])
    dispersion = mad / abs(normalized_margin) if normalized_margin else 99.0
    confidence = "HIGH" if len(usable) >= 5 and ratios[-1] > 0 and dispersion <= 0.35 else "MEDIUM"
    if len(positive_ratios) / len(ratios) < 0.75 or ratios[-1] <= 0 or dispersion > 0.75:
        confidence = "LOW"
    decision_usable = confidence in {"HIGH", "MEDIUM"}

    warnings = [
        "WACC 的无风险利率、股权风险溢价和 Beta 是透明默认假设，不是实时市场报价；可在投资决策前手工替换。",
        "净债务采用最近年报货币资金与可识别有息负债；受限资金、经营性现金和表外负债尚未单独调整。",
        "FCFF/股按历史中位利润率归一化，未直接采用单一年份异常现金流。",
    ]
    if ratios[-1] <= 0:
        warnings.append("最新年度 FCFF 为负，估值依赖历史正常化现金流，置信度已降为 LOW。")
    if not decision_usable:
        warnings.append("历史 FCFF 波动较大，本结果仅作为现金流底值，不应作为主要目标价或仓位依据。")
    if normalized_margin >= 0.60:
        warnings.append("历史 FCFF 利润率超过模型上限，已按 60% 封顶。")

    return {
        "available": True,
        "method": f"{forecast_years}-year two-stage FCFF DCF",
        "model": "non-financial corporate FCFF",
        "horizon": horizon,
        "forecast_years": forecast_years,
        "currency": defaults["currency"],
        "current_price": round(current_price, 2),
        "confidence": confidence,
        "decision_usable": decision_usable,
        "valuation_role": "primary scenario valuation" if decision_usable else "low-confidence cash-flow floor",
        "source_periods": {
            "fcff": str(latest.get("period") or latest["year"]),
            "balance": str(latest_balance.get("period_text") or latest_balance.get("date_time_str") or _report_year(latest_balance)),
            "income": str(latest_income.get("period_text") or latest_income.get("date_time_str") or _report_year(latest_income)),
        },
        "historical_inputs": {
            "years": [row["year"] for row in usable],
            "fcff_per_share": [round(row["fcff_per_share"], 4) for row in usable],
            "revenue_per_share": [round(row["revenue_per_share"], 4) for row in usable],
            "normalized_fcff_margin_pct": round(normalized_margin * 100.0, 2),
            "revenue_cagr_pct": round(revenue_growth * 100.0, 2),
            "revenue_growth_basis": growth_basis,
            "positive_fcff_years": len(positive_ratios),
        },
        "capital_structure": {
            "shares_inferred": round(shares, 0),
            "share_basis": share_basis,
            "cash": round(cash, 2),
            "interest_bearing_debt": round(debt, 2),
            "debt_items": debt_items,
            "net_debt": round(net_debt, 2),
            "net_debt_per_share": round(net_debt_per_share, 4),
            "effective_tax_rate_pct": round(effective_tax * 100.0, 2),
        },
        "wacc": {
            "risk_free_rate_pct": round(defaults["risk_free_rate"] * 100.0, 2),
            "equity_risk_premium_pct": round(defaults["equity_risk_premium"] * 100.0, 2),
            "beta": beta,
            "cost_of_equity_pct": round(cost_of_equity * 100.0, 2),
            "pre_tax_cost_of_debt_pct": round(cost_of_debt * 100.0, 2),
            "base_wacc_pct": round(wacc * 100.0, 2),
        },
        "scenarios": scenarios,
        "sensitivity": {
            "terminal_growth_columns_pct": [round(value * 100.0, 2) for value in sensitivity_growth],
            "rows": sensitivity,
        },
        "implied_revenue_growth": implied_growth,
        "warnings": warnings,
    }


def _dcf_value(
    revenue_per_share: float,
    revenue_growth: float,
    fcff_margin: float,
    wacc: float,
    terminal_growth: float,
    net_debt_per_share: float,
    forecast_years: int = 5,
) -> dict[str, float]:
    if terminal_growth >= wacc:
        raise ValueError("terminal growth must be below WACC")
    revenue = revenue_per_share
    pv_explicit = 0.0
    fcff = 0.0
    for year in range(1, forecast_years + 1):
        revenue *= 1.0 + revenue_growth
        fcff = revenue * fcff_margin
        pv_explicit += fcff / ((1.0 + wacc) ** year)
    terminal_value = fcff * (1.0 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / ((1.0 + wacc) ** forecast_years)
    return {
        "pv_explicit": pv_explicit,
        "pv_terminal": pv_terminal,
        "enterprise_value_per_share": pv_explicit + pv_terminal,
        "equity_value_per_share": pv_explicit + pv_terminal - net_debt_per_share,
    }


def _solve_implied_growth(
    *,
    target_price: float,
    revenue_per_share: float,
    fcff_margin: float,
    wacc: float,
    terminal_growth: float,
    net_debt_per_share: float,
    forecast_years: int = 5,
) -> dict[str, Any]:
    low, high = -0.20, 0.50

    def price(growth: float) -> float:
        return _dcf_value(revenue_per_share, growth, fcff_margin, wacc, terminal_growth, net_debt_per_share, forecast_years)["equity_value_per_share"]

    low_value, high_value = price(low), price(high)
    if target_price < low_value:
        return {"available": False, "status": "below_range", "search_range_pct": [-20.0, 50.0]}
    if target_price > high_value:
        return {"available": False, "status": "above_range", "search_range_pct": [-20.0, 50.0]}
    for _ in range(80):
        mid = (low + high) / 2.0
        if price(mid) < target_price:
            low = mid
        else:
            high = mid
    result = (low + high) / 2.0
    return {
        "available": True,
        "annual_revenue_growth_pct": round(result * 100.0, 2),
        "interpretation": f"在基准 FCFF 利润率、WACC 和永续增长率不变时，当前股价隐含的未来 {forecast_years} 年年均收入增速",
    }


def _financial_quality(evidence: dict[str, Any]) -> dict[str, Any]:
    section = evidence.get("financial_quality")
    return section if isinstance(section, dict) else {}


def _annual_statements(evidence: dict[str, Any]) -> dict[str, Any]:
    section = _financial_quality(evidence).get("annual_statements")
    return section if isinstance(section, dict) else {}


def _annual_reports(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data", payload)
    reports = data.get("report_list", []) if isinstance(data, dict) else []
    result = []
    for report in reports if isinstance(reports, list) else []:
        if not isinstance(report, dict):
            continue
        financial_type = report.get("financial_type")
        period = str(report.get("period_text") or "")
        if financial_type == 7 or period.endswith("/FY") or period.endswith("FY"):
            result.append(report)
    return result


def _fcff_series(reports: list[dict[str, Any]], income_reports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    revenue_by_year = {
        _report_year(report): _item_value(
            report,
            ids=(1001, 1002, 3001, 3002),
            names=("营业总收入", "营业收入"),
        )
        for report in income_reports
    }
    result = []
    for report in reports:
        year = _report_year(report)
        ebit_per_share = _item_value(report, ids=(1011, 3013), names=("每股息税前利润", "每股EBIT"))
        ebit = _item_value(report, ids=(1026, 3028), names=("息税前利润（元）", "EBIT"))
        revenue_per_share = _item_value(report, ids=(1010, 3012), names=("每股营业总收入", "每股营业收入"))
        if revenue_per_share is None and ebit is not None and ebit_per_share is not None and ebit > 0 and ebit_per_share > 0:
            absolute_revenue = revenue_by_year.get(year)
            if absolute_revenue is not None and absolute_revenue > 0:
                revenue_per_share = absolute_revenue / (ebit / ebit_per_share)
        result.append({
            "year": year,
            "period": report.get("period_text") or report.get("date_time_str"),
            "fcff_per_share": _item_value(report, ids=(1008, 3010), names=("每股企业自由现金流",)),
            "revenue_per_share": revenue_per_share,
            "ebit_per_share": ebit_per_share,
            "ebit": ebit,
        })
    return [row for row in result if row["year"] > 0]


def _infer_shares(latest_main: dict[str, Any], latest_income: dict[str, Any]) -> tuple[float | None, str]:
    ebit = latest_main.get("ebit")
    ebit_per_share = latest_main.get("ebit_per_share")
    if isinstance(ebit, (int, float)) and isinstance(ebit_per_share, (int, float)) and ebit > 0 and ebit_per_share > 0:
        return ebit / ebit_per_share, "EBIT ÷ 每股 EBIT"
    net_profit = _item_value(latest_income, ids=(3047,), names=("归属母公司净利润",))
    eps = _item_value(latest_income, ids=(3051,), names=("基本每股收益",))
    if net_profit is not None and eps is not None and net_profit > 0 and eps > 0:
        return net_profit / eps, "归母净利润 ÷ 基本 EPS"
    return None, "Unavailable"


def _interest_bearing_debt(report: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    keywords = ("短期借款", "长期借款", "应付债券", "一年内到期的非流动负债", "一年内到期非流动负债", "租赁负债")
    total = 0.0
    items = []
    for item in report.get("item_list", []) if isinstance(report.get("item_list"), list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name") or "")
        value = _number(item.get("data"))
        if value is None or value <= 0 or not any(keyword in name for keyword in keywords):
            continue
        total += value
        items.append({"name": name, "value": round(value, 2)})
    return total, items


def _item_value(report: dict[str, Any], *, ids: tuple[int, ...], names: tuple[str, ...]) -> float | None:
    fallback = None
    for item in report.get("item_list", []) if isinstance(report.get("item_list"), list) else []:
        if not isinstance(item, dict):
            continue
        value = _number(item.get("data"))
        if value is None:
            continue
        if item.get("field_id") in ids:
            return value
        display_name = str(item.get("display_name") or "").replace("-", "").replace(":", "")
        if any(name.lower() in display_name.lower() for name in names):
            fallback = value
    return fallback


def _revenue_cagr(rows: list[dict[str, Any]]) -> float | None:
    ordered = sorted((row for row in rows if row.get("revenue_per_share") and row["revenue_per_share"] > 0), key=lambda row: row["year"])
    if len(ordered) < 3:
        return None
    first, last = ordered[0], ordered[-1]
    years = last["year"] - first["year"]
    if years <= 0:
        return None
    return (last["revenue_per_share"] / first["revenue_per_share"]) ** (1.0 / years) - 1.0


def _absolute_revenue_cagr(income_reports: list[dict[str, Any]]) -> float | None:
    """Use absolute revenue so stock splits and dilution do not fake growth."""
    values = []
    for report in income_reports:
        revenue = _item_value(report, ids=(1001, 1002, 3001, 3002), names=("营业总收入", "营业收入"))
        year = _report_year(report)
        if year > 0 and revenue is not None and revenue > 0:
            values.append((year, revenue))
    values = sorted(values)[-4:]
    if len(values) < 3:
        return None
    first, last = values[0], values[-1]
    years = last[0] - first[0]
    if years <= 0:
        return None
    return (last[1] / first[1]) ** (1.0 / years) - 1.0


def _report_year(report: dict[str, Any]) -> int:
    try:
        return int(report.get("fiscal_year") or str(report.get("date_time_str") or "")[:4])
    except (TypeError, ValueError):
        return 0


def _current_price(evidence: dict[str, Any]) -> float | None:
    snapshot = evidence.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    data = snapshot.get("data", snapshot)
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    for row in rows:
        if isinstance(row, dict):
            value = _number(row.get("last_price"))
            if value is not None:
                return value
    return None


def _market(evidence: dict[str, Any]) -> str:
    ticker = evidence.get("ticker")
    if isinstance(ticker, dict) and ticker.get("market") in MARKET_ASSUMPTIONS:
        return str(ticker["market"])
    code = str(ticker.get("code") if isinstance(ticker, dict) else "")
    if code.endswith(".HK"):
        return "HK"
    if code.endswith(".US"):
        return "US"
    return "A"


def _industry_text(evidence: dict[str, Any]) -> str:
    values = []
    for section_name in ("model", "catalyst"):
        section = evidence.get(section_name)
        rows = section.get("rows", []) if isinstance(section, dict) else []
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("industry"):
                values.append(str(row["industry"]))
    if evidence.get("industry"):
        values.append(str(evidence["industry"]))
    return " ".join(values)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "").strip())
        except ValueError:
            return None
    return None


def _clamp(value: float | None, low: float, high: float) -> float:
    if value is None:
        return low
    return max(low, min(high, value))


def _unavailable(reason: str, model: str = "non-financial corporate FCFF") -> dict[str, Any]:
    return {"available": False, "method": "5-year two-stage FCFF DCF", "model": model, "reason": reason}
