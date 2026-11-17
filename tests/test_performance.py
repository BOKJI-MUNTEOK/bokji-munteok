"""화면 최적화가 답변·가중치·캐시 최신성을 바꾸지 않는지 확인한다."""
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import test_all
import pipeline
import score

ROOT = test_all.ROOT


class TestPerformance(unittest.TestCase):
    def setUp(self):
        test_all.TestApp.setUp(self)

    def app(self, page):
        script = f"import runpy\napp = runpy.run_path({str(ROOT / 'app.py')!r})\napp[{page!r}]()"
        return AppTest.from_string(script, default_timeout=30)

    def test_wizard_keeps_answers_validates_age_and_reuses_scores(self):
        with patch("pipeline.build", wraps=pipeline.build) as build:
            at = self.app("mine").run()
            self.assertFalse(at.exception)
            self.assertEqual(build.call_count, 1)
            at.button(key="form_next").click().run()
            self.assertEqual(at.session_state.fi, 1)
            at.button(key="form_next").click().run()
            self.assertEqual(at.session_state.fi, 1)
            self.assertTrue(at.error)
            # 입력 직후 다음을 눌러도 최신 값을 저장한다.
            at.number_input(key="f_age").set_value(32)
            at.button(key="form_next").click().run()
            self.assertFalse(at.error)
            self.assertEqual(at.session_state.form["age"], "32세")
            self.assertEqual(at.session_state.fi, 2)
            at.button(key="form_previous").click().run()
            self.assertEqual(at.number_input(key="f_age").value, 32)
            at.number_input(key="f_age").set_value(41)
            at.button(key="form_next").click().run()
            for _ in range(4):
                at.button(key="form_next").click().run()
            self.assertEqual(at.session_state.fi, 6)
            self.assertEqual(at.session_state.me["age"], 41)
            self.assertTrue(any("맞춤 복지" in m.value for m in at.markdown))
            at.button(key="form_reset").click().run()
            self.assertEqual(at.session_state.fi, 0)
            self.assertEqual(at.session_state.form, {})
            self.assertFalse(at.exception)
            self.assertEqual(build.call_count, 1)

    def test_changed_region_drops_old_district_and_previous_preserves_selection(self):
        at = self.app("mine").run()
        sido = next(s["sido"] for s in self.sample if s["src"] == "지자체" and s["sido"])
        at.selectbox(key="f_sido").select(sido).run()
        district = at.selectbox(key="f_sgg").options[-1]
        at.selectbox(key="f_sgg").select(district).run()
        at.button(key="form_next").click().run()
        at.button(key="form_previous").click().run()
        self.assertEqual(at.selectbox(key="f_sido").value, sido)
        self.assertEqual(at.selectbox(key="f_sgg").value, district)
        at.selectbox(key="f_sido").select("선택 안 함").run()
        self.assertEqual(at.selectbox(key="f_sgg").value, "전체")
        at.button(key="form_next").click().run()
        self.assertEqual(at.session_state.form["place"], "없음")
        self.assertFalse(at.exception)

    def test_chat_answers_and_restart_keep_working_with_callbacks(self):
        with patch("pipeline.build", wraps=pipeline.build) as build:
            at = self.app("mine")
            at.session_state["mode"] = "상담으로 찾기"
            at.run()
            for answer in ("없음", "32살", "직장인", "혼자 살아요", "없음", "생활비"):
                at.chat_input[0].set_value(answer).run()
                self.assertFalse(at.exception)
            self.assertEqual(at.session_state.qi, 6)
            self.assertEqual(at.session_state.me["age"], 32)
            next(b for b in at.button if b.label == "처음부터").click().run()
            self.assertEqual(at.session_state.qi, 0)
            self.assertIsNone(at.session_state.prof["age"])
            self.assertEqual(build.call_count, 1)

    def test_weight_change_and_reset_match_original_score_formula(self):
        with patch("pipeline.build", wraps=pipeline.build) as build:
            at = self.app("analysis")
            at.session_state["analysis_tab"] = "점수 상세"
            at.run()
            self.assertEqual(len(at.slider), 6)
            defaults = at.dataframe[0].value["점수"].tolist()
            self.assertEqual(build.call_count, 1)
            at.slider(key="_w_no_online").set_value(0.0).run()
            self.assertEqual(at.session_state.W["no_online"], 0)
            self.assertEqual(build.call_count, 2)
            expected = pipeline.build({**score.DEFAULT_WEIGHTS, "no_online": 0}, services=self.sample)
            self.assertEqual(at.dataframe[0].value["점수"].tolist(), [r["score"] for r in expected])
            next(b for b in at.button if b.label == "기본값으로").click().run()
            self.assertEqual(at.session_state.W, score.DEFAULT_WEIGHTS)
            self.assertEqual(at.dataframe[0].value["점수"].tolist(), defaults)
            self.assertEqual(build.call_count, 3)  # expected 계산 1회 포함, 초기화는 캐시 재사용
            self.assertFalse(at.exception)

    def test_analysis_only_computes_visible_tab(self):
        with patch("evaluate.load_gold", return_value={}) as gold:
            at = self.app("analysis").run()
            self.assertFalse(at.exception)
            self.assertEqual(len(at.slider), 0)
            self.assertEqual(len(at.dataframe), 0)
            gold.assert_not_called()
            at.session_state["analysis_tab"] = "검증"
            at.run()
            gold.assert_called_once()
            self.assertFalse(at.exception)

    def test_new_catalog_version_recalculates_but_unchanged_version_does_not(self):
        self.api.catalog.return_value["sync"] = {"revision": 1, "sources": {
            source: {"ready": 25, "total": 25} for source in ("central", "local")}}
        script = (f"import runpy\napp = runpy.run_path({str(ROOT / 'app.py')!r})\n"
                  "import streamlit as st\nst.session_state['clear_catalog'] = app['services'].clear\n")
        with patch("pipeline.build", wraps=pipeline.build) as build:
            at = AppTest.from_string(script, default_timeout=30).run()
            at.run()
            self.assertEqual(build.call_count, 1)
            self.api.catalog.return_value["sync"]["revision"] += 1
            at.session_state["clear_catalog"]()
            at.run()
            self.assertEqual(build.call_count, 2)
            self.assertFalse(at.exception)

    def test_loaded_detail_opens_without_database_request(self):
        for service in self.sample:
            service["detail_fetched_at"] = 1794873600
        at = self.app("browse").run()
        first = next(b for b in at.button if b.label == "신청 방법 보기")
        first.click().run()
        self.assertFalse(at.exception)
        self.assertFalse(at.error)
        self.api.detail.assert_not_called()
        self.assertTrue(any("상세 조회:" in c.value for c in at.caption))


if __name__ == "__main__":
    unittest.main()
