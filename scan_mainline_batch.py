from __future__ import annotations

import json
import math
from datetime import datetime

import pandas as pd
from futu import KLType, OpenQuoteContext, RET_OK


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


def pct(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0) or pd.isna(a) or pd.isna(b):
        return None
    return (float(a) / float(b) - 1.0) * 100.0


def safe_num(value):
    try:
        if value is None or pd.isna(value):
            return None
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    except Exception:
        return None


def stage(row: dict) -> str:
    r5 = row.get("ret_5d") or 0
    r10 = row.get("ret_10d") or 0
    r20 = row.get("ret_20d") or 0
    dist_high = row.get("dist_60d_high_pct")
    above_ma20 = bool(row.get("above_ma20"))
    above_ma60 = bool(row.get("above_ma60"))
    vol_ratio = row.get("turnover_ratio_5v20") or 0
    day_pct = row.get("day_pct") or 0
    drawdown = abs(dist_high or 0)

    if above_ma20 and above_ma60 and drawdown <= 5 and (r5 >= 15 or r10 >= 22 or day_pct >= 8):
        return "高位加速"
    if above_ma20 and above_ma60 and drawdown <= 8 and vol_ratio >= 1.35 and (r5 < 12 or day_pct < 5):
        return "高位分歧"
    if above_ma20 and above_ma60 and r20 >= 12 and drawdown <= 12:
        return "主升"
    if (not above_ma20 and r5 < 0 and r10 < 0) or (drawdown >= 18 and r20 < 0):
        return "退潮"
    return "分歧"


def role(code: str, stage_value: str) -> tuple[str, str]:
    role_map = {
        "SH.688256": ("趋势龙头/情绪锚", "国产AI芯片弹性最大，趋势辨识度最高"),
        "SH.688041": ("主线容量核心", "国产算力芯片容量核心"),
        "SZ.000977": ("主线容量核心/情绪前排", "AI服务器整机核心，短线弹性强"),
        "SH.603019": ("主线容量核心", "服务器/HPC权重锚"),
        "SZ.002371": ("半导体设备趋势核心", "设备链容量核心"),
        "SH.688012": ("半导体设备趋势核心", "刻蚀/薄膜设备核心"),
        "SH.688120": ("设备补涨/高弹性", "CMP设备弹性标的"),
        "SH.688019": ("材料跟随/补涨", "材料链核心但弹性弱于设备"),
        "SZ.300666": ("材料跟随/补涨", "靶材材料链弹性"),
        "SH.600584": ("先进封装容量核心", "封测大市值核心"),
        "SZ.002156": ("先进封装高弹性", "封测弹性前排"),
        "SZ.002185": ("封测跟风", "封测链跟随"),
        "SZ.301018": ("液冷低位/补涨", "算力配套温控弹性"),
        "SH.688008": ("AI存储链容量核心", "内存接口/CXL核心"),
        "SZ.300223": ("存储链跟风", "存储/边缘AI弹性较弱"),
        "SZ.000938": ("网络/服务器跟随", "算力网络与服务器链跟随"),
    }
    base_role, reason = role_map[code]
    if stage_value == "高位加速":
        base_role = base_role + "，短线加速前排"
    elif stage_value == "退潮":
        base_role = base_role + "，当前退潮观察"
    elif stage_value == "高位分歧":
        base_role = base_role + "，高位换手分歧"
    return base_role, reason


def main() -> None:
    codes = [code for code, _, _ in STOCKS]
    ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
    snapshot_map = {}
    try:
        ret, data = ctx.get_market_snapshot(codes)
        if ret == RET_OK and data is not None and not data.empty:
            snapshot_map = {str(row["code"]): row for _, row in data.iterrows()}

        rows = []
        for code, name, theme in STOCKS:
            ret, kl = ctx.request_history_kline(code, ktype=KLType.K_DAY, max_count=80)
            if ret != RET_OK or kl is None or kl.empty:
                rows.append({"code": code, "name": name, "theme": theme, "error": str(kl)})
                continue
            kl = kl.copy()
            for col in ["close", "high", "low", "turnover", "volume"]:
                kl[col] = pd.to_numeric(kl[col], errors="coerce")
            last = kl.iloc[-1]
            close = safe_num(last["close"])
            snap = snapshot_map.get(code)
            last_price = safe_num(snap.get("last_price")) if snap is not None else close
            prev_close = safe_num(snap.get("prev_close")) if snap is not None else safe_num(kl.iloc[-2]["close"])
            high20 = safe_num(kl["high"].tail(20).max())
            high60 = safe_num(kl["high"].tail(60).max())
            low60 = safe_num(kl["low"].tail(60).min())
            ma20 = safe_num(kl["close"].tail(20).mean())
            ma60 = safe_num(kl["close"].tail(60).mean())
            turnover = safe_num(snap.get("turnover")) if snap is not None else safe_num(last["turnover"])
            avg_turnover5 = safe_num(kl["turnover"].tail(5).mean())
            avg_turnover20 = safe_num(kl["turnover"].tail(20).mean())
            row = {
                "code": code,
                "name": name,
                "theme": theme,
                "last_price": last_price,
                "prev_close": prev_close,
                "day_pct": pct(last_price, prev_close),
                "ret_5d": pct(close, safe_num(kl["close"].iloc[-6])) if len(kl) >= 6 else None,
                "ret_10d": pct(close, safe_num(kl["close"].iloc[-11])) if len(kl) >= 11 else None,
                "ret_20d": pct(close, safe_num(kl["close"].iloc[-21])) if len(kl) >= 21 else None,
                "ret_60d": pct(close, safe_num(kl["close"].iloc[-61])) if len(kl) >= 61 else None,
                "dist_20d_high_pct": pct(close, high20),
                "dist_60d_high_pct": pct(close, high60),
                "dist_60d_low_pct": pct(close, low60),
                "ma20": ma20,
                "ma60": ma60,
                "above_ma20": bool(close and ma20 and close >= ma20),
                "above_ma60": bool(close and ma60 and close >= ma60),
                "turnover": turnover,
                "avg_turnover5": avg_turnover5,
                "avg_turnover20": avg_turnover20,
                "turnover_ratio_5v20": (avg_turnover5 / avg_turnover20) if avg_turnover5 and avg_turnover20 else None,
                "high_60d": high60,
                "low_60d": low60,
            }
            row["stage"] = stage(row)
            row["mainline_role"], row["role_reason"] = role(code, row["stage"])
            rows.append(row)
        print(json.dumps({"as_of": datetime.now().isoformat(timespec="seconds"), "rows": rows}, ensure_ascii=False, indent=2))
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
