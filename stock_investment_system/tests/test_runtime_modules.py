from __future__ import annotations

import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from stock_investment_system.config import SelectionConfig
from stock_investment_system.futu_client import FutuClient, _normalize_stock_screen_items
from stock_investment_system.futu_models import build_quality_base, derive_industry_strength, enrich_flow, enrich_valuation
from stock_investment_system.models import event_flow_confirmation, industry_flow_quality, quality_growth
from stock_investment_system.parameters import weighted_score
from stock_investment_system.run_models import main
from stock_investment_system.scoring import top_watchlist


class RuntimeModuleTests(unittest.TestCase):
    def test_weighted_score_normalizes_weight_sum(self) -> None:
        data = pd.DataFrame({"quality": [80.0], "growth": [60.0]})

        score = weighted_score(data, {"quality": 2.0, "growth": 1.0})

        self.assertAlmostEqual(float(score.iloc[0]), 73.3333333333, places=4)

    def test_top_watchlist_prefers_bucket_then_score_and_fills_missing_columns(self) -> None:
        data = pd.DataFrame(
            [
                {"code": "A", "bucket": "satellite", "total_score": 99.0},
                {"code": "B", "bucket": "core", "total_score": 70.0},
                {"code": "C", "bucket": "core", "total_score": 80.0},
            ]
        )

        watchlist = top_watchlist(
            data,
            score_col="total_score",
            limit=2,
            preferred_bucket="core",
            columns=["code", "missing_col", "total_score"],
        )

        self.assertEqual(watchlist["code"].tolist(), ["C", "B"])
        self.assertIn("missing_col", watchlist.columns)

    def test_all_models_run_with_sample_client(self) -> None:
        config = SelectionConfig(max_watchlist=5, max_market_candidates=8, max_flow_candidates=8)
        client = FutuClient.from_sample()

        results = [
            quality_growth.run(client, config, report_date="sample"),
            industry_flow_quality.run(client, config, report_date="sample"),
            event_flow_confirmation.run(client, config, report_date="sample"),
        ]

        for result in results:
            self.assertFalse(result.watchlist.empty, result.model_name)
            self.assertLessEqual(len(result.watchlist), config.max_watchlist)
            self.assertEqual(result.metadata["report_date"], "sample")

    def test_event_model_requires_positive_flow_confirmation(self) -> None:
        config = SelectionConfig(max_watchlist=10, max_market_candidates=10, max_flow_candidates=10)
        client = FutuClient.from_sample()

        result = event_flow_confirmation.run(client, config, report_date="sample")

        self.assertTrue(((result.watchlist["flow_net_10d"] > 0) | (result.watchlist["flow_net_20d"] > 0)).all())

    def test_cli_markdown_output_does_not_require_tabulate(self) -> None:
        stream = StringIO()
        argv = ["run_models", "--model", "quality", "--max-watchlist", "2"]

        with patch("sys.argv", argv), redirect_stdout(stream):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertIn("Model 1: Quality Plus Growth", stream.getvalue())

    def test_cli_csv_output_uses_chinese_headers(self) -> None:
        stream = StringIO()
        argv = ["run_models", "--model", "quality", "--max-watchlist", "2", "--format", "csv"]

        with patch("sys.argv", argv), redirect_stdout(stream):
            exit_code = main()

        output = stream.getvalue()
        self.assertEqual(exit_code, 0)
        self.assertIn("模型,股票代码,股票名称", output.lstrip("\ufeff"))
        self.assertIn("质量成长", output)
        self.assertIn("行情来源", output)
        self.assertIn("选择理由", output)
        self.assertNotIn("selection_reason", output)

    def test_sample_client_can_refresh_latest_price_from_snapshot(self) -> None:
        client = FutuClient.from_sample()
        snapshot = pd.DataFrame(
            [
                {
                    "code": "300308",
                    "futu_code": "SZ.300308",
                    "name": "中际旭创",
                    "latest_price": 1121.90,
                    "turnover_amount": 32709220000,
                    "pe_ratio": 115.874,
                    "pe_ttm_ratio": 83.698,
                    "pb_ratio": 36.116,
                    "circular_market_val": 1245241000000,
                }
            ]
        )

        with patch.object(client, "_snapshot_candidates", return_value=snapshot):
            client.refresh_market_snapshot()

        row = client.base_data[client.base_data["code"] == "300308"].iloc[0]
        self.assertEqual(row["latest_price"], 1121.90)
        self.assertEqual(row["pe_dynamic"], 83.698)
        self.assertEqual(row["pb"], 36.116)
        self.assertEqual(row["turnover_amount"], 32709220000)
        self.assertEqual(row["float_market_cap"], 1245241000000)
        self.assertEqual(row["price_source"], "live_futu_snapshot")

    def test_missing_quality_inputs_are_filled_without_crashing(self) -> None:
        base = pd.DataFrame(
            [{
                "code": "600000",
                "futu_code": "SH.600000",
                "name": "测试股票",
                "industry": "银行",
                "latest_price": 10.0,
                "pe_dynamic": 8.0,
                "pb": 0.8,
                "turnover_amount": 1000000.0,
                "float_market_cap": 1000000000.0,
                "risk_flags": "",
                "pct_change_20d": 2.0,
            }]
        )
        client = FutuClient(base_data=base)

        scored, rejected, _ = build_quality_base(
            client,
            SelectionConfig(max_market_candidates=1, min_turnover_amount=0, min_float_market_cap=0),
        )

        self.assertTrue(rejected.empty)
        self.assertEqual(len(scored), 1)
        self.assertTrue((scored["fundamental_quality_score"] == 30).all())
        self.assertTrue((scored["growth_quality_score"] == 36).all())
        self.assertEqual(scored.iloc[0]["score_input_mode"], "partial_raw_proxy")

    def test_saved_score_columns_are_recomputed_from_raw_features(self) -> None:
        base = FutuClient.from_sample().base_data.head(1).copy()
        base["fundamental_quality_score"] = 99.0
        base["growth_quality_score"] = 99.0
        base["roe"] = 10.0
        base["revenue_growth"] = 5.0
        client = FutuClient(base_data=base)

        scored, _, _ = build_quality_base(client, SelectionConfig(max_market_candidates=1))

        self.assertNotEqual(float(scored.iloc[0]["fundamental_quality_score"]), 99.0)
        self.assertEqual(float(scored.iloc[0]["growth_quality_score"]), 55.0)

    def test_stock_name_itself_triggers_st_risk_gate(self) -> None:
        base = FutuClient.from_sample().base_data.head(1).copy()
        base["name"] = "*ST测试"
        base["risk_flags"] = ""

        scored, rejected, _ = build_quality_base(FutuClient(base_data=base), SelectionConfig(max_market_candidates=1))

        self.assertTrue(scored.empty)
        self.assertEqual(rejected.iloc[0]["reject_reason"], "risk_flag")

    def test_single_stock_industry_score_is_shrunk_toward_neutral(self) -> None:
        data = pd.DataFrame([{
            "code": "A", "industry": "单一样本行业", "capital_flow_score": 100,
            "price_volume_score": 100, "flow_net_20d": 1, "pct_change_20d": 10,
        }])

        result = derive_industry_strength(data)

        self.assertEqual(int(result.iloc[0]["industry_member_count"]), 1)
        self.assertGreater(float(result.iloc[0]["industry_strength_score"]), 50)
        self.assertLess(float(result.iloc[0]["industry_strength_score"]), 100)

    def test_csv_loader_preserves_leading_zero_security_codes(self) -> None:
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", encoding="utf-8") as handle:
            handle.write("code,futu_code,name\n000001,SZ.000001,平安银行\n")
            handle.flush()
            loaded = FutuClient.from_csv(handle.name).base_data

        self.assertEqual(str(loaded.iloc[0]["code"]), "000001")
        self.assertEqual(str(loaded.iloc[0]["futu_code"]), "SZ.000001")

    def test_v2_stock_screen_nested_results_and_units_are_normalized(self) -> None:
        items = [{"results": [
            {"type": "basic", "property": {"name": 1101}, "sval": "000725"},
            {"type": "basic", "property": {"name": 1102}, "sval": "京东方A"},
            {"type": "cumulative", "property": {"name": 3105}, "dval": 355_000_000_000.0},
            {"type": "cumulative", "property": {"name": 3102}, "dval": -0.10},
            {"type": "financial", "property": {"name": 4903}, "ival": 195_000_000_000_000},
            {"type": "financial", "property": {"name": 4110}, "dval": 0.12},
        ]}]

        frame = _normalize_stock_screen_items(items)

        self.assertEqual(frame.iloc[0]["futu_code"], "SZ.000725")
        self.assertEqual(float(frame.iloc[0]["turnover_amount"]), 3_550_000_000.0)
        self.assertEqual(float(frame.iloc[0]["float_market_cap"]), 195_000_000_000.0)
        self.assertEqual(float(frame.iloc[0]["pct_change_20d"]), -10.0)
        self.assertEqual(float(frame.iloc[0]["roe"]), 12.0)

    def test_live_market_universe_is_cached_across_models(self) -> None:
        client = FutuClient.from_sample()
        config = SelectionConfig(live_market_screen=True, max_market_candidates=2)
        live = client.base_data.head(2).copy()
        live["data_source"] = "live_futu_stock_screen"

        with patch.object(client, "_stock_screen_candidates", return_value=live) as screen:
            first = client.market_candidates(config)
            second = client.market_candidates(config)

        self.assertEqual(screen.call_count, 1)
        self.assertEqual(first["code"].tolist(), second["code"].tolist())

    def test_valuation_enrichment_keeps_score_and_percentile_consistent(self) -> None:
        client = FutuClient.from_sample()
        config = SelectionConfig(max_market_candidates=8)
        scored, _, _ = build_quality_base(client, config)

        enriched = enrich_valuation(client, scored, count=8)
        expected = (100 - enriched["valuation_percentile"]).clip(0, 100)

        self.assertTrue((enriched["valuation_score"].round(6) == expected.round(6)).all())

    def test_missing_flow_data_does_not_receive_a_neutral_flow_score(self) -> None:
        client = FutuClient(
            base_data=FutuClient.from_sample().base_data.head(2),
            flow_data=pd.DataFrame(columns=["code"]),
        )
        config = SelectionConfig(max_market_candidates=2)
        scored, _, _ = build_quality_base(client, config)

        enriched = enrich_flow(client, scored, count=2, rank_col="quality_total_score")

        self.assertTrue((enriched["capital_flow_score"] == 0).all())
        self.assertTrue((enriched["flow_acceleration_score"] == 0).all())

    def test_launcher_requests_live_quote_refresh(self) -> None:
        script = Path("/Users/jun/Documents/BY股票投资/stock_investment_system/launcher/run_stock_system.sh").read_text()

        self.assertIn("--refresh-quotes", script)
        self.assertIn("--live-universe", script)
        self.assertIn('cd "$PROJECT_DIR"', script)
        self.assertIn("deep_research_pipeline.py", script)
        self.assertIn("launch_deep_research.py", script)
        self.assertIn("DEEP_RESEARCH_BACKGROUND", script)
        self.assertIn("DEEP_RESEARCH_AUTO", script)

    def test_csv_output_starts_with_header_when_refresh_prints_logs(self) -> None:
        stream = StringIO()
        argv = ["run_models", "--model", "quality", "--max-watchlist", "1", "--refresh-quotes", "--format", "csv"]

        with patch("sys.argv", argv), patch.object(FutuClient, "refresh_market_snapshot", lambda self: print("连接日志")), redirect_stdout(stream):
            exit_code = main()

        self.assertEqual(exit_code, 0)
        self.assertTrue(stream.getvalue().startswith("\ufeff模型,股票代码,股票名称"))


if __name__ == "__main__":
    unittest.main()
