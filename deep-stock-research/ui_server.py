#!/usr/bin/env python3
"""One-click local web interface for Deep Stock Research."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import threading
import uuid
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


APP_ROOT = Path(__file__).resolve().parent
WORKSPACE_ROOT = APP_ROOT.parent
UI_ROOT = APP_ROOT / "ui"
COLLECTOR = APP_ROOT / "scripts" / "collect_deep_research_data.py"
SRC_ROOT = APP_ROOT / "src"
PYTHON = WORKSPACE_ROOT / "stock_investment_system" / ".venv313" / "bin" / "python"
OUTPUT_ROOT = WORKSPACE_ROOT / "outputs" / "deep_research"
RUNTIME_ROOT = APP_ROOT / ".runtime"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def normalize_codes(raw: str) -> list[str]:
    values = re.split(r"[,，;；\s]+", raw.strip())
    result: list[str] = []
    for value in values:
        if not value:
            continue
        code = value.upper().replace("_", ".")
        if re.fullmatch(r"(?:SH\.|SZ\.)?\d{6}", code):
            result.append(code)
        elif re.fullmatch(r"(?:HK\.)?\d{5}", code):
            result.append(code)
        elif re.fullmatch(r"(?:US\.)?[A-Z][A-Z0-9.-]{0,9}", code):
            result.append(code)
    return list(dict.fromkeys(result))


def health() -> dict[str, Any]:
    return {
        "python_ready": PYTHON.is_file(),
        "python_version": ".".join(map(str, __import__("sys").version_info[:3])),
        "asharehub_ready": importlib.util.find_spec("asharehub") is not None,
        "asharehub_key_ready": bool(os.environ.get("ASHAREHUB_API_KEY")),
        "akshare_ready": importlib.util.find_spec("akshare") is not None,
        "futu_ready": importlib.util.find_spec("futu") is not None,
        "output_root": str(OUTPUT_ROOT),
    }


def request_markdown(payload: dict[str, Any], codes: list[str], as_of: date) -> str:
    return "\n".join(
        [
            "# 深度股票研究请求",
            "",
            f"- 提交时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
            f"- 研究截止日：{as_of.isoformat()}",
            f"- 股票代码：{', '.join(codes)}",
            f"- 数据配置：AShareHub={'启用' if payload.get('use_asharehub', True) else '停用'}；Futu={'启用' if payload.get('use_futu') else '停用'}",
            "",
            "## 研究要求",
            "",
            str(payload.get("requirement") or "生成标准深度研究证据底稿。"),
            "",
            "## 说明",
            "",
            "本文件由本机一键界面保存。research_brief.md 是数据证据底稿，不等同于自动买卖建议。",
            "",
        ]
    )


def output_directories(stdout: str) -> list[Path]:
    directories: list[Path] = []
    for line in stdout.splitlines():
        match = re.search(r"\bbrief=(.+)$", line.strip())
        if match:
            path = Path(match.group(1)).expanduser()
            directories.append(path.parent)
    return list(dict.fromkeys(directories))


def run_job(job_id: str, payload: dict[str, Any], codes: list[str]) -> None:
    with JOBS_LOCK:
        JOBS[job_id].update(status="running", started_at=datetime.now().isoformat(timespec="seconds"))

    try:
        as_of = date.fromisoformat(str(payload.get("as_of") or date.today().isoformat()))
        use_asharehub = bool(payload.get("use_asharehub", True))
        use_futu = bool(payload.get("use_futu", False))
        if use_asharehub and not os.environ.get("ASHAREHUB_API_KEY"):
            raise RuntimeError("未读取到 AShareHub API Key。请关闭系统，在终端配置后重新双击启动。")

        date_root = OUTPUT_ROOT / as_of.strftime("%Y%m%d")
        command = [
            str(PYTHON),
            str(COLLECTOR),
            *codes,
            "--as-of",
            as_of.isoformat(),
            "--output-dir",
            str(date_root),
            "--language",
            "zh-CN",
            "--asharehub-profile",
            str(payload.get("profile") or "core"),
        ]
        if not use_asharehub:
            command.append("--skip-asharehub")
        if not use_futu:
            command.append("--skip-futu")
        if payload.get("refresh"):
            command.append("--refresh-asharehub")

        process_env = os.environ.copy()
        process_env["PYTHONPATH"] = str(SRC_ROOT)
        process_env["PYTHONPYCACHEPREFIX"] = str(RUNTIME_ROOT / "pycache")
        completed = subprocess.run(
            command,
            cwd=str(APP_ROOT),
            text=True,
            capture_output=True,
            timeout=1800,
            check=False,
            env=process_env,
        )
        directories = output_directories(completed.stdout)
        request_text = request_markdown(payload, codes, as_of)
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "research_request.md").write_text(request_text, encoding="utf-8")

        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "采集器运行失败").strip()
            raise RuntimeError(message)
        if not directories:
            raise RuntimeError("采集器已结束，但没有找到输出目录。")

        with JOBS_LOCK:
            JOBS[job_id].update(
                status="complete",
                finished_at=datetime.now().isoformat(timespec="seconds"),
                stdout=completed.stdout,
                stderr=completed.stderr,
                output_dirs=[str(path) for path in directories],
            )
    except Exception as exc:
        with JOBS_LOCK:
            JOBS[job_id].update(
                status="error",
                finished_at=datetime.now().isoformat(timespec="seconds"),
                error=str(exc),
            )


class Handler(BaseHTTPRequestHandler):
    server_version = "DeepStockResearchUI/2.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_bytes(self, data: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, value: Any, status: int = 200) -> None:
        self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", status)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/index.html"}:
            self.send_bytes((UI_ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if parsed.path == "/api/health":
            self.send_json(health())
            return
        if parsed.path == "/api/status":
            job_id = parse_qs(parsed.query).get("id", [""])[0]
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id, {"status": "unknown"}))
            self.send_json(job)
            return
        if parsed.path == "/api/file":
            relative = unquote(parse_qs(parsed.query).get("path", [""])[0])
            target = (OUTPUT_ROOT / relative).resolve()
            if OUTPUT_ROOT.resolve() not in target.parents or not target.is_file():
                self.send_json({"error": "文件不存在或路径无效"}, 404)
                return
            content_type = "application/json; charset=utf-8" if target.suffix == ".json" else "text/plain; charset=utf-8"
            self.send_bytes(target.read_bytes(), content_type)
            return
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        if urlparse(self.path).path != "/api/run":
            self.send_json({"error": "Not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("请求内容格式错误")
            codes = normalize_codes(str(payload.get("codes", "")))
            if not codes:
                raise ValueError("请填写有效股票代码，例如 600519、HK.00700 或 US.AAPL")
            if len(codes) > 3:
                raise ValueError("单次最多研究 3 只股票，以免超过 AShareHub 免费额度。")
            date.fromisoformat(str(payload.get("as_of") or date.today().isoformat()))
            job_id = uuid.uuid4().hex[:12]
            with JOBS_LOCK:
                JOBS[job_id] = {"status": "queued", "codes": codes, "request": payload}
            threading.Thread(target=run_job, args=(job_id, payload, codes), daemon=True).start()
            self.send_json({"job_id": job_id, "codes": codes})
        except Exception as exc:
            self.send_json({"error": str(exc)}, 400)


def main() -> None:
    parser = argparse.ArgumentParser(description="Start the local Deep Stock Research UI")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--open-browser", action="store_true")
    args = parser.parse_args()
    if not PYTHON.is_file():
        raise FileNotFoundError(f"Python 3.13 环境不存在：{PYTHON}")
    server = None
    selected_port = args.port
    for candidate in range(args.port, args.port + 10):
        try:
            server = ThreadingHTTPServer((HOST, candidate), Handler)
            selected_port = candidate
            break
        except OSError:
            continue
    if server is None:
        raise OSError(f"无法在 {args.port}-{args.port + 9} 端口启动本机研究界面")
    url = f"http://{HOST}:{selected_port}"
    print(f"深度股票研究系统已启动：{url}", flush=True)
    if args.open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
