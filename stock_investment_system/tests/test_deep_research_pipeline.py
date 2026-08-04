from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import mock_open, patch

from stock_investment_system.launch_deep_research import build_command, start_detached

from stock_investment_system.deep_research_pipeline import (
    DEEP_COLUMNS,
    build_model_rows,
    parse_watchlist,
    select_codes,
    summary_columns,
    write_enriched_csv,
    write_html,
)


class DeepResearchPipelineTests(unittest.TestCase):
    def sample_rows(self):
        return [
            {"模型": "质量成长", "股票代码": "300750", "股票名称": "宁德时代", "总分": "76", "估值分": "61", "行业": "电池"},
            {"模型": "行业轮动", "股票代码": "300750", "股票名称": "宁德时代", "总分": "68", "资金流分": "80", "行业": "电池"},
            {"模型": "质量成长", "股票代码": "600519", "股票名称": "贵州茅台", "总分": "80", "估值分": "70", "行业": "白酒"},
        ]

    def test_parse_watchlist_skips_futu_log_lines(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "watchlist.csv"
            path.write_text(
                "OpenD connection log\n\ufeff模型,股票代码,股票名称,总分\n质量成长,300750,宁德时代,76\nDisconnected log\n",
                encoding="utf-8",
            )

            fields, rows = parse_watchlist(path)

            self.assertEqual(fields[:3], ["模型", "股票代码", "股票名称"])
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["股票代码"], "300750")

    def test_select_codes_deduplicates_and_ranks_by_best_model_score(self):
        codes = select_codes(self.sample_rows(), 0)

        self.assertEqual(codes, ["600519.SH", "300750.SZ"])

    def test_build_model_rows_reuses_generated_scores(self):
        result = build_model_rows(self.sample_rows(), {"300750.SZ"})

        self.assertEqual(len(result["300750"]), 2)
        self.assertEqual(result["300750"][0]["valuation_score"], 61.0)
        self.assertEqual(result["300750"][1]["capital_flow_score"], 80.0)

    def test_enriched_csv_and_html_include_research_conclusions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            research_dir = root / "宁德时代"
            research_dir.mkdir()
            for name in ("research_brief.md", "research_derived.json", "research_raw.json"):
                (research_dir / name).write_text("{}", encoding="utf-8")
            summary = {
                "code": "300750.SZ",
                "name": "宁德时代",
                "models": "质量成长、行业轮动",
                "model_max_score": 76.0,
                "directory": research_dir,
                "raw": {},
                "conclusion": {
                    "posture": "TIMING WATCH",
                    "total_score": 72,
                    "confidence": "LOW",
                    "position_band": "2%-4%",
                    "hard_limits": [],
                    "next_catalyst": "下一期财报",
                    "key_risk": "估值偏高",
                    "primary_invalidation": "利润转负",
                },
                "derived": {
                    "research_score": {"posture": "TIMING WATCH", "total_score": 72, "confidence": "HIGH", "position_band": "2%-4%", "hard_limits": []},
                    "fair_value": {"available": True, "scenarios": {"bear": {"value": 300}, "base": {"value": 380, "upside_pct": 5}, "bull": {"value": 430}}, "current_price_implied_basis": {"available": True, "required_growth_pct": 10}},
                    "dcf_valuation": {"available": True, "confidence": "LOW", "valuation_role": "low-confidence cash-flow floor", "scenarios": {"bear": {"value": 150}, "base": {"value": 240, "upside_pct": -30}, "bull": {"value": 390}}},
                    "next_catalyst": "下一期财报",
                    "key_risk": "估值偏高",
                    "primary_invalidation": "利润转负",
                },
            }
            source_rows = [self.sample_rows()[0]]
            csv_path = root / "enhanced.csv"
            html_path = root / "summary.html"
            portfolio_path = root / "portfolio.md"
            original_path = root / "original.csv"
            original_path.write_text("", encoding="utf-8")
            portfolio_path.write_text("", encoding="utf-8")

            write_enriched_csv(csv_path, list(source_rows[0]), source_rows, {"300750.SZ": summary})
            write_html(
                html_path,
                original_path,
                csv_path,
                portfolio_path,
                [summary],
                __import__("datetime").datetime.now(),
            )

            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                row = next(csv.DictReader(handle))
            html_text = html_path.read_text(encoding="utf-8")

            self.assertTrue(set(DEEP_COLUMNS).issubset(row))
            self.assertEqual(row["深研姿态"], "TIMING WATCH")
            self.assertEqual(row["相对估值基准值"], "380")
            self.assertIn("宁德时代", html_text)
            self.assertIn("LOW", html_text)

    def test_summary_columns_preserves_failure_without_crashing(self):
        result = summary_columns({"error": "研究失败"})

        self.assertEqual(result["深研姿态"], "研究失败")

    def test_detached_launcher_starts_new_session_and_logs_output(self):
        fake_process = type("Process", (), {"pid": 2468})()
        with patch("pathlib.Path.open", mock_open()) as opened, patch(
            "stock_investment_system.launch_deep_research.subprocess.Popen",
            return_value=fake_process,
        ) as popen:
            pid = start_detached(["runtime", "pipeline"], Path("/tmp/research.log"))

        self.assertEqual(pid, 2468)
        opened.assert_called_once_with("ab", buffering=0)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(popen.call_args.kwargs["stderr"], __import__("subprocess").STDOUT)

    def test_detached_launcher_builds_pipeline_command(self):
        args = type(
            "Args",
            (),
            {
                "watchlist_csv": Path("watchlist.csv"),
                "timestamp": "20260801_120000",
                "horizon": "MEDIUM",
                "max_stocks": 0,
                "open_report": True,
            },
        )()

        command = build_command(args)

        self.assertIn("deep_research_pipeline.py", command[1])
        self.assertIn("--open-report", command)
        self.assertEqual(command[command.index("--max-stocks") + 1], "0")


if __name__ == "__main__":
    unittest.main()
