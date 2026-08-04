# Futu endpoints for on-demand deep research

Use `docs/Futu-API-Doc-en-Python.md` as the authoritative signature and field reference. Use `docs/futu-stock-api-map-v1.md` for project normalization and known A-share behavior.

## Market, technical, and industry

| Need | Endpoint | Notes |
|---|---|---|
| Snapshot | `get_market_snapshot` | Price, status, liquidity, market cap, PE/PB, and 52-week range. |
| Price history | `request_history_kline` | Use for requested stocks only; paginate and preserve adjustment mode. |
| Flow | `get_capital_flow`, `get_capital_distribution` | Calculate multi-window persistence; distribution is current context only. |
| Plates | `get_owner_plate`, `get_plate_list`, `get_plate_stock` | Keep industry and concepts distinct. |
| Mainline | `get_heat_map_data`, `get_rise_fall_distribution`, industrial-chain endpoints | Prefer saved snapshots when available. |

## Financial and business

| Need | Endpoint | Notes |
|---|---|---|
| Statements/audit | `get_financials_statements` | Follow `next_key`; separate report and release dates. |
| Revenue mix | `get_financials_revenue_breakdown` | Product, region, or business-line mix when supported. |
| Efficiency | `get_company_operational_efficiency` | Verify A-share coverage. |
| Company/executives | `get_company_profile`, `get_company_executives`, `get_company_executive_background` | Background only; verify market availability. |
| Valuation | `get_valuation_detail`, `get_valuation_plate_stock_list` | Use correct valuation type and economically comparable peers. |

## Events, ownership, and research

| Need | Endpoint | Notes |
|---|---|---|
| Corporate actions | `get_corporate_actions_dividends`, `get_corporate_actions_buybacks`, `get_corporate_actions_stock_splits` | Separate announcement, implementation, and ex-date status. |
| Ownership | `get_shareholders_overview`, `get_shareholders_holding_changes`, `get_shareholders_holder_detail`, `get_shareholders_institutional` | Follow pagination and preserve holding/publication dates. |
| Earnings reactions | `get_financials_earnings_price_move`, `get_financials_earnings_price_history` | Subject to market support. |
| Analyst data | `get_research_analyst_consensus`, `get_research_rating_summary` | A-share support may be absent; never assume consensus. |
| News/calendars | `get_search_news`, `get_earnings_calendar`, `get_dividend_calendar`, `get_economic_calendar` | Verify claims against original sources and check market coverage. |
| Positioning | `get_top_ten_buy_sell_brokers` | Context only. Short/options endpoints are generally HK/US-oriented. |

## Call rules

- Run per-stock endpoints only for requested stocks; batch snapshot and membership calls.
- Cache by code, endpoint, report period, and retrieval timestamp.
- Preserve structured extracts when saving reports.
- Surface errors, empty results, and unsupported markets.
- Do not infer A-share support from HK/US examples in the API documentation.
