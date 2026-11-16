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
        svc["apply"] = "○ 신청서류 : 신청서, 개인정보동의서, 통장사본"
        self.assertEqual(rules.detect_documents(svc), ["신청서", "개인정보동의서", "통장사본"])
        self.assertTrue(rules.detect_visit(svc))
        self.assertFalse(rules.detect_online(svc))


class TestEvaluate(unittest.TestCase):
    def test_compare(self):
        a = {"1": {"online": True, "visit_required": False, "n_agencies": 1, "n_conditions": 2, "n_documents": None}}
        b = {"1": {"online": True, "visit_required": True, "n_agencies": 1, "n_conditions": 4, "n_documents": None}}
        c = evaluate.compare(a, b)
        self.assertEqual((c["online"], c["visit_required"], c["n_conditions_mae"]), (100.0, 0.0, 2.0))

    def test_load_gold_merges_person_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            head = "servId,servNm,online,visit_required,n_agencies,n_conditions,n_documents\n"
            (d / "gold_a.csv").write_text(head + "1,가,Y,N,2,3,\n2,나,,,,,\n", encoding="utf-8-sig")
            (d / "gold_b.csv").write_text(head.strip() + ",메모\n3,다,N,Y,1,2,4,\n4,라,Y,N,1,1,,AI 초안(검수 전)\n", encoding="utf-8-sig")
            (d / "gold_template.csv").write_text(head + "9,틀,Y,Y,1,1,1\n", encoding="utf-8-sig")
            old, evaluate.EVAL = evaluate.EVAL, d
            try:
                g = evaluate.load_gold()
            finally:
                evaluate.EVAL = old
        self.assertEqual(sorted(g), ["1", "3"])  # 빈 줄·틀·검수 전 초안 제외
        self.assertEqual(g["1"]["n_documents"], None)
        self.assertEqual(g["3"]["n_documents"], 4)


class TestMine(unittest.TestCase):
    def test_match_tags(self):
        import match
        svc = {"src": "지자체", "sido": "경기도", "sgg": "성남시", "region": "경기도 성남시",
               "life": ["노년"], "groups": ["저소득"], "themes": []}
        self.assertIsNone(match.match(svc, sido="부산광역시", life=["노년"], groups=["저소득"]))
        self.assertIsNone(match.match(svc, sido="경기도", life=["노년"]))  # 저소득 대상인데 해당 없음
        self.assertEqual(match.match(svc, sido="경기도", life=["노년"], groups=["저소득"]), ["경기도 성남시", "노년", "저소득"])
        svc["groups"] = ["장애인", "저소득"]
        self.assertIsNone(match.match(svc, sido="경기도", groups=["저소득"]))  # 장애인 대상은 저소득만으로 안 됨

    def test_intake_steps(self):
        import intake
        places = [("전남광주통합특별시", "해남군")]
        p = intake.new_profile()
        for key, text in (("name", "민수"), ("place", "해남 살아요"), ("age", "32살"), ("job", "구직 중"),
                          ("family", "아이 3명 이상"), ("special", "없음"), ("need", "집·월세")):
            self.assertIsNone(intake.take(p, key, text, places))
        self.assertEqual(intake.conditions(p), {"sido": "전남광주통합특별시", "sgg": "해남군", "life": ["청년", "영유아", "아동"],
                                                "groups": ["다자녀"], "themes": ["일자리", "주거"]})
        self.assertEqual(intake.find_place("경상북도", [("전남광주통합특별시", "북구"), ("경상북도", "고령군")]), ("경상북도", ""))
        self.assertIsNotNone(intake.take(p, "age", "몰라요", places))  # 나이를 못 알아들으면 다시 묻는다
        intake.take(p, "need", "집·월세, 치매 어머니 간병", places)  # 기타로 직접 적은 말은 요약에 남긴다
        self.assertIn("직접 적음: 치매 어머니 간병", intake.summary(p))

    def test_paperwork(self):
        import paperwork
        row = {"ai": None, "ext": {"documents": None}}
        self.assertEqual(paperwork.documents(row), (paperwork.BASIC, False))  # 목록 없으면 기본 서류
        self.assertTrue(paperwork.where("소득금액증명원")[1])  # 홈택스 온라인 발급


class TestPacket(unittest.TestCase):
    def test_zip(self):
        import io, zipfile
        import packet
        from pipeline import build
        r = next(x for x in build() if len(x["svc"]["form_files"]) >= 2)

        def fetch(url):  # 첫 파일만 받히고 나머지는 실패하는 복지로 흉내
            if url != r["svc"]["form_files"][0]["url"]:
                raise TimeoutError
            return b"hwp"
        data, failed = packet.build_zip(r, None, {}, fetch=fetch)
        names = zipfile.ZipFile(io.BytesIO(data)).namelist()
        self.assertTrue(any(n.endswith(".pdf") for n in names))
        self.assertEqual(len(failed), len(r["svc"]["form_files"]) - 1)
        self.assertIn("내려받지_못한_파일.txt", names)


class TestApp(unittest.TestCase):
    def test_app_runs(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.title[0].value, "복지온길")
        self.assertEqual(len(at.radio), 0)  # 점수는 기준표로만 계산(AI 선택지 없음)

    def test_demo_link_shows_service(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
        at.query_params["service"] = "주거약자 이사지원"
        at.run()
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(any("주거약자 이사지원" in m.value for m in at.markdown))  # 시연 서비스 카드

    def test_score_ignores_ai_cache(self):
        from pipeline import build
        self.assertTrue(all(r["ext_source"] == "규칙" for r in build()))


if __name__ == "__main__":
    unittest.main()
