# Stock Investment System

完整选股规则、估值约束和当前局限见根目录 `SYSTEM_RULES_AND_LIMITATIONS.md`。

This directory contains the installed model layer from the supplied package.

## Installed Files

- `models/quality_growth.py`: quality plus growth watchlist model.
- `models/industry_flow_quality.py`: industry flow plus quality watchlist model.
- `models/event_flow_confirmation.py`: event plus flow confirmation watchlist model.
- `model_parameters_v1.toml`: V1 rule weights and thresholds.
- `requirements.txt`: base Python dependencies.

## Runtime Modules

The missing runtime layer has been reconstructed:

- `config.py`: shared runtime configuration.
- `futu_client.py`: data adapter for sample data, CSV input, and read-only best-effort Futu OpenD calls.
- `futu_models.py`: base universe, enrichment, industry strength, rotation, and entry-context logic.
- `parameters.py`: TOML parameter loading and weighted scoring.
- `scoring.py`: model result object and watchlist ranking.
- `utils.py`: small dataframe helpers.
- `run_models.py`: command-line entry point. The installed launcher uses
  `langchao_candidates.csv` by default; pass `--sample-data` only for the demo
  dataset.

## Runtime Dependency

The strategy code is designed for Futu OpenD / `futu-api`. OpenD must be
running and logged in before live market data can be queried.

## Click To Start

Double-click this app in Finder:

```text
/Users/jun/Documents/BY股票投资/股票投资系统.app
```

The app shows buttons for:

- running all models
- selecting one model
- opening the generated result file automatically

Result files are saved under:

```text
/Users/jun/Documents/BY股票投资/stock_investment_system/outputs
```

The launcher writes CSV files with Chinese column names and Chinese status text.

## Run With The Installed Candidate Universe

From the workspace root:

```bash
/Users/jun/Documents/BY股票投资/stock_investment_system/env.sh -m stock_investment_system.run_models --model all --max-watchlist 5 --refresh-quotes

To run the built-in demo data instead:

```bash
/Users/jun/Documents/BY股票投资/stock_investment_system/env.sh -m stock_investment_system.run_models --model all --max-watchlist 5 --sample-data
```
```

JSON output:

```bash
/Users/jun/Documents/BY股票投资/stock_investment_system/env.sh -m stock_investment_system.run_models --model all --max-watchlist 5 --format json
```

CSV output:

```bash
/Users/jun/Documents/BY股票投资/stock_investment_system/env.sh -m stock_investment_system.run_models --model all --max-watchlist 5 --format csv
```

## Run Tests

```bash
/Users/jun/Documents/BY股票投资/stock_investment_system/env.sh -m unittest discover -s /Users/jun/Documents/BY股票投资/stock_investment_system/tests -v
```

## CSV Input

You can supply a candidate universe CSV:

```bash
/Users/jun/Documents/BY股票投资/stock_investment_system/env.sh -m stock_investment_system.run_models --data-csv /path/to/candidates.csv
```

Recommended CSV columns:

- `futu_code`, such as `SH.600519`
- `code`
- `name`
- `industry`
- `latest_price`
- `pe_dynamic`
- `pb`
- `turnover_amount`
- `float_market_cap`
- `risk_flags`
- `fundamental_quality_score`
- `growth_quality_score`
- `valuation_score`
- `price_volume_score`
- `pct_change_20d`
