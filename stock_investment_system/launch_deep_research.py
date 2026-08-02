#!/usr/bin/env python3
"""Start the watchlist deep-research pipeline in a detached process."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


SYSTEM_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SYSTEM_DIR.parent
RUNTIME = SYSTEM_DIR / "env.sh"
PIPELINE = SYSTEM_DIR / "deep_research_pipeline.py"


def build_command(args: argparse.Namespace) -> list[str]:
    command = [
        str(RUNTIME),
        str(PIPELINE),
        "--watchlist-csv",
        str(args.watchlist_csv.resolve()),
        "--timestamp",
        args.timestamp,
        "--horizon",
        args.horizon,
        "--max-stocks",
        str(args.max_stocks),
    ]
    if args.open_report:
        command.append("--open-report")
    return command


def start_detached(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab", buffering=0) as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    return process.pid


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start stock deep research in a detached process")
    parser.add_argument("--watchlist-csv", type=Path, required=True)
    parser.add_argument("--timestamp", required=True)
    parser.add_argument("--horizon", choices=["SHORT", "MEDIUM", "LONG"], default="MEDIUM")
    parser.add_argument("--max-stocks", type=int, default=0)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--open-report", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    pid = start_detached(build_command(args), args.log.resolve())
    print(f"深度研究已在后台启动，PID={pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
