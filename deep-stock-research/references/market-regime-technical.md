# Market-regime context for technical analysis

Interpret a stock's chart as the interaction of stock-specific information, industry/style exposure, and the broad-market regime. Absolute MA position remains a fact; it is not a complete signal.

## Required same-cutoff context

For A-shares, prefer the same-date project snapshot at `outputs/daily_model_results/YYYYMMDD/market_risk_snapshot.json`, then cross-check material anomalies when necessary. Record:

- market risk level/score, recovery status, and confidence;
- representative broad-index 1/5/20-day returns, 20-day drawdown, MA20/MA60 state, and volatility;
- advancers, decliners, unchanged, decline ratio, median stock return, limit-up/down counts and ratios, and severe-down tail;
- industry/mainline phase and breadth for the stock's relevant plate;
- source universe, timestamp, intraday/close status, and warnings.

Counts from different feeds can disagree because of universe coverage, suspended stocks, ST treatment, Beijing Exchange inclusion, or limit-status definitions. Preserve the source universe and do not mix numerators and denominators. Use “historical/extreme” only after comparison with a defined history, not because a raw count sounds large.

## Stock-relative calculations

Use matching adjustment, dates, and horizons. Calculate when supported:

- `MA distance = close / MA_n - 1` and MA slope; do not report only above/below;
- 1/5/20/60-day stock returns versus a broad benchmark and relevant industry/style benchmark;
- `excess return = stock return - benchmark return` for each matching horizon;
- stock drawdown/volatility versus benchmark and industry;
- stock turnover/volume and capital-flow abnormality versus its own history.

Do not compare an A-share stock mechanically with only the Shanghai Composite when its listing venue, size/style, or industry makes another benchmark more representative. State the benchmark choice and limitation.

## Shock-day interpretation

On a broad liquidation or limit-down cascade, classify the stock before interpreting MA/support:

- **Market-beta damage:** stock performance is broadly consistent with market and industry. An MA break is real price damage but weak evidence of company-specific deterioration.
- **Relative resilience:** stock declines less or holds structure better than both benchmarks. This is tentative relative strength, not an automatic buy signal; require persistence through stabilization/recovery.
- **Idiosyncratic weakness:** stock materially underperforms both market and industry, especially with abnormal volume, persistent outflow, or company news. Treat MA failure as more informative.
- **Illiquidity/limit constraint:** limit-down or failed liquidity makes apparent support, closing price, and flow signals less reliable. Do not assume executable entry/exit levels.

A single panic close can distort MA distance, volatility, volume, and support/resistance. Keep absolute damage visible, but separate `systematic shock` from `stock-specific signal`. Do not convert relative outperformance during a crash into a bullish call while market risk remains red.

## Recovery and decision implications

After an extreme-breadth session, define evidence needed for stabilization rather than guessing the bottom. Check subsequent breadth, limit-down contraction, index/industry stabilization, stock excess return, volume/flow normalization, and reclaim or rejection of short/medium MAs. State whether the evidence changes:

- entry timing and required margin of safety;
- position/risk budget;
- confidence in support/resistance;
- the technical invalidation level;
- valuation risk premium or scenario multiple.

Use this report form:

`绝对状态 → 市场/行业状态 → 相对强弱 → 系统性或个股性归因 → 对入场/失效条件的影响 → 下一确认信号`

If same-session breadth or a suitable benchmark is unavailable, mark the contextual technical conclusion `Unavailable` or low confidence. Never use stale market conditions to explain a current stock move.
