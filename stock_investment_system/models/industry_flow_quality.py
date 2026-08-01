from __future__ import annotations

import pandas as pd

from ..config import SelectionConfig
from ..futu_client import FutuClient
from ..futu_models import add_entry_position_context, add_rotation_overlay, build_quality_base, derive_industry_strength, enrich_flow, enrich_valuation
from ..parameters import model_parameters, parameter_metadata, weighted_score
from ..scoring import ModelResult, top_watchlist
from ..utils import safe_merge


MODEL_NAME = "Model 2: Industry Flow Plus Quality"
DESCRIPTION = (
    "Catch sector rotation without buying low-quality stocks. Futu V1 derives industry strength "
    "from stock-screen momentum factors and stock-level capital flow instead of a direct industry-flow endpoint."
)


def run(
    client: FutuClient,
    config: SelectionConfig,
    *,
    report_date: str | None = None,
) -> ModelResult:
    params = model_parameters("model2_industry_flow_quality", config)
    scored, rejected, metadata = build_quality_base(client, config)
    plate_counts = client.plate_metadata()

    candidate_count = max(config.max_watchlist * 4, config.max_market_candidates, 60)
    scored = enrich_valuation(client, scored, count=candidate_count)

    scored["stock_quality_blend"] = weighted_score(scored, params["stock_quality_blend_weights"]).round(2)
    scored["total_score"] = weighted_score(scored, params["pre_industry_score_weights"]).round(2)

    scored["pre_industry_score"] = weighted_score(scored, params["pre_industry_score_weights"]).round(2)
    scored = enrich_flow(client, scored, count=candidate_count, rank_col="pre_industry_score")

    membership = client.owner_plate(
        scored.sort_values("pre_industry_score", ascending=False).head(candidate_count)["futu_code"].astype(str).tolist()
    )
    if not membership.empty:
        industry_membership = membership[membership["plate_type"].astype(str).str.contains("INDUSTRY", case=False, na=False)]
        if not industry_membership.empty:
            primary_plate = industry_membership.drop_duplicates("code")[["code", "plate_name"]].rename(
                columns={"plate_name": "owner_plate_industry"}
            )
            scored = safe_merge(scored, primary_plate, on="code", suffix="_owner_plate")
            scored["industry"] = scored.get("industry").combine_first(scored.get("owner_plate_industry"))

    industry_strength = derive_industry_strength(scored)
    scored = safe_merge(scored, industry_strength, on="industry", suffix="_industry")
    if "industry_strength_score" not in scored:
        scored["industry_strength_score"] = 45.0
    scored["industry_strength_score"] = scored["industry_strength_score"].fillna(45.0)
    scored["total_score"] = weighted_score(scored, params["total_score_weights"]).round(2)
    scored = add_rotation_overlay(scored)
    scored = add_entry_position_context(scored)
    scored["selection_reason"] = scored.apply(_reason, axis=1)

    columns = [
        "code",
        "name",
        "bucket",
        "total_score",
        "stock_quality_blend",
        "fundamental_quality_score",
        "growth_quality_score",
        "valuation_score",
        "industry_strength_score",
        "industry_member_count",
        "industry_coverage_ratio",
        "industry_net_flow",
        "industry_pct_change",
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
        "flow_net_20d",
        "large_order_net_20d",
        "price_volume_score",
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
        preferred_bucket="core",
        columns=columns,
    )

    top_industries = []
    if not industry_strength.empty:
        top_industries = industry_strength.sort_values("industry_strength_score", ascending=False).head(10)[
            "industry"
        ].tolist()

    metadata.update(
        {
            "report_date": report_date or "futu_latest",
            "industry_strength_method": "derived_from_stock_screen_momentum_snapshot_and_capital_flow",
            "plate_counts": plate_counts,
            "top_industries": ", ".join(map(str, top_industries)),
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
    if row.get("industry_strength_score", 0) >= 70:
        reasons.append("derived industry strength positive")
    if row.get("capital_flow_score", 0) >= 65:
        reasons.append("stock flow supports rotation")
    if row.get("stock_quality_blend", 0) >= 70:
        reasons.append("stock quality supports rotation")
    if row.get("price_volume_score", 0) >= 65:
        reasons.append("price/volume confirms")
    if row.get("entry_position_state") in {"constructive_entry_zone", "improving_watch"}:
        reasons.append(str(row.get("entry_position_state")))
    state = row.get("rotation_state")
    if state in {"confirmed_rotation_leader", "crowded_rotation_leader", "exhaustion_risk"}:
        reasons.append(str(state))
    if not reasons:
        reasons.append("quality candidate in acceptable Futu-derived industry context")
    return ", ".join(reasons)
