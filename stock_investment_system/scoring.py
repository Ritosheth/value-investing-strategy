from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class ModelResult:
    model_name: str
    description: str
    watchlist: pd.DataFrame
    metadata: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "description": self.description,
            "watchlist": self.watchlist.to_dict(orient="records"),
            "metadata": self.metadata,
            "warnings": self.warnings,
        }


def top_watchlist(
    df: pd.DataFrame,
    *,
    score_col: str,
    limit: int,
    preferred_bucket: str | None,
    columns: list[str],
) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=columns)

    ranked = df.copy()
    if score_col not in ranked:
        ranked[score_col] = 0.0
    if "bucket" not in ranked:
        ranked["bucket"] = ""
    ranked["_bucket_rank"] = 0 if preferred_bucket is None else (ranked["bucket"] != preferred_bucket).astype(int)
    ranked["_score"] = pd.to_numeric(ranked[score_col], errors="coerce").fillna(0.0)
    ranked = ranked.sort_values(["_bucket_rank", "_score"], ascending=[True, False]).head(limit)

    for column in columns:
        if column not in ranked:
            ranked[column] = pd.NA
    return ranked[columns].reset_index(drop=True)
