#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from valuation_engine import derive_dcf_valuation


COMPONENTS = {
    "model": {"weight": 15, "critical": False},
    "financial_quality": {"weight": 20, "critical": True},
    "valuation": {"weight": 15, "critical": True},
    "catalyst": {"weight": 15, "critical": False},
    "technical_flow": {"weight": 15, "critical": True},
    "governance_risk": {"weight": 15, "critical": True},
    "data_confidence": {"weight": 5, "critical": False},
}


POSTURE_ORDER = [
    "CORE CANDIDATE",
    "TIMING WATCH",
    "EVENT CANDIDATE",
    "REJECT-RISK WATCH",
    "INSUFFICIENT EVIDENCE",
]


def normalize_ticker(value: str) -> dict[str, str]:
    original = value.strip()
    code = original.upper().replace("_", ".")
    code = re.sub(r"\s+", "", code)

    if re.fullmatch(r"\d{6}", code):
        suffix = "SH" if code.startswith("6") else "BJ" if code.startswith(("4", "8")) else "SZ"
        normalized = f"{code}.{suffix}"
        return {"input": original, "code": normalized, "market": "A", "futu_code": f"{suffix}.{code}", "name": normalized}

    if re.fullmatch(r"\d{1,5}\.HK", code):
        number = code.split(".")[0].zfill(5)
        normalized = f"{number}.HK"
        return {"input": original, "code": normalized, "market": "HK", "futu_code": f"HK.{number}", "name": normalized}

    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", code):
        number, suffix = code.split(".")
        return {"input": original, "code": code, "market": "A", "futu_code": f"{suffix}.{number}", "name": code}

    if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}(\.US)?", code):
        symbol = code.removesuffix(".US")
        return {"input": original, "code": f"{symbol}.US", "market": "US", "futu_code": f"US.{symbol}", "name": f"{symbol}.US"}

    raise ValueError(f"Unsupported ticker format: {original}")


def score_research(evidence: dict[str, Any]) -> dict[str, Any]:
    component_scores: dict[str, float] = {}
    unavailable: list[str] = []
    hard_limits: list[str] = []

    for name, meta in COMPONENTS.items():
        value = _component_score(evidence.get(name))
        if value is None:
            unavailable.append(name)
            value = 0.0
        component_scores[name] = value

    total = sum(component_scores[name] * COMPONENTS[name]["weight"] / 100 for name in COMPONENTS)
    total = round(total * 2) / 2
    confidence = _confidence(component_scores, unavailable)

    if component_scores["governance_risk"] and component_scores["governance_risk"] < 45:
        hard_limits.append("governance_risk_below_45")
    if component_scores["valuation"] and component_scores["valuation"] < 35:
        hard_limits.append("valuation_below_35")
    if component_scores["technical_flow"] and component_scores["technical_flow"] < 35:
        hard_limits.append("technical_flow_below_35")

    missing_critical = [name for name in unavailable if COMPONENTS[name]["critical"]]
    if missing_critical:
        posture = "INSUFFICIENT EVIDENCE"
    elif hard_limits:
        posture = "REJECT-RISK WATCH"
    elif total >= 75 and confidence in {"HIGH", "MEDIUM"}:
        posture = "CORE CANDIDATE"
    elif total >= 68:
        posture = "TIMING WATCH"
    elif total >= 60 and component_scores["catalyst"] >= 70:
        posture = "EVENT CANDIDATE"
    elif total >= 55:
        posture = "REJECT-RISK WATCH"
    else:
        posture = "INSUFFICIENT EVIDENCE"

    return {
        "total_score": total,
        "component_scores": component_scores,
        "weights": {name: meta["weight"] for name, meta in COMPONENTS.items()},
        "posture": posture,
        "confidence": confidence,
        "position_band": position_band(posture, confidence, hard_limits, unavailable),
        "unavailable_components": unavailable,
        "hard_limits": hard_limits,
    }


def position_band(posture: str, confidence: str, hard_limits: list[str], unavailable: list[str]) -> str:
    if hard_limits or posture in {"REJECT-RISK WATCH", "INSUFFICIENT EVIDENCE"}:
        return "0%"
    if confidence == "LOW" or len(unavailable) >= 2:
        return "0%-2%"
    if posture == "CORE CANDIDATE":
        return "4%-6%" if confidence == "HIGH" else "2%-4%"
    if posture == "TIMING WATCH":
        return "2%-4%"
    if posture == "EVENT CANDIDATE":
        return "0%-2%"
    return "0%"


def load_evidence(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("--evidence-json must contain a JSON object")
    # Also accept a previously generated research_raw.json for reproducible
    # re-rendering without re-querying Futu/OpenD.
    if isinstance(data.get("evidence"), dict) and isinstance(data.get("ticker"), dict):
        code = data["ticker"].get("code")
        if code:
            return {str(code): data["evidence"]}
    return data


def select_evidence(all_evidence: dict[str, Any], ticker: dict[str, str]) -> dict[str, Any]:
    for key in (ticker["code"], ticker["input"], ticker["futu_code"], ticker["code"].split(".")[0]):
        value = all_evidence.get(key)
        if isinstance(value, dict):
            return value
    if _looks_like_single_stock_evidence(all_evidence):
        return all_evidence
    return {}


def write_bundle(
    ticker: dict[str, str],
    evidence: dict[str, Any],
    output_root: Path,
    as_of: date,
    horizon: str,
    language: str,
) -> dict[str, Any]:
    date_dir = output_root / as_of.strftime("%Y%m%d")
    display_ticker = enrich_ticker_name(ticker, evidence)
    stock_dir = date_dir / safe_dir_name(display_ticker["name"])
    stock_dir.mkdir(parents=True, exist_ok=True)

    warnings = evidence.get("warnings", [])
    if not isinstance(warnings, list):
        warnings = [str(warnings)]
    if not evidence:
        warnings.append("No structured evidence was supplied; all unavailable components are scored as gaps.")

    raw = {
        "ticker": display_ticker,
        "as_of": as_of.isoformat(),
        "horizon": horizon,
        "language": language,
        "evidence": evidence,
        "warnings": warnings,
        "sources": evidence.get("sources", []) if isinstance(evidence.get("sources", []), list) else [],
    }
    research_score = score_research(evidence)
    fair_value = derive_fair_value(evidence, as_of, horizon)
    dcf_valuation = derive_dcf_valuation(evidence, horizon)
    research_context = derive_research_context(evidence, research_score, fair_value, dcf_valuation)
    derived = {
        "ticker": display_ticker,
        "as_of": as_of.isoformat(),
        "horizon": horizon,
        "research_score": research_score,
        "fair_value": fair_value,
        "dcf_valuation": dcf_valuation,
        "next_catalyst": _field(evidence, "catalyst", "next_catalyst") or evidence.get("next_catalyst") or research_context["next_catalyst"],
        "primary_invalidation": evidence.get("primary_invalidation") or research_context["primary_invalidation"],
        "key_risk": evidence.get("key_risk") or research_context["key_risk"],
        "research_context": research_context,
        "calculation_notes": [
            "Scores are deterministic weighted component scores.",
            "Unavailable evidence is listed explicitly and does not receive neutral credit.",
            "DCF is a deterministic FCFF scenario model and fails closed when annual cash-flow or capital-structure evidence is insufficient.",
            "DCF is reported as an independent valuation constraint and does not silently rewrite the local model score.",
            "Position bands are shadow-only research sizing guidance, not production portfolio changes.",
        ],
    }

    (stock_dir / "research_raw.json").write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    (stock_dir / "research_derived.json").write_text(json.dumps(derived, ensure_ascii=False, indent=2), encoding="utf-8")
    (stock_dir / "research_brief.md").write_text(render_brief(raw, derived), encoding="utf-8")
    return {"code": ticker["code"], "output_dir": str(stock_dir), "research_score": research_score}


def render_brief(raw: dict[str, Any], derived: dict[str, Any]) -> str:
    ticker = raw["ticker"]
    score = derived["research_score"]
    unavailable = ", ".join(score["unavailable_components"]) or "无"
    hard_limits = ", ".join(score["hard_limits"]) or "无"
    warnings = raw["warnings"] or ["无"]

    lines = [
        f"# {ticker['name']} 深度研究证据简报",
        "",
        "## 研究快照",
        f"- 股票代码：{ticker['code']}",
        f"- 股票名称：{ticker['name']}",
        f"- 市场：{ticker['market']}",
        f"- 截止日期：{raw['as_of']}",
        f"- 研究周期：{raw['horizon']}",
        f"- 研究姿态：{score['posture']}",
        f"- 研究置信度：{score['confidence']}",
        f"- 综合评分：{score['total_score']}",
        "",
        "## 合理价值区间（相对估值）",
    ]
    fair_value = derived.get("fair_value", {})
    if fair_value.get("available"):
        lines.extend(
            [
                f"- 当前股价：{fair_value['current_price']:.2f} 元",
                f"- 当前估值：{fair_value['multiple_name']} {fair_value['current_multiple']:.2f} 倍，历史估值分位 {fair_value['valuation_percentile']:.1f}%",
                f"- 估值基准：{fair_value['basis_label']} {fair_value['basis_value']:.4f} 元 × 近{fair_value['lookback_years']}年历史 {fair_value['multiple_name']} 分位数",
                f"- 财务基准来源：{fair_value.get('basis_derivation', '财务指标直接值')}",
                f"- 保守情景：{fair_value['multiple_name']} {fair_value['scenarios']['bear']['multiple']:.2f}，合理价值 {fair_value['scenarios']['bear']['value']:.2f} 元，较现价 {fair_value['scenarios']['bear']['upside_pct']:+.1f}%",
                f"- 基准情景：{fair_value['multiple_name']} {fair_value['scenarios']['base']['multiple']:.2f}，合理价值 {fair_value['scenarios']['base']['value']:.2f} 元，较现价 {fair_value['scenarios']['base']['upside_pct']:+.1f}%",
                f"- 乐观情景：{fair_value['multiple_name']} {fair_value['scenarios']['bull']['multiple']:.2f}，合理价值 {fair_value['scenarios']['bull']['value']:.2f} 元，较现价 {fair_value['scenarios']['bull']['upside_pct']:+.1f}%",
                f"- 口径说明：不假设未来盈利增长；这是基于历史 {fair_value['multiple_name']} 的估值锚定区间，不等同于券商目标价或确定性收益预测。样本数 {fair_value['multiple_sample_size']} 个交易日。",
            ]
        )
        implied_basis = fair_value.get("current_price_implied_basis", {})
        if implied_basis.get("available"):
            lines.append(
                f"- 现价反推：若回到历史中位 {fair_value['multiple_name']} {fair_value['scenarios']['base']['multiple']:.2f} 倍，"
                f"需 {fair_value['basis_label']} 达到 {implied_basis['required_basis_value']:.4f} 元，"
                f"相当于当前基准值的 {implied_basis['multiple_of_current_basis']:.2f} 倍（增幅 {implied_basis['required_growth_pct']:+.1f}%）。"
            )
    else:
        lines.append(f"- 暂无法计算：{fair_value.get('reason', '缺少股价、TTM EPS 或历史估值数据')}")

    dcf = derived.get("dcf_valuation", {})
    lines.extend(["", "## 目标价区间（FCFF DCF）"])
    if dcf.get("available"):
        currency = dcf.get("currency", "CNY")
        scenarios = dcf["scenarios"]
        value_label = "目标价" if dcf.get("decision_usable", True) else "现金流底值"
        lines.extend(
            [
                f"- 当前股价：{dcf['current_price']:.2f} {currency}",
                f"- 保守{value_label}：{scenarios['bear']['value']:.2f} {currency}，较现价 {scenarios['bear']['upside_pct']:+.1f}%",
                f"- 基准{value_label}：{scenarios['base']['value']:.2f} {currency}，较现价 {scenarios['base']['upside_pct']:+.1f}%",
                f"- 乐观{value_label}：{scenarios['bull']['value']:.2f} {currency}，较现价 {scenarios['bull']['upside_pct']:+.1f}%",
                f"- 基准假设：未来5年收入增速 {scenarios['base']['revenue_growth_pct']:.2f}%，FCFF利润率 {scenarios['base']['fcff_margin_pct']:.2f}%，WACC {scenarios['base']['wacc_pct']:.2f}%，永续增长率 {scenarios['base']['terminal_growth_pct']:.2f}%",
                f"- 现金流口径：历史 {len(dcf['historical_inputs']['years'])} 个年度，归一化 FCFF 利润率 {dcf['historical_inputs']['normalized_fcff_margin_pct']:.2f}%，模型置信度 {dcf['confidence']}",
                f"- 净债务调整：{dcf['capital_structure']['net_debt_per_share']:.2f} {currency}/股（负数表示净现金）",
            ]
        )
        if not dcf.get("decision_usable", True):
            lines.append("- 适用性提示：历史 FCFF 波动过大，本组数值仅作为低置信度现金流底值，不作为主要目标价；优先参考相对估值及未来盈利预测。")
        implied = dcf.get("implied_revenue_growth", {})
        if implied.get("available"):
            lines.append(f"- 当前价格隐含增速：未来5年收入年均增长 {implied['annual_revenue_growth_pct']:.2f}%")
        else:
            lines.append(f"- 当前价格隐含增速：超出模型搜索区间 -20% 至 50%（{implied.get('status', 'Unavailable')}）")
        lines.extend(["", "### WACC / 永续增长率敏感性（每股价值）", "", "| WACC \\ 永续增长率 | " + " | ".join(f"{value:.2f}%" for value in dcf["sensitivity"]["terminal_growth_columns_pct"]) + " |", "|---:" + "|---:" * len(dcf["sensitivity"]["terminal_growth_columns_pct"]) + "|"])
        for row in dcf["sensitivity"]["rows"]:
            lines.append(f"| {row['wacc_pct']:.2f}% | " + " | ".join(f"{cell['value']:.2f}" for cell in row["values"]) + " |")
        lines.extend(["", "- 口径说明：这是基于明确假设的情景估值，不是确定性收益承诺；WACC、增长率和现金流利润率均可在 JSON 中逐项审计。"])
    else:
        lines.append(f"- 暂无法计算：{dcf.get('reason', '年度 FCFF 或资本结构证据不足')}")
    lines.extend(
        [
            "",
            "## 仓位建议",
            f"- 影子仓位区间：{score['position_band']}",
            "- 说明：该区间只用于研究优先级和组合讨论，不自动改写生产模型、实盘仓位或参数。",
            "",
            "## 评分拆解",
        ]
    )
    dcf_base_upside = _numeric(dcf.get("scenarios", {}).get("base", {}).get("upside_pct")) if dcf.get("available") else None
    if dcf_base_upside is not None and dcf_base_upside < 0:
        score_heading = lines.index("## 评分拆解")
        dcf_constraint_label = "DCF 基准目标价" if dcf.get("decision_usable", True) else "DCF 基准现金流底值"
        lines[score_heading:score_heading] = [
            f"- 估值约束：{dcf_constraint_label}较现价 {dcf_base_upside:+.1f}%；该冲突未机械改写模型评分，实际仓位需单独下调或等待假设被验证。",
            "",
        ]
    for name, value in score["component_scores"].items():
        lines.append(f"- {name}：{value}，权重 {score['weights'][name]}%")
    lines.extend(
        [
            "",
            "## 证据缺口",
            f"- 不可用组件：{unavailable}",
            f"- 硬性限制：{hard_limits}",
            "",
            "## 下一催化与证伪",
            f"- 下一催化：{derived.get('next_catalyst') or 'Unavailable'}",
            f"- 主要风险：{derived.get('key_risk')}",
            f"- 证伪触发：{derived.get('primary_invalidation')}",
            "",
            "## 采集警告",
        ]
    )
    for warning in warnings:
        lines.append(f"- {warning}")
    lines.append("")
    return "\n".join(lines)


def derive_fair_value(evidence: dict[str, Any], as_of: date, horizon: str) -> dict[str, Any]:
    """Derive a traceable three-scenario valuation anchor from local evidence.

    This is intentionally a relative-valuation range rather than a forward price
    target: the valuation metric returned by Futu is multiplied by recent
    historical percentiles. PE uses TTM EPS, PB uses book value per share, and
    PS uses revenue per share. No growth assumption is silently introduced.
    """
    snapshot_rows = _data_rows(evidence.get("snapshot"))
    current_price = _numeric(snapshot_rows[0].get("last_price")) if snapshot_rows else None

    financials = evidence.get("financial_quality", {})
    if isinstance(financials, dict):
        financials = financials.get("financials", financials)
    if not isinstance(financials, dict):
        financials = {}
    financial_data = financials.get("data", financials)
    reports = financial_data.get("report_list", []) if isinstance(financial_data, dict) else []
    metric_candidates: dict[int, list[tuple[str, float]]] = {1: [], 2: [], 3: []}
    fallback_eps: list[tuple[str, float]] = []
    for report in reports if isinstance(reports, list) else []:
        if not isinstance(report, dict):
            continue
        report_date = str(report.get("date_time_str") or report.get("period_text") or "")
        for item in report.get("item_list", []) if isinstance(report.get("item_list"), list) else []:
            if not isinstance(item, dict):
                continue
            value = _numeric(item.get("data"))
            if value is None or value <= 0:
                continue
            field_id = item.get("field_id")
            name = str(item.get("display_name", ""))
            if field_id == 3006 or "每股收益_TTM" in name or "TTM每股收益" in name:
                metric_candidates[1].append((report_date, value))
            elif field_id in {1003, 1004, 3005} or "基本每股收益" in name or "稀释每股收益" in name or "每股收益（摊薄）" in name:
                fallback_eps.append((report_date, value))
            elif field_id in {1002, 3002} or "每股净资产" in name:
                metric_candidates[2].append((report_date, value))
            elif field_id in {1010, 3012} or "每股营业总收入" in name:
                metric_candidates[3].append((report_date, value))

    valuation = evidence.get("valuation", {})
    if isinstance(valuation, dict):
        valuation = valuation.get("valuation_detail", valuation)
    if not isinstance(valuation, dict):
        valuation = {}
    valuation_data = valuation.get("data", valuation)
    valuation_type = _numeric(valuation_data.get("valuation_type")) if isinstance(valuation_data, dict) else None
    metric_specs = {
        1: ("PE", "TTM 每股收益", "EPS", "PE"),
        2: ("PB", "每股净资产", "BPS", "PB"),
        3: ("PS", "每股营业总收入", "RPS", "PS"),
    }
    spec = metric_specs.get(int(valuation_type or 0))
    if spec is None:
        if current_price is None:
            return {"available": False, "reason": "当前股价缺失；估值类型也缺失或不支持"}
        return {"available": False, "reason": "估值类型缺失或不支持，未将未知指标误当作 PE"}
    multiple_name, basis_label, basis_short, _ = spec
    trend = valuation_data.get("trend", {}) if isinstance(valuation_data, dict) else {}
    if not isinstance(trend, dict):
        trend = {}
    current_multiple = _numeric(trend.get("current_value"))
    if current_multiple is None or current_multiple <= 0:
        return {"available": False, "reason": f"当前 {multiple_name} 缺失"}

    basis_derivation = "财务指标直接值"
    if int(valuation_type) == 1 and not metric_candidates[1]:
        # Some Futu statement schemas expose quarterly/basic EPS but omit the
        # dedicated TTM field. Current price/current PE recovers the exact TTM
        # denominator used by the same valuation endpoint and is safer than
        # treating Q1 or H1 EPS as a full-year figure.
        if current_price is not None:
            metric_candidates[1] = [(f"{as_of.isoformat()}（当前价÷富途当前PE反推）", current_price / current_multiple)]
            basis_derivation = "当前股价 ÷ 富途当前 PE 反推 TTM EPS"
        else:
            metric_candidates[1] = fallback_eps
            basis_derivation = "最近可用基本/稀释 EPS（非 TTM，低置信度降级）"
    basis_source = metric_candidates[int(valuation_type)]
    basis_source.sort(key=lambda item: item[0], reverse=True)
    basis_value = basis_source[0][1] if basis_source else None
    if current_price is None or basis_value is None:
        missing = []
        if current_price is None:
            missing.append("当前股价")
        if basis_value is None:
            missing.append(basis_label)
        return {"available": False, "reason": "、".join(missing) + "缺失"}

    implied_multiple = current_price / basis_value
    if abs(implied_multiple - current_multiple) / max(current_multiple, 1e-9) > 0.25:
        return {
            "available": False,
            "reason": f"当前价格与财务基准不一致：价格/基准值约 {implied_multiple:.2f} 倍，但接口当前 {multiple_name} 为 {current_multiple:.2f} 倍",
        }
    historical = trend.get("historical_items", [])
    cutoff_year = as_of.year - 3
    cutoff = date(cutoff_year, as_of.month, min(as_of.day, 28))
    multiple_values: list[float] = []
    for item in historical if isinstance(historical, list) else []:
        if not isinstance(item, dict):
            continue
        value = _numeric(item.get("value"))
        if value is None or value <= 0:
            continue
        try:
            item_date = date.fromisoformat(str(item.get("time_str", ""))[:10])
        except ValueError:
            continue
        if cutoff <= item_date <= as_of:
            multiple_values.append(value)

    if len(multiple_values) < 30:
        current_multiple = _numeric(trend.get("current_value"))
        if current_multiple is None or current_multiple <= 0:
            return {"available": False, "reason": f"近三年历史 {multiple_name} 样本不足，且当前 {multiple_name} 缺失"}
        multiple_values = [current_multiple]
        lookback_years = 0
        basis = f"当前 {multiple_name}（历史样本不足）"
    else:
        multiple_values.sort()
        lookback_years = 3
        basis = f"近三年历史 {multiple_name} 分位数"

    bear_multiple = _percentile(multiple_values, 0.25)
    base_multiple = _percentile(multiple_values, 0.50)
    bull_multiple = _percentile(multiple_values, 0.75)
    scenarios = {}
    for key, label, multiple in (("bear", "保守", bear_multiple), ("base", "基准", base_multiple), ("bull", "乐观", bull_multiple)):
        value = basis_value * multiple
        scenarios[key] = {
            "label": label,
            "multiple": round(multiple, 2),
            "basis_value": round(basis_value, 4),
            "value": round(value, 2),
            "upside_pct": round((value / current_price - 1) * 100, 1),
        }

    required_basis_value = current_price / base_multiple
    current_price_implied_basis = {
        "available": True,
        "multiple": round(base_multiple, 2),
        "required_basis_value": round(required_basis_value, 4),
        "multiple_of_current_basis": round(required_basis_value / basis_value, 2),
        "required_growth_pct": round((required_basis_value / basis_value - 1.0) * 100.0, 1),
    }

    return {
        "available": True,
        "method": f"{basis_label} × {multiple_name} historical percentile",
        "basis": basis,
        "horizon": horizon,
        "current_price": round(current_price, 2),
        "current_multiple": round(current_multiple, 2),
        "valuation_percentile": round(_numeric(trend.get("valuation_percentile")) or 0.0, 1),
        "valuation_type": int(valuation_type),
        "multiple_name": multiple_name,
        "basis_label": basis_label,
        "basis_short": basis_short,
        "basis_value": round(basis_value, 4),
        "basis_derivation": basis_derivation,
        "basis_source_period": basis_source[0][0],
        "eps_ttm": round(basis_value, 4) if int(valuation_type) == 1 else None,
        "eps_source_period": basis_source[0][0] if int(valuation_type) == 1 else None,
        "lookback_years": lookback_years,
        "multiple_sample_size": len(multiple_values),
        "pe_sample_size": len(multiple_values) if int(valuation_type) == 1 else None,
        "scenarios": scenarios,
        "current_price_implied_basis": current_price_implied_basis,
        "warnings": [
            "未加入未来盈利增长假设；如需12个月目标价，应另行接入盈利预测或 DCF 假设。",
            f"已按接口返回的 {multiple_name} 类型计算，未将其强行解释为 PE。",
            f"财务基准来源：{basis_derivation}。",
        ],
    }


def _data_rows(section: Any) -> list[dict[str, Any]]:
    if not isinstance(section, dict):
        return []
    data = section.get("data", section)
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        rows = data.get("rows")
        if isinstance(rows, list):
            return [item for item in rows if isinstance(item, dict)]
        return [data] if "last_price" in data else []
    return []


def enrich_ticker_name(ticker: dict[str, str], evidence: dict[str, Any]) -> dict[str, str]:
    """Prefer the issuer name returned by the market evidence over the code."""
    enriched = dict(ticker)
    candidates: list[Any] = []
    evidence_ticker = evidence.get("ticker")
    if isinstance(evidence_ticker, dict):
        candidates.append(evidence_ticker.get("name"))
    snapshot_rows = _data_rows(evidence.get("snapshot"))
    if snapshot_rows:
        candidates.append(snapshot_rows[0].get("name"))
    for candidate in candidates:
        name = str(candidate or "").strip()
        if name and name not in {ticker.get("code"), ticker.get("name"), ticker.get("input")}:
            enriched["name"] = name
            break
    return enriched


def _numeric(value: Any) -> float | None:
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


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one value")
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def derive_research_context(
    evidence: dict[str, Any],
    research_score: dict[str, Any],
    fair_value: dict[str, Any],
    dcf_valuation: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Turn available evidence into explicit, testable catalyst and risk text."""
    catalyst = evidence.get("catalyst", {})
    catalyst_rows = catalyst.get("rows", []) if isinstance(catalyst, dict) else []
    rows = [row for row in catalyst_rows if isinstance(row, dict)]
    event_scores = [_numeric(row.get("event_score")) for row in rows]
    catalyst_scores = [_numeric(row.get("catalyst_score")) for row in rows]
    event_score = max((value for value in event_scores if value is not None), default=None)
    catalyst_score = max((value for value in catalyst_scores if value is not None), default=None)

    flow_rows = []
    technical = evidence.get("technical_flow", {})
    if isinstance(technical, dict):
        flow = technical.get("capital_flow", {})
        flow_rows = flow.get("data", []) if isinstance(flow, dict) else []
    flow_rows = [row for row in flow_rows if isinstance(row, dict)]
    flow_10d = _first_numeric(flow_rows, "flow_net_10d")
    flow_20d = _first_numeric(flow_rows, "flow_net_20d")
    flow_positive = flow_10d is not None and flow_20d is not None and flow_10d > 0 and flow_20d > 0

    if event_score is not None or catalyst_score is not None:
        catalyst_text = "下一事件窗口：下一期财报/业绩预告（证据包未提供具体日期）"
        checks = ["验证盈利增长是否延续"]
        if flow_positive:
            checks.append("验证10日、20日主力净流入是否继续为正")
        if event_score is not None:
            checks.append(f"当前事件评分 {event_score:.1f}")
        if catalyst_score is not None:
            checks.append(f"催化评分 {catalyst_score:.1f}")
        next_catalyst = catalyst_text + "；" + "，".join(checks) + "。"
    else:
        next_catalyst = "未采集到带日期的公司事件；需补充公告日历或下一期业绩披露日期。"

    risk_parts: list[str] = []
    if fair_value.get("available"):
        base = fair_value.get("scenarios", {}).get("base", {})
        base_upside = _numeric(base.get("upside_pct"))
        if base_upside is not None and base_upside <= 5:
            risk_parts.append(f"基准合理价值较现价仅{base_upside:+.1f}%")
    dcf_valuation = dcf_valuation or {}
    if dcf_valuation.get("available"):
        dcf_upside = _numeric(dcf_valuation.get("scenarios", {}).get("base", {}).get("upside_pct"))
        if dcf_upside is not None and dcf_upside <= 5:
            dcf_label = "DCF基准目标价" if dcf_valuation.get("decision_usable", True) else "DCF基准现金流底值"
            risk_parts.append(f"{dcf_label}较现价{dcf_upside:+.1f}%")

    margin_yoy, cash_yoy = _latest_quality_changes(evidence)
    if margin_yoy is not None and margin_yoy < 0:
        risk_parts.append(f"最新销售净利率同比{margin_yoy:+.1f}%")
    if cash_yoy is not None and cash_yoy < 0:
        risk_parts.append(f"净利润现金含量同比{cash_yoy:+.1f}%")
    if flow_positive:
        risk_parts.append("资金流正向但需观察持续性")
    key_risk = "；".join(risk_parts) + "。" if risk_parts else _default_key_risk(research_score)

    invalidation_parts = ["下一期 EPS/扣非利润同比转负或明显低于预期"]
    if margin_yoy is not None or cash_yoy is not None:
        invalidation_parts.append("利润率或经营现金含量继续恶化")
    if flow_positive:
        invalidation_parts.append("10日、20日主力净流入同时转负")
    invalidation_parts.append("估值评分跌破35或关键证据补齐后仍无法支撑当前结论")
    return {
        "next_catalyst": next_catalyst,
        "key_risk": key_risk,
        "primary_invalidation": "；".join(invalidation_parts) + "。",
    }


def _first_numeric(rows: list[dict[str, Any]], key: str) -> float | None:
    for row in rows:
        value = _numeric(row.get(key))
        if value is not None:
            return value
    return None


def _latest_quality_changes(evidence: dict[str, Any]) -> tuple[float | None, float | None]:
    financials = evidence.get("financial_quality", {})
    if isinstance(financials, dict):
        financials = financials.get("financials", financials)
    if not isinstance(financials, dict):
        return None, None
    data = financials.get("data", financials)
    reports = data.get("report_list", []) if isinstance(data, dict) else []
    if not isinstance(reports, list) or not reports:
        return None, None
    reports = sorted(
        [report for report in reports if isinstance(report, dict)],
        key=lambda report: str(report.get("date_time_str", "")),
        reverse=True,
    )
    items = reports[0].get("item_list", [])
    margin_yoy = None
    cash_yoy = None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("display_name", ""))
        if name == "销售净利率":
            margin_yoy = _numeric(item.get("yoy"))
        elif name == "净利润现金含量":
            cash_yoy = _numeric(item.get("yoy"))
    return margin_yoy, cash_yoy


def safe_dir_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value).strip("_") or "stock"


def write_multi_stock_outputs(results: list[dict[str, Any]], output_root: Path, as_of: date) -> None:
    if len(results) <= 1:
        return
    date_dir = output_root / as_of.strftime("%Y%m%d")
    summary_path = date_dir / "deep_research_summary.csv"
    fields = ["code", "research_posture", "confidence", "score", "position_band", "output_dir"]
    with summary_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in results:
            score = item["research_score"]
            writer.writerow(
                {
                    "code": item["code"],
                    "research_posture": score["posture"],
                    "confidence": score["confidence"],
                    "score": score["total_score"],
                    "position_band": score["position_band"],
                    "output_dir": item["output_dir"],
                }
            )
    synthesis = [
        "# Portfolio Synthesis",
        "",
        "This deterministic collector only summarizes per-stock research scores.",
        "Use the final deep-research reports to compare industry concentration, catalyst clustering, and correlated risks.",
        "",
    ]
    (date_dir / "portfolio_synthesis.md").write_text("\n".join(synthesis), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect deterministic evidence scaffolds for deep stock research.")
    parser.add_argument("codes", nargs="+", help="A-share, Hong Kong, or US tickers to research.")
    parser.add_argument("--output-root", type=Path, default=Path("outputs/deep_research"))
    parser.add_argument("--as-of", default=date.today().isoformat(), help="Research cutoff date, YYYY-MM-DD.")
    parser.add_argument("--horizon", default="MEDIUM", choices=["SHORT", "MEDIUM", "LONG"])
    parser.add_argument("--language", default="zh-CN", choices=["zh-CN", "en"])
    parser.add_argument("--evidence-json", type=Path, help="Optional JSON evidence map keyed by normalized code.")
    parser.add_argument("--model-output", type=Path, help="Reserved for saved model output ingestion.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    as_of = date.fromisoformat(args.as_of)
    all_evidence = load_evidence(args.evidence_json)
    results: list[dict[str, Any]] = []

    for code in args.codes:
        ticker = normalize_ticker(code)
        evidence = select_evidence(all_evidence, ticker)
        if args.model_output:
            evidence = merge_model_output(evidence, args.model_output, ticker)
        results.append(write_bundle(ticker, evidence, args.output_root, as_of, args.horizon, args.language))

    write_multi_stock_outputs(results, args.output_root, as_of)
    for item in results:
        score = item["research_score"]
        print(f"{item['code']}: {score['posture']} score={score['total_score']} position={score['position_band']}")
        print(f"  output: {item['output_dir']}")
    return 0


def merge_model_output(evidence: dict[str, Any], path: Path, ticker: dict[str, str]) -> dict[str, Any]:
    if not path.exists():
        merged = dict(evidence)
        merged.setdefault("warnings", []).append(f"Model output not found: {path}")
        return merged

    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        selected = select_evidence(data if isinstance(data, dict) else {}, ticker)
        if selected:
            merged = dict(evidence)
            merged["model"] = selected.get("model", selected)
            return merged

    merged = dict(evidence)
    merged.setdefault("warnings", []).append(f"Unsupported or unmatched model output: {path}")
    return merged


def _component_score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _clamp(float(value))
    if isinstance(value, dict):
        for key in ("score", "total_score", "confidence_score"):
            if isinstance(value.get(key), (int, float)):
                return _clamp(float(value[key]))
    return None


def _confidence(component_scores: dict[str, float], unavailable: list[str]) -> str:
    data_score = component_scores.get("data_confidence", 0.0)
    if data_score >= 80 and len(unavailable) <= 1:
        return "HIGH"
    if data_score >= 60 and len(unavailable) <= 2:
        return "MEDIUM"
    return "LOW"


def _clamp(value: float) -> float:
    return max(0.0, min(100.0, round(value, 2)))


def _field(evidence: dict[str, Any], section: str, key: str) -> Any:
    value = evidence.get(section)
    if isinstance(value, dict):
        return value.get(key)
    return None


def _default_key_risk(score: dict[str, Any]) -> str:
    if score["unavailable_components"]:
        return "关键证据缺失导致结论置信度受限"
    if score["hard_limits"]:
        return "硬性风险限制触发，需先排除风险事项"
    return "基本面、估值、资金流或催化剂出现与当前结论相反的新证据"


def _default_invalidation(score: dict[str, Any]) -> str:
    if score["posture"] == "CORE CANDIDATE":
        return "财务质量或资金流评分跌破60，或治理风险评分跌破45"
    if score["posture"] == "TIMING WATCH":
        return "技术/资金流无法改善且催化剂落空"
    if score["posture"] == "EVENT CANDIDATE":
        return "催化剂日期后未出现基本面或资金确认"
    return "补齐关键证据后仍无法支撑最低研究阈值"


def _looks_like_single_stock_evidence(data: dict[str, Any]) -> bool:
    return any(key in data for key in COMPONENTS) or "sources" in data or "warnings" in data


if __name__ == "__main__":
    raise SystemExit(main())
