from __future__ import annotations

import pandas as pd

from ..config import SelectionConfig
from ..futu_client import FutuClient
from ..futu_models import add_entry_position_context, add_rotation_overlay, build_quality_base, enrich_corporate_actions, enrich_events, enrich_flow, enrich_valuation
from ..parameters import model_parameters, parameter_metadata, weighted_score
from ..scoring import ModelResult, top_watchlist


MODEL_NAME = "Model 3: Event Plus Flow Confirmation"
DESCRIPTION = (
    "Find catalyst-driven candidates where event, analyst, dividend, or buyback context is confirmed "
    "by positive 10-day or 20-day Futu stock-level capital flow."
)


def run(
    client: FutuClient,
    config: SelectionConfig,
    *,
    report_date: str | None = None,
) -> ModelResult:
    params = model_parameters("model3_event_flow_confirmation", config)
    scored, rejected, metadata = build_quality_base(client, config)
    candidate_count = max(config.max_flow_candidates, config.max_watchlist * 3)
    scored = enrich_valuation(client, scored, count=candidate_count)
    scored["stock_quality_blend"] = weighted_score(scored, params["stock_quality_blend_weights"]).round(2)
    scored["pre_event_score"] = weighted_score(scored, params["pre_event_score_weights"]).round(2)

    scored = enrich_events(client, scored, count=candidate_count, rank_col="pre_event_score")
    scored = enrich_corporate_actions(client, scored, count=candidate_count, rank_col="pre_event_score")
    if "event_score" not in scored:
        scored["event_score"] = 0.0
    if "expectation_score" not in scored:
        scored["expectation_score"] = 0.0
    scored["event_score"] = scored["event_score"].fillna(0.0)
    scored["expectation_score"] = scored["expectation_score"].fillna(0.0)

    scored["catalyst_score"] = weighted_score(scored, params["catalyst_score_weights"]).clip(0, 100).round(2)
    scored["pre_flow_rank_score"] = weighted_score(scored, params["pre_flow_rank_score_weights"]).round(2)

    if config.fetch_stock_flow:
        scored = enrich_flow(client, scored, count=candidate_count, rank_col="pre_flow_rank_score")
    else:
        client.warn("Stock-level Futu capital-flow enrichment disabled; Model 3 requires flow confirmation by design.")
    if "capital_flow_score" not in scored:
        scored["capital_flow_score"] = 0.0
    scored["capital_flow_score"] = scored["capital_flow_score"].fillna(0.0)

    scored["total_score"] = weighted_score(scored, params["total_score_weights"]).round(2)
    scored = add_rotation_overlay(scored)
    scored = add_entry_position_context(scored)

    discovered_by_event = scored["catalyst_score"] > 0
    thresholds = params["flow_confirmation_thresholds"]
    confirmed_by_flow = (
        scored.get("flow_net_10d", 0) > thresholds.get("flow_net_10d_min", 0.0)
    ) | (
        scored.get("flow_net_20d", 0) > thresholds.get("flow_net_20d_min", 0.0)
    )
    scored = scored[discovered_by_event & confirmed_by_flow].copy()
    scored["selection_reason"] = scored.apply(_reason, axis=1)

    columns = [
        "code",
        "name",
        "bucket",
        "total_score",
        "event_score",
        "expectation_score",
        "catalyst_score",
        "rotation_state",
        "research_posture",
        "entry_position_state",
        "entry_action",
        "entry_position_score",
        "rotation_leader_score",
        "rotation_durability_score",
        "rotation_heat_score",
        "flow_acceleration_score",
        "capital_flow_score",
        "stock_quality_blend",
        "fundamental_quality_score",
        "growth_quality_score",
        "valuation_score",
        "flow_net",
        "flow_net_5d",
        "flow_net_10d",
        "flow_net_20d",
        "large_order_net_20d",
        "flow_positive_ratio",
        "flow_positive_ratio_20d",
        "flow_days",
        "industry",
        "latest_price",
        "pe_dynamic",
        "pb",
        "turnover_amount",
        "price_source",
        "score_input_mode",
        "feature_coverage",
        "risk_flags",
        "selection_reason",
    ]
    watchlist = top_watchlist(
        scored,
        score_col="total_score",
        limit=config.max_watchlist,
        # bucket is reserved for quality/satellite classification; this
        # model does not create an "event" bucket, so score should decide.
        preferred_bucket=None,
        columns=columns,
    )

    metadata.update(
        {
            "report_date": report_date or "futu_latest",
            "fund_flow_required": True,
            "fund_flow_rule": "flow_net_10d > 0 or flow_net_20d > 0",
            "shareholder_change_source": "unsupported_for_a_share_in_futu_smoke_test",
            "research_rating_summary_source": "unsupported_for_a_share_in_futu_smoke_test",
            "survivors_after_gates": len(scored),
            "flow_confirmed_event_candidates": len(scored),
            "rejected_by_gates": len(rejected),
            "max_watchlist": config.max_watchlist,
            **parameter_metadata(params),
        }
    )
    return ModelResult(
        model_name=MODEL_NAME,
        description=DESCRIPTION,
        watchlist=watchlist,
        metadata=metadata,
        warnings=client.warnings.copy(),
    )


def _reason(row: pd.Series) -> str:
    reasons: list[str] = []
    if row.get("event_score", 0) >= 65:
        reasons.append("earnings/event context visible")
    if row.get("expectation_score", 0) >= 65:
        reasons.append("analyst consensus support")
    if row.get("capital_flow_score", 0) >= 65:
        reasons.append("persistent Futu stock-level flow confirms")
    if row.get("flow_net_10d", 0) > 0:
        reasons.append("10d main inflow positive")
    if row.get("flow_net_20d", 0) > 0:
        reasons.append("20d main inflow positive")
    if row.get("stock_quality_blend", 0) >= 65:
        reasons.append("quality gate supportive")
    if row.get("entry_position_state") in {"constructive_entry_zone", "improving_watch"}:
        reasons.append(str(row.get("entry_position_state")))
    state = row.get("rotation_state")
    if state in {"confirmed_rotation_leader", "crowded_rotation_leader", "exhaustion_risk"}:
        reasons.append(str(state))
    if not reasons:
        reasons.append("event/flow candidate requiring confirmation")
    return ", ".join(reasons)
