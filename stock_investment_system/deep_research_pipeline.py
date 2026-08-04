#!/usr/bin/env python3
"""Run deterministic deep research for a generated watchlist and aggregate it.

The pipeline is deliberately separate from model ranking. A failed research job
never invalidates or deletes the original watchlist CSV. It produces a new,
enriched CSV and a self-contained HTML report after all requested stocks finish.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import html
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import quote


SYSTEM_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SYSTEM_DIR.parent
DEEP_RESEARCH_DIR = PROJECT_ROOT / "deep-stock-research"
RESEARCH_COLLECTOR = DEEP_RESEARCH_DIR / "scripts" / "collect_deep_research_data.py"
RUNTIME = SYSTEM_DIR / "env.sh"
RESEARCH_RUNTIME = (
    SYSTEM_DIR / ".venv313" / "bin" / "python"
    if (SYSTEM_DIR / ".venv313" / "bin" / "python").is_file()
    else RUNTIME
)
DEEP_OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "deep_research"


MODEL_COLUMN_MAP = {
    "总分": "total_score",
    "基本面质量分": "fundamental_quality_score",
    "成长质量分": "growth_quality_score",
    "估值分": "valuation_score",
    "量价分": "price_volume_score",
    "资金流分": "capital_flow_score",
    "事件分": "event_score",
    "预期分": "expectation_score",
    "催化分": "catalyst_score",
    "当日净流入": "flow_net",
    "5日净流入": "flow_net_5d",
    "10日净流入": "flow_net_10d",
    "20日净流入": "flow_net_20d",
    "20日大单净流入": "large_order_net_20d",
    "资金流入占比": "flow_positive_ratio",
    "20日资金流入占比": "flow_positive_ratio_20d",
    "资金统计天数": "flow_days",
}

DEEP_COLUMNS = [
    "深研姿态",
    "深研评分",
    "深研置信度",
    "影子仓位",
    "硬性限制",
    "相对估值保守值",
    "相对估值基准值",
    "相对估值乐观值",
    "相对估值基准空间%",
    "现价所需财务增幅%",
    "DCF用途",
    "DCF置信度",
    "DCF保守值",
    "DCF基准值",
    "DCF乐观值",
    "DCF基准空间%",
    "下一催化",
    "主要风险",
    "证伪条件",
    "深研结论文件",
    "深研目录",
]


def parse_watchlist(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a watchlist even when legacy OpenD log lines precede the header."""
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.lstrip("\ufeff").startswith("模型,股票代码,")),
        None,
    )
    if header_index is None:
        raise ValueError(f"未找到股票投资系统 CSV 表头：{path}")
    reader = csv.DictReader(lines[header_index:])
    fields = [str(field or "").lstrip("\ufeff") for field in (reader.fieldnames or [])]
    rows: list[dict[str, str]] = []
    for raw in reader:
        normalized = {str(key or "").lstrip("\ufeff"): str(value or "").strip() for key, value in raw.items() if key is not None}
        code = normalized.get("股票代码", "")
        if re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|BJ))?", code):
            rows.append(normalized)
    if not rows:
        raise ValueError(f"CSV 中没有可研究的 A 股代码：{path}")
    return fields, rows


def normalize_code(value: str) -> str:
    code = value.strip().upper()
    if re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", code):
        return code
    if re.fullmatch(r"\d{6}", code):
        suffix = "SH" if code.startswith("6") else "BJ" if code.startswith(("4", "8")) else "SZ"
        return f"{code}.{suffix}"
    raise ValueError(f"不支持的股票代码：{value}")


def select_codes(rows: list[dict[str, str]], max_stocks: int) -> list[str]:
    ranked: dict[str, tuple[float, int]] = {}
    for index, row in enumerate(rows):
        code = normalize_code(row["股票代码"])
        score = number(row.get("总分")) or 0.0
        prior = ranked.get(code)
        if prior is None or score > prior[0]:
            ranked[code] = (score, index)
    ordered = sorted(ranked, key=lambda code: (-ranked[code][0], ranked[code][1]))
    return ordered[:max_stocks] if max_stocks > 0 else ordered


def build_model_rows(rows: list[dict[str, str]], selected_codes: set[str]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        normalized = normalize_code(row["股票代码"])
        if normalized not in selected_codes:
            continue
        item: dict[str, Any] = {
            "model_name": row.get("模型"),
            "code": normalized.split(".")[0],
            "name": row.get("股票名称"),
            "industry": row.get("行业"),
            "research_posture": row.get("研究姿态"),
            "entry_action": row.get("入场动作"),
            "selection_reason": row.get("选择理由"),
            "price_source": row.get("行情来源"),
        }
        for source, target in MODEL_COLUMN_MAP.items():
            value = number(row.get(source))
            if value is not None:
                item[target] = value
        result.setdefault(normalized.split(".")[0], []).append({key: value for key, value in item.items() if value not in (None, "")})
    return result


def run_checked(command: list[str], *, timeout: int, label: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=str(PROJECT_ROOT),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "unknown error").strip()
        raise RuntimeError(f"{label}失败：{detail}")
    return completed


def find_research_dir(code: str, as_of: date) -> Path | None:
    date_dir = DEEP_OUTPUT_ROOT / as_of.strftime("%Y%m%d")
    matches = []
    if not date_dir.is_dir():
        return None
    for child in date_dir.iterdir():
        raw_path = child / "research_raw.json"
        derived_path = child / "research_derived.json"
        if not raw_path.is_file() or not derived_path.is_file():
            continue
        try:
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        metadata = research_metadata(raw, {})
        raw_code = re.sub(r"\D", "", str(metadata.get("code") or ""))[-6:]
        if raw_code == code.split(".", 1)[0]:
            matches.append(child)
    return max(matches, key=lambda path: (path / "research_derived.json").stat().st_mtime) if matches else None


def research_metadata(raw: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any]:
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    ticker = raw.get("ticker") if isinstance(raw.get("ticker"), dict) else {}
    if not ticker and isinstance(derived.get("ticker"), dict):
        ticker = derived.get("ticker", {})
    code = metadata.get("code") or ticker.get("code")
    return {
        "code": code,
        "futu_code": metadata.get("futu_code") or ticker.get("futu_code") or code,
        "stock_name": metadata.get("stock_name") or ticker.get("name") or code,
        "as_of": metadata.get("as_of") or raw.get("as_of") or derived.get("as_of"),
    }


def build_conclusion(raw: dict[str, Any], derived: dict[str, Any]) -> dict[str, Any]:
    """Create a conservative, auditable conclusion from collected evidence."""
    legacy_score = derived.get("research_score")
    if isinstance(legacy_score, dict) and legacy_score.get("posture"):
        components = legacy_score.get("component_scores", {})
        labels = {
            "model": "模型证据",
            "financial_quality": "财务质量",
            "valuation": "估值",
            "catalyst": "催化剂",
            "technical_flow": "技术与资金",
            "governance_risk": "治理风险",
            "data_confidence": "数据完整度",
        }
        scored = [
            (number(value), labels.get(key, key))
            for key, value in components.items()
            if number(value) is not None
        ]
        strongest = max(scored, default=(None, "可用证据"), key=lambda item: item[0] or 0)
        confirming = f"{strongest[1]}评分 {strongest[0]:.1f}" if strongest[0] is not None else "已有研究证据"
        key_risk = str(derived.get("key_risk") or "关键风险尚未结构化")
        invalidation = str(derived.get("primary_invalidation") or "下一期财务与价格信号同时恶化")
        total_score = number(legacy_score.get("total_score"))
        posture = str(legacy_score.get("posture"))
        confidence = str(legacy_score.get("confidence") or "LOW")
        summary = (
            f"综合评分 {format_number(total_score)}，当前归类为 {posture}。"
            f"最强确认是“{confirming}”，主要矛盾是“{key_risk}”。"
            f"结论置信度为 {confidence}，应以“{invalidation}”作为首要证伪条件。"
        )
        return {
            "posture": posture,
            "total_score": total_score,
            "confidence": confidence,
            "position_band": legacy_score.get("position_band") or "0%（仅观察）",
            "hard_limits": legacy_score.get("hard_limits") or [],
            "strongest_confirming_evidence": confirming,
            "confirming_evidence": [confirming],
            "strongest_contradictory_evidence": key_risk,
            "contradictory_evidence": [key_risk],
            "next_catalyst": derived.get("next_catalyst") or "尚未取得明确日期的重大催化剂",
            "key_risk": key_risk,
            "primary_invalidation": invalidation,
            "summary": summary,
            "evidence_points_available": len(scored),
            "collection_warning_count": len(raw.get("warnings") or []),
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "method": "legacy deterministic conclusion compatibility adapter",
        }

    price = derived.get("price", {})
    indicator = derived.get("latest_financial_indicators", {})
    model = derived.get("latest_model_selection", {})
    fair = derived.get("fair_value", {})
    disclosure = derived.get("latest_disclosure_plan", {})
    warnings = raw.get("warnings", []) if isinstance(raw.get("warnings"), list) else []

    model_score = number(model.get("total_score"))
    roe = number(indicator.get("roe"))
    profit_yoy = number(indicator.get("netprofit_yoy"))
    debt_ratio = number(indicator.get("debt_to_assets"))
    return_20d = number(price.get("return_20d_pct"))
    return_60d = number(price.get("return_60d_pct"))
    drawdown_60d = number(price.get("max_drawdown_60d_pct"))
    base_upside = number(nested(fair, "scenarios", "base", "upside_pct")) if fair.get("available") else None

    score = 50.0
    if model_score is not None:
        score += max(-10.0, min(10.0, (model_score - 60.0) * 0.5))
    if base_upside is not None:
        score += 12 if base_upside >= 20 else 7 if base_upside >= 10 else 2 if base_upside >= 0 else -7 if base_upside <= -10 else -2
    if roe is not None:
        score += 7 if roe >= 15 else 3 if roe >= 8 else -10 if roe < 0 else -2
    if profit_yoy is not None:
        score += 7 if profit_yoy >= 20 else 2 if profit_yoy >= 0 else -10 if profit_yoy <= -30 else -4
    if debt_ratio is not None:
        score += 4 if debt_ratio <= 50 else -8 if debt_ratio >= 75 else 0
    if return_20d is not None:
        score += 4 if 0 <= return_20d <= 20 else -4 if return_20d < -15 or return_20d > 35 else 0
    if drawdown_60d is not None and drawdown_60d <= -30:
        score -= 5
    score = round(max(0.0, min(100.0, score)), 1)

    evidence_flags = [
        bool(price.get("close")),
        bool(indicator),
        bool(derived.get("latest_ashare_fundamentals")),
        bool(model),
        bool(fair.get("available")),
        bool(derived.get("latest_futu_snapshot")),
        bool(raw.get("market_history")),
    ]
    evidence_count = sum(evidence_flags)
    confidence = "HIGH" if evidence_count >= 6 and len(warnings) <= 3 else "MEDIUM" if evidence_count >= 4 else "LOW"

    hard_limits: list[str] = []
    if not price.get("close"):
        hard_limits.append("缺少截止日价格")
    if not indicator:
        hard_limits.append("缺少可用财务指标")
    if not fair.get("available"):
        hard_limits.append("合理估值不可用")
    if debt_ratio is not None and debt_ratio >= 85:
        hard_limits.append("资产负债率过高")
    if profit_yoy is not None and profit_yoy <= -50:
        hard_limits.append("净利润同比大幅下滑")
    extreme_price_move = (
        (return_20d is not None and return_20d <= -30)
        or (drawdown_60d is not None and drawdown_60d <= -40)
    )
    if extreme_price_move:
        hard_limits.append("极端价格波动，需等待企稳确认")

    next_date = disclosure.get("pre_date") or disclosure.get("actual_date") or disclosure.get("ann_date")
    next_catalyst = f"计划财务披露（{next_date}）" if next_date else "尚未取得明确日期的重大催化剂"

    confirmations: list[str] = []
    contradictions: list[str] = []
    if model_score is not None:
        target = confirmations if model_score >= 65 else contradictions
        target.append(f"选股模型得分 {model_score:.1f}")
    if profit_yoy is not None:
        target = confirmations if profit_yoy >= 0 else contradictions
        target.append(f"归母净利润同比 {profit_yoy:+.1f}%")
    if roe is not None:
        target = confirmations if roe >= 10 else contradictions
        target.append(f"ROE {roe:.1f}%")
    if base_upside is not None:
        target = confirmations if base_upside >= 5 else contradictions
        target.append(f"历史相对估值基准空间 {base_upside:+.1f}%")
    if return_20d is not None:
        target = confirmations if 0 <= return_20d <= 25 else contradictions
        target.append(f"20日收益 {return_20d:+.1f}%")
    if drawdown_60d is not None and drawdown_60d <= -20:
        contradictions.append(f"近60日最大回撤 {drawdown_60d:.1f}%")
    if debt_ratio is not None and debt_ratio >= 65:
        contradictions.append(f"资产负债率 {debt_ratio:.1f}%")
    if not confirmations:
        confirmations.append("尚无足够的强确认信号")
    if not contradictions:
        contradictions.append("当前未发现单一压倒性反证，但仍需等待下一期财务数据验证")

    if evidence_count < 3 or not price.get("close"):
        posture = "INSUFFICIENT EVIDENCE"
    elif score < 40 or any(item in hard_limits for item in ("资产负债率过高", "净利润同比大幅下滑")):
        posture = "REJECT-RISK WATCH"
    elif extreme_price_move:
        posture = "TIMING WATCH"
    elif score >= 72 and (base_upside is None or base_upside >= 5) and not hard_limits:
        posture = "CORE CANDIDATE"
    elif next_date and score >= 58:
        posture = "EVENT CANDIDATE"
    else:
        posture = "TIMING WATCH"

    if posture in {"REJECT-RISK WATCH", "INSUFFICIENT EVIDENCE"}:
        position_band = "0%（仅观察）"
    elif confidence == "LOW":
        position_band = "0%–2%（证据补齐前）"
    elif score >= 75:
        position_band = "3%–5%（研究上限，非交易指令）"
    else:
        position_band = "0%–3%（研究上限，非交易指令）"

    if profit_yoy is not None and profit_yoy > 0:
        primary_invalidation = "下一期归母净利润同比转负、核心增长逻辑未兑现，或极端回撤继续扩大且无法企稳"
    elif fair.get("available"):
        bear_value = nested(fair, "scenarios", "bear", "value")
        primary_invalidation = f"价格持续跌破保守估值 {bear_value} 元且基本面同步恶化"
    else:
        primary_invalidation = "下一期财务质量继续恶化，且无法补齐合理估值所需数据"

    key_risk = contradictions[0]
    summary = (
        f"综合评分 {score:.1f}，当前归类为 {posture}。"
        f"最强确认是“{confirmations[0]}”，主要矛盾是“{contradictions[0]}”。"
        f"结论置信度为 {confidence}，应以“{primary_invalidation}”作为首要证伪条件。"
    )
    return {
        "posture": posture,
        "total_score": score,
        "confidence": confidence,
        "position_band": position_band,
        "hard_limits": hard_limits,
        "strongest_confirming_evidence": confirmations[0],
        "confirming_evidence": confirmations,
        "strongest_contradictory_evidence": contradictions[0],
        "contradictory_evidence": contradictions,
        "next_catalyst": next_catalyst,
        "key_risk": key_risk,
        "primary_invalidation": primary_invalidation,
        "summary": summary,
        "evidence_points_available": evidence_count,
        "collection_warning_count": len(warnings),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "method": "deterministic evidence-weighted conclusion v1",
    }


def write_stock_conclusion(
    path: Path,
    raw: dict[str, Any],
    derived: dict[str, Any],
    conclusion: dict[str, Any],
) -> None:
    metadata = research_metadata(raw, derived)
    price = derived.get("price", {})
    indicator = derived.get("latest_financial_indicators", {})
    model = derived.get("latest_model_selection", {})
    fair = derived.get("fair_value", {})
    legacy_evidence = raw.get("evidence", {}) if isinstance(raw.get("evidence"), dict) else {}
    if not indicator:
        legacy_financial = nested(legacy_evidence, "financial_quality", "derived_metrics") or {}
        indicator = {
            "roe": legacy_financial.get("roe"),
            "netprofit_yoy": legacy_financial.get("profit_cagr_3y"),
            "debt_to_assets": legacy_financial.get("debt_to_assets"),
        }
    if not model:
        legacy_model = legacy_evidence.get("model", {}) if isinstance(legacy_evidence.get("model"), dict) else {}
        model = {"total_score": legacy_model.get("score"), "model_name": "股票投资系统模型"}
    scenarios = fair.get("scenarios", {}) if fair.get("available") else {}
    methods = fair.get("methods", {}) if fair.get("available") else {}
    warnings = raw.get("warnings", []) if isinstance(raw.get("warnings"), list) else []

    valuation_text = "合理估值不可用：" + str(fair.get("reason") or "输入不足")
    if fair.get("available"):
        reasonable_range = fair.get("reasonable_value_range") or [
            nested(scenarios, "bear", "value"),
            nested(scenarios, "bull", "value"),
        ]
        valuation_text = (
            f"当前价 {format_number(fair.get('current_price'))} 元；合理区间 "
            f"{format_number(nested(reasonable_range, 0))}–"
            f"{format_number(nested(reasonable_range, 1))} 元；"
            f"保守/基准/乐观 {format_number(nested(scenarios, 'bear', 'value'))} / "
            f"{format_number(nested(scenarios, 'base', 'value'))} / "
            f"{format_number(nested(scenarios, 'bull', 'value'))} 元，"
            f"基准空间 {format_pct(nested(scenarios, 'base', 'upside_pct'))}。"
        )
    method_lines = [
        f"- {item.get('label')}：样本 {item.get('sample_size')} 个交易日，权重 {number(item.get('weight')) * 100:.0f}%"
        for item in methods.values()
        if isinstance(item, dict) and number(item.get("weight")) is not None
    ]
    warning_lines = "\n".join(f"- {item}" for item in warnings[:20]) or "- 无"
    hard_limit_lines = "、".join(conclusion.get("hard_limits", [])) or "无"
    report = f"""# {metadata.get('stock_name') or metadata.get('code')} 深度分析结论

**股票代码：** {metadata.get('futu_code') or metadata.get('code')}

**数据截止：** {metadata.get('as_of')}

**研究姿态：** {conclusion.get('posture')}
**综合评分 / 置信度：** {conclusion.get('total_score')} / {conclusion.get('confidence')}

## 执行结论

{conclusion.get('summary')}

- 研究跟踪权重：{conclusion.get('position_band')}
- 硬性限制：{hard_limit_lines}
- 下一催化：{conclusion.get('next_catalyst')}
- 主要风险：{conclusion.get('key_risk')}
- 首要证伪条件：{conclusion.get('primary_invalidation')}

## 估值结论

{valuation_text}

- 估值日期：{fair.get('valuation_date') or metadata.get('as_of')}
- 估值方法：{fair.get('method') or 'Unavailable'}
- 估值置信度：{fair.get('confidence') or 'Unavailable'}
{chr(10).join(method_lines) if method_lines else '- 估值方法明细：Unavailable'}

## 财务质量

- 报告期：{indicator.get('end_date') or 'Unavailable'}
- ROE：{format_pct(indicator.get('roe'))}
- 毛利率：{format_pct(indicator.get('grossprofit_margin'))}
- 归母净利润同比：{format_pct(indicator.get('netprofit_yoy'))}
- 资产负债率：{format_pct(indicator.get('debt_to_assets'))}

## 行情与交易状态

- 收盘价：{format_number(price.get('close'))}
- 5 / 20 / 60 日收益：{format_pct(price.get('return_5d_pct'))} / {format_pct(price.get('return_20d_pct'))} / {format_pct(price.get('return_60d_pct'))}
- 20日年化波动率：{format_pct(price.get('annualized_vol_20d_pct'))}
- 近60日最大回撤：{format_pct(price.get('max_drawdown_60d_pct'))}

## 模型原始理由

- 模型：{model.get('model_slug') or model.get('model_name') or 'Unavailable'}
- 排名 / 得分：{model.get('rank', 'Unavailable')} / {model.get('total_score', 'Unavailable')}
- 原研究姿态：{model.get('research_posture') or 'Unavailable'}
- 选股理由：{model.get('selection_reason') or 'Unavailable'}

## 多空证据

### 确认证据

{chr(10).join(f'- {item}' for item in conclusion.get('confirming_evidence', []))}

### 反证与矛盾

{chr(10).join(f'- {item}' for item in conclusion.get('contradictory_evidence', []))}

## 数据缺口与采集警告

{warning_lines}

## 方法说明

本结论由可复核规则根据截止日行情、财务、模型与估值数据自动生成。评分用于排序研究优先级，不是收益预测或自动交易指令；历史估值分位也不等于保证目标价。
"""
    path.write_text(report, encoding="utf-8")


def collect_summaries(codes: list[str], rows: list[dict[str, str]], as_of: date) -> list[dict[str, Any]]:
    source_by_code: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        source_by_code.setdefault(normalize_code(row["股票代码"]), []).append(row)
    summaries = []
    for code in codes:
        directory = find_research_dir(code, as_of)
        if directory is None:
            summaries.append({"code": code, "name": source_by_code[code][0].get("股票名称") or code, "error": "未找到研究输出"})
            continue
        derived = json.loads((directory / "research_derived.json").read_text(encoding="utf-8"))
        raw = json.loads((directory / "research_raw.json").read_text(encoding="utf-8"))
        conclusion = build_conclusion(raw, derived)
        write_stock_conclusion(directory / "deep_research.md", raw, derived, conclusion)
        (directory / "research_conclusion.json").write_text(
            json.dumps(conclusion, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        source_rows = source_by_code.get(code, [])
        metadata = research_metadata(raw, derived)
        summaries.append({
            "code": code,
            "name": metadata.get("stock_name") or source_rows[0].get("股票名称") or code,
            "models": "、".join(dict.fromkeys(row.get("模型", "") for row in source_rows if row.get("模型"))),
            "model_max_score": max((number(row.get("总分")) or 0.0 for row in source_rows), default=0.0),
            "directory": directory,
            "derived": derived,
            "raw": raw,
            "conclusion": conclusion,
        })
    return summaries


def summary_columns(summary: dict[str, Any]) -> dict[str, Any]:
    if summary.get("error"):
        return {"深研姿态": "研究失败", "主要风险": summary["error"]}
    derived = summary["derived"]
    score = summary.get("conclusion", {})
    fair = derived.get("fair_value", {})
    fair_scenarios = fair.get("scenarios", {}) if fair.get("available") else {}
    return {
        "深研姿态": score.get("posture"),
        "深研评分": score.get("total_score"),
        "深研置信度": score.get("confidence"),
        "影子仓位": score.get("position_band"),
        "硬性限制": "、".join(score.get("hard_limits", [])) or "无",
        "相对估值保守值": nested(fair_scenarios, "bear", "value"),
        "相对估值基准值": nested(fair_scenarios, "base", "value"),
        "相对估值乐观值": nested(fair_scenarios, "bull", "value"),
        "相对估值基准空间%": nested(fair_scenarios, "base", "upside_pct"),
        "DCF用途": "新版暂不自动生成 DCF；采用可追溯的历史相对估值",
        "下一催化": score.get("next_catalyst"),
        "主要风险": score.get("key_risk"),
        "证伪条件": score.get("primary_invalidation"),
        "深研结论文件": str(summary.get("directory", "") / "deep_research.md") if summary.get("directory") else "",
        "深研目录": str(summary.get("directory", "")),
    }


def write_enriched_csv(
    path: Path,
    fields: list[str],
    rows: list[dict[str, str]],
    summary_by_code: dict[str, dict[str, Any]],
) -> None:
    output_fields = list(dict.fromkeys([*fields, *DEEP_COLUMNS]))
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            item = dict(row)
            summary = summary_by_code.get(normalize_code(row["股票代码"]))
            if summary:
                item.update({key: csv_value(value) for key, value in summary_columns(summary).items()})
            writer.writerow(item)


def write_html(
    path: Path,
    watchlist_path: Path,
    enriched_csv: Path,
    portfolio_path: Path,
    summaries: list[dict[str, Any]],
    generated_at: datetime,
) -> None:
    posture_counts: dict[str, int] = {}
    hard_limit_count = 0
    cards = []
    for summary in summaries:
        columns = summary_columns(summary)
        posture = str(columns.get("深研姿态") or "Unavailable")
        posture_counts[posture] = posture_counts.get(posture, 0) + 1
        if columns.get("硬性限制") not in (None, "", "无"):
            hard_limit_count += 1
        cards.append(render_stock_card(summary, columns))
    candidate_count = sum(posture_counts.get(key, 0) for key in ("CORE CANDIDATE", "TIMING WATCH", "EVENT CANDIDATE"))
    risk_count = sum(posture_counts.get(key, 0) for key in ("REJECT-RISK WATCH", "INSUFFICIENT EVIDENCE", "研究失败"))
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>股票投资系统 · 深度研究汇总</title>
<style>
:root{{--ink:#172238;--muted:#68758b;--line:#dce4ed;--paper:#f4f7fa;--card:#fff;--blue:#1769aa;--green:#14806f;--red:#a23f49;--amber:#a46a13}}
*{{box-sizing:border-box}} body{{margin:0;background:linear-gradient(145deg,#edf4fa,#fafafa 55%,#fff4e4);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
main{{width:min(1440px,calc(100% - 32px));margin:0 auto;padding:32px 0 52px}} h1{{font-size:clamp(30px,4vw,50px);margin:8px 0 10px;letter-spacing:-.035em}} .eyebrow{{font-size:12px;color:var(--blue);font-weight:800;letter-spacing:.12em}} .sub{{color:var(--muted);line-height:1.7}} .stats{{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:12px;margin:24px 0}} .stat,.stock{{background:rgba(255,255,255,.9);border:1px solid var(--line);border-radius:18px;box-shadow:0 12px 32px rgba(40,60,90,.07)}} .stat{{padding:18px}} .stat b{{display:block;font-size:28px}} .stat span{{font-size:12px;color:var(--muted)}} .toolbar{{display:flex;gap:10px;flex-wrap:wrap;margin:0 0 18px}} a.button{{text-decoration:none;color:var(--blue);background:#fff;border:1px solid var(--line);border-radius:10px;padding:9px 12px;font-size:13px}} .grid{{display:grid;gap:14px}} .stock{{padding:20px}} .stock-head{{display:flex;justify-content:space-between;gap:14px;align-items:flex-start}} .stock h2{{margin:0;font-size:21px}} .meta{{font-size:12px;color:var(--muted);margin-top:5px}} .badge{{border-radius:999px;padding:7px 10px;font-size:12px;font-weight:800;white-space:nowrap}} .good{{background:#e7f6f2;color:var(--green)}} .warn{{background:#fff4dc;color:var(--amber)}} .bad{{background:#fdebed;color:var(--red)}} .metrics{{display:grid;grid-template-columns:repeat(5,minmax(130px,1fr));gap:9px;margin:16px 0}} .metric{{padding:11px;background:#f6f9fb;border-radius:11px}} .metric strong{{display:block;font-size:11px;color:var(--muted);margin-bottom:5px}} .metric b{{font-size:17px}} .details{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}} .detail{{font-size:13px;line-height:1.65}} .detail strong{{display:block;color:#435268;margin-bottom:3px}} .links{{margin-top:13px;display:flex;gap:8px;flex-wrap:wrap}} .links a{{font-size:12px;color:var(--blue)}} .notice{{margin-top:20px;padding:14px 16px;border-left:4px solid #efb14f;background:#fff8e9;border-radius:9px;font-size:12px;line-height:1.7;color:#655438}}
@media(max-width:900px){{.stats{{grid-template-columns:1fr 1fr}}.metrics{{grid-template-columns:1fr 1fr}}.details{{grid-template-columns:1fr}}}} @media(max-width:520px){{.stats,.metrics{{grid-template-columns:1fr}}}}
</style></head><body><main>
<div class="eyebrow">STOCK INVESTMENT SYSTEM · DEEP RESEARCH</div><h1>选股与深度研究汇总</h1>
<div class="sub">生成时间：{escape(generated_at.astimezone().isoformat(timespec='seconds'))} · 研究股票 {len(summaries)} 只。原模型负责产生候选，深度研究负责核验财务、估值、风险、催化与证伪条件。</div>
<section class="stats"><div class="stat"><b>{len(summaries)}</b><span>研究股票</span></div><div class="stat"><b>{candidate_count}</b><span>候选/择时观察</span></div><div class="stat"><b>{risk_count}</b><span>拒绝风险/证据不足</span></div><div class="stat"><b>{hard_limit_count}</b><span>存在硬性限制</span></div></section>
<div class="toolbar"><a class="button" href="{file_href(watchlist_path)}">原始选股 CSV</a><a class="button" href="{file_href(enriched_csv)}">含深研结论 CSV</a><a class="button" href="{file_href(portfolio_path)}">组合研究结论</a></div>
<section class="grid">{''.join(cards)}</section>
<div class="notice">本报告是自动生成的研究结论，不是交易指令。合理价值采用可追溯的历史相对估值；硬性限制、证据缺口和证伪条件应优先于综合评分。自动研究失败不会修改原始选股 CSV。</div>
</main></body></html>"""
    path.write_text(document, encoding="utf-8")


def write_portfolio_synthesis(path: Path, summaries: list[dict[str, Any]], generated_at: datetime) -> None:
    posture_counts: dict[str, int] = {}
    industries: dict[str, list[str]] = {}
    risk_counts: dict[str, int] = {}
    rows: list[str] = []
    for summary in summaries:
        conclusion = summary.get("conclusion", {})
        posture = str(conclusion.get("posture") or "研究失败")
        posture_counts[posture] = posture_counts.get(posture, 0) + 1
        raw = summary.get("raw", {})
        model = (raw.get("model_evidence") or [{}])[0]
        industry = str(model.get("industry") or "行业不可用")
        industries.setdefault(industry, []).append(str(summary.get("name") or summary.get("code")))
        risk = str(conclusion.get("key_risk") or summary.get("error") or "风险不可用")
        risk_counts[risk] = risk_counts.get(risk, 0) + 1
        columns = summary_columns(summary)
        rows.append(
            f"| {summary.get('name')} | {summary.get('code')} | {posture} | "
            f"{columns.get('深研评分') or 'Unavailable'} | {columns.get('深研置信度') or 'Unavailable'} | "
            f"{format_number(columns.get('相对估值基准值'))} | {format_pct(columns.get('相对估值基准空间%'))} | "
            f"{conclusion.get('key_risk') or summary.get('error') or 'Unavailable'} |"
        )
    posture_text = "、".join(f"{key} {value}只" for key, value in sorted(posture_counts.items())) or "无"
    concentration = sorted(industries.items(), key=lambda item: (-len(item[1]), item[0]))
    concentration_lines = "\n".join(
        f"- {industry}：{len(names)}只（{'、'.join(names)}）" for industry, names in concentration
    ) or "- 行业数据不可用"
    repeated_risks = sorted(risk_counts.items(), key=lambda item: (-item[1], item[0]))[:5]
    risk_lines = "\n".join(f"- {risk}（{count}只）" for risk, count in repeated_risks) or "- 风险数据不可用"
    path.write_text(
        f"""# 股票投资系统 · 组合深度研究结论

**生成时间：** {generated_at.astimezone().isoformat(timespec='seconds')}

**覆盖股票：** {len(summaries)}只
**姿态分布：** {posture_text}

## 逐股结论

| 股票 | 代码 | 深研姿态 | 评分 | 置信度 | 基准估值 | 基准空间 | 主要风险 |
|---|---|---:|---:|---:|---:|---:|---|
{chr(10).join(rows)}

## 行业集中度

{concentration_lines}

## 共同风险

{risk_lines}

## 使用说明

该汇总用于比较研究优先级与证据强弱，不构成组合配置或自动交易指令。行业集中、相同催化日期和相同风险来源会放大组合相关性，应在实际决策前单独复核。
""",
        encoding="utf-8",
    )


def write_summary_csv(path: Path, summaries: list[dict[str, Any]]) -> None:
    fields = [
        "code", "name", "models", "research_posture", "research_score", "confidence",
        "fair_value_bear", "fair_value_base", "fair_value_bull", "fair_value_base_upside_pct",
        "next_catalyst", "key_risk", "primary_invalidation", "deep_research_file",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for summary in summaries:
            columns = summary_columns(summary)
            directory = summary.get("directory")
            writer.writerow({
                "code": summary.get("code"),
                "name": summary.get("name"),
                "models": summary.get("models"),
                "research_posture": columns.get("深研姿态"),
                "research_score": columns.get("深研评分"),
                "confidence": columns.get("深研置信度"),
                "fair_value_bear": columns.get("相对估值保守值"),
                "fair_value_base": columns.get("相对估值基准值"),
                "fair_value_bull": columns.get("相对估值乐观值"),
                "fair_value_base_upside_pct": columns.get("相对估值基准空间%"),
                "next_catalyst": columns.get("下一催化"),
                "key_risk": columns.get("主要风险"),
                "primary_invalidation": columns.get("证伪条件"),
                "deep_research_file": str(directory / "deep_research.md") if isinstance(directory, Path) else "",
            })


def render_stock_card(summary: dict[str, Any], columns: dict[str, Any]) -> str:
    posture = str(columns.get("深研姿态") or "Unavailable")
    badge_class = "good" if posture in {"CORE CANDIDATE", "TIMING WATCH", "EVENT CANDIDATE"} else "bad" if posture in {"REJECT-RISK WATCH", "INSUFFICIENT EVIDENCE", "研究失败"} else "warn"
    fair_range = value_range(columns.get("相对估值保守值"), columns.get("相对估值基准值"), columns.get("相对估值乐观值"))
    dcf_range = value_range(columns.get("DCF保守值"), columns.get("DCF基准值"), columns.get("DCF乐观值"))
    directory = summary.get("directory")
    links = ""
    if isinstance(directory, Path):
        links = f'<div class="links"><a href="{file_href(directory / "deep_research.md")}">深度分析结论</a><a href="{file_href(directory / "research_brief.md")}">研究简报</a><a href="{file_href(directory / "research_derived.json")}">派生数据</a><a href="{file_href(directory / "research_raw.json")}">原始证据</a></div>'
    metrics = [
        ("模型最高分", format_number(summary.get("model_max_score"))),
        ("深研评分", format_number(columns.get("深研评分"))),
        ("相对估值 保/基/乐", fair_range),
        ("相对估值基准空间", format_pct(columns.get("相对估值基准空间%"))),
        ("研究结论", escape((summary.get("conclusion") or {}).get("confidence") or "Unavailable")),
    ]
    metric_html = "".join(f'<div class="metric"><strong>{escape(label)}</strong><b>{value}</b></div>' for label, value in metrics)
    return f"""<article class="stock"><div class="stock-head"><div><h2>{escape(summary.get('name'))} <small>{escape(summary.get('code'))}</small></h2><div class="meta">来源模型：{escape(summary.get('models') or 'Unavailable')} · 深研置信度 {escape(columns.get('深研置信度') or 'Unavailable')} · 影子仓位 {escape(columns.get('影子仓位') or 'Unavailable')}</div></div><span class="badge {badge_class}">{escape(posture)}</span></div>
<div class="metrics">{metric_html}</div><div class="details"><div class="detail"><strong>下一催化</strong>{escape(columns.get('下一催化') or 'Unavailable')}</div><div class="detail"><strong>主要风险</strong>{escape(columns.get('主要风险') or 'Unavailable')}</div><div class="detail"><strong>证伪条件</strong>{escape(columns.get('证伪条件') or 'Unavailable')}</div></div>{links}</article>"""


def number(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def nested(value: Any, *keys: Any) -> Any:
    current: Any = value
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return None
    return current


def csv_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def escape(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def format_number(value: Any) -> str:
    numeric = number(value)
    return f"{numeric:.2f}" if numeric is not None else "Unavailable"


def format_pct(value: Any) -> str:
    numeric = number(value)
    return f"{numeric:+.1f}%" if numeric is not None else "Unavailable"


def value_range(bear: Any, base: Any, bull: Any) -> str:
    values = [number(value) for value in (bear, base, bull)]
    if any(value is None for value in values):
        return "Unavailable"
    return " / ".join(f"{value:.2f}" for value in values if value is not None)


def file_href(path: Path) -> str:
    return "file://" + quote(str(path.resolve()))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Auto-run deep research for a stock-system watchlist CSV")
    parser.add_argument("--watchlist-csv", type=Path, required=True)
    parser.add_argument("--timestamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--horizon", choices=["SHORT", "MEDIUM", "LONG"], default="MEDIUM")
    parser.add_argument("--max-stocks", type=int, default=0, help="0 researches all unique stocks")
    parser.add_argument("--open-report", action="store_true", help="Open the completed HTML report on macOS")
    parser.add_argument(
        "--reuse-existing",
        action="store_true",
        help="Reuse same-date raw/derived files instead of collecting again (diagnostics and recovery)",
    )
    parser.add_argument("--skip-futu", action="store_true", help="Pass --skip-futu to the collector")
    parser.add_argument("--skip-asharehub", action="store_true", help="Pass --skip-asharehub to the collector")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    watchlist_path = args.watchlist_csv.resolve()
    fields, rows = parse_watchlist(watchlist_path)
    codes = select_codes(rows, args.max_stocks)
    timestamp_date = re.match(r"(\d{8})", str(args.timestamp))
    today = (
        datetime.strptime(timestamp_date.group(1), "%Y%m%d").date()
        if timestamp_date
        else date.today()
    )
    date_dir = DEEP_OUTPUT_ROOT / today.strftime("%Y%m%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    model_rows_path = date_dir / f"model_rows_{args.timestamp}.json"
    model_rows_path.write_text(json.dumps(build_model_rows(rows, set(codes)), ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.reuse_existing:
        if not RESEARCH_COLLECTOR.is_file():
            raise FileNotFoundError(f"深度研究采集器不存在：{RESEARCH_COLLECTOR}")
        collector_command = [
            str(RESEARCH_RUNTIME),
            str(RESEARCH_COLLECTOR),
            *(code.split(".", 1)[0] for code in codes),
            "--output-dir",
            str(date_dir),
            "--as-of",
            today.isoformat(),
            "--language",
            "zh-CN",
        ]
        if args.skip_futu:
            collector_command.append("--skip-futu")
        if args.skip_asharehub:
            collector_command.append("--skip-asharehub")
        collection_started_at = datetime.now().timestamp()
        completed = subprocess.run(
            collector_command,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=3600,
            check=False,
        )
        if completed.stdout:
            print(completed.stdout.rstrip())
        if completed.returncode != 0:
            updated: list[str] = []
            for code in codes:
                directory = find_research_dir(code, today)
                raw_path = directory / "research_raw.json" if directory else None
                if raw_path and raw_path.stat().st_mtime >= collection_started_at:
                    updated.append(code)
            if not updated:
                detail = (completed.stderr or completed.stdout or "unknown error").strip()
                raise RuntimeError(f"深研数据采集失败：{detail}")
            print(
                f"警告：采集器部分失败，本次已更新 {len(updated)}/{len(codes)} 只股票的研究文件；继续生成汇总。",
                file=sys.stderr,
            )

    summaries = collect_summaries(codes, rows, today)
    summary_by_code = {summary["code"]: summary for summary in summaries}
    base = watchlist_path.stem
    enriched_csv = watchlist_path.with_name(f"{base}_深度研究.csv")
    html_path = watchlist_path.with_name(f"{base}_深度研究.html")
    portfolio_path = date_dir / f"portfolio_synthesis_{args.timestamp}.md"
    summary_csv = date_dir / f"deep_research_summary_{args.timestamp}.csv"
    generated_at = datetime.now()
    write_enriched_csv(enriched_csv, fields, rows, summary_by_code)
    write_portfolio_synthesis(portfolio_path, summaries, generated_at)
    write_portfolio_synthesis(date_dir / "portfolio_synthesis.md", summaries, generated_at)
    write_summary_csv(summary_csv, summaries)
    write_summary_csv(date_dir / "deep_research_summary.csv", summaries)
    write_html(html_path, watchlist_path, enriched_csv, portfolio_path, summaries, generated_at)
    print(str(html_path))
    if args.open_report:
        subprocess.run(["/usr/bin/open", str(html_path)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
