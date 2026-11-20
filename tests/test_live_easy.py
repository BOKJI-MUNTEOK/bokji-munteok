"""미리 만든 쉬운 설명이 없는 서비스: 화면을 열 때 AI로 만들고, 실패하면 원문을 보여 준다."""
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import test_all

EASY = {"easy": {"who": "만 65세 이상 어르신", "conditions": ["소득 하위 70%"], "what": "매달 10만 원",
                 "steps": ["주민센터에 신청서를 내요."]}, "check": {"ok": True}}


class LiveEasy(unittest.TestCase):
    def setUp(self):
        test_all.TestApp.setUp(self)

    def app(self):
        script = f"""import runpy, pipeline
app = runpy.run_path({str(test_all.ROOT / 'app.py')!r})
row = next(r for r in app['ROWS'] if r['svc']['servId'] == {self.target!r})
row = dict(row, ai=None)
app['easy_cards'](row)
"""
        return AppTest.from_string(script, default_timeout=30)

    def setUpTarget(self):
        self.target = self.sample[0]["servId"]

    def test_makes_explanation_when_cache_missing_and_reuses_it(self):
        self.setUpTarget()
        with patch("chat_ai.available", return_value=True), patch("llm.make_easy_short", return_value=EASY) as make:
            at = self.app().run()
            self.assertFalse(at.exception)
            self.assertTrue(any("매달 10만 원" in m.value for m in at.markdown))
            self.assertTrue(any("AI가 복지로 공식 안내문을 쉬운 말로" in c.value for c in at.caption))
            self.assertTrue(any(e.label.startswith("원문 보기") for e in at.expander))
            self.assertFalse([b for b in at.button if "설명" in b.label])  # 설명을 듣기 위해 누를 버튼이 없다(자동)
            at.run()  # 다시 열어도 같은 서비스는 새로 만들지 않는다
        self.assertEqual(make.call_count, 1)

    def test_long_lists_are_folded_to_three(self):
        self.setUpTarget()
        long = {"easy": {"who": "누구나", "conditions": [f"조건{i}" for i in range(6)], "what": "지원",
                         "steps": [f"단계{i}" for i in range(5)]}, "check": {"ok": True}}
        with patch("chat_ai.available", return_value=True), patch("llm.make_easy_short", return_value=long):
            at = self.app().run()
        self.assertFalse(at.exception)
        shown = " ".join(m.value for m in at.markdown)
        self.assertIn("조건2", shown)
        self.assertTrue(any(e.label == "자세히 보기" for e in at.expander))

    def test_failure_or_no_key_shows_original(self):
        self.setUpTarget()
        with patch("chat_ai.available", return_value=True), patch("llm.make_easy_short", side_effect=TimeoutError("SECRET")):
            at = self.app().run()
        self.assertFalse(at.exception)
        self.assertTrue(any("공식 안내를 그대로" in i.value for i in at.info))
        self.assertNotIn("SECRET", str(at.info))
        with patch("chat_ai.available", return_value=False), patch("llm.make_easy_short", side_effect=AssertionError("no call")):
            at = self.app().run()
        self.assertFalse(at.exception)
        self.assertTrue(any("공식 안내를 그대로" in i.value for i in at.info))


class FormSplit(unittest.TestCase):
    def setUp(self):
        test_all.TestApp.setUp(self)

    def test_browse_splits_services_by_form_availability(self):
        target = self.sample[0]["servId"]
        script = f"""import runpy
app = runpy.run_path({str(test_all.ROOT / 'app.py')!r})
app['browse']()
"""
        at = AppTest.from_string(script, default_timeout=30).run()
        self.assertFalse(at.exception)
        labels = [t.label for t in at.tabs]
        self.assertTrue(labels[0].startswith("📝 신청서 바로 작성 가능"))
        self.assertTrue(labels[1].startswith("📄 신청서 파일 없음"))
        import document_workspace
        ready = sum(any(document_workspace.attachment_kind(f["name"]) == "form" for f in s["form_files"]) for s in self.sample)
        self.assertIn(f"{ready:,}개", labels[0])
        self.assertIn(f"{len(self.sample) - ready:,}개", labels[1])
        self.assertTrue(any("신청서 바로 작성" in m.value for m in at.markdown))


if __name__ == "__main__":
    unittest.main()
