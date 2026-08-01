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
    min_turnover_amount: float = 0.0
    min_float_market_cap: float = 0.0
    fetch_stock_flow: bool = True
    opend_host: str = "127.0.0.1"
    opend_port: int = 11111
    seed_codes: tuple[str, ...] = ()
    risk_keywords: tuple[str, ...] = ("ST", "*ST", "退")
    parameter_file: Path = field(
        default_factory=lambda: Path(__file__).with_name("model_parameters_v1.toml")
    )
