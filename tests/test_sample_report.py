"""보고서의 표본·결측 처리와 오프라인 재현성을 검증한다."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import evaluate
import llm
import sample_report
from parse import load_all


class TestSampleReport(unittest.TestCase):
    def test_missing_document_counts_are_not_zero_in_mae(self):
        base = {"online": False, "visit_required": True, "n_agencies": 1, "n_conditions": 2}
        pred = {"a": {**base, "n_documents": None}, "b": {**base, "n_documents": 0},
                "c": {**base, "n_documents": 4}}
        gold = {"a": {**base, "n_documents": None}, "b": {**base, "n_documents": None},
                "c": {**base, "n_documents": 2}}
        cmp = evaluate.compare(pred, gold)
        self.assertEqual(cmp["n_documents"], 33.3)
        self.assertEqual(cmp["n_documents_mae"], 2.0)
        self.assertEqual(cmp["n_documents_mae_n"], 1)
        cmp = evaluate.compare({"a": pred["a"]}, {"a": gold["a"]})
        self.assertIsNone(cmp["n_documents_mae"])
        self.assertEqual(cmp["n_documents_mae_n"], 0)

    def test_missing_ai_uses_same_cohort_without_rule_fallback(self):
        services = load_all(ROOT / "data" / "sample")[:2]
        excluded = services[0]["servId"]
        original = llm.load_cached
        with patch("llm.load_cached", side_effect=lambda sid, svc: None if sid == excluded else original(sid, svc)):
            report = sample_report.build_report(services)
        cohort = report["cohorts"]["전체"]
        self.assertEqual(cohort["gold_n"], 2)
        self.assertEqual(cohort["excluded_ai_ids"], [excluded])
        self.assertEqual((cohort["rule"]["n"], cohort["ai"]["n"]), (1, 1))

    def test_changed_source_excluded_from_gold_and_ai(self):
        svc = {**load_all(ROOT / "data" / "sample")[0], "criteria": "변경된 자격 조건"}
        report = sample_report.build_report([svc])
        self.assertEqual(report["sample_n"], 1)
        self.assertEqual(report["eligible_gold_n"], 0)
        self.assertEqual(report["ai_n"], 0)
        self.assertEqual(report["cohorts"]["전체"]["compared_n"], 0)

    def test_checked_in_artifacts_reproduce_without_network(self):
        with patch("urllib.request.urlopen", side_effect=AssertionError("보고서는 네트워크를 쓰지 않습니다")):
            report = sample_report.build_report()
            contents = sample_report.artifacts(report)
        self.assertEqual(report["sample_n"], 50)
        self.assertEqual(report["cohorts"]["전체"]["compared_n"], 50)
        for name, content in contents.items():
            self.assertEqual((ROOT / "eval" / "results" / name).read_bytes(), content.encode("utf-8"), name)
        summary = json.loads(contents["summary.json"])
        self.assertTrue(summary["input_sha256"])


if __name__ == "__main__":
    unittest.main()
