from __future__ import annotations

from typing import Iterable, Mapping

import pandas as pd

from .config import SelectionConfig
from .futu_client import FutuClient
from .parameters import weighted_score
from .utils import safe_merge


def build_quality_base(client: FutuClient, config: SelectionConfig) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    raw = client.market_candidates(config)
    if raw.empty:
        return _empty_base(), pd.DataFrame(), {"initial_candidates": 0}

    scored = raw.copy()
    _ensure_identity_columns(scored)
    _ensure_numeric(scored, ["latest_price", "pe_dynamic", "pb", "turnover_amount", "float_market_cap", "pct_change_20d"])
    _ensure_quality_scores(scored)
    _ensure_risk_flags(scored)

    rejected_parts: list[pd.DataFrame] = []
    risk_mask = scored["risk_flags"].astype(str).apply(
        lambda value: any(keyword and keyword in value for keyword in config.risk_keywords)
    )
    if risk_mask.any():
        rejected = scored[risk_mask].copy()
        rejected["reject_reason"] = "risk_flag"
        rejected_parts.append(rejected)
    scored = scored[~risk_mask].copy()

    liquidity_mask = scored["turnover_amount"] >= float(config.min_turnover_amount)
    cap_mask = scored["float_market_cap"] >= float(config.min_float_market_cap)
    if (~(liquidity_mask & cap_mask)).any():
        rejected = scored[~(liquidity_mask & cap_mask)].copy()
        rejected["reject_reason"] = "liquidity_or_market_cap"
        rejected_parts.append(rejected)
    scored = scored[liquidity_mask & cap_mask].copy()

    scored["quality_total_score"] = weighted_score(
        scored,
        {
            "fundamental_quality_score": 0.35,
            "growth_quality_score": 0.35,
            "valuation_score": 0.20,
            "price_volume_score": 0.10,
        },
    ).round(2)
    scored["bucket"] = scored["quality_total_score"].map(lambda value: "core" if value >= 65 else "satellite")

    rejected_df = pd.concat(rejected_parts, ignore_index=True) if rejected_parts else pd.DataFrame()
    return scored, rejected_df, {"initial_candidates": len(raw)}


def recompute_quality_total(
    scored: pd.DataFrame,
    *,
    weights: Mapping[str, float] | Iterable[tuple[str, float]],
) -> pd.DataFrame:
    out = scored.copy()
    out["quality_total_score"] = weighted_score(out, weights).round(2)
    out["bucket"] = out["quality_total_score"].map(lambda value: "core" if value >= 65 else "satellite")
    return out


def enrich_valuation(client: FutuClient, scored: pd.DataFrame, *, count: int, rank_col: str = "quality_total_score") -> pd.DataFrame:
    out = scored.copy()
    codes = _top_codes(out, count, rank_col)
    valuation = client.valuation(codes)
    out = safe_merge(out, valuation, on="code", suffix="_valuation")

    # Prefer fresh PE/PB values from the enrichment source.  Previously the
    # merge created pe_dynamic_valuation/pb_valuation, while the original
    # valuation_score survived unchanged, so the displayed valuation
    # percentile and the score could describe different data.
    fresh_valuation_mask = pd.Series(False, index=out.index)
    for column in ("pe_dynamic", "pb"):
        fresh_column = f"{column}_valuation"
        if fresh_column not in out:
            continue
        fresh = pd.to_numeric(out[fresh_column], errors="coerce")
        existing = pd.to_numeric(out.get(column, pd.Series(pd.NA, index=out.index)), errors="coerce")
        out[column] = fresh.combine_first(existing)
        fresh_valuation_mask |= fresh.notna()

    out["valuation_percentile"] = _valuation_percentile(out).round(2)
    derived_score = (100 - out["valuation_percentile"]).clip(0, 100)
    if "valuation_score" not in out:
        out["valuation_score"] = derived_score
    else:
        existing_score = pd.to_numeric(out["valuation_score"], errors="coerce")
        out["valuation_score"] = existing_score.where(existing_score.notna(), derived_score)
        out["valuation_score"] = derived_score.where(fresh_valuation_mask, out["valuation_score"])
    out["valuation_score"] = pd.to_numeric(out["valuation_score"], errors="coerce").fillna(50.0).clip(0, 100).round(2)
    return out


def enrich_corporate_actions(
    client: FutuClient,
    scored: pd.DataFrame,
    *,
    count: int,
    rank_col: str = "quality_total_score",
) -> pd.DataFrame:
    out = scored.copy()
    actions = client.corporate_actions(_top_codes(out, count, rank_col))
    out = safe_merge(out, actions, on="code", suffix="_actions")
    for column, default in [("has_dividend", False), ("has_buyback", False), ("shareholder_return_score", 0.0)]:
        if column not in out:
            out[column] = default
    out["shareholder_return_score"] = pd.to_numeric(out["shareholder_return_score"], errors="coerce").fillna(0.0).clip(0, 100)
    return out


def enrich_flow(client: FutuClient, scored: pd.DataFrame, *, count: int, rank_col: str) -> pd.DataFrame:
    out = scored.copy()
    flow = client.capital_flow(_top_codes(out, count, rank_col))
    out = safe_merge(out, flow, on="code", suffix="_flow")
    if "flow_data_available" not in out:
        available = pd.Series(False, index=out.index)
        if "flow_days" in out:
            available |= pd.to_numeric(out["flow_days"], errors="coerce").fillna(0).gt(0)
        for column in ["flow_net", "flow_net_5d", "flow_net_10d", "flow_net_20d"]:
            if column in out:
                available |= pd.to_numeric(out[column], errors="coerce").notna()
        out["flow_data_available"] = available
    for column in [
        "flow_net",
        "flow_net_5d",
        "flow_net_10d",
        "flow_net_20d",
        "large_order_net_20d",
        "flow_positive_ratio",
        "flow_positive_ratio_20d",
        "flow_days",
    ]:
        if column not in out:
            out[column] = 0.0
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0.0)
    out["capital_flow_score"] = _capital_flow_score(out)
    out["flow_acceleration_score"] = _flow_acceleration_score(out)
    return out


def enrich_events(client: FutuClient, scored: pd.DataFrame, *, count: int, rank_col: str) -> pd.DataFrame:
    out = scored.copy()
    events = client.events(_top_codes(out, count, rank_col))
    out = safe_merge(out, events, on="code", suffix="_events")
    for column in ["event_score", "expectation_score"]:
        if column not in out:
            out[column] = 0.0
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0.0).clip(0, 100)
    return out


def derive_industry_strength(scored: pd.DataFrame) -> pd.DataFrame:
    if scored.empty or "industry" not in scored:
        return pd.DataFrame(columns=["industry", "industry_strength_score", "industry_net_flow", "industry_pct_change"])
    work = scored.copy()
    for column in ["capital_flow_score", "price_volume_score", "flow_net_20d", "pct_change_20d"]:
        if column not in work:
            work[column] = 0.0
        work[column] = pd.to_numeric(work[column], errors="coerce").fillna(0.0)
    grouped = work.groupby("industry", dropna=False).agg(
        industry_net_flow=("flow_net_20d", "sum"),
        industry_pct_change=("pct_change_20d", "mean"),
        _flow=("capital_flow_score", "mean"),
        _momentum=("price_volume_score", "mean"),
    )
    grouped["industry_strength_score"] = (grouped["_flow"] * 0.55 + grouped["_momentum"] * 0.45).clip(0, 100).round(2)
    return grouped.reset_index()[["industry", "industry_strength_score", "industry_net_flow", "industry_pct_change"]]


def add_rotation_overlay(scored: pd.DataFrame) -> pd.DataFrame:
    out = scored.copy()
    for column, default in [
        ("industry_strength_score", 50.0),
        ("capital_flow_score", 50.0),
        ("price_volume_score", 50.0),
        ("flow_acceleration_score", 50.0),
    ]:
        if column not in out:
            out[column] = default
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(default)
    out["rotation_leader_score"] = (
        out["industry_strength_score"] * 0.35 + out["capital_flow_score"] * 0.35 + out["price_volume_score"] * 0.30
    ).clip(0, 100).round(2)
    out["rotation_durability_score"] = (
        out["industry_strength_score"] * 0.45 + out["flow_acceleration_score"] * 0.25 + out["fundamental_quality_score"] * 0.30
    ).clip(0, 100).round(2)
    out["rotation_heat_score"] = (out["price_volume_score"] * 0.60 + out["flow_acceleration_score"] * 0.40).clip(0, 100).round(2)
    out["rotation_state"] = out.apply(_rotation_state, axis=1)
    out["research_posture"] = out["rotation_state"].map(_research_posture)
    return out


def add_entry_position_context(scored: pd.DataFrame) -> pd.DataFrame:
    out = scored.copy()
    for column, default in [("valuation_score", 50.0), ("price_volume_score", 50.0), ("rotation_heat_score", 50.0)]:
        if column not in out:
            out[column] = default
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(default)
    out["entry_position_score"] = (
        out["valuation_score"] * 0.40 + out["price_volume_score"] * 0.35 + (100 - out["rotation_heat_score"]) * 0.25
    ).clip(0, 100).round(2)
    out["entry_position_state"] = out["entry_position_score"].map(_entry_state)
    out["entry_action"] = out["entry_position_state"].map(_entry_action)
    return out


def _ensure_identity_columns(df: pd.DataFrame) -> None:
    if "futu_code" not in df and "code" in df:
        df["futu_code"] = df["code"]
    if "code" not in df and "futu_code" in df:
        df["code"] = df["futu_code"].astype(str).str.split(".").str[-1]
    if "code" in df:
        df["code"] = df["code"].astype(str).str.strip()
    if "futu_code" in df:
        df["futu_code"] = df["futu_code"].astype(str).str.strip()
    if "name" not in df:
        df["name"] = df["code"]
    if "industry" not in df:
        df["industry"] = "unknown"


def _ensure_numeric(df: pd.DataFrame, columns: list[str]) -> None:
    for column in columns:
        if column not in df:
            df[column] = 0.0
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0)


def _ensure_quality_scores(df: pd.DataFrame) -> None:
    if "fundamental_quality_score" not in df:
        roe = _numeric_column(df, "roe")
        df["fundamental_quality_score"] = (50 + roe * 2).clip(0, 100)
    if "growth_quality_score" not in df:
        growth_column = "revenue_growth" if "revenue_growth" in df else "pct_change_20d"
        growth = _numeric_column(df, growth_column)
        df["growth_quality_score"] = (50 + growth).clip(0, 100)
    if "valuation_score" not in df:
        df["valuation_score"] = (100 - _valuation_percentile(df)).clip(0, 100)
    if "price_volume_score" not in df:
        change = pd.to_numeric(df.get("pct_change_20d", 0.0), errors="coerce").fillna(0.0)
        turnover = pd.to_numeric(df.get("turnover_amount", 0.0), errors="coerce").fillna(0.0)
        df["price_volume_score"] = (50 + change + turnover.rank(pct=True) * 25).clip(0, 100)
    for column in ["fundamental_quality_score", "growth_quality_score", "valuation_score", "price_volume_score"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0).clip(0, 100)


def _ensure_risk_flags(df: pd.DataFrame) -> None:
    if "risk_flags" not in df:
        df["risk_flags"] = ""
    df["risk_flags"] = df["risk_flags"].fillna("").astype(str)


def _numeric_column(df: pd.DataFrame, column: str, default: float = 0.0) -> pd.Series:
    if column not in df:
        return pd.Series(default, index=df.index, dtype="float64")
    return pd.to_numeric(df[column], errors="coerce").fillna(default)


def _valuation_percentile(df: pd.DataFrame) -> pd.Series:
    pe = _numeric_column(df, "pe_dynamic").where(lambda values: values > 0, pd.NA)
    pb = _numeric_column(df, "pb").where(lambda values: values > 0, pd.NA)
    combined = pe.rank(pct=True).fillna(0.5) * 0.65 + pb.rank(pct=True).fillna(0.5) * 0.35
    return (combined * 100).clip(0, 100)


def _capital_flow_score(df: pd.DataFrame) -> pd.Series:
    flow_base = df["flow_net_20d"]
    if "turnover_amount" in df:
        turnover = pd.to_numeric(df["turnover_amount"], errors="coerce").replace(0, pd.NA)
        intensity = flow_base / turnover
        flow_base = intensity.where(intensity.notna(), flow_base)
    flow_rank = flow_base.rank(pct=True).fillna(0.5) * 100
    positive = df["flow_positive_ratio_20d"].clip(0, 1) * 100
    large_order = df["large_order_net_20d"].rank(pct=True).fillna(0.5) * 100
    score = (flow_rank * 0.45 + positive * 0.35 + large_order * 0.20).clip(0, 100)
    available = df.get("flow_data_available", pd.Series(True, index=df.index)).astype(bool)
    return score.where(available, 0.0).round(2)


def _flow_acceleration_score(df: pd.DataFrame) -> pd.Series:
    acceleration = df["flow_net_5d"] - (df["flow_net_20d"] / 4.0)
    score = (acceleration.rank(pct=True).fillna(0.5) * 100).clip(0, 100)
    available = df.get("flow_data_available", pd.Series(True, index=df.index)).astype(bool)
    return score.where(available, 0.0).round(2)


def _top_codes(df: pd.DataFrame, count: int, rank_col: str) -> list[str]:
    if df.empty:
        return []
    if rank_col not in df:
        rank_col = "quality_total_score" if "quality_total_score" in df else df.columns[0]
    return df.sort_values(rank_col, ascending=False).head(count)["futu_code"].astype(str).tolist()


def _rotation_state(row: pd.Series) -> str:
    leader = row.get("rotation_leader_score", 0)
    heat = row.get("rotation_heat_score", 0)
    if leader >= 75 and heat >= 82:
        return "crowded_rotation_leader"
    if leader >= 72:
        return "confirmed_rotation_leader"
    if heat >= 85 and leader < 65:
        return "exhaustion_risk"
    if leader >= 60:
        return "improving_rotation"
    return "neutral"


def _research_posture(state: str) -> str:
    return {
        "confirmed_rotation_leader": "active_research",
        "crowded_rotation_leader": "watch_for_pullback",
        "exhaustion_risk": "avoid_chasing",
        "improving_rotation": "track",
    }.get(state, "watch")


def _entry_state(score: float) -> str:
    if score >= 68:
        return "constructive_entry_zone"
    if score >= 58:
        return "base_building"
    if score >= 48:
        return "improving_watch"
    return "extended_or_unclear"


def _entry_action(state: str) -> str:
    return {
        "constructive_entry_zone": "research_entry_plan",
        "base_building": "wait_for_breakout_or_pullback",
        "improving_watch": "monitor",
        "extended_or_unclear": "avoid_new_entry",
    }[state]


def _empty_base() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "code",
            "futu_code",
            "name",
            "industry",
            "bucket",
            "fundamental_quality_score",
            "growth_quality_score",
            "valuation_score",
            "price_volume_score",
            "quality_total_score",
        ]
    )
