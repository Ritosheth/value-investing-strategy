# AShareHub API for deep stock research

Official documentation: `https://asharehub.com/zh/docs`. Current API prefix: `/v2`; v1 is frozen. Codes use `000001.SZ` / `600519.SH`, responses are JSON, and the Python SDK returns pandas DataFrames.

## Authentication and safety

- Read the key from `ASHAREHUB_API_KEY`.
- Send it only as `X-API-Key`; do not place it in URLs, reports, logs, code, or Git.
- Base URL: `https://asharehub.com`.
- Free/Pro/Business documented daily quotas: 100 / 10,000 / 50,000 requests. A `429` means stop or wait for the next quota window.
- This project assumes the 100-call plan. Use the collector's default 16-endpoint `core` profile, same-cutoff cache, local daily usage ledger, and 20-call reserve. Use `--asharehub-profile full` only when omitted technical, flow, or event endpoints are material.
- Never refresh same-cutoff data merely to confirm that the API works. The local ledger cannot see calls from other clients, so stop immediately when the server returns `429`.
- The official SDK requires Python 3.8+: `pip install asharehub`; use SDK 0.7.0+ for v2.

REST pattern:

```text
GET https://asharehub.com/v2/<path>?symbol=000001.SZ
X-API-Key: <read from ASHAREHUB_API_KEY>
```

## High-value endpoint map

| Research need | REST endpoint | SDK method | Key fields/usage |
|---|---|---|---|
| Daily valuation/size | `/v2/market/fundamentals` | `fundamentals()` | `pe`, `pe_ttm`, `pb`, `ps`, turnover, shares, market caps. |
| Financial indicators | `/v2/financials/indicators` | `financial_indicators()` | `ann_date`, `end_date`, ROE, margins, EPS/BPS, liquidity and efficiency. |
| Income statement | `/v2/financials/income` | `income()` | Revenue, costs, expenses, profit, parent profit, EBIT/EBITDA. |
| Balance sheet | `/v2/financials/balance-sheet` | `balance_sheet()` | Assets, liabilities, cash, receivables, inventory, goodwill, equity. |
| Cash flow | `/v2/financials/cash-flow` | `cash_flow()` | Operating/investing/financing cash flow and free cash flow. |
| Earnings forecast | `/v2/financials/forecast` | `forecast()` | Type, profit range, YoY change range, summary and reason. |
| Earnings express | `/v2/financials/express` | `express()` | Preliminary revenue/profit/assets/EPS/ROE before final filing. |
| Audit opinion | `/v2/financials/audit` | `audit()` | Opinion, auditor, signers and fee. |
| Main business | `/v2/financials/main-business` | `main_business()` | Segment/product/region sales, costs and profit. |
| Disclosure calendar | `/v2/financials/disclosure-date` | `disclosure_date()` | Planned, modified and actual reporting dates. |
| Dividends | `/v2/shareholders/dividend` | `dividend()` | Proposal/implementation status, cash/stock dividend and key dates. |
| Analyst reports | `/v2/financials/analyst-reports` | `analyst_reports()` | Institution, author, rating, targets and forecast-period EPS/PE/ROE. |
| Holder count/trades | `/v2/market/shareholders`, `/v2/market/holder-trade` | `shareholders()`, `holder_trade()` | Concentration trend, insider/major-holder changes and dates. |
| Margin financing | `/v2/market/margin` | `margin()` | Financing/short balances, purchases, sales and repayments. |
| Block trades | `/v2/market/block-trade` | `block_trade()` | Price, size, amount, buyer and seller; derive discount/premium. |
| Dragon–Tiger activity | `/v2/market/top-list`, `/v2/market/top-inst` | `top_list()`, `top_inst()` | Trigger reason, aggregate and seat-level buying/selling. |
| Stock/market flow | `/v2/flows/moneyflow`, `/v2/flows/moneyflow-hsgt` | `moneyflow()`, `moneyflow_hsgt()` | Order-size flow and market-wide Stock Connect flow. |
| Northbound holdings | `/v2/flows/northbound-holdings` | `northbound_holdings()` | Per-stock holding volume, ratio and channel. |
| Technical factors | `/v2/market/technical-factors` | `technical_factors()` | QFQ/HFQ prices, MACD, KDJ, RSI, BOLL and CCI. |
| Technical factors Pro | `/v2/market/technical-factors-pro` | `technical_factors_pro()` | 260+ columns; request only needed fields when possible. |
| Chip distribution | `/v2/chips/distribution` | `chip_distribution()` | Cost percentiles, weighted cost and model-estimated winner rate. |
| Industry/concepts | `/v2/reference/industries`, `/v2/market/concepts`, `/v2/market/concept-members` | `industry_list()`, `concepts()`, `concept_members()` | SW2021 hierarchy and daily concept membership. |
| News discovery | `/v2/news/flash` | `news_flash()` | CLS/Jin10/Sina flash text, timestamp, importance and optional source URL. |

## Research priority by module

- **Financial quality:** indicators + all three statements + audit.
- **Business mix:** main-business; use official reports for narrative and footnotes.
- **Catalysts:** forecast, express, disclosure-date, dividend, holder-trade, analyst-reports.
- **Positioning:** margin, block trades, Dragon–Tiger, northbound holdings, money flow.
- **Entry/timing:** technical factors and chips, cross-checked with Futu price/flow.
- **Peer context:** SW2021 industries plus daily valuation.

## Data caveats

- Income and cash-flow quarterly rows are cumulative year-to-date; difference consecutive periods to derive standalone quarters.
- Use `ann_date` or `f_ann_date` for point-in-time availability. Never join by `end_date` alone.
- Forecasts, disclosure plans, and dividend proposals can be revised; preserve every dated version.
- Daily prices are unadjusted. Use adjustment factors or technical-factor adjusted prices consistently.
- QFQ histories can be retroactively recalculated after corporate actions. Save raw retrieval snapshots for leakage-safe studies.
- Chip distribution is an estimated model, not actual account-level holdings.
- Dragon–Tiger data covers only stocks triggering exchange disclosure rules and is an ex-post abnormal-activity signal.
- Analyst reports have inconsistent forecast horizons and institution coverage; aggregate by forecast year and track contributor count.
- The northbound-holdings page says daily, but its displayed sample uses quarter-end dates. Verify actual returned cadence before calling it daily.
- News flash is a discovery feed from secondary sources; verify material claims against original announcements.
- Reconcile units carefully: financial statements are yuan; some flows/market data use yuan, ten-thousand yuan, thousand yuan, or million yuan depending on endpoint.

## Source arbitration

1. Official exchange/company/regulator filing text.
2. Point-in-time structured AShareHub records with announcement dates.
3. Futu structured/live data for market, flow, and overlapping fields.
4. Secondary news and analyst interpretation.

When Futu and AShareHub disagree, report both timestamps, field definitions, units, and adjustment conventions. Do not silently choose one.
