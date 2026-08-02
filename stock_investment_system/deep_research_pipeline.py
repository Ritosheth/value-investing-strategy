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
EVIDENCE_COLLECTOR = DEEP_RESEARCH_DIR / "scripts" / "collect_futu_evidence.py"
RESEARCH_COLLECTOR = DEEP_RESEARCH_DIR / "scripts" / "collect_deep_research_data.py"
RUNTIME = SYSTEM_DIR / "env.sh"
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
        derived_path = child / "research_derived.json"
        if not derived_path.is_file():
            continue
        try:
            derived = json.loads(derived_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ticker = derived.get("ticker", {}) if isinstance(derived, dict) else {}
        if isinstance(ticker, dict) and ticker.get("code") == code:
            matches.append(child)
    return max(matches, key=lambda path: (path / "research_derived.json").stat().st_mtime) if matches else None


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
        source_rows = source_by_code.get(code, [])
        summaries.append({
            "code": code,
            "name": derived.get("ticker", {}).get("name") or source_rows[0].get("股票名称") or code,
            "models": "、".join(dict.fromkeys(row.get("模型", "") for row in source_rows if row.get("模型"))),
            "model_max_score": max((number(row.get("总分")) or 0.0 for row in source_rows), default=0.0),
            "directory": directory,
            "derived": derived,
        })
    return summaries


def summary_columns(summary: dict[str, Any]) -> dict[str, Any]:
    if summary.get("error"):
        return {"深研姿态": "研究失败", "主要风险": summary["error"]}
    derived = summary["derived"]
    score = derived.get("research_score", {})
    fair = derived.get("fair_value", {})
    dcf = derived.get("dcf_valuation", {})
    fair_scenarios = fair.get("scenarios", {}) if fair.get("available") else {}
    dcf_scenarios = dcf.get("scenarios", {}) if dcf.get("available") else {}
    implied = fair.get("current_price_implied_basis", {})
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
        "现价所需财务增幅%": implied.get("required_growth_pct") if implied.get("available") else None,
        "DCF用途": dcf.get("valuation_role") if dcf.get("available") else dcf.get("reason"),
        "DCF置信度": dcf.get("confidence"),
        "DCF保守值": nested(dcf_scenarios, "bear", "value"),
        "DCF基准值": nested(dcf_scenarios, "base", "value"),
        "DCF乐观值": nested(dcf_scenarios, "bull", "value"),
        "DCF基准空间%": nested(dcf_scenarios, "base", "upside_pct"),
        "下一催化": derived.get("next_catalyst"),
        "主要风险": derived.get("key_risk"),
        "证伪条件": derived.get("primary_invalidation"),
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
<div class="toolbar"><a class="button" href="{file_href(watchlist_path)}">原始选股 CSV</a><a class="button" href="{file_href(enriched_csv)}">含深研结论 CSV</a></div>
<section class="grid">{''.join(cards)}</section>
<div class="notice">本报告是自动生成的研究底稿，不是交易指令。低置信度 DCF 仅作现金流底值；硬性限制、证据缺口和证伪条件应优先于综合评分。自动研究失败不会修改原始选股 CSV。</div>
</main></body></html>"""
    path.write_text(document, encoding="utf-8")


def render_stock_card(summary: dict[str, Any], columns: dict[str, Any]) -> str:
    posture = str(columns.get("深研姿态") or "Unavailable")
    badge_class = "good" if posture in {"CORE CANDIDATE", "TIMING WATCH", "EVENT CANDIDATE"} else "bad" if posture in {"REJECT-RISK WATCH", "INSUFFICIENT EVIDENCE", "研究失败"} else "warn"
    fair_range = value_range(columns.get("相对估值保守值"), columns.get("相对估值基准值"), columns.get("相对估值乐观值"))
    dcf_range = value_range(columns.get("DCF保守值"), columns.get("DCF基准值"), columns.get("DCF乐观值"))
    directory = summary.get("directory")
    links = ""
    if isinstance(directory, Path):
        links = f'<div class="links"><a href="{file_href(directory / "research_brief.md")}">研究简报</a><a href="{file_href(directory / "research_derived.json")}">派生数据</a><a href="{file_href(directory / "research_raw.json")}">原始证据</a></div>'
    metrics = [
        ("模型最高分", format_number(summary.get("model_max_score"))),
        ("深研评分", format_number(columns.get("深研评分"))),
        ("相对估值 保/基/乐", fair_range),
        ("相对估值基准空间", format_pct(columns.get("相对估值基准空间%"))),
        ("DCF用途/基准值", f"{escape(columns.get('DCF用途') or 'Unavailable')} · {format_number(columns.get('DCF基准值'))}"),
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


def nested(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
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
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    watchlist_path = args.watchlist_csv.resolve()
    fields, rows = parse_watchlist(watchlist_path)
    codes = select_codes(rows, args.max_stocks)
    today = date.today()
    date_dir = DEEP_OUTPUT_ROOT / today.strftime("%Y%m%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    model_rows_path = date_dir / f"model_rows_{args.timestamp}.json"
    evidence_path = date_dir / f"evidence_bundle_{args.timestamp}.json"
    model_rows_path.write_text(json.dumps(build_model_rows(rows, set(codes)), ensure_ascii=False, indent=2), encoding="utf-8")

    run_checked(
        [str(RUNTIME), str(EVIDENCE_COLLECTOR), *codes, "--output", str(evidence_path), "--model-rows-json", str(model_rows_path)],
        timeout=2400,
        label="Futu/OpenD 深研证据采集",
    )
    run_checked(
        [str(RUNTIME), str(RESEARCH_COLLECTOR), *codes, "--output-root", str(DEEP_OUTPUT_ROOT), "--as-of", today.isoformat(), "--horizon", args.horizon, "--language", "zh-CN", "--evidence-json", str(evidence_path)],
        timeout=900,
        label="深研结论生成",
    )

    summaries = collect_summaries(codes, rows, today)
    summary_by_code = {summary["code"]: summary for summary in summaries}
    base = watchlist_path.stem
    enriched_csv = watchlist_path.with_name(f"{base}_深度研究.csv")
    html_path = watchlist_path.with_name(f"{base}_深度研究.html")
    write_enriched_csv(enriched_csv, fields, rows, summary_by_code)
    write_html(html_path, watchlist_path, enriched_csv, summaries, datetime.now())
    print(str(html_path))
    if args.open_report:
        subprocess.run(["/usr/bin/open", str(html_path)], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
