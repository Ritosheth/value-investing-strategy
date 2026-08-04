---
name: deep-stock-research
description: Produce on-demand, evidence-backed, causally deep research for one or more A-share, Hong Kong, or US stocks using project model outputs, Futu OpenD data, official filings, current external sources, and same-cutoff market-regime evidence. Trace material facts through first- and second-order business, financial, valuation, geopolitical, policy, competitive, market-breadth, and relative-strength implications, then define directional scenarios and falsifiable monitoring signals. Use for interested stocks, model-selection validation, comparisons, investment theses, catalysts, risks, valuation, overseas expansion, technical/MA interpretation, or dated deep-research reports. Do not run automatically in the daily model pipeline.
---

# Deep Stock Research

Research only stocks explicitly requested by the user. Keep this workflow separate from daily model execution and production ranking.

Run the collector from the project root with the active project Python interpreter, for example `python scripts/collect_deep_research_data.py <codes>`. On this macOS workspace, use `stock_investment_system/env.sh` or the interpreter under `stock_investment_system/.venv` when invoking it directly.

## Runtime and network permission

AShareHub requires outbound HTTPS access to `https://asharehub.com`. The default workspace sandbox blocks that socket on this machine, commonly producing `ConnectError: [WinError 10013]`. For every A-share research run, invoke the collector with the command tool's `sandbox_permissions: "require_escalated"` and a short user-facing justification that elevated network access is needed for AShareHub evidence. Do not treat the first sandbox-denied attempt as an AShareHub data gap.

If an A-share collection reports `WinError 10013`, rerun the same collector command once with `sandbox_permissions: "require_escalated"` before continuing. Keep the API key in `ASHAREHUB_API_KEY`, use the default `core` profile, and never put the key in the command, URL, report, or logs. Only record AShareHub as unavailable after the authorized retry fails for a non-sandbox reason (for example HTTP 429, authentication failure, or a documented endpoint error).

## Research contract

- Treat saved prediction data and explicitly sourced market data as the quantitative base.
- Use official filings and exchange disclosures for claims not covered by Futu.
- Separate facts, calculations, interpretations, and unavailable evidence.
- Never fabricate missing metrics or silently substitute stale data.
- Do not stop at a factual observation, generic risk label, or “需要关注.” For every thesis-critical issue, explain the causal transmission to company fundamentals, assess likely direction and horizon, test alternatives, and specify confirming and falsifying evidence.
- Never interpret a stock's moving averages, drawdown, support/resistance, or one-day price/flow in isolation. Condition technical conclusions on the same-cutoff broad-market regime, breadth, relevant industry/style benchmark, and stock-relative performance.
- Keep conclusions shadow-only. Do not change production scores or parameters unless explicitly requested.

## Workflow

1. Normalize each ticker and record the research cutoff and horizon.
2. Run `scripts/collect_deep_research_data.py <codes>` from the project root to create reproducible raw evidence, derived metrics, and a deterministic brief. For A-share codes, this invocation must use elevated network permission as specified above. Chinese (`zh-CN`) is the default report language; pass `--language en` only when requested. Keep the default AShareHub `core` profile unless lower-priority endpoints are material.
3. Load saved model rank, score, reasons, states, and point-in-time `feature_json` from the collector output when available.
4. Read [references/futu-endpoints.md](references/futu-endpoints.md) and inspect collector warnings. Add supported Futu evidence only for requested stocks when the standard bundle is insufficient. Never use Futu `request_history_kline` for requested stocks, benchmarks, or same-plate constituents; historical daily bars must use the non-Futu chain documented below.
5. Read [references/asharehub-api.md](references/asharehub-api.md) for A-shares. Use AShareHub to complement Futu with structured financial, event, ownership, positioning, technical-factor, and Stock Connect history.
6. Read [references/external-data-gaps.md](references/external-data-gaps.md) and fill remaining gaps from official sources.
7. For the research cutoff, load `outputs/daily_model_results/YYYYMMDD/market_risk_snapshot.json` and `market_risk_report.md` when available. Read [references/market-regime-technical.md](references/market-regime-technical.md). If the snapshot is missing, stale, intraday-only, or uses incomplete benchmark coverage, derive only supported market context from dated non-Futu/official data and expose the gap; never silently pair current stock data with an older market session.
8. Calculate financial trends, cash conversion, leverage, working capital, valuation, absolute and benchmark/industry-relative returns, MA distance/slope, drawdown, volatility, flow persistence, event proximity, and multi-stock overlap deterministically.
9. Identify the 3–5 thesis-critical issues by expected impact on revenue, margin, cash flow, capital intensity, risk premium, or valuation. Read [references/causal-deep-dive.md](references/causal-deep-dive.md) and build an evidence-backed causal deep dive for each. Include company execution, competition, policy, macro, geopolitics, and cross-border dependencies only where there is a plausible transmission channel.
10. Analyze model rationale, financial quality, competition, valuation, industry/macro, catalysts, market regime, technical/flow, governance, contradictions, and invalidation.
11. Tag evidence as `Observed`, `Derived`, `Qualitative`, or `Unavailable`.
12. Read [references/report-template.md](references/report-template.md) and deliver the report in Chinese unless the user requests another language.
13. Include an analysis-estimated value whenever the evidence supports a defensible numeric calculation. Show the valuation date/cutoff, primary method, forecast metric and period, every key input, formula or calculation path, and the current-price discount/premium. Give a central/base estimate plus a bear/base/bull range when scenario evidence is available. Label it as `分析估算价值` or `合理价值区间`, not as a guaranteed target price, analyst consensus, or automatic buy/sell signal. If the required earnings, cash-flow, share-count, debt/cash, or peer/historical inputs are unavailable or contradictory, write `Unavailable` and explain why instead of inventing an input.

Keep original prediction timestamps distinct from current research data. Convert endpoint errors, empty responses, and unsupported markets into warnings, but distinguish sandbox-denied network errors from genuine provider or endpoint failures and perform the elevated retry first. Never treat missing data as neutral or let the LLM estimate missing numeric inputs.

For A-share historical K-lines, including every stock sampled from the same industry/concept plate, use `stock_selection.market_data.a_share_history.fetch_a_share_history`. Its fallback order is Tencent (`stock_zh_a_hist_tx`) → Sina (`stock_zh_a_daily`) → Eastmoney (`stock_zh_a_hist`). Preserve the selected `source`, adjustment mode, requested window, and every failed-provider warning. Do not fall back to Futu under any circumstance. Bound peer sampling before fetching, reuse same-cutoff results, and mark industry-relative evidence `Unavailable` if the non-Futu chain is exhausted.

Use InvestSkill prompts as analytical checklists, especially financial-report, competitor, valuation, earnings-call, catalyst, sector, technical, validator, and portfolio-review. Do not copy generic weights, US-specific thresholds, or automatic BUY/SELL labels.

## Evidence and sourcing

For A-shares, prefer CNInfo, SSE, SZSE, BSE, company IR, regulators, and official statistics. Preserve publisher, publication date, URL, retrieval date, and reporting period. Confidence measures source authority, freshness, completeness, and consistency; it is not a calibrated return probability.

For current policy, diplomatic, geopolitical, trade, sanctions, tariff, and overseas-operating claims, search current primary sources. Use relevant governments, regulators, customs authorities, investment-promotion agencies, treaty texts, and company disclosures from every material jurisdiction; do not rely only on the Chinese or host-country diplomatic narrative. Distinguish ceremonial statements from binding policy, implemented rules, operating evidence, and measurable company exposure. Treat search-result snippets and unsourced summaries as leads, not evidence.

Read the AShareHub key from `ASHAREHUB_API_KEY`; never write it into source files, reports, logs, URLs, or version control. Prefer the `X-API-Key` header. Treat AShareHub as a structured secondary source: official filings override conflicts. Futu may remain a live snapshot/flow source, but it must never supply historical K-lines.

Conserve AShareHub's 100-call daily quota. Reuse same-cutoff cache, keep the default 20-call reserve, and never use `--refresh-asharehub` or the `full` profile without a concrete evidence need. Stop immediately on HTTP 429. The local ledger tracks this script's calls only; a server-side 429 remains authoritative when other clients consumed quota.

## Output

Default all report prose and headings to Simplified Chinese. Preserve official names, endpoint names, field names, and quoted source language when translation would reduce precision.

The collector writes `research_raw.json`, `research_derived.json`, and `research_brief.md` under `outputs/deep_research/YYYYMMDD/<stock_name>/`. Treat the brief as a deterministic evidence base, not the final investment conclusion. Save the completed report as `deep_research.md` in the same stock directory. For multiple stocks, write `deep_research_summary.csv` and `portfolio_synthesis.md` at the date level. Otherwise return the same structure in the response.

Before delivery, verify timestamps, sources, visible gaps, reproducible calculations, equal treatment of contradictory evidence, and a final posture with horizon, confidence, next catalyst, key risk, invalidation triggers, and the analysis-estimated value or an explicit valuation data gap. Apply the causal-depth gate in [references/causal-deep-dive.md](references/causal-deep-dive.md): no thesis-critical item may end as a generic watchpoint without mechanism, direction, horizon, and monitorable evidence. Apply the technical-context gate in [references/market-regime-technical.md](references/market-regime-technical.md): no MA/support/resistance conclusion may be delivered without same-date market regime, breadth, relative performance, and data-universe caveats or an explicit `Unavailable`. Do not change any production score or parameter.
