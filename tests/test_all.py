import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import evaluate
import numcheck
import rules
import score
from parse import clean, load_all


class TestParse(unittest.TestCase):
    def test_load_50(self):
        rows = load_all()
        self.assertEqual(len(rows), 50)
        self.assertEqual(sum(r["src"] == "중앙" for r in rows), 25)

    def test_double_escaped_entities(self):
        self.assertEqual(clean("&amp;#9312; 맞벌이\r가정"), "① 맞벌이\n가정")


class TestNumcheck(unittest.TestCase):
    def test_units_equivalent(self):
        self.assertTrue(numcheck.check("연 200만원 한도", "1년에 2,000,000원까지")["ok"])

    def test_range_and_missing(self):
        r = numcheck.check("19~44세 기혼여성, 기준중위소득 180% 이하", "44세 이하 기혼 여성")
        self.assertEqual(sorted(r["missing"]), ["180%", "19세"])

    def test_year_ignored(self):
        self.assertEqual(numcheck.numbers("2026년 기준"), [])


class TestScore(unittest.TestCase):
    def ext(self, **kw):
        base = {"online": True, "visit_required": False, "agencies": [], "conditions": [], "documents": None}
        return {**base, **kw}

    def test_zero_and_max(self):
        read0 = {"points": 0}
        self.assertEqual(score.score(self.ext(), read0)["score"], 0)
        full = self.ext(online=False, visit_required=True, agencies=["신청", "조사", "결정"],
                        conditions=[{"type": "x", "text": ""}] * 6, documents=["a"] * 8)
        self.assertEqual(score.score(full, {"points": 3})["score"], 100)

    def test_reason_text(self):
        e = self.ext(online=False, visit_required=True, agencies=["신청", "조사", "결정"],
                     conditions=[{"type": "x", "text": ""}] * 5)
        r = score.score(e, {"points": 0})
        self.assertTrue(r["reason"].startswith("방문만 가능, 조건 5개, 기관 3단계, 서류 정보 없음"))


class TestRules(unittest.TestCase):
    def test_document_list(self):
        svc = {"src": "지자체", "apply_method_name": "방문", "criteria": "", "target": "",
               "apply": "읍면사무소에 신청.[구비서류]1. 화장장려금 지원 신청서 1부.2. 화장증명서 1부.3. 통장사본 1부."}
        self.assertEqual(rules.detect_documents(svc), ["화장장려금 지원 신청서", "화장증명서", "통장사본"])
        self.assertTrue(rules.detect_visit(svc))
        self.assertFalse(rules.detect_online(svc))


class TestEvaluate(unittest.TestCase):
    def test_compare(self):
        a = {"1": {"online": True, "visit_required": False, "n_agencies": 1, "n_conditions": 2, "n_documents": None}}
        b = {"1": {"online": True, "visit_required": True, "n_agencies": 1, "n_conditions": 4, "n_documents": None}}
        c = evaluate.compare(a, b)
        self.assertEqual((c["online"], c["visit_required"], c["n_conditions_mae"]), (100.0, 0.0, 2.0))


class TestApp(unittest.TestCase):
    def test_app_runs(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.title[0].value, "복지문턱")
        self.assertEqual(len(at.radio), 0)  # 점수는 기준표로만 계산(AI 선택지 없음)

    def test_score_ignores_ai_cache(self):
        from pipeline import build
        self.assertTrue(all(r["ext_source"] == "규칙" for r in build()))


if __name__ == "__main__":
    unittest.main()
