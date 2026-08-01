from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "collect_deep_research_data.py"


def load_collector():
    import importlib.util

    spec = importlib.util.spec_from_file_location("collect_deep_research_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class CollectorTests(unittest.TestCase):
    @staticmethod
    def dcf_evidence(industry="软件", fcff_values=None):
        fcff_values = fcff_values or [5.0, 6.0, 7.0, 8.0, 9.0]
        years = [2021, 2022, 2023, 2024, 2025]
        revenue_values = [50.0, 55.0, 60.0, 66.0, 72.0]
        main_reports = []
        for year, fcff, revenue in zip(years, fcff_values, revenue_values):
            items = [
                {"field_id": 3010, "display_name": "每股企业自由现金流量", "data": fcff},
                {"field_id": 3012, "display_name": "每股营业总收入", "data": revenue},
                {"field_id": 3013, "display_name": "每股息税前利润", "data": 12.0 if year == 2025 else 10.0},
                {"field_id": 3028, "display_name": "息税前利润", "data": 1_200_000_000 if year == 2025 else 1_000_000_000},
            ]
            main_reports.append({"fiscal_year": year, "financial_type": 7, "period_text": f"{year}/FY", "item_list": items})
        income_reports = []
        for year, revenue in zip(years, [5_000_000_000, 5_500_000_000, 6_000_000_000, 6_600_000_000, 7_200_000_000]):
            items = [{"field_id": 3001, "display_name": "营业总收入", "data": revenue}]
            if year == 2025:
                items.extend([
                    {"field_id": 3017, "display_name": "-利息费用", "data": 25_000_000},
                    {"field_id": 3038, "display_name": "利润总额", "data": 1_500_000_000},
                    {"field_id": 3039, "display_name": "减:所得税费用", "data": 300_000_000},
                    {"field_id": 3047, "display_name": "归属母公司净利润", "data": 1_000_000_000},
                    {"field_id": 3051, "display_name": "基本每股收益", "data": 10.0},
                ])
            income_reports.append({"fiscal_year": year, "financial_type": 7, "period_text": f"{year}/FY", "item_list": items})
        return {
            "ticker": {"code": "300001.SZ", "market": "A"},
            "snapshot": {"data": [{"last_price": 100.0, "name": "测试公司"}]},
            "model": {"score": 70, "rows": [{"industry": industry}]},
            "financial_quality": {
                "score": 75,
                "annual_statements": {
                    "main_index": {"data": {"report_list": main_reports}},
                    "balance": {
                        "data": {
                            "report_list": [{
                                "fiscal_year": 2025,
                                "financial_type": 7,
                                "period_text": "2025/FY",
                                "item_list": [
                                    {"field_id": 3003, "display_name": "货币资金", "data": 1_000_000_000},
                                    {"field_id": 3067, "display_name": "短期借款", "data": 300_000_000},
                                    {"field_id": 3084, "display_name": "长期借款", "data": 200_000_000},
                                ],
                            }]
                        }
                    },
                    "income": {
                        "data": {
                            "report_list": income_reports
                        }
                    },
                },
            },
        }

    def test_normalize_ticker_supports_a_hk_and_us_codes(self):
        collector = load_collector()

        self.assertEqual(
            collector.normalize_ticker("301217"),
            {
                "input": "301217",
                "code": "301217.SZ",
                "market": "A",
                "futu_code": "SZ.301217",
                "name": "301217.SZ",
            },
        )
        self.assertEqual(collector.normalize_ticker("600519.SH")["futu_code"], "SH.600519")
        self.assertEqual(collector.normalize_ticker("0700.HK")["futu_code"], "HK.00700")
        self.assertEqual(collector.normalize_ticker("AAPL")["futu_code"], "US.AAPL")

    def test_research_scoring_maps_score_confidence_and_position_band(self):
        collector = load_collector()

        evidence = {
            "model": {"total_score": 82, "rank": 3, "selection_reason": "quality and flow"},
            "financial_quality": {"score": 78},
            "valuation": {"score": 66},
            "catalyst": {"score": 72, "next_catalyst": "annual report"},
            "technical_flow": {"score": 70},
            "governance_risk": {"score": 80},
            "data_confidence": {"score": 82},
        }

        result = collector.score_research(evidence)

        self.assertEqual(result["total_score"], 75.0)
        self.assertEqual(result["posture"], "CORE CANDIDATE")
        self.assertEqual(result["confidence"], "HIGH")
        self.assertEqual(result["position_band"], "4%-6%")
        self.assertEqual(result["hard_limits"], [])

    def test_missing_critical_evidence_forces_insufficient_evidence(self):
        collector = load_collector()

        result = collector.score_research({})

        self.assertEqual(result["posture"], "INSUFFICIENT EVIDENCE")
        self.assertEqual(result["position_band"], "0%")
        self.assertIn("financial_quality", result["unavailable_components"])

    def test_fair_value_uses_ttm_eps_and_recent_pe_percentiles(self):
        collector = load_collector()

        evidence = {
            "snapshot": {"data": [{"last_price": 100}]},
            "financial_quality": {
                "financials": {
                    "data": {
                        "report_list": [
                            {
                                "date_time_str": "2026-03-31",
                                "item_list": [{"field_id": 3006, "display_name": "每股收益_TTM（元）", "data": 10}],
                            }
                        ]
                    }
                }
            },
            "valuation": {
                "valuation_detail": {
                    "data": {
                        "valuation_type": 1,
                        "trend": {
                            "current_value": 10,
                            "valuation_percentile": 50,
                            "historical_items": [
                                {"time_str": "2024-01-01", "value": value}
                                for value in range(10, 110)
                            ]
                        }
                    }
                }
            },
        }

        result = collector.derive_fair_value(evidence, date(2026, 8, 1), "MEDIUM")

        self.assertTrue(result["available"])
        self.assertEqual(result["eps_ttm"], 10.0)
        self.assertEqual(result["pe_sample_size"], 100)
        self.assertLess(result["scenarios"]["bear"]["value"], result["scenarios"]["base"]["value"])
        self.assertLess(result["scenarios"]["base"]["value"], result["scenarios"]["bull"]["value"])

    def test_fair_value_uses_bps_when_futu_returns_pb(self):
        collector = load_collector()

        evidence = {
            "snapshot": {"data": [{"last_price": 10}]},
            "financial_quality": {
                "financials": {
                    "data": {
                        "report_list": [
                            {
                                "date_time_str": "2026-03-31",
                                "item_list": [{"field_id": 3002, "display_name": "每股净资产（元）", "data": 20}],
                            }
                        ]
                    }
                }
            },
            "valuation": {
                "valuation_detail": {
                    "data": {
                        "valuation_type": 2,
                        "trend": {
                            "current_value": 0.5,
                            "valuation_percentile": 50,
                            "historical_items": [
                                {"time_str": "2024-01-01", "value": value}
                                for value in [0.4] * 25 + [0.6] * 25
                            ]
                        },
                    }
                }
            },
        }

        result = collector.derive_fair_value(evidence, date(2026, 8, 1), "MEDIUM")

        self.assertTrue(result["available"])
        self.assertEqual(result["multiple_name"], "PB")
        self.assertEqual(result["basis_label"], "每股净资产")
        self.assertEqual(result["basis_value"], 20.0)
        self.assertEqual(result["scenarios"]["base"]["value"], 10.0)

    def test_fair_value_reports_missing_inputs(self):
        collector = load_collector()

        result = collector.derive_fair_value({}, date(2026, 8, 1), "MEDIUM")

        self.assertFalse(result["available"])
        self.assertIn("当前股价", result["reason"])

    def test_fair_value_recovers_ttm_eps_from_current_price_and_futu_pe(self):
        collector = load_collector()
        evidence = {
            "snapshot": {"data": [{"last_price": 100.0}]},
            "financial_quality": {
                "financials": {
                    "data": {
                        "report_list": [{
                            "date_time_str": "2026-03-31",
                            "period_text": "2026/Q1",
                            "item_list": [{"field_id": 1003, "display_name": "基本每股收益（元）", "data": 0.5}],
                        }]
                    }
                }
            },
            "valuation": {
                "valuation_detail": {
                    "data": {
                        "valuation_type": 1,
                        "trend": {
                            "current_value": 20.0,
                            "valuation_percentile": 50.0,
                            "historical_items": [
                                {"time_str": "2025-01-01", "value": value}
                                for value in range(10, 30)
                            ] * 2,
                        },
                    }
                }
            },
        }

        result = collector.derive_fair_value(evidence, date(2026, 8, 1), "SHORT")

        self.assertTrue(result["available"])
        self.assertEqual(result["basis_value"], 5.0)
        self.assertIn("当前股价", result["basis_derivation"])

    def test_dcf_supports_new_futu_field_ids_and_derives_revenue_per_share(self):
        collector = load_collector()
        evidence = self.dcf_evidence()
        main_reports = evidence["financial_quality"]["annual_statements"]["main_index"]["data"]["report_list"]
        mapping = {3010: 1008, 3013: 1011, 3028: 1026}
        for report in main_reports:
            report["item_list"] = [item for item in report["item_list"] if item["field_id"] != 3012]
            for item in report["item_list"]:
                item["field_id"] = mapping.get(item["field_id"], item["field_id"])
        income_reports = evidence["financial_quality"]["annual_statements"]["income"]["data"]["report_list"]
        for report in income_reports:
            for item in report["item_list"]:
                if item["field_id"] == 3001:
                    item["field_id"] = 1001

        result = collector.derive_dcf_valuation(evidence, "SHORT")

        self.assertTrue(result["available"])
        self.assertEqual(len(result["historical_inputs"]["revenue_per_share"]), 5)

    def test_dcf_valuation_builds_auditable_scenarios_and_sensitivity(self):
        collector = load_collector()

        result = collector.derive_dcf_valuation(self.dcf_evidence(), "MEDIUM")

        self.assertTrue(result["available"])
        self.assertEqual(result["method"], "5-year two-stage FCFF DCF")
        self.assertLess(result["scenarios"]["bear"]["value"], result["scenarios"]["base"]["value"])
        self.assertLess(result["scenarios"]["base"]["value"], result["scenarios"]["bull"]["value"])
        self.assertEqual(len(result["sensitivity"]["rows"]), 3)
        self.assertEqual(len(result["sensitivity"]["rows"][0]["values"]), 3)
        self.assertEqual(result["capital_structure"]["share_basis"], "EBIT ÷ 每股 EBIT")
        self.assertLess(result["capital_structure"]["net_debt_per_share"], 0)
        self.assertEqual(result["historical_inputs"]["revenue_growth_basis"], "利润表绝对营业收入近3年 CAGR")

    def test_dcf_valuation_excludes_financial_companies(self):
        collector = load_collector()

        result = collector.derive_dcf_valuation(self.dcf_evidence(industry="银行"), "MEDIUM")

        self.assertFalse(result["available"])
        self.assertIn("金融行业", result["reason"])

    def test_dcf_valuation_fails_closed_on_persistently_negative_fcff(self):
        collector = load_collector()

        result = collector.derive_dcf_valuation(
            self.dcf_evidence(fcff_values=[-5.0, -4.0, -3.0, -2.0, -1.0]),
            "MEDIUM",
        )

        self.assertFalse(result["available"])
        self.assertIn("FCFF", result["reason"])

    def test_brief_renders_dcf_target_price_section(self):
        import tempfile

        collector = load_collector()
        with tempfile.TemporaryDirectory() as temp_dir:
            result = collector.write_bundle(
                collector.normalize_ticker("300001"),
                self.dcf_evidence(),
                Path(temp_dir),
                date(2026, 8, 1),
                "MEDIUM",
                "zh-CN",
            )
            brief = (Path(result["output_dir"]) / "research_brief.md").read_text(encoding="utf-8")
            derived = json.loads((Path(result["output_dir"]) / "research_derived.json").read_text(encoding="utf-8"))

            self.assertIn("目标价区间（FCFF DCF）", brief)
            self.assertIn("基准目标价", brief)
            self.assertTrue(derived["dcf_valuation"]["available"])

    def test_brief_includes_stock_name_from_snapshot(self):
        collector = load_collector()

        ticker = collector.normalize_ticker("300750")
        evidence = {"snapshot": {"data": [{"name": "宁德时代"}]}}
        enriched = collector.enrich_ticker_name(ticker, evidence)

        self.assertEqual(enriched["code"], "300750.SZ")
        self.assertEqual(enriched["name"], "宁德时代")

    def test_cli_writes_reproducible_research_bundle(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            evidence_path = tmp_path / "evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "301217.SZ": {
                            "model": {"total_score": 79, "rank": 8},
                            "financial_quality": {"score": 76},
                            "valuation": {"score": 64},
                            "catalyst": {"score": 70},
                            "technical_flow": {"score": 68},
                            "governance_risk": {"score": 78},
                            "data_confidence": {"score": 80},
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "301217",
                    "--output-root",
                    str(tmp_path / "outputs"),
                    "--as-of",
                    "2026-07-07",
                    "--evidence-json",
                    str(evidence_path),
                ],
                check=True,
                text=True,
                capture_output=True,
            )

            self.assertIn("301217.SZ", completed.stdout)
            stock_dir = tmp_path / "outputs" / "20260707" / "301217.SZ"
            raw = json.loads((stock_dir / "research_raw.json").read_text(encoding="utf-8"))
            derived = json.loads((stock_dir / "research_derived.json").read_text(encoding="utf-8"))
            brief = (stock_dir / "research_brief.md").read_text(encoding="utf-8")

            self.assertEqual(raw["ticker"]["code"], "301217.SZ")
            self.assertIn(derived["research_score"]["posture"], {"CORE CANDIDATE", "TIMING WATCH"})
            self.assertIn("研究快照", brief)
            self.assertIn("仓位建议", brief)

    def test_cli_uses_stock_name_for_output_directory(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp_dir:
            tmp_path = Path(temp_dir)
            evidence_path = tmp_path / "evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "300750.SZ": {
                            "snapshot": {"data": [{"name": "宁德时代", "last_price": 395.3}]},
                            "model": {"total_score": 79},
                            "financial_quality": {"score": 76},
                            "valuation": {"score": 64},
                            "catalyst": {"score": 70},
                            "technical_flow": {"score": 68},
                            "governance_risk": {"score": 78},
                            "data_confidence": {"score": 80},
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "300750.SZ",
                    "--output-root",
                    str(tmp_path / "outputs"),
                    "--as-of",
                    "2026-07-07",
                    "--evidence-json",
                    str(evidence_path),
                ],
                check=True,
                text=True,
                capture_output=True,
            )
            stock_dir = tmp_path / "outputs" / "20260707" / "宁德时代"
            self.assertTrue((stock_dir / "research_brief.md").is_file())
            brief = (stock_dir / "research_brief.md").read_text(encoding="utf-8")
            self.assertIn("股票代码：300750.SZ", brief)
            self.assertIn("股票名称：宁德时代", brief)

    def test_skill_instructions_do_not_hardcode_windows_python_path(self):
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")

        self.assertNotIn("E:\\stock", text)
        self.assertNotIn("Scripts\\python.exe", text)


if __name__ == "__main__":
    unittest.main()
