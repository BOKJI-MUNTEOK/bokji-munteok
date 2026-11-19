"""전체 분석 집계가 앱 구간 기준·소집단 제외·서식 구분을 지키는지 확인한다(DB 없이)."""
import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import full_report


def row(score, src="지자체", sido="서울특별시", life=(), forms=()):
    ext = {"online": False, "visit_required": True, "agencies": ["a"], "conditions": ["c1", "c2"], "documents": None}
    parts = {"온라인 신청 불가": 3.0, "방문 필수": 2.0}
    return {"svc": {"servId": f"S{score}", "servNm": f"서비스{score}", "src": src, "sido": sido, "region": sido,
                    "life": list(life), "groups": [], "form_files": [{"name": n} for n in forms]},
            "score": score, "raw": 5.0, "parts": parts, "ext": ext, "read": {"points": 2}, "reason": "이유"}


class TestFullReport(unittest.TestCase):
    def test_bands_groups_and_forms(self):
        rows = [row(70, forms=["신청서.hwp"])] + [row(30, life=["노년"]) for _ in range(30)] + [row(50, src="중앙", sido="")]
        s = full_report.summarize(rows, {"central": 1, "local": 31}, 0, datetime(2026, 11, 19))
        self.assertEqual(s["scored"], 32)
        self.assertEqual((s["overall"]["band_낮음"], s["overall"]["band_보통"], s["overall"]["band_높음"]), (93.8, 3.1, 3.1))
        self.assertEqual([g["이름"] for g in s["by_sido"]], ["서울특별시"])  # 지자체만, 30건 이상
        self.assertEqual([g["이름"] for g in s["by_life"]], ["노년"])
        self.assertEqual(s["forms"]["with_form"]["n"], 1)
        self.assertIn("| 건수 |", full_report.markdown(s))


if __name__ == "__main__":
    unittest.main()
