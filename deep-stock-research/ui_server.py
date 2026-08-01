#!/usr/bin/env python3
"""Local launcher for the project's deep stock research collector.

This server intentionally uses only the Python standard library so it can be
started on a clean macOS installation. It creates a reproducible evidence
bundle and saves the user's research request next to the generated brief.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import uuid
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
UI_ROOT = PROJECT_ROOT / "deep-stock-research" / "ui"
COLLECTOR = PROJECT_ROOT / "deep-stock-research" / "scripts" / "collect_deep_research_data.py"
EVIDENCE_COLLECTOR = PROJECT_ROOT / "deep-stock-research" / "scripts" / "collect_futu_evidence.py"
RUNTIME = PROJECT_ROOT / "stock_investment_system" / "env.sh"
OUTPUT_ROOT = PROJECT_ROOT / "outputs" / "deep_research"
HOST = "127.0.0.1"
DEFAULT_PORT = 8765

JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def normalize_codes(raw: str) -> list[str]:
    """Keep the UI forgiving while passing only supported ticker formats on."""
    values = re.split(r"[,，;；\s]+", raw.strip())
    result: list[str] = []
    for value in values:
        if not value:
            continue
        code = value.upper().replace("_", ".")
        if re.fullmatch(r"\d{6}(?:\.(?:SH|SZ|BJ))?", code):
            result.append(code)
        elif re.fullmatch(r"\d{1,5}\.HK", code):
            result.append(code)
        elif re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}(?:\.US)?", code):
            result.append(code)
    return list(dict.fromkeys(result))


def safe_dir_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", value).strip("_") or "stock"


def ticker_dir(code: str, as_of: date) -> Path:
    """Mirror the collector's path convention without importing its module."""
    normalized = code.upper().replace("_", ".")
    if re.fullmatch(r"\d{6}", normalized):
        suffix = "SH" if normalized.startswith("6") else "BJ" if normalized.startswith(("4", "8")) else "SZ"
        name = f"{normalized}.{suffix}"
    elif re.fullmatch(r"\d{1,5}\.HK", normalized):
        name = f"{normalized.split('.')[0].zfill(5)}.HK"
    elif re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", normalized):
        name = normalized
    else:
        name = f"{normalized.removesuffix('.US')}.US"
    return OUTPUT_ROOT / as_of.strftime("%Y%m%d") / safe_dir_name(name)


def find_output_dir(code: str, as_of: date) -> Path:
    """Find the collector directory, whose name is the issuer name when known."""
    date_dir = OUTPUT_ROOT / as_of.strftime("%Y%m%d")
    expected_code = code.upper().replace("_", ".")
    if re.fullmatch(r"\d{6}", expected_code):
        suffix = "SH" if expected_code.startswith("6") else "BJ" if expected_code.startswith(("4", "8")) else "SZ"
        expected_code = f"{expected_code}.{suffix}"
    code_dir = ticker_dir(code, as_of)
    named_matches: list[Path] = []
    code_match: Path | None = None
    if date_dir.is_dir():
        for child in date_dir.iterdir():
            derived_path = child / "research_derived.json"
            if not derived_path.is_file():
                continue
            try:
                derived = json.loads(derived_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            ticker = derived.get("ticker", {}) if isinstance(derived, dict) else {}
            if isinstance(ticker, dict) and ticker.get("code") == expected_code:
                if child == code_dir:
                    code_match = child
                else:
                    named_matches.append(child)
    if named_matches:
        return sorted(named_matches)[0]
    return code_match or code_dir


def request_markdown(payload: dict[str, Any], codes: list[str], as_of: date) -> str:
    lines = [
        "# 深度股票研究请求",
        "",
        f"- 提交时间：{datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"- 研究截止日：{as_of.isoformat()}",
        f"- 股票代码：{', '.join(codes)}",
        f"- 投资风格：{payload.get('style') or '未指定'}",
        f"- 持有周期：{payload.get('horizon') or '未指定'}",
        f"- 风险偏好：{payload.get('risk') or '未指定'}",
        f"- 研究深度：{payload.get('depth') or '标准尽调'}",
        "",
        "## 用户研究要求",
        "",
        (payload.get("requirement") or "未填写"),
        "",
        "## 运行说明",
        "",
        "本次启动器已先读取本地选股模型和 Futu/OpenD 的只读行情、财务、估值、资金流、公司资料，再生成 research_raw.json、research_derived.json、research_brief.md。",
        "research_brief.md 是可复现的证据简报，不等同于完整的联网深度研究结论；缺失数据会保留为证据缺口。",
    ]
    return "\n".join(lines) + "\n"


def run_job(job_id: str, payload: dict[str, Any], codes: list[str]) -> None:
    as_of = date.today()
    with JOBS_LOCK:
        JOBS[job_id].update(status="running", started_at=datetime.now().isoformat(timespec="seconds"))

    try:
        evidence_path = OUTPUT_ROOT / as_of.strftime("%Y%m%d") / "evidence_bundle.json"
        evidence_command = [str(RUNTIME), str(EVIDENCE_COLLECTOR), *codes, "--output", str(evidence_path)]
        evidence_env = os.environ.copy()
        evidence_env.setdefault("FUTU_SKILL_ROOT", str(Path.home() / ".codex" / "skills" / "futuapi" / "scripts"))
        evidence_run = subprocess.run(
            evidence_command,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=1200,
            check=False,
            env=evidence_env,
        )
        if evidence_run.returncode != 0:
            raise RuntimeError((evidence_run.stderr or evidence_run.stdout or "Futu/OpenD 证据采集失败").strip())

        command = [
            str(RUNTIME),
            str(COLLECTOR),
            *codes,
            "--output-root",
            str(OUTPUT_ROOT),
            "--as-of",
            as_of.isoformat(),
            "--horizon",
            str(payload.get("horizon") or "MEDIUM").upper(),
            "--language",
            "zh-CN",
            "--evidence-json",
            str(evidence_path),
        ]
        completed = subprocess.run(
            command,
            cwd=str(PROJECT_ROOT),
            text=True,
            capture_output=True,
            timeout=300,
            check=False,
        )
        request_text = request_markdown(payload, codes, as_of)
        output_dirs: list[str] = []
        for code in codes:
            directory = find_output_dir(code, as_of)
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "research_request.md").write_text(request_text, encoding="utf-8")
            output_dirs.append(str(directory))

        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "采集器运行失败").strip())

        with JOBS_LOCK:
            JOBS[job_id].update(
                status="complete",
                finished_at=datetime.now().isoformat(timespec="seconds"),
                stdout=("[Futu/OpenD evidence]\n" + evidence_run.stdout + "\n[collector]\n" + completed.stdout),
                stderr=(evidence_run.stderr + "\n" + completed.stderr).strip(),
                output_dirs=output_dirs,
                evidence_file=str(evidence_path),
            )
    except Exception as exc:  # Keep the error visible in the UI instead of killing the server.
        with JOBS_LOCK:
            JOBS[job_id].update(
                status="error",
                finished_at=datetime.now().isoformat(timespec="seconds"),
                error=str(exc),
            )


class Handler(BaseHTTPRequestHandler):
    server_version = "DeepStockResearchUI/1.0"

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
            self.send_bytes(target.read_bytes(), "text/plain; charset=utf-8")
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
                raise ValueError("请至少填写一个有效股票代码，例如 600519、0700.HK 或 AAPL")
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
    print(f"Deep Stock Research UI: {url}", flush=True)
    if args.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
