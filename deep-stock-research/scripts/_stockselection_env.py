"""Small cross-platform bootstrap used by the shared collector.

The shared Windows project normally supplies this helper to re-exec the
collector inside its project virtualenv.  On macOS/Linux the caller can
select the interpreter directly, so this fallback intentionally does nothing.
"""

from __future__ import annotations


def ensure_stockselection_venv() -> None:
    """Keep direct script execution portable when no project bootstrap exists."""

