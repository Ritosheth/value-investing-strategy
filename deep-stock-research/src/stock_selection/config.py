from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_DAILY_MODEL_OUTPUT_DIR = DEFAULT_OUTPUT_DIR / "daily_model_results"
DEFAULT_EVALUATION_OUTPUT_DIR = DEFAULT_OUTPUT_DIR / "evaluation"
DEFAULT_TUNING_OUTPUT_DIR = DEFAULT_OUTPUT_DIR / "tuning"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DB_PATH = DEFAULT_DATA_DIR / "stock_selection.sqlite"
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "configs"
DEFAULT_PARAMETER_FILE = DEFAULT_CONFIG_DIR / "model_parameters_v1.toml"
DEFAULT_BENCHMARK_CODE = "399001"
DEFAULT_BENCHMARK_NAME = "Shenzhen Component"


@dataclass(frozen=True)
class SelectionConfig:
    max_watchlist: int = 20
    min_turnover_amount: float = 50_000_000.0
    min_float_market_cap: float = 2_000_000_000.0
    output_dir: Path = DEFAULT_OUTPUT_DIR
    fetch_market_snapshot: bool = True
    fetch_stock_flow: bool = True
    max_flow_candidates: int = 40
    max_market_candidates: int = 10
    futu_host: str = "127.0.0.1"
    futu_port: int = 11111
    max_screen_results: int = 6000
    db_path: Path = DEFAULT_DB_PATH
    parameter_file: Path = DEFAULT_PARAMETER_FILE
    parameter_version: str = "rules_v1"
