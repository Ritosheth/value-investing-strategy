from __future__ import annotations

import time
import datetime as dt
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from .utils import normalize_code
from .futu_runtime import prepare_futu_runtime


class FutuUnavailable(RuntimeError):
    """Raised when a required Futu endpoint cannot provide usable data."""


def install_protobuf_label_shim() -> None:
    try:
        from google._upb._message import FieldDescriptor
    except Exception:
        try:
            from google.protobuf.descriptor import FieldDescriptor
        except Exception:
            return

    if isinstance(getattr(FieldDescriptor, "label", None), property):
        return

    def label(self: Any) -> int:
        if getattr(self, "is_repeated", False):
            return self.LABEL_REPEATED
        if getattr(self, "is_required", False):
            return self.LABEL_REQUIRED
        return self.LABEL_OPTIONAL

    try:
        FieldDescriptor.label = property(label)
    except Exception:
        return


@dataclass(frozen=True)
class FlowSummary:
    code: str
    flow_net_5d: float = 0.0
    flow_net_10d: float = 0.0
    flow_net_20d: float = 0.0
    large_order_net_20d: float = 0.0
    flow_positive_ratio_20d: float = 0.0
    flow_days: int = 0


@dataclass(frozen=True)
class HistoryKlineQuota:
    used_quota: int | None = None
    remain_quota: int | None = None
    detail_list: tuple[dict[str, Any], ...] = ()


class FutuClient:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 11111,
        min_request_interval_seconds: float = 0.55,
        max_retries: int = 4,
        rate_limit_cooldown_seconds: float = 31.0,
        max_rate_limit_retries: int | None = None,
        endpoint_min_interval_seconds: dict[str, float] | None = None,
    ) -> None:
        prepare_futu_runtime()
        install_protobuf_label_shim()
        try:
            import futu
            from futu import OpenQuoteContext, SysConfig
        except ImportError as exc:
            raise FutuUnavailable("Missing dependency: futu. Activate .venv or install futu-api.") from exc

        SysConfig.enable_console_log(False)
        self.futu = futu
        self.quote_ctx = OpenQuoteContext(host=host, port=port)
        self.host = host
        self.port = port
        self.warnings: list[str] = []
        self._warned: set[str] = set()
        self.min_request_interval_seconds = min_request_interval_seconds
        self.max_retries = max_retries
        self.rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        # None deliberately means "wait until Futu accepts the request". Rate
        # limiting is not an endpoint failure and must not abort a daily run.
        self.max_rate_limit_retries = max_rate_limit_retries
        self._last_request_at = 0.0
        self._last_request_by_method: dict[str, float] = {}
        conservative_intervals = {
            "get_heat_map_data": 3.1,
            "get_rise_fall_distribution": 3.1,
            "get_industrial_chain_list": 3.1,
            "get_industrial_chain_detail": 3.1,
            "get_industrial_chain_by_plate": 3.1,
            "get_industrial_plate_stock": 3.1,
        }
        if endpoint_min_interval_seconds:
            conservative_intervals.update(endpoint_min_interval_seconds)
        self.endpoint_min_interval_seconds = conservative_intervals

    def close(self) -> None:
        self.quote_ctx.close()

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def warn_once(self, key: str, message: str) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        self.warn(message)

    def subscription_status(self, *, is_all_conn: bool = True) -> dict[str, Any]:
        data = self._call("query_subscription", is_all_conn=is_all_conn, required=False)
        payload = api_payload(data)
        return payload if isinstance(payload, dict) else {}

    def unsubscribe_all(self) -> bool:
        ret = self.quote_ctx.unsubscribe_all()
        if not isinstance(ret, tuple) or not ret:
            return False
        ret_code = ret[0]
        if ret_code == self.futu.RET_OK:
            return True
        message = ret[1] if len(ret) > 1 else "unknown error"
        self.warn_once("unsubscribe_all_failed", f"unsubscribe_all failed: {message}")
        return False

    def history_kline_quota(self, *, get_detail: bool = True) -> HistoryKlineQuota | None:
        data = self._call("get_history_kl_quota", get_detail=get_detail, required=False)
        if data is None:
            return None
        return normalize_history_kline_quota(data)

    def _log_endpoint(self, message: str) -> None:
        print(str(message).replace("\ufffd", "?").encode("ascii", errors="replace").decode("ascii"), flush=True)

    def _call(self, method: str, *args: Any, required: bool = True, **kwargs: Any) -> Any:
        started = time.perf_counter()
        self._log_endpoint(f"[futu] start {method} args={args!r} kwargs={kwargs!r}")
        transient_attempt = 0
        rate_limit_attempt = 0
        while True:
            self._wait_for_request_slot(method)
            try:
                ret = getattr(self.quote_ctx, method)(*args, **kwargs)
            except Exception as exc:
                retry_kind = self._retry_kind(exc)
                if retry_kind == "rate_limit" and self._can_retry_rate_limit(rate_limit_attempt):
                    rate_limit_attempt += 1
                    self._sleep_before_retry(method, exc, rate_limit_attempt, rate_limit=True)
                    continue
                if retry_kind == "transient" and transient_attempt < self.max_retries:
                    transient_attempt += 1
                    self._sleep_before_retry(method, exc, transient_attempt, rate_limit=False)
                    continue
                elapsed = time.perf_counter() - started
                self._log_endpoint(f"[futu] fail {method} elapsed={elapsed:.2f}s error={exc}")
                if required:
                    raise FutuUnavailable(f"{method} failed: {exc}") from exc
                self.warn_once(f"{method}:{exc}", f"{method} skipped: {exc}")
                return None

            if not isinstance(ret, tuple) or not ret:
                return ret
            ret_code = ret[0]
            data = ret[1] if len(ret) > 1 else None
            if ret_code != self.futu.RET_OK:
                retry_kind = self._retry_kind(data)
                if retry_kind == "rate_limit" and self._can_retry_rate_limit(rate_limit_attempt):
                    rate_limit_attempt += 1
                    self._sleep_before_retry(method, data, rate_limit_attempt, rate_limit=True)
                    continue
                if retry_kind == "transient" and transient_attempt < self.max_retries:
                    transient_attempt += 1
                    self._sleep_before_retry(method, data, transient_attempt, rate_limit=False)
                    continue
                elapsed = time.perf_counter() - started
                self._log_endpoint(f"[futu] fail {method} elapsed={elapsed:.2f}s error={data}")
                if required:
                    raise FutuUnavailable(f"{method} failed: {data}")
                self.warn_once(f"{method}:{data}", f"{method} skipped: {data}")
                return None

            payload = data if len(ret) == 2 else ret[1:]
            rows = self._row_count(payload)
            elapsed = time.perf_counter() - started
            self._log_endpoint(f"[futu] ok {method} rows={rows} elapsed={elapsed:.2f}s")
            return payload

    def _wait_for_request_slot(self, method: str | None = None) -> None:
        now = time.perf_counter()
        global_interval = max(self.min_request_interval_seconds, 0.0)
        method_intervals = getattr(self, "endpoint_min_interval_seconds", {})
        method_interval = max(float(method_intervals.get(method, 0.0)), 0.0) if method else 0.0
        last_by_method = getattr(self, "_last_request_by_method", {})
        global_wait = global_interval - (now - self._last_request_at)
        method_wait = method_interval - (now - last_by_method.get(method, 0.0)) if method else 0.0
        wait_seconds = max(global_wait, method_wait)
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        requested_at = time.perf_counter()
        self._last_request_at = requested_at
        if method:
            last_by_method[method] = requested_at
            self._last_request_by_method = last_by_method

    def _retry_kind(self, error: Any) -> str | None:
        text = str(error).lower()
        if any(
            marker in text
            for marker in (
                "frequency",
                "frequent",
                "too many request",
                "request too fast",
                "rate limit",
                "频率",
                "频繁",
                "过快",
            )
        ):
            return "rate_limit"
        if any(marker in text for marker in ("timed out", "timeout", "temporarily", "temporary", "busy")):
            return "transient"
        return None

    def _should_retry(self, error: Any) -> bool:
        return self._retry_kind(error) is not None

    def _can_retry_rate_limit(self, completed_attempts: int) -> bool:
        return self.max_rate_limit_retries is None or completed_attempts < self.max_rate_limit_retries

    def _sleep_before_retry(self, method: str, error: Any, attempt: int, *, rate_limit: bool | None = None) -> None:
        if rate_limit is None:
            rate_limit = self._retry_kind(error) == "rate_limit"
        delay = self.rate_limit_cooldown_seconds if rate_limit else min(2.0**attempt, 10.0)
        self._log_endpoint(f"[futu] retry {method} attempt={attempt} wait={delay:.1f}s error={error}")
        time.sleep(delay)

    def _row_count(self, payload: Any) -> int | str:
        payload = api_payload(payload)
        if hasattr(payload, "__len__"):
            return len(payload)
        return "?"

    def universe(self) -> pd.DataFrame:
        frames = []
        for market in (self.futu.Market.SH, self.futu.Market.SZ):
            frame = pd.DataFrame(self._call("get_stock_basicinfo", market, self.futu.SecurityType.STOCK))
            if not frame.empty:
                frames.append(frame)
        if not frames:
            raise FutuUnavailable("get_stock_basicinfo returned no A-share rows")
        out = pd.concat(frames, ignore_index=True)
        out["futu_code"] = out["code"].astype(str)
        out["code"] = out["futu_code"].map(normalize_code)
        out["name"] = out["name"].astype(str)
        return out.drop_duplicates("code")

    def market_snapshot(self, codes: list[str], *, batch_size: int = 400) -> pd.DataFrame:
        frames = []
        for start in range(0, len(codes), batch_size):
            batch = codes[start : start + batch_size]
            data = self._call("get_market_snapshot", batch)
            frame = pd.DataFrame(data)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            raise FutuUnavailable("get_market_snapshot returned no rows")
        return normalize_snapshot(pd.concat(frames, ignore_index=True))

    def stock_screen(self, *, max_results: int = 6000, page_count: int = 200) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        page_from = 0
        while page_from < max_results:
            req = build_stock_screen_request(page_from=page_from, page_count=page_count)
            data = self._call("get_stock_screen", req)
            last_page, all_count, items = data
            rows.extend(parse_stock_screen_items(items))
            page_from += page_count
            if last_page or page_from >= all_count:
                break
        if not rows:
            raise FutuUnavailable("get_stock_screen returned no usable rows")
        return pd.DataFrame(rows).drop_duplicates("code")

    def history_summary(self, futu_code: str, *, start: str, end: str) -> dict[str, Any]:
        frame = self._history_kline_frame(futu_code, start=start, end=end)
        if frame.empty or "close" not in frame:
            return {"code": normalize_code(futu_code)}
        frame = frame.sort_values("time_key")
        latest = frame.iloc[-1]
        row: dict[str, Any] = {
            "code": normalize_code(futu_code),
            "latest_price": latest.get("close"),
            "turnover_amount": latest.get("turnover"),
            "turnover_rate": latest.get("turnover_rate"),
        }
        for days in (20, 60, 120):
            if len(frame) >= days:
                base = float(frame.iloc[-days]["close"])
                if base:
                    row[f"return_{days}d"] = (float(latest["close"]) / base - 1.0) * 100.0
        row["sixty_day_change"] = row.get("return_60d", 0.0)
        return row

    def daily_price_history(
        self,
        futu_code: str,
        *,
        start: str,
        end: str,
        adjust: str = "qfq",
    ) -> pd.DataFrame:
        frame = self._history_kline_frame(futu_code, start=start, end=end, adjust=adjust)
        if frame.empty:
            return pd.DataFrame()
        return normalize_daily_price_history(frame)

    def capital_flow_summary(self, futu_code: str, *, start: str | None, end: str | None) -> dict[str, Any]:
        frame = self._capital_flow_frame(futu_code, period_type=self.futu.PeriodType.DAY, start=start, end=end)
        if frame.empty:
            return {"code": normalize_code(futu_code)}
        if "capital_flow_item_time" in frame:
            frame = frame.sort_values("capital_flow_item_time")
        main = pd.to_numeric(frame.get("main_in_flow", 0.0), errors="coerce").fillna(0.0)
        super_flow = pd.to_numeric(frame.get("super_in_flow", 0.0), errors="coerce").fillna(0.0)
        big_flow = pd.to_numeric(frame.get("big_in_flow", 0.0), errors="coerce").fillna(0.0)
        row = {
            "code": normalize_code(futu_code),
            "flow_net": float(main.tail(10).sum()),
            "flow_net_5d": float(main.tail(5).sum()),
            "flow_net_10d": float(main.tail(10).sum()),
            "flow_net_20d": float(main.tail(20).sum()),
            "flow_net_60d": float(main.tail(60).sum()),
            "large_order_net_20d": float((super_flow + big_flow).tail(20).sum()),
            "large_order_net_60d": float((super_flow + big_flow).tail(60).sum()),
            "flow_positive_ratio_20d": float((main.tail(20) > 0).mean() * 100.0),
            "flow_positive_ratio": float((main.tail(10) > 0).mean() * 100.0),
            "flow_days": int(min(len(frame), 20)),
        }
        return row

    def _history_kline_frame(
        self,
        futu_code: str,
        *,
        start: str,
        end: str,
        adjust: str = "qfq",
        max_count: int = 1000,
    ) -> pd.DataFrame:
        quota = self.history_kline_quota(get_detail=True)
        if quota is not None and not history_quota_allows_request(futu_code, quota):
            self.warn_once(
                f"history_kline_quota_exhausted:{futu_code}",
                (
                    "Skipped request_history_kline for "
                    f"{futu_code}: historical candlestick quota is exhausted "
                    f"({quota.used_quota} used, {quota.remain_quota} remaining)."
                ),
            )
            return pd.DataFrame()

        frames: list[pd.DataFrame] = []
        page_req_key = None
        while True:
            kwargs = {
                "start": start,
                "end": end,
                "ktype": self.futu.KLType.K_DAY,
                "autype": {
                    "hfq": self.futu.AuType.HFQ,
                    "qfq": self.futu.AuType.QFQ,
                    "": self.futu.AuType.NONE,
                }.get(adjust, self.futu.AuType.QFQ),
                "max_count": max_count,
                "required": False,
            }
            if page_req_key is not None:
                kwargs["page_req_key"] = page_req_key
            data = self._call("request_history_kline", futu_code, **kwargs)
            frame = payload_frame(data)
            if not frame.empty:
                frames.append(frame)
            page_req_key = data[1] if isinstance(data, tuple) and len(data) > 1 else None
            if page_req_key is None:
                break
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        return out.drop_duplicates(["code", "time_key"] if {"code", "time_key"}.issubset(out.columns) else None)

    def _capital_flow_frame(self, futu_code: str, *, period_type: Any, start: str | None, end: str | None) -> pd.DataFrame:
        windows = capital_flow_windows(start, end)
        frames: list[pd.DataFrame] = []
        for window_start, window_end in windows:
            data = self._call(
                "get_capital_flow",
                futu_code,
                period_type=period_type,
                start=window_start,
                end=window_end,
                required=False,
            )
            frame = payload_frame(data)
            if not frame.empty:
                frames.append(frame)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        if "capital_flow_item_time" in out.columns:
            out = out.drop_duplicates(["capital_flow_item_time"])
        else:
            out = out.drop_duplicates()
        return out

    def valuation_detail(self, futu_code: str) -> dict[str, Any]:
        data = self._call("get_valuation_detail", futu_code, required=False)
        frame = payload_frame(data)
        row: dict[str, Any] = {"code": normalize_code(futu_code)}
        if frame.empty:
            return row
        trend = frame.iloc[0].get("trend")
        if isinstance(trend, dict):
            row["valuation_percentile"] = trend.get("valuation_percentile")
            row["valuation_current_value"] = trend.get("current_value")
        return row

    def financial_statement_probe(self, futu_code: str) -> bool:
        data = self._call("get_financials_statements", futu_code, num=10, required=False)
        return data is not None

    def plate_metadata(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for market in (self.futu.Market.SH, self.futu.Market.SZ):
            for plate_class in (self.futu.Plate.INDUSTRY, self.futu.Plate.CONCEPT):
                data = self._call("get_plate_list", market, plate_class, required=False)
                frame = payload_frame(data)
                counts[f"{market}_{plate_class}"] = len(frame)
        return counts

    def owner_plate(self, futu_codes: list[str]) -> pd.DataFrame:
        data = self._call("get_owner_plate", futu_codes, required=False)
        frame = payload_frame(data)
        if frame.empty:
            return pd.DataFrame()
        frame["code"] = frame["code"].map(normalize_code)
        return frame

    def plate_catalog(self) -> pd.DataFrame:
        """Return the A-share industry/concept catalogue without duplicate SH/SZ rows."""
        frames: list[pd.DataFrame] = []
        # Futu documents that SH and SZ both return the combined A-share plate
        # set. Query only SH to halve calls and avoid duplicate rate usage.
        market = self.futu.Market.SH
        for plate_class in (self.futu.Plate.INDUSTRY, self.futu.Plate.CONCEPT):
            data = self._call("get_plate_list", market, plate_class, required=False)
            frame = payload_frame(data)
            if frame.empty:
                continue
            frame["plate_type"] = str(plate_class)
            frame["source_market"] = str(market)
            frames.append(frame)
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        return out.drop_duplicates("code").reset_index(drop=True)

    def industrial_chain_catalog(self, *, market: Any | None = None, count: int = 50) -> pd.DataFrame:
        market = market or self.futu.Market.SH
        frames: list[pd.DataFrame] = []
        page = None
        while True:
            data = self._call(
                "get_industrial_chain_list",
                market,
                count=count,
                page=page,
                required=False,
            )
            frame = payload_frame(data)
            if not frame.empty:
                frames.append(frame)
            page = data[1] if isinstance(data, tuple) and len(data) > 1 else None
            if not page:
                break
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True).drop_duplicates("chain_id")

    def industrial_chain_detail(self, chain_id: int) -> dict[str, Any]:
        data = self._call("get_industrial_chain_detail", int(chain_id), required=False)
        payload = api_payload(data)
        return payload if isinstance(payload, dict) else {}

    def industrial_chains_by_plate(self, plate_id: int) -> list[dict[str, Any]]:
        data = self._call("get_industrial_chain_by_plate", int(plate_id), required=False)
        payload = api_payload(data)
        return payload if isinstance(payload, list) else []

    def industrial_plate_stocks(
        self,
        *,
        chain_id: int | None = None,
        plate_id: int | None = None,
        count: int = 200,
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        page = None
        while True:
            data = self._call(
                "get_industrial_plate_stock",
                chain_id=chain_id,
                plate_id=plate_id,
                market_list=[self.futu.Market.SH, self.futu.Market.SZ],
                count=count,
                page=page,
                required=False,
            )
            frame = payload_frame(data)
            if not frame.empty:
                frames.append(frame)
            page = data[1] if isinstance(data, tuple) and len(data) > 1 else None
            if not page:
                break
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        if "security" in out:
            out["code"] = out["security"].map(normalize_code)
        return out.drop_duplicates("security" if "security" in out else None)

    def heat_map(
        self,
        *,
        plate_type: Any,
        market: Any | None = None,
        count: int = 200,
    ) -> pd.DataFrame:
        """Fetch all heat-map pages sequentially, ordered by Futu heat."""
        market = market or self.futu.Market.SH
        frames: list[pd.DataFrame] = []
        page = None
        rank_offset = 0
        while True:
            data = self._call(
                "get_heat_map_data",
                market,
                sort_field=self.futu.HeatMapSortField.HOT,
                ascend=False,
                count=count,
                page=page,
                plate_type=plate_type,
                required=False,
            )
            frame = payload_frame(data)
            if not frame.empty:
                frame["heat_rank"] = range(rank_offset + 1, rank_offset + len(frame) + 1)
                rank_offset += len(frame)
                frames.append(frame)
            page = data[1] if isinstance(data, tuple) and len(data) > 1 else None
            if not page:
                break
        if not frames:
            return pd.DataFrame()
        out = pd.concat(frames, ignore_index=True)
        out["plate_type"] = str(plate_type)
        return out.drop_duplicates("plate").reset_index(drop=True)

    def rise_fall_distribution(self, *, security: str | None = None, market: Any | None = None) -> dict[str, Any]:
        data = self._call(
            "get_rise_fall_distribution",
            security=security,
            market=market,
            required=False,
        )
        payload = api_payload(data)
        return payload if isinstance(payload, dict) else {}

    def corporate_action_flags(self, futu_code: str) -> dict[str, Any]:
        row = {"code": normalize_code(futu_code), "has_dividend": False, "has_buyback": False}
        dividends = self._call("get_corporate_actions_dividends", futu_code, required=False)
        div_frame = payload_frame(dividends)
        if not div_frame.empty:
            items = div_frame.iloc[0].get("dividend_list")
            row["has_dividend"] = isinstance(items, list) and len(items) > 0
        buybacks = self._call("get_corporate_actions_buybacks", futu_code, num=10, required=False)
        buy_frame = payload_frame(buybacks)
        if not buy_frame.empty:
            items = buy_frame.iloc[0].get("a_buy_back_list")
            row["has_buyback"] = isinstance(items, list) and len(items) > 0
        return row

    def event_context(self, futu_code: str) -> dict[str, Any]:
        row: dict[str, Any] = {"code": normalize_code(futu_code)}
        move = self._call("get_financials_earnings_price_move", futu_code, period_count=4, required=False)
        move_frame = payload_frame(move)
        if not move_frame.empty:
            raw_changes = move_frame.get("change_ratio", move_frame.get("price_change_ratio", pd.Series(0.0, index=move_frame.index)))
            if not isinstance(raw_changes, pd.Series):
                raw_changes = pd.Series(raw_changes, index=move_frame.index)
            changes = pd.to_numeric(raw_changes, errors="coerce")
            if changes.notna().any():
                row["earnings_price_move"] = float(changes.tail(12).mean())
            row["earnings_event_count"] = len(move_frame)

        consensus = self._call("get_research_analyst_consensus", futu_code, required=False)
        consensus_frame = payload_frame(consensus)
        if not consensus_frame.empty:
            latest = consensus_frame.iloc[0]
            for column in ("rating", "total", "buy", "hold", "sell", "average", "highest", "lowest"):
                if column in consensus_frame.columns:
                    row[column if column != "total" else "analyst_total"] = latest.get(column)

        self.warn_once(
            "research_rating_summary_a_share",
            "Futu get_research_rating_summary is not used for A-share V1: smoke test showed it supports US stocks/REITs only.",
        )
        self.warn_once(
            "shareholder_a_share",
            "Futu shareholder overview/holding-change/detail/institutional endpoints are not used for A-share V1: smoke test showed A-share is unsupported.",
        )
        return row


def api_payload(data: Any) -> Any:
    if isinstance(data, tuple):
        if len(data) == 3 and isinstance(data[2], list):
            return data[2]
        if data and hasattr(data[0], "to_dict"):
            return data[0]
        if len(data) >= 2 and hasattr(data[1], "to_dict"):
            return data[1]
        if len(data) >= 2:
            return data[1]
    return data


def payload_frame(data: Any) -> pd.DataFrame:
    payload = api_payload(data)
    if payload is None:
        return pd.DataFrame()
    if isinstance(payload, pd.DataFrame):
        return payload.copy()
    if isinstance(payload, dict):
        return pd.DataFrame([payload])
    return pd.DataFrame(payload)


def normalize_history_kline_quota(data: Any) -> HistoryKlineQuota:
    payload = data
    if isinstance(payload, dict):
        detail = payload.get("detail_list") or payload.get("details") or []
        return HistoryKlineQuota(
            used_quota=_optional_int(payload.get("used_quota")),
            remain_quota=_optional_int(payload.get("remain_quota")),
            detail_list=tuple(item for item in detail if isinstance(item, dict)),
        )

    if isinstance(payload, tuple):
        items = list(payload)
    elif isinstance(payload, list):
        items = payload
    else:
        items = [payload]

    used = _optional_int(items[0]) if len(items) >= 1 else None
    remain = _optional_int(items[1]) if len(items) >= 2 else None
    detail_items: list[dict[str, Any]] = []
    if len(items) >= 3:
        detail_payload = items[2]
        if isinstance(detail_payload, list):
            detail_items.extend(item for item in detail_payload if isinstance(item, dict))
        elif isinstance(detail_payload, dict):
            detail_items.append(detail_payload)
        for extra in items[3:]:
            if isinstance(extra, dict):
                detail_items.append(extra)
    return HistoryKlineQuota(used_quota=used, remain_quota=remain, detail_list=tuple(detail_items))


def history_quota_allows_request(futu_code: str, quota: HistoryKlineQuota) -> bool:
    target = str(futu_code).upper()
    used_codes = {str(item.get("code", "")).upper() for item in quota.detail_list}
    if target in used_codes:
        return True
    if quota.remain_quota is None:
        return True
    return quota.remain_quota > 0


def _optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def capital_flow_windows(start: str | None, end: str | None, *, max_days: int = 365) -> list[tuple[str | None, str | None]]:
    if start is None or end is None:
        return [(start, end)]
    try:
        start_date = dt.date.fromisoformat(start)
        end_date = dt.date.fromisoformat(end)
    except ValueError:
        return [(start, end)]
    if start_date > end_date:
        return [(start, end)]

    windows: list[tuple[str | None, str | None]] = []
    current = start_date
    while current <= end_date:
        window_end = min(current + dt.timedelta(days=max_days - 1), end_date)
        windows.append((current.isoformat(), window_end.isoformat()))
        current = window_end + dt.timedelta(days=1)
    return windows


def normalize_snapshot(snapshot: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "code": snapshot["code"].map(normalize_code),
            "futu_code": snapshot["code"].astype(str),
            "name": snapshot.get("name", "").astype(str),
        }
    )
    column_map = {
        "latest_price": "last_price",
        "turnover_amount": "turnover",
        "turnover_rate": "turnover_rate",
        "float_market_cap": "circular_market_val",
        "total_market_cap": "total_market_val",
        "pe_dynamic": "pe_ttm_ratio",
        "pb": "pb_ratio",
        "suspension": "suspension",
        "sec_status": "sec_status",
        "volume_ratio": "volume_ratio",
        "amplitude": "amplitude",
        "avg_price": "avg_price",
        "high_52w": "highest52weeks_price",
        "low_52w": "lowest52weeks_price",
    }
    for target, source in column_map.items():
        if source in snapshot.columns:
            out[target] = snapshot[source]
    if "pe_dynamic" not in out and "pe_ratio" in snapshot.columns:
        out["pe_dynamic"] = snapshot["pe_ratio"]
    if {"last_price", "prev_close_price"}.issubset(snapshot.columns):
        prev_close = pd.to_numeric(snapshot["prev_close_price"], errors="coerce")
        last_price = pd.to_numeric(snapshot["last_price"], errors="coerce")
        out["return_1d"] = ((last_price / prev_close - 1.0) * 100.0).where(prev_close > 0)
    if {"latest_price", "high_52w", "low_52w"}.issubset(out.columns):
        latest = pd.to_numeric(out["latest_price"], errors="coerce")
        high = pd.to_numeric(out["high_52w"], errors="coerce")
        low = pd.to_numeric(out["low_52w"], errors="coerce")
        spread = high - low
        out["price_position_52w"] = ((latest - low) / spread * 100.0).where(spread > 0)
    return out.drop_duplicates("code")


def normalize_daily_price_history(frame: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "code": frame["code"].map(normalize_code) if "code" in frame else "",
            "futu_code": frame["code"].astype(str) if "code" in frame else "",
            "trade_date": pd.to_datetime(frame["time_key"]).dt.date.astype(str),
        }
    )
    column_map = {
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "prev_close": "last_close",
        "volume": "volume",
        "turnover": "turnover",
        "turnover_rate": "turnover_rate",
        "change_rate": "change_rate",
        "pe_ratio": "pe_ratio",
    }
    for target, source in column_map.items():
        if source in frame.columns:
            out[target] = frame[source]
    return out.drop_duplicates(["code", "trade_date"]).sort_values(["code", "trade_date"])


def build_stock_screen_request(*, page_from: int, page_count: int) -> Any:
    from futu import StockScreenRequest
    from futu.quote.stock_screen_const import (
        BasicProperty,
        CumulativeProperty,
        FinancialProperty,
        ScrMarket,
        SimpleField,
        SimpleProperty,
        Term,
    )

    req = StockScreenRequest()
    req.add_simple_field(field=SimpleField.MARKET, values=[ScrMarket.CN])
    for prop in (BasicProperty.CODE, BasicProperty.NAME, BasicProperty.INDUSTRY):
        req.add_retrieve_basic(name=prop)
    for prop in (
        SimpleProperty.PRICE,
        SimpleProperty.MARKET_CAP,
        SimpleProperty.PE_TTM,
        SimpleProperty.PB,
        SimpleProperty.VOLUME_RATIO,
        SimpleProperty.PRICE_CHANGE_RATE,
        SimpleProperty.PRICE_TO_52W_HIGH,
        SimpleProperty.PRICE_TO_52W_LOW,
    ):
        req.add_retrieve_simple(name=prop)
    for days in (5, 20, 60, 120):
        req.add_retrieve_cumulative(name=CumulativeProperty.PRICE_CHANGE_PCT, days=days)
    for days in (5, 20, 60):
        req.add_retrieve_cumulative(name=CumulativeProperty.TURNOVER_RATIO, days=days)
    for prop in (
        FinancialProperty.REVENUE_GROWTH,
        FinancialProperty.NET_PROFIT_GROWTH,
        FinancialProperty.ROE,
        FinancialProperty.GROSS_PROFIT_RATIO,
        FinancialProperty.NOCF_PER_SHARE,
        FinancialProperty.DEBT_TO_ASSETS,
        FinancialProperty.CURRENT_RATIO,
    ):
        req.add_retrieve_financial(name=prop, term=Term.ANNUAL)
    req.page_from = page_from
    req.page_count = page_count
    return req


def parse_stock_screen_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from futu.quote.stock_screen_const import BasicProperty, CumulativeProperty, FinancialProperty, SimpleProperty

    field_map = {
        ("basic", int(BasicProperty.CODE)): "code",
        ("basic", int(BasicProperty.NAME)): "name",
        ("basic", int(BasicProperty.INDUSTRY)): "industry",
        ("simple", int(SimpleProperty.PRICE)): "latest_price",
        ("simple", int(SimpleProperty.MARKET_CAP)): "total_market_cap",
        ("simple", int(SimpleProperty.PE_TTM)): "pe_dynamic",
        ("simple", int(SimpleProperty.PB)): "pb",
        ("simple", int(SimpleProperty.VOLUME_RATIO)): "volume_ratio",
        ("simple", int(SimpleProperty.PRICE_CHANGE_RATE)): "return_1d",
        ("simple", int(SimpleProperty.PRICE_TO_52W_HIGH)): "price_to_52w_high",
        ("simple", int(SimpleProperty.PRICE_TO_52W_LOW)): "price_to_52w_low",
        ("financial", int(FinancialProperty.REVENUE_GROWTH)): "revenue_yoy",
        ("financial", int(FinancialProperty.NET_PROFIT_GROWTH)): "net_profit_yoy",
        ("financial", int(FinancialProperty.ROE)): "roe",
        ("financial", int(FinancialProperty.GROSS_PROFIT_RATIO)): "gross_margin",
        ("financial", int(FinancialProperty.NOCF_PER_SHARE)): "operating_cf_per_share",
        ("financial", int(FinancialProperty.DEBT_TO_ASSETS)): "debt_asset_rate",
        ("financial", int(FinancialProperty.CURRENT_RATIO)): "current_ratio",
    }
    cumulative_field_map = {
        (int(CumulativeProperty.PRICE_CHANGE_PCT), 5): "return_5d",
        (int(CumulativeProperty.PRICE_CHANGE_PCT), 20): "return_20d",
        (int(CumulativeProperty.PRICE_CHANGE_PCT), 60): "return_60d",
        (int(CumulativeProperty.PRICE_CHANGE_PCT), 120): "return_120d",
        (int(CumulativeProperty.TURNOVER_RATIO), 5): "turnover_ratio_5d",
        (int(CumulativeProperty.TURNOVER_RATIO), 20): "turnover_ratio_20d",
        (int(CumulativeProperty.TURNOVER_RATIO), 60): "turnover_ratio_60d",
    }
    rows: list[dict[str, Any]] = []
    pct_fields = {"revenue_yoy", "net_profit_yoy", "roe", "gross_margin", "debt_asset_rate"}
    for item in items:
        row: dict[str, Any] = {}
        for result in item.get("results", []):
            prop = result.get("property") or {}
            result_type = result.get("type")
            prop_name = int(prop.get("name", -1))
            target = field_map.get((result_type, prop_name))
            if result_type == "cumulative":
                target = cumulative_field_map.get((prop_name, int(prop.get("days", 0))))
            if not target:
                continue
            value = result.get("sval", result.get("dval", result.get("ival")))
            if value is None:
                continue
            if target == "code":
                row["code"] = normalize_code(value)
            elif target in pct_fields:
                row[target] = float(value) * 100.0
            else:
                row[target] = value
        if "return_60d" in row:
            row["sixty_day_change"] = row["return_60d"]
        if row.get("code"):
            rows.append(row)
    return rows
