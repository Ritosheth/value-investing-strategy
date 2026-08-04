# Deep research report template

1. Research snapshot: stock, market, price, cutoff, horizon, selection status, confidence, same-cutoff market risk level/recovery status.
2. Executive conclusion: posture, strongest evidence, contradiction, key risk, next catalyst.
3. Model rationale: model/rank/score/bucket, original timestamp, overlap, segment evidence.
4. Business and competition: revenue mix, moat direction, pricing power, market share, disruption.
5. Financial/accounting quality: growth, margins, cash conversion, leverage, liquidity, working capital, audit.
6. Valuation: current, historical, peer-relative, and defensible scenarios. Add an analysis-estimated value or reasonable-value range when supported: valuation cutoff, primary method, forecast period/metric, key inputs, calculation, central/base estimate, bear/base/bull range, and current-price discount/premium. This is a research estimate, not a guaranteed target price or automatic trading instruction. If the inputs are not reliable enough, mark it `Unavailable` and record the gap.
7. Thesis-critical causal deep dives: select 3–5 material issues. For each show `事实 → 暴露 → 机制 → 财务影响 → 估值/姿态 → 验证信号`, then bear/base/bull paths, strongest competing explanation, horizon, and dated/threshold monitoring evidence. A generic “需要关注” is incomplete.
8. Industry/macro and market regime: plate/mainline phase, cycle exposure, regulation, macro drivers, broad-index trend, advance/decline breadth, median return, limit-down tail, liquidity, and recovery status. Include source universe and data confidence; connect only material channels to company fundamentals or risk premium.
9. Catalysts: event, date, status, probability only when evidence supports it, impact, confirmation, source.
10. Technical/flow in context: report absolute MA distance/slope, drawdown, volume and flow; then same-horizon broad/industry benchmark returns, excess returns, shock classification, and whether MA/support failure is systematic or stock-specific. Use `绝对状态 → 市场/行业状态 → 相对强弱 → 归因 → 入场/失效影响 → 下一确认信号`. Never convert crash-day relative resilience into an automatic bullish call.
11. Governance/ownership: executives, holder changes, pledges, lockups, buybacks, related parties, penalties.
12. Bull case, bear case, and contradictions with evidence tags.
13. Invalidation and monitoring triggers, including the next evidence release for each thesis-critical deep dive.
14. Source register with endpoint/URL, period, publication and retrieval timestamps.
15. Data-gap register with attempted source and effect on confidence and direction.

```text
Research posture: CORE CANDIDATE / TIMING WATCH / EVENT CANDIDATE / REJECT-RISK WATCH / INSUFFICIENT EVIDENCE
Horizon: SHORT / MEDIUM / LONG
Research confidence: HIGH / MEDIUM / LOW
Analysis-estimated value: [central estimate or reasonable-value range, valuation date, primary method; or Unavailable with reason]
Current-price discount/premium to estimated value: [percentage; or Unavailable]
Strongest confirming evidence:
Strongest contradictory evidence:
Next material catalyst:
Primary invalidation trigger:
```

Multi-stock CSV columns:
`code,name,models,latest_rank,research_posture,horizon,confidence,financial_quality,valuation_view,industry_context,catalyst,next_catalyst_date,technical_flow_state,key_risk,main_contradiction,primary_invalidation,data_as_of`

For `portfolio_synthesis.md`, compare industry/factor concentration, catalyst clustering, correlated risks, and genuinely distinct exposure.
