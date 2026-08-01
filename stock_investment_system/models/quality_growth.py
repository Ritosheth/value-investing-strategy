from __future__ import annotations

import pandas as pd

from ..config import SelectionConfig
from ..futu_client import FutuClient
from ..futu_models import (
    add_entry_position_context,
    add_rotation_overlay,
    build_quality_base,
    enrich_corporate_actions,
    enrich_valuation,
    recompute_quality_total,
)
from ..parameters import model_parameters, normalized_weight_tuple, parameter_metadata, weighted_score
from ..scoring import ModelResult, top_watchlist


MODEL_NAME = "Model 1: Quality Plus Growth"
DESCRIPTION = (
    "Find medium-term candidates with acceptable liquidity/risk, stronger financial quality, "
    "growth quality, and reasonable valuation using Futu OpenD endpoints."
)


def run(
    client: FutuClient,
    config: SelectionConfig,
    *,
    report_date: str | None = None,
) -> ModelResult:
    params = model_parameters("model1_quality_growth", config)
    scored, rejected, metadata = build_quality_base(client, config)
    initial_weights = normalized_weight_tuple(
        params["initial_quality_weights"],
        ("fundamental_quality_score", "growth_quality_score", "valuation_score", "price_volume_score"),
    )
    scored = enrich_valuation(client, scored, count=max(config.max_market_candidates, config.max_watchlist))
    scored = recompute_quality_total(scored, weights=initial_weights)
    scored = enrich_corporate_actions(client, scored, count=config.max_watchlist)
    scored["total_score"] = weighted_score(scored, params["total_score_weights"]).round(2)
    scored = add_rotation_overlay(scored)
    scored = add_entry_position_context(scored)
    scored["selection_reason"] = scored.apply(_reason, axis=1)

    columns = [
        "code",
        "name",
        "bucket",
        "total_score",
        "fundamental_quality_score",
        "growth_quality_score",
        "valuation_score",
        "price_volume_score",
        "rotation_state",
        "research_posture",
        "entry_position_state",
        "entry_action",
        "entry_position_score",
        "rotation_leader_score",
        "rotation_durability_score",
        "rotation_heat_score",
        "valuation_percentile",
        "industry",
        "latest_price",
        "pe_dynamic",
        "pb",
        "turnover_amount",
        "float_market_cap",
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
        preferred_bucket="core",
        columns=columns,
    )

    metadata.update(
        {
            "report_date": report_date or "futu_latest",
            "survivors_after_gates": len(scored),
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
    if row.get("fundamental_quality_score", 0) >= 70:
        reasons.append("strong quality")
    if row.get("growth_quality_score", 0) >= 70:
        reasons.append("growth improving")
    if row.get("valuation_score", 0) >= 65:
        reasons.append("valuation acceptable")
    if row.get("price_volume_score", 0) >= 65:
        reasons.append("market trend/liquidity supportive")
    if row.get("entry_position_state") in {"constructive_entry_zone", "base_building"}:
        reasons.append(str(row.get("entry_position_state")))
    if row.get("has_dividend"):
        reasons.append("dividend record")
    if row.get("has_buyback"):
        reasons.append("buyback context")
    state = row.get("rotation_state")
    if state in {"confirmed_rotation_leader", "crowded_rotation_leader"}:
        reasons.append(str(state))
    if not reasons:
        reasons.append("balanced quality-growth profile")
    return ", ".join(reasons)
