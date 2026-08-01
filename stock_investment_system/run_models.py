from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import json
from pathlib import Path
import sys

import pandas as pd

from .config import SelectionConfig
from .futu_client import FutuClient
from .models import event_flow_confirmation, industry_flow_quality, quality_growth


MODELS = {
    "quality": quality_growth,
    "industry": industry_flow_quality,
    "event": event_flow_confirmation,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run stock investment watchlist models.")
    parser.add_argument("--model", choices=["all", *MODELS.keys()], default="all")
    parser.add_argument("--data-csv", help="Candidate universe CSV. Defaults to langchao_candidates.csv.")
    parser.add_argument("--sample-data", action="store_true", help="Use the built-in demo data instead of the project candidate CSV.")
    parser.add_argument("--max-watchlist", type=int, default=20)
    parser.add_argument("--format", choices=["json", "markdown", "csv"], default="markdown")
    parser.add_argument("--refresh-quotes", action="store_true", help="Refresh quote fields from Futu OpenD before scoring.")
    args = parser.parse_args()

    config = SelectionConfig(max_watchlist=args.max_watchlist)
    default_csv = Path(__file__).with_name("langchao_candidates.csv")
    if args.sample_data:
        client = FutuClient.from_sample()
    elif args.data_csv:
        client = FutuClient.from_csv(args.data_csv)
    elif default_csv.exists():
        client = FutuClient.from_csv(default_csv)
    else:
        client = FutuClient.from_sample()
        client.warn(f"Default candidate CSV not found: {default_csv}; using built-in sample data.")
    if args.refresh_quotes:
        with redirect_stdout(sys.stderr):
            client.refresh_market_snapshot()
    selected = MODELS.values() if args.model == "all" else [MODELS[args.model]]

    results = [module.run(client, config) for module in selected]
    if args.format == "json":
        print(json.dumps([result.to_dict() for result in results], ensure_ascii=False, indent=2))
    elif args.format == "csv":
        sys.stdout.write("\ufeff")
        sys.stdout.write(_format_csv(results))
        for result in results:
            if result.watchlist.empty:
                print(f"[{result.model_name}] no candidates after all gates; check event/flow data availability.", file=sys.stderr)
            for warning in result.warnings:
                print(f"[{result.model_name}] warning: {warning}", file=sys.stderr)
    else:
        for result in results:
            print(f"\n## {result.model_name}\n")
            print(_format_table(result.watchlist))
            if result.warnings:
                print("\nWarnings:")
                for warning in result.warnings:
                    print(f"- {warning}")
    client.close()
    return 0


def _format_table(df):
    try:
        return df.to_markdown(index=False)
    except ImportError:
        return df.to_string(index=False)


MODEL_NAME_CN = {
    "Model 1: Quality Plus Growth": "质量成长",
    "Model 2: Industry Flow Plus Quality": "行业轮动",
    "Model 3: Event Plus Flow Confirmation": "事件资金确认",
}


COLUMN_CN = {
    "model": "模型",
    "code": "股票代码",
    "name": "股票名称",
    "bucket": "分层",
    "total_score": "总分",
    "fundamental_quality_score": "基本面质量分",
    "growth_quality_score": "成长质量分",
    "valuation_score": "估值分",
    "price_volume_score": "量价分",
    "stock_quality_blend": "个股质量综合分",
    "industry_strength_score": "行业强度分",
    "industry_net_flow": "行业净流入",
    "industry_pct_change": "行业涨跌幅",
    "event_score": "事件分",
    "expectation_score": "预期分",
    "catalyst_score": "催化分",
    "capital_flow_score": "资金流分",
    "flow_acceleration_score": "资金加速度分",
    "flow_net": "当日净流入",
    "flow_net_5d": "5日净流入",
    "flow_net_10d": "10日净流入",
    "flow_net_20d": "20日净流入",
    "large_order_net_20d": "20日大单净流入",
    "flow_positive_ratio": "资金流入占比",
    "flow_positive_ratio_20d": "20日资金流入占比",
    "flow_days": "资金统计天数",
    "rotation_state": "轮动状态",
    "research_posture": "研究姿态",
    "entry_position_state": "入场位置状态",
    "entry_action": "入场动作",
    "entry_position_score": "入场位置分",
    "rotation_leader_score": "轮动龙头分",
    "rotation_durability_score": "轮动持续性分",
    "rotation_heat_score": "轮动热度分",
    "valuation_percentile": "估值分位",
    "industry": "行业",
    "latest_price": "最新价",
    "pe_dynamic": "动态市盈率",
    "pb": "市净率",
    "turnover_amount": "成交额",
    "float_market_cap": "流通市值",
    "risk_flags": "风险标记",
    "price_source": "行情来源",
    "selection_reason": "选择理由",
}


VALUE_CN = {
    "core": "核心",
    "satellite": "卫星",
    "confirmed_rotation_leader": "轮动龙头确认",
    "crowded_rotation_leader": "轮动过热龙头",
    "exhaustion_risk": "过热衰竭风险",
    "improving_rotation": "轮动改善",
    "neutral": "中性",
    "active_research": "重点研究",
    "watch_for_pullback": "等待回调",
    "avoid_chasing": "避免追高",
    "track": "跟踪",
    "watch": "观察",
    "constructive_entry_zone": "较适合建仓区",
    "base_building": "筑底观察",
    "improving_watch": "改善观察",
    "extended_or_unclear": "偏高或不清晰",
    "research_entry_plan": "制定入场计划",
    "wait_for_breakout_or_pullback": "等待突破或回调",
    "monitor": "继续观察",
    "avoid_new_entry": "暂不新开仓",
    "sample": "样例数据",
    "csv": "CSV文件",
    "live_futu_snapshot": "富途实时快照",
}


REASON_CN = {
    "strong quality": "质量较强",
    "growth improving": "成长改善",
    "valuation acceptable": "估值可接受",
    "market trend/liquidity supportive": "趋势和流动性支持",
    "dividend record": "有分红记录",
    "buyback context": "有回购背景",
    "balanced quality-growth profile": "质量成长较均衡",
    "derived industry strength positive": "行业强度较好",
    "stock flow supports rotation": "个股资金支持轮动",
    "stock quality supports rotation": "个股质量支持轮动",
    "price/volume confirms": "量价确认",
    "quality candidate in acceptable Futu-derived industry context": "富途推导行业环境可接受的质量候选",
    "earnings/event context visible": "业绩或事件催化可见",
    "analyst consensus support": "分析师预期支持",
    "persistent Futu stock-level flow confirms": "个股持续资金流确认",
    "10d main inflow positive": "10日主力净流入为正",
    "20d main inflow positive": "20日主力净流入为正",
    "quality gate supportive": "质量门槛支持",
    "event/flow candidate requiring confirmation": "事件资金候选，仍需确认",
}


def _format_csv(results) -> str:
    frames = []
    for result in results:
        frame = result.watchlist.copy()
        frame.insert(0, "model", MODEL_NAME_CN.get(result.model_name, result.model_name))
        frames.append(frame)
    if not frames:
        return ""

    combined = pd.concat(frames, ignore_index=True)
    combined = _localize_values(combined)
    combined = combined.rename(columns={column: COLUMN_CN.get(column, column) for column in combined.columns})
    return combined.to_csv(index=False, lineterminator="\n")


def _localize_values(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for column in ["bucket", "rotation_state", "research_posture", "entry_position_state", "entry_action", "price_source"]:
        if column in out:
            out[column] = out[column].map(lambda value: VALUE_CN.get(value, value))
    if "selection_reason" in out:
        out["selection_reason"] = out["selection_reason"].map(_translate_reason)
    return out


def _translate_reason(value) -> str:
    text = "" if pd.isna(value) else str(value)
    parts = [part.strip() for part in text.split(",") if part.strip()]
    translated = []
    for part in parts:
        translated.append(REASON_CN.get(part, VALUE_CN.get(part, part)))
    return "，".join(translated)


if __name__ == "__main__":
    raise SystemExit(main())
