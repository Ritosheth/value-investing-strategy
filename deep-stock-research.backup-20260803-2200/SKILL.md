---
name: deep-stock-research
description: Produce on-demand, evidence-backed deep research for one or more A-share, Hong Kong, or US stocks using project model outputs, Futu OpenD data, official filings, and current external sources. Use for interested stocks, model-selection validation, comparisons, investment theses, catalysts, risks, valuation, or dated deep-research reports. Do not run automatically in the daily model pipeline.
---

# Deep Stock Research

Research only stocks explicitly requested by the user. Keep this workflow separate from daily model execution and production ranking.

Run the collector with the active project Python interpreter, for example `python scripts/collect_deep_research_data.py <codes>` from the project root, or explicitly with the local virtualenv interpreter selected by the current shell. Do not hard-code a machine-specific Python path in reports, scripts, or instructions.

## Research contract

- Treat Futu and saved prediction data as the quantitative base.
- Use official filings and exchange disclosures for claims not covered by Futu.
- Separate facts, calculations, interpretations, and unavailable evidence.
- Never fabricate missing metrics or silently substitute stale data.
- Keep conclusions shadow-only. Do not change production scores or parameters unless explicitly requested.

## Workflow

1. Normalize each ticker and record the research cutoff and horizon.
2. Run `scripts/collect_deep_research_data.py <codes>` from the project root to create reproducible raw evidence, derived metrics, deterministic scoring, shadow position guidance, and a deterministic brief. Chinese (`zh-CN`) is the default report language; pass `--language en` only when requested. Keep the default AShareHub `core` profile unless lower-priority endpoints are material.
3. Load saved model rank, score, reasons, states, and point-in-time `feature_json` from the collector output when available.
4. Read [references/futu-endpoints.md](references/futu-endpoints.md) and inspect collector warnings. Add supported Futu evidence only for requested stocks when the standard bundle is insufficient.
5. Read [references/asharehub-api.md](references/asharehub-api.md) for A-shares. Use AShareHub to complement Futu with structured financial, event, ownership, positioning, technical-factor, and Stock Connect history.
6. Read [references/external-data-gaps.md](references/external-data-gaps.md) and fill remaining gaps from official sources.
7. Calculate financial trends, cash conversion, leverage, working capital, valuation, returns, drawdown, volatility, flow persistence, event proximity, and multi-stock overlap deterministically.
8. Analyze model rationale, financial quality, competition, valuation, industry/macro, catalysts, technical/flow, governance, contradictions, and invalidation.
9. Tag evidence as `Observed`, `Derived`, `Qualitative`, or `Unavailable`.
10. Read [references/score-position-rules.md](references/score-position-rules.md) before stating a research posture or position band. Keep sizing guidance shadow-only and never present it as an automatic trade instruction.
11. Read [references/report-template.md](references/report-template.md) and deliver the report in Chinese unless the user requests another language.

Keep original prediction timestamps distinct from current research data. Convert endpoint errors, empty responses, and unsupported markets into warnings. Never treat missing data as neutral or let the LLM estimate missing numeric inputs.

Use InvestSkill prompts as analytical checklists, especially financial-report, competitor, valuation, earnings-call, catalyst, sector, technical, validator, and portfolio-review. Do not copy generic weights, US-specific thresholds, or automatic BUY/SELL labels.

## Evidence and sourcing

For A-shares, prefer CNInfo, SSE, SZSE, BSE, company IR, regulators, and official statistics. Preserve publisher, publication date, URL, retrieval date, and reporting period. Confidence measures source authority, freshness, completeness, and consistency; it is not a calibrated return probability.

Read the AShareHub key from `ASHAREHUB_API_KEY`; never write it into source files, reports, logs, URLs, or version control. Prefer the `X-API-Key` header. Treat AShareHub as a structured secondary source: official filings override conflicts, while Futu remains the primary live-market source unless a documented comparison shows otherwise.

Conserve AShareHub's 100-call daily quota. Reuse same-cutoff cache, keep the default 20-call reserve, and never use `--refresh-asharehub` or the `full` profile without a concrete evidence need. Stop immediately on HTTP 429. The local ledger tracks this script's calls only; a server-side 429 remains authoritative when other clients consumed quota.

## Output

Default all report prose and headings to Simplified Chinese. Preserve official names, endpoint names, field names, and quoted source language when translation would reduce precision.

The collector writes `research_raw.json`, `research_derived.json`, and `research_brief.md` under `outputs/deep_research/YYYYMMDD/<stock_name>/`. Treat the brief as a deterministic evidence base, not the final investment conclusion. Save the completed report as `deep_research.md` in the same stock directory. For multiple stocks, write `deep_research_summary.csv` and `portfolio_synthesis.md` at the date level. Otherwise return the same structure in the response.

Before delivery, verify timestamps, sources, visible gaps, reproducible calculations, equal treatment of contradictory evidence, and a final posture with horizon, confidence, next catalyst, key risk, and invalidation triggers. Do not change any production score or parameter.
