from __future__ import annotations

import json
import math
import subprocess
import sys
from datetime import datetime
from pathlib import Path


STOCKS = [
    ("SZ.000977", "浪潮信息", "AI算力服务器/整机"),
    ("SZ.000938", "紫光股份", "AI算力服务器/网络设备"),
    ("SH.688012", "中微公司", "半导体设备"),
    ("SH.688120", "华海清科", "半导体设备/CMP"),
    ("SH.688019", "安集科技", "半导体材料"),
    ("SH.600584", "长电科技", "先进封装/封测"),
    ("SZ.002185", "华天科技", "封测"),
    ("SH.688256", "寒武纪", "AI芯片/国产算力"),
    ("SH.688041", "海光信息", "国产CPU/GPU/算力芯片"),
    ("SZ.301018", "申菱环境", "液冷温控/数据中心"),
    ("SZ.300666", "江丰电子", "半导体材料/靶材"),
    ("SZ.002156", "通富微电", "先进封装/封测"),
    ("SH.603019", "中科曙光", "AI算力服务器/HPC"),
    ("SH.688008", "澜起科技", "内存接口/CXL/AI存储链"),
    ("SZ.300223", "北京君正", "存储/边缘AI芯片"),
    ("SZ.002371", "北方华创", "半导体设备"),
]

KLINE_SCRIPT = Path("/Users/jun/.codex/skills/futuapi/scripts/quote/get_kline.py")


def load_json_from_output(text: str):
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise ValueError("no JSON line found")


def num(value):
    try:
        value = float(value)
        return None if math.isnan(value) or math.isinf(value) else value
    except Exception:
        return None


def pct(a, b):
    a = num(a)
    b = num(b)
    if a is None or b in (None, 0):
        return None
    return (a / b - 1) * 100


def avg(values):
    clean = [num(value) for value in values if num(value) is not None]
    return sum(clean) / len(clean) if clean else None


def classify(row):
    r5 = row.get("ret_5d") or 0
    r10 = row.get("ret_10d") or 0
    r20 = row.get("ret_20d") or 0
    dist_high = row.get("dist_60d_high_pct")
    drawdown = abs(dist_high or 0)
    day_pct = row.get("day_pct") or 0
    vol_ratio = row.get("turnover_ratio_5v20") or 0
    above20 = row.get("above_ma20")
    above60 = row.get("above_ma60")

    if above20 and above60 and drawdown <= 5 and (r5 >= 15 or r10 >= 22 or day_pct >= 8):
        return "高位加速"
    if above20 and above60 and drawdown <= 8 and vol_ratio >= 1.25 and (r5 < 15 or day_pct < 5):
        return "高位分歧"
    if above20 and above60 and r20 >= 12 and drawdown <= 12:
        return "主升"
    if (not above20 and r5 < 0 and r10 < 0) or (drawdown >= 18 and r20 < 0):
        return "退潮"
    return "分歧"


def role(code, stage):
    roles = {
        "SH.688256": "趋势龙头/情绪锚",
        "SH.688041": "主线容量核心",
        "SZ.000977": "主线容量核心/情绪前排",
        "SH.603019": "主线容量核心",
        "SZ.002371": "半导体设备趋势核心",
        "SH.688012": "半导体设备趋势核心",
        "SH.688120": "设备补涨/高弹性",
        "SH.688019": "材料跟随/补涨",
        "SZ.300666": "材料跟随/补涨",
        "SH.600584": "先进封装容量核心",
        "SZ.002156": "先进封装高弹性",
        "SZ.002185": "封测跟风",
        "SZ.301018": "液冷低位/补涨",
        "SH.688008": "AI存储链容量核心",
        "SZ.300223": "存储链跟风",
        "SZ.000938": "网络/服务器跟随",
    }
    suffix = {
        "高位加速": "，短线加速前排",
        "高位分歧": "，高位换手分歧",
        "退潮": "，当前退潮观察",
        "主升": "，趋势主升",
        "分歧": "，分歧观察",
    }[stage]
    return roles[code] + suffix


def scan_one(code, name, theme):
    proc = subprocess.run(
        ["python3", str(KLINE_SCRIPT), code, "--ktype", "1d", "--num", "80", "--json"],
        check=True,
        text=True,
        capture_output=True,
        timeout=25,
    )
    payload = load_json_from_output(proc.stdout)
    data = payload["data"]
    closes = [num(x["close"]) for x in data]
    highs = [num(x["high"]) for x in data]
    lows = [num(x["low"]) for x in data]
    turnovers = [num(x["turnover"]) for x in data]
    close = closes[-1]
    prev = closes[-2] if len(closes) >= 2 else None
    high20 = max(highs[-20:])
    high60 = max(highs[-60:])
    low60 = min(lows[-60:])
    ma20 = avg(closes[-20:])
    ma60 = avg(closes[-60:])
    avg5 = avg(turnovers[-5:])
    avg20 = avg(turnovers[-20:])
    row = {
        "code": code,
        "name": name,
        "theme": theme,
        "date": data[-1]["time"][:10],
        "close": close,
        "day_pct": pct(close, prev),
        "ret_5d": pct(close, closes[-6]) if len(closes) >= 6 else None,
        "ret_10d": pct(close, closes[-11]) if len(closes) >= 11 else None,
        "ret_20d": pct(close, closes[-21]) if len(closes) >= 21 else None,
        "ret_60d": pct(close, closes[-61]) if len(closes) >= 61 else None,
        "dist_20d_high_pct": pct(close, high20),
        "dist_60d_high_pct": pct(close, high60),
        "dist_60d_low_pct": pct(close, low60),
        "ma20": ma20,
        "ma60": ma60,
        "above_ma20": close is not None and ma20 is not None and close >= ma20,
        "above_ma60": close is not None and ma60 is not None and close >= ma60,
        "turnover": turnovers[-1],
        "turnover_ratio_5v20": (avg5 / avg20) if avg5 and avg20 else None,
    }
    row["stage"] = classify(row)
    row["mainline_role"] = role(code, row["stage"])
    return row


def main():
    rows = []
    errors = []
    for stock in STOCKS:
        try:
            rows.append(scan_one(*stock))
        except subprocess.CalledProcessError as exc:
            code, name, theme = stock
            errors.append({
                "code": code,
                "name": name,
                "theme": theme,
                "error": str(exc),
                "stdout": exc.stdout[-1000:] if exc.stdout else "",
                "stderr": exc.stderr[-1000:] if exc.stderr else "",
            })
        except Exception as exc:
            code, name, theme = stock
            errors.append({"code": code, "name": name, "theme": theme, "error": str(exc)})
    print(json.dumps({"as_of": datetime.now().isoformat(timespec="seconds"), "rows": rows, "errors": errors}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
