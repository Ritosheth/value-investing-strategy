from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

from .config import SelectionConfig


def model_parameters(model_key: str, config: SelectionConfig) -> dict:
    params = _load_parameters(Path(config.parameter_file))
    model = params.get(model_key)
    if not isinstance(model, dict):
        raise KeyError(f"Missing model parameters: {model_key}")
    return model


@lru_cache(maxsize=8)
def _load_parameters(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def normalized_weight_tuple(weights: Mapping[str, float], fields: Iterable[str]) -> tuple[tuple[str, float], ...]:
    selected = {field: float(weights.get(field, 0.0)) for field in fields}
    total = sum(value for value in selected.values() if value > 0)
    if total <= 0:
        raise ValueError("Weight sum must be positive")
    return tuple((field, value / total) for field, value in selected.items())


def weighted_score(df: pd.DataFrame, weights: Mapping[str, float] | Iterable[tuple[str, float]]) -> pd.Series:
    pairs = dict(weights)
    total = sum(float(value) for value in pairs.values() if float(value) > 0)
    if total <= 0:
        raise ValueError("Weight sum must be positive")

    score = pd.Series(0.0, index=df.index, dtype="float64")
    for column, weight in pairs.items():
        normalized_weight = float(weight) / total
        values = pd.to_numeric(df[column], errors="coerce").fillna(0.0) if column in df else 0.0
        score = score + values * normalized_weight
    return score.clip(0.0, 100.0)


def parameter_metadata(params: Mapping[str, object]) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for key, value in params.items():
        if isinstance(value, Mapping):
            metadata[f"parameters.{key}"] = ", ".join(
                f"{inner_key}={inner_value}" for inner_key, inner_value in value.items()
            )
    return metadata

