from __future__ import annotations

import os
from pathlib import Path


def prepare_futu_runtime(project_root: Path | None = None) -> Path:
    """Route Futu SDK logs to a workspace-writable process-local directory.

    The Futu Python SDK creates its file logger during import. In restricted
    environments the normal roaming AppData directory or macOS home directory
    may be readable but not writable, causing import to fail before OpenD is
    contacted.
    """
    root = project_root or Path(__file__).resolve().parents[2]
    appdata = Path(os.environ.get("FUTU_APPDATA_DIR", root / ".runtime" / "futu_appdata"))
    appdata.mkdir(parents=True, exist_ok=True)
    os.environ["APPDATA"] = str(appdata)
    os.environ["appdata"] = str(appdata)
    if os.name != "nt":
        futu_home = root / ".runtime" / "futu_home"
        futu_home.mkdir(parents=True, exist_ok=True)
        os.environ["HOME"] = str(futu_home)
    return appdata
