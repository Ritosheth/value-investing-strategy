# External evidence gaps beyond Futu

| Research need | Missing evidence | Preferred endpoint/source to locate |
|---|---|---|
| Full filings/announcements | MD&A, footnotes, risks, exact language | CNInfo/SSE/SZSE/BSE search and PDF API with stable IDs and timestamps. |
| Investor Q&A/transcripts | Tone, evasiveness, guidance changes | Company IR, exchange Q&A, performance-briefing transcript, or licensed transcript API. |
| Penalties/violations | Regulatory and disclosure enforcement | CSRC, exchange, SAMR, and sector-regulator enforcement APIs. |
| Share pledges | Pledge ratio and history | Official pledge announcements or structured pledge API. |
| Lock-up releases | Restricted-share supply calendar | Exchange/CNInfo unlock announcements or structured calendar. |
| Contracts/litigation/guarantees/related parties | Announcement-level facts | Official disclosure full-text/category API. |
| Industry share/capacity | Moat and competitive position | NBS, ministries, associations, regulators, filings, or credible industry databases. |
| Product/commodity prices | Margin drivers | Official exchanges, government price data, industry indices, or licensed APIs. |
| Fund ownership beyond Stock Connect | Complete mutual-fund and institutional position history | Fund periodic-holdings or licensed institutional-ownership APIs. |
| China macro/policy calendar | Domestic policy and releases | PBOC, NBS, MOF, customs, and official policy/data APIs. |

Prefer point-in-time APIs with stable security IDs, publication and effective dates, revision status, original document URLs, pagination, and documented rate limits. Avoid latest-only overwritten values.

AShareHub now covers structured A-share analyst reports, shareholder counts/trades, market-wide Stock Connect flow, and per-stock northbound holdings. Keep these out of the missing list, but validate actual update cadence and preserve retrieval snapshots before using them in historical evaluation.
