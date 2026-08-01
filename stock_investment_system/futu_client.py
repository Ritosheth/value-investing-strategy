from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from .config import SelectionConfig


@dataclass
class FutuClient:
    """Small data adapter used by the rule models.

    The adapter can run from in-memory/sample data immediately. Live OpenD calls
    are best-effort and deliberately read-only.
    """

    base_data: pd.DataFrame | None = None
    valuation_data: pd.DataFrame | None = None
    corporate_action_data: pd.DataFrame | None = None
    flow_data: pd.DataFrame | None = None
    event_data: pd.DataFrame | None = None
    owner_plate_data: pd.DataFrame | None = None
    host: str = "127.0.0.1"
    port: int = 11111
    warnings: list[str] = field(default_factory=list)
    _quote_ctx: Any = field(default=None, init=False, repr=False)

    @classmethod
    def from_sample(cls) -> "FutuClient":
        return cls(**_sample_frames())

    @classmethod
    def from_csv(cls, path: str | Path) -> "FutuClient":
        # Keep security identifiers as text so 000001 and other leading-zero
        # A-share codes are not converted into integers by pandas.
        base_data = pd.read_csv(path, dtype={"code": "string", "futu_code": "string"})
        if "price_source" not in base_data:
            base_data["price_source"] = "csv"
        return cls(base_data=base_data)

    def warn(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)

    def market_candidates(self, config: SelectionConfig) -> pd.DataFrame:
        if self.base_data is not None:
            return self.base_data.copy().head(config.max_market_candidates)
        if config.seed_codes:
            return self._snapshot_candidates(config.seed_codes)
        self.warn("No base universe supplied. Use FutuClient.from_sample(), from_csv(), or config.seed_codes.")
        return pd.DataFrame()

    def refresh_market_snapshot(self) -> None:
        """Refresh quote fields for the current base universe from Futu OpenD."""

        if self.base_data is None or self.base_data.empty:
            return
        if "futu_code" not in self.base_data:
            self.warn("Cannot refresh market snapshot because base_data has no futu_code column.")
            return

        futu_codes = self.base_data["futu_code"].dropna().astype(str).tolist()
        snapshot = self._snapshot_candidates(futu_codes)
        if snapshot.empty:
            self.warn("Live quote refresh returned no rows; using existing candidate data.")
            self.base_data["price_source"] = self.base_data.get("price_source", "sample")
            return

        updates = _quote_update_frame(snapshot)
        if updates.empty:
            self.warn("Live quote refresh returned no usable quote columns; using existing candidate data.")
            self.base_data["price_source"] = self.base_data.get("price_source", "sample")
            return

        base = self.base_data.copy()
        base["code"] = base["code"].astype(str)
        updates["code"] = updates["code"].astype(str)
        base = base.merge(updates, on="code", how="left", suffixes=("", "_live"))
        for column in ["name", "latest_price", "turnover_amount", "float_market_cap", "pe_dynamic", "pb"]:
            live_column = f"{column}_live"
            if live_column in base:
                existing = base[column] if column in base else pd.Series(pd.NA, index=base.index)
                live_values = base[live_column]
                if column == "turnover_amount":
                    live_values = live_values.mask(pd.to_numeric(live_values, errors="coerce").fillna(0.0) <= 0)
                base[column] = live_values.where(live_values.notna(), existing)
                base = base.drop(columns=[live_column])
        base["price_source"] = base["latest_price_live_source"].fillna(base.get("price_source", "sample"))
        base = base.drop(columns=["latest_price_live_source"])
        self.base_data = base

    def valuation(self, codes: list[str]) -> pd.DataFrame:
        if self.valuation_data is not None:
            return self.valuation_data[self.valuation_data["code"].isin(_plain_codes(codes))].copy()
        snapshot = self._snapshot_candidates(codes)
        if snapshot.empty:
            return pd.DataFrame(columns=["code", "pe_dynamic", "pb"])
        return snapshot.reindex(columns=["code", "pe_dynamic", "pb"])

    def corporate_actions(self, codes: list[str]) -> pd.DataFrame:
        if self.corporate_action_data is not None:
            return self.corporate_action_data[self.corporate_action_data["code"].isin(_plain_codes(codes))].copy()
        rows: list[dict[str, Any]] = []
        for code in codes:
            plain = _plain_code(code)
            try:
                ctx = self._quote_context()
                div_ret, div_data = ctx.get_corporate_actions_dividends(code)
                buy_ret, buy_data = ctx.get_corporate_actions_buybacks(code)
                has_dividend = div_ret == 0 and bool((div_data or {}).get("dividend_list"))
                buy_list = (buy_data or {}).get("a_buy_back_list")
                has_buyback = buy_ret == 0 and buy_list is not None and not buy_list.empty
                shareholder_score = (60.0 if has_dividend else 0.0) + (40.0 if has_buyback else 0.0)
                rows.append(
                    {
                        "code": plain,
                        "has_dividend": has_dividend,
                        "has_buyback": has_buyback,
                        "shareholder_return_score": shareholder_score,
                    }
                )
            except Exception as exc:  # pragma: no cover - depends on OpenD/data permission
                self.warn(f"corporate actions unavailable for {code}: {exc}")
                rows.append({"code": plain, "has_dividend": False, "has_buyback": False, "shareholder_return_score": 0.0})
        return pd.DataFrame(rows, columns=["code", "has_dividend", "has_buyback", "shareholder_return_score"])

    def capital_flow(self, codes: list[str]) -> pd.DataFrame:
        if self.flow_data is not None:
            return self.flow_data[self.flow_data["code"].isin(_plain_codes(codes))].copy()
        rows: list[dict[str, Any]] = []
        for code in codes:
            rows.append(self._live_flow_row(code))
        return pd.DataFrame(rows)

    def events(self, codes: list[str]) -> pd.DataFrame:
        if self.event_data is not None:
            return self.event_data[self.event_data["code"].isin(_plain_codes(codes))].copy()
        rows: list[dict[str, Any]] = []
        for code in codes:
            plain = _plain_code(code)
            event_score = 0.0
            expectation_score = 0.0
            try:
                ctx = self._quote_context()
                consensus_ret, consensus = ctx.get_research_analyst_consensus(code)
                if consensus_ret == 0 and isinstance(consensus, dict):
                    positive = float(consensus.get("strong_buy", 0.0) or 0.0) + float(consensus.get("buy", 0.0) or 0.0)
                    expectation_score = max(0.0, min(100.0, positive))

                earnings_ret, earnings = ctx.get_financials_earnings_price_move(code)
                if earnings_ret == 0 and earnings is not None and not earnings.empty:
                    dates = pd.to_datetime(earnings.get("pub_trading_day_str"), errors="coerce").dropna()
                    if not dates.empty:
                        age_days = max(0, (pd.Timestamp.today().normalize() - dates.max()).days)
                        event_score = 75.0 if age_days <= 90 else 55.0 if age_days <= 180 else 25.0
            except Exception as exc:  # pragma: no cover - depends on OpenD/data permission
                self.warn(f"event data unavailable for {code}: {exc}")
            rows.append({"code": plain, "event_score": event_score, "expectation_score": expectation_score})
        return pd.DataFrame(rows, columns=["code", "event_score", "expectation_score"])

    def owner_plate(self, futu_codes: list[str]) -> pd.DataFrame:
        if self.owner_plate_data is not None:
            return self.owner_plate_data[self.owner_plate_data["code"].isin(_plain_codes(futu_codes))].copy()
        try:
            ctx = self._quote_context()
            ret, data = ctx.get_owner_plate(futu_codes)
            if ret != 0:
                self.warn(f"owner_plate failed: {data}")
                return pd.DataFrame()
            return data.rename(columns={"stock_code": "code"})
        except Exception as exc:  # pragma: no cover - depends on OpenD
            self.warn(f"owner_plate unavailable: {exc}")
            return pd.DataFrame()

    def plate_metadata(self) -> dict[str, Any]:
        if self.owner_plate_data is not None and not self.owner_plate_data.empty:
            return {"known_plate_rows": int(len(self.owner_plate_data))}
        return {}

    def close(self) -> None:
        if self._quote_ctx is not None:
            self._quote_ctx.close()
            self._quote_ctx = None

    def _quote_context(self):  # pragma: no cover - depends on OpenD
        if self._quote_ctx is None:
            from futu import OpenQuoteContext

            self._quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
        return self._quote_ctx

    def _snapshot_candidates(self, codes: tuple[str, ...] | list[str]) -> pd.DataFrame:
        if not codes:
            return pd.DataFrame()
        try:
            ctx = self._quote_context()
            ret, data = ctx.get_market_snapshot(list(codes))
            if ret != 0:
                self.warn(f"market snapshot failed: {data}")
                return pd.DataFrame()
            return _normalize_snapshot(data)
        except Exception as exc:  # pragma: no cover - depends on OpenD
            self.warn(f"market snapshot unavailable: {exc}")
            return pd.DataFrame()

    def _live_flow_row(self, code: str) -> dict[str, Any]:
        plain = _plain_code(code)
        try:
            ctx = self._quote_context()
            # Use daily rows.  The previous default (INTRADAY) returned an
            # empty frame after market close and could not support 5/10/20-day
            # persistence calculations.
            ret, data = ctx.get_capital_flow(code, period_type="DAY")
            if ret != 0 or data is None or data.empty:
                self.warn(f"capital flow unavailable for {code}: {data}")
                return {"code": plain, "flow_data_available": False}
            flow_column = "main_in_flow" if "main_in_flow" in data else "in_flow"
            net = pd.to_numeric(data.get(flow_column), errors="coerce").fillna(0.0)
            large_order = (
                pd.to_numeric(data.get("super_in_flow", 0.0), errors="coerce").fillna(0.0)
                + pd.to_numeric(data.get("big_in_flow", 0.0), errors="coerce").fillna(0.0)
            )
            return {
                "code": plain,
                "flow_net": float(net.tail(1).sum()),
                "flow_net_5d": float(net.tail(5).sum()),
                "flow_net_10d": float(net.tail(10).sum()),
                "flow_net_20d": float(net.tail(20).sum()),
                "large_order_net_20d": float(large_order.tail(20).sum()),
                "flow_positive_ratio": float(net.tail(5).gt(0).mean()),
                "flow_positive_ratio_20d": float(net.tail(20).gt(0).mean()),
                "flow_days": int(len(data)),
                "flow_data_available": True,
            }
        except Exception as exc:  # pragma: no cover - depends on OpenD
            self.warn(f"capital flow unavailable for {code}: {exc}")
            return {"code": plain, "flow_data_available": False}


def _plain_code(code: str) -> str:
    return str(code).split(".")[-1]


def _plain_codes(codes: list[str] | tuple[str, ...]) -> list[str]:
    return [_plain_code(code) for code in codes]


def _normalize_snapshot(data: pd.DataFrame) -> pd.DataFrame:
    if data.empty:
        return data
    out = data.copy()
    out["futu_code"] = out.get("code", "")
    out["code"] = out["futu_code"].astype(str).map(_plain_code)
    _copy_first_available(out, "name", ["name", "stock_name"])
    _copy_first_available(out, "latest_price", ["latest_price", "last_price"])
    _copy_first_available(out, "turnover_amount", ["turnover_amount", "turnover"])
    _copy_first_available(out, "float_market_cap", ["float_market_cap", "circular_market_val", "float_market_val"])
    _copy_first_available(out, "pe_dynamic", ["pe_ttm_ratio", "pe_ratio", "pe_dynamic"])
    _copy_first_available(out, "pb", ["pb_ratio", "pb"])
    return out


def _quote_update_frame(snapshot: pd.DataFrame) -> pd.DataFrame:
    if snapshot.empty or "code" not in snapshot:
        return pd.DataFrame()
    out = snapshot.copy()
    _copy_first_available(out, "name", ["name", "stock_name"])
    _copy_first_available(out, "latest_price", ["latest_price", "last_price"])
    _copy_first_available(out, "turnover_amount", ["turnover_amount", "turnover"])
    _copy_first_available(out, "float_market_cap", ["float_market_cap", "circular_market_val", "float_market_val"])
    _copy_first_available(out, "pe_dynamic", ["pe_ttm_ratio", "pe_ratio", "pe_dynamic"])
    _copy_first_available(out, "pb", ["pb_ratio", "pb"])
    columns = ["code", "name", "latest_price", "turnover_amount", "float_market_cap", "pe_dynamic", "pb"]
    existing = [column for column in columns if column in out.columns]
    if "code" not in existing:
        return pd.DataFrame()
    updates = out[existing].copy()
    updates["latest_price_live_source"] = "live_futu_snapshot"
    return updates.drop_duplicates("code")


def _copy_first_available(df: pd.DataFrame, target: str, candidates: list[str]) -> None:
    if target in df.columns:
        return
    for candidate in candidates:
        if candidate in df.columns:
            df[target] = df[candidate]
            return


def _sample_frames() -> dict[str, pd.DataFrame]:
    base = pd.DataFrame(
        [
            ["SH.688146", "688146", "中船特气", "电子化学品", 38.2, 42.0, 4.1, 720000000, 19000000000, "", 82, 78, 62, 74, 8.5],
            ["SZ.300308", "300308", "中际旭创", "光通信", 156.4, 35.0, 8.8, 1600000000, 116000000000, "", 76, 88, 58, 82, 12.2],
            ["SH.600519", "600519", "贵州茅台", "白酒", 1510.0, 24.0, 8.5, 2100000000, 1800000000000, "", 91, 58, 66, 54, -1.4],
            ["SZ.300750", "300750", "宁德时代", "电池", 238.8, 28.0, 5.2, 1900000000, 990000000000, "", 84, 76, 64, 71, 5.1],
            ["SH.688981", "688981", "中芯国际", "半导体", 88.6, 96.0, 4.9, 2500000000, 510000000000, "", 65, 72, 42, 86, 18.0],
            ["SZ.000001", "000001", "平安银行", "银行", 11.5, 5.8, 0.55, 530000000, 223000000000, "", 70, 45, 82, 48, -3.0],
            ["SH.600030", "600030", "中信证券", "证券", 27.3, 18.0, 1.45, 1300000000, 326000000000, "", 68, 61, 70, 69, 6.8],
            ["SZ.002371", "002371", "北方华创", "半导体设备", 332.0, 55.0, 9.2, 980000000, 176000000000, "", 79, 84, 52, 79, 10.4],
        ],
        columns=[
            "futu_code",
            "code",
            "name",
            "industry",
            "latest_price",
            "pe_dynamic",
            "pb",
            "turnover_amount",
            "float_market_cap",
            "risk_flags",
            "fundamental_quality_score",
            "growth_quality_score",
            "valuation_score",
            "price_volume_score",
            "pct_change_20d",
        ],
    )
    base["price_source"] = "sample"
    valuation = base[["code", "pe_dynamic", "pb", "valuation_score"]].copy()
    corporate = pd.DataFrame(
        [
            ["688146", False, False, 0],
            ["300308", False, True, 35],
            ["600519", True, True, 88],
            ["300750", True, False, 45],
            ["688981", False, False, 0],
            ["000001", True, True, 76],
            ["600030", True, False, 50],
            ["002371", False, False, 0],
        ],
        columns=["code", "has_dividend", "has_buyback", "shareholder_return_score"],
    )
    flow = pd.DataFrame(
        [
            ["688146", 120, 380, 760, 1050, 420, 0.70, 0.65, 20],
            ["300308", 300, 900, 1800, 2600, 1100, 0.82, 0.75, 20],
            ["600519", -80, -200, -350, -420, -160, 0.35, 0.40, 20],
            ["300750", 210, 520, 860, 920, 310, 0.68, 0.60, 20],
            ["688981", 450, 1300, 2200, 3600, 1800, 0.86, 0.80, 20],
            ["000001", -40, -120, -220, -300, -100, 0.30, 0.35, 20],
            ["600030", 180, 500, 720, 880, 260, 0.62, 0.58, 20],
            ["002371", 160, 480, 980, 1250, 520, 0.72, 0.70, 20],
        ],
        columns=[
            "code",
            "flow_net",
            "flow_net_5d",
            "flow_net_10d",
            "flow_net_20d",
            "large_order_net_20d",
            "flow_positive_ratio",
            "flow_positive_ratio_20d",
            "flow_days",
        ],
    )
    events = pd.DataFrame(
        [
            ["688146", 70, 62],
            ["300308", 78, 84],
            ["600519", 20, 45],
            ["300750", 66, 70],
            ["688981", 74, 68],
            ["000001", 10, 40],
            ["600030", 55, 58],
            ["002371", 69, 73],
        ],
        columns=["code", "event_score", "expectation_score"],
    )
    owner = pd.DataFrame(
        [[row.code, "INDUSTRY", row.industry] for row in base.itertuples()],
        columns=["code", "plate_type", "plate_name"],
    )
    return {
        "base_data": base,
        "valuation_data": valuation,
        "corporate_action_data": corporate,
        "flow_data": flow,
        "event_data": events,
        "owner_plate_data": owner,
    }
