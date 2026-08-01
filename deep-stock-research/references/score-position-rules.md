# Research scoring and shadow position rules

Use these rules after deterministic evidence collection and before writing the final posture. They are research triage rules, not production portfolio rules, and they must not change model scores, trading parameters, or live holdings.

## Component weights

| Component | Weight | Evidence examples | Missing-data treatment |
|---|---:|---|---|
| Model validation | 15% | Saved model rank, score, bucket, reason, point-in-time features. | Non-critical, but no neutral credit. |
| Financial quality | 20% | Growth, margin, ROE, cash conversion, leverage, working capital, audit. | Critical; missing forces `INSUFFICIENT EVIDENCE`. |
| Valuation | 15% | Absolute, historical, peer-relative, and scenario valuation. | Critical; missing forces `INSUFFICIENT EVIDENCE`. |
| Catalyst | 15% | Earnings, disclosure date, dividend, buyback, policy, product, order, capacity. | Non-critical, but no neutral credit. |
| Technical and flow | 15% | Trend, drawdown, volatility, support/resistance, capital-flow persistence, crowding. | Critical; missing forces `INSUFFICIENT EVIDENCE`. |
| Governance and risk hygiene | 15% | Holder changes, pledges, lockups, penalties, related parties, guarantees, litigation. | Critical; missing forces `INSUFFICIENT EVIDENCE`. |
| Data confidence | 5% | Source authority, freshness, completeness, consistency, retrieval reproducibility. | Non-critical, but missing keeps confidence low. |

Each component is scored from 0 to 100. Unavailable evidence receives 0 for that component and is also listed in the data-gap register. Do not assign 50 as a default for missing evidence.

## Posture mapping

Apply hard limits first:

- If any critical component is unavailable: `INSUFFICIENT EVIDENCE`.
- If governance/risk hygiene is below 45: `REJECT-RISK WATCH`.
- If valuation is below 35: `REJECT-RISK WATCH`.
- If technical and flow is below 35: `REJECT-RISK WATCH`.

Then map the weighted score:

| Weighted score and condition | Research posture |
|---|---|
| `>= 75` and confidence is `HIGH` or `MEDIUM` | `CORE CANDIDATE` |
| `>= 68` | `TIMING WATCH` |
| `>= 60` and catalyst score is `>= 70` | `EVENT CANDIDATE` |
| `>= 55` | `REJECT-RISK WATCH` |
| Otherwise | `INSUFFICIENT EVIDENCE` |

## Confidence mapping

- `HIGH`: data confidence score is at least 80 and no more than one non-critical component is unavailable.
- `MEDIUM`: data confidence score is at least 60 and no more than two non-critical components are unavailable.
- `LOW`: anything below the medium threshold.

Confidence is about evidence quality, not expected return probability.

## Shadow position bands

Use the band only for research discussion and monitoring priority.

| Condition | Shadow position band |
|---|---|
| Hard limit triggered, `REJECT-RISK WATCH`, or `INSUFFICIENT EVIDENCE` | `0%` |
| Low confidence or at least two unavailable components | `0%-2%` |
| `CORE CANDIDATE` with high confidence | `4%-6%` |
| `CORE CANDIDATE` with medium confidence | `2%-4%` |
| `TIMING WATCH` | `2%-4%` |
| `EVENT CANDIDATE` | `0%-2%` |

Never output an automatic buy, sell, or rebalance instruction. Pair every band with horizon, next catalyst, key risk, and invalidation trigger.
