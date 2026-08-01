from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SelectionConfig:
    """Runtime knobs shared by the three selection models."""

    market: str = "CN"
    max_watchlist: int = 20
    max_market_candidates: int = 120
    max_flow_candidates: int = 80
    min_turnover_amount: float = 50_000_000.0
    min_float_market_cap: float = 3_000_000_000.0
    min_listed_days: int = 120
    fetch_stock_flow: bool = True
    live_market_screen: bool = False
    trust_input_scores: bool = False
    opend_host: str = "127.0.0.1"
    opend_port: int = 11111
    seed_codes: tuple[str, ...] = ()
    risk_keywords: tuple[str, ...] = ("ST", "*ST", "退")
    parameter_file: Path = field(
        default_factory=lambda: Path(__file__).with_name("model_parameters_v1.toml")
    )
