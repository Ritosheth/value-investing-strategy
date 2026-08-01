#!/usr/bin/env python3
"""Evaluate saved research decisions against later read-only market snapshots."""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path
from typing import Any


WORKSPACE_ROOT = Path(__file__).resolve().parents[2]
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))

from stock_investment_system.futu_client import FutuClient


def to_futu_code(code: str) -> str:
    value = code.strip().upper()
    if "." in value:
        number, market = value.split(".", 1)
        if market in {"SH", "SZ", "BJ", "HK", "US"}:
            return f"{market}.{number}"
        if number in {"SH", "SZ", "BJ", "HK", "US"}:
            return value
    if value.isdigit() and len(value) == 6:
        market = "SH" if value.startswith("6") else "BJ" if value.startswith(("4", "8")) else "SZ"
        return f"{market}.{value}"
    return f"US.{value.removesuffix('.US')}"


def evaluate_rows(rows: list[dict[str, Any]], prices: dict[str, float], evaluation_date: date) -> list[dict[str, Any]]:
    evaluated: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        code = str(row.get("code") or "")
        start_price = _number(row.get("current_price"))
        end_price = prices.get(code)
        try:
            holding_days = (evaluation_date - date.fromisoformat(str(row.get("as_of")))).days
        except ValueError:
            holding_days = None
        row["evaluation_date"] = evaluation_date.isoformat()
        row["holding_days"] = holding_days
        row["evaluation_price"] = end_price
        row["forward_return_pct"] = round((end_price / start_price - 1) * 100, 2) if start_price and end_price else None
        row["benchmark_return_pct"] = None
        row["excess_return_pct"] = None
        evaluated.append(row)
    return evaluated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate saved stock-research decisions at current Futu prices.")
    parser.add_argument("--ledger", type=Path, default=Path("outputs/deep_research/research_history.csv"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--evaluation-date", default=date.today().isoformat())
    args = parser.parse_args(argv)
    if not args.ledger.exists():
        raise FileNotFoundError(f"Research history not found: {args.ledger}")
    with args.ledger.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    client = FutuClient()
    try:
        futu_codes = [to_futu_code(str(row.get("code") or "")) for row in rows]
        snapshot = client.market_snapshot(list(dict.fromkeys(futu_codes)))
    finally:
        client.close()
    prices = {
        _display_code(str(item.futu_code)): float(item.latest_price)
        for item in snapshot.itertuples()
        if _number(getattr(item, "latest_price", None)) is not None
    }
    evaluated = evaluate_rows(rows, prices, date.fromisoformat(args.evaluation_date))
    output = args.output or args.ledger.with_name(f"{args.ledger.stem}_evaluated_{args.evaluation_date.replace('-', '')}.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) + ["evaluation_date", "holding_days", "evaluation_price", "forward_return_pct", "benchmark_return_pct", "excess_return_pct"] if rows else []
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(evaluated)
    print(output)
    if client.warnings:
        for warning in client.warnings:
            print(f"warning: {warning}", file=sys.stderr)
    return 0


def _display_code(futu_code: str) -> str:
    market, code = futu_code.split(".", 1)
    return f"{code}.{market}"


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
