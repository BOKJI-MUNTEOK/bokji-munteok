"""화면 최적화가 답변·가중치·캐시 최신성을 바꾸지 않는지 확인한다."""
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import test_all
import analysis_cache
import pipeline
import rules
import score

ROOT = test_all.ROOT


class TestPerformance(unittest.TestCase):
    def setUp(self):
        test_all.TestApp.setUp(self)
        analysis_cache.clear_cache()
        self.addCleanup(analysis_cache.clear_cache)

    def app(self, page):
        script = f"import runpy\napp = runpy.run_path({str(ROOT / 'app.py')!r})\napp[{page!r}]()"
        return AppTest.from_string(script, default_timeout=30)

    def observed_app(self):
        script = (f"import runpy\napp = runpy.run_path({str(ROOT / 'app.py')!r})\n"
                  "import streamlit as st\n"
                  "st.session_state['observed_rows'] = app['ROWS']\n"
                  "st.session_state['observed_catalog'] = app['CATALOG']\n"
                  "st.session_state['clear_catalog'] = app['services'].clear\n"
                  "if page := st.session_state.get('test_page'):\n    app[page]()\n")
        return AppTest.from_string(script, default_timeout=30)

    def set_revision(self, revision):
        self.api.catalog.return_value["sync"] = {"revision": revision, "sources": {
            source: {"ready": 25, "total": 25} for source in ("central", "local")}}

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

    def test_new_check_time_and_warning_refresh_without_rebuilding_scores(self):
        self.set_revision(1)
        catalog_at = self.api.catalog.return_value["updated_at"]
        self.api.catalog.return_value["sync"]["sources"]["central"].update(
            added=2, changed=3, removed=1, catalog_at=catalog_at)
        self.api.catalog.return_value["sync"]["sources"]["local"].update(
            added=4, changed=1, removed=2, catalog_at=catalog_at)
        with patch("pipeline.build", wraps=pipeline.build) as build:
            at = self.observed_app().run()
            old_rows = at.session_state.observed_rows
            self.assertTrue(any("최근 목록 변경: 추가 6건 · 수정 4건 · 삭제 3건" in c.value for c in at.caption))
            refreshed = deepcopy(self.api.catalog.return_value)
            checked_at = catalog_at + 6 * 3600
            refreshed["sync"]["sources"]["central"].update(
                added=0, changed=0, removed=0, catalog_checked_at=checked_at)
            refreshed["sync"]["sources"]["local"].update(
                added=0, changed=0, removed=0, catalog_checked_at=checked_at + 600)
            refreshed["warnings"] = ["중앙부처 오늘 호출 한도에 도달했습니다."]
            self.api.catalog.return_value = refreshed
            at.session_state.clear_catalog()
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(build.call_count, 1)
            self.assertIs(at.session_state.observed_rows, old_rows)
            self.assertEqual(at.session_state.observed_catalog["updated_at"], refreshed["updated_at"])
            self.assertTrue(any("중앙부처 오늘 호출 한도" in w.value for w in at.warning))
            self.assertTrue(any("최근 목록 변경: 추가 0건 · 수정 0건 · 삭제 0건" in c.value for c in at.caption))
            checked_text = datetime.fromtimestamp(checked_at, timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
            self.assertTrue(any(checked_text in c.value for c in at.caption))

    def test_one_changed_service_does_not_reextract_other_services(self):
        self.set_revision(1)
        with patch("pipeline.build", wraps=pipeline.build) as build, \
                patch("rules.extract", wraps=rules.extract) as extract, \
                patch("score.readability", wraps=score.readability) as read:
            at = self.observed_app().run()
            self.assertFalse(at.exception)
            initial = (extract.call_count, read.call_count)
            original = deepcopy(at.session_state.observed_rows)
            updated = deepcopy(self.sample)
            updated[0]["criteria"] += " 19세 이상 취업한 청년에게 변경 적용"
            refreshed = deepcopy(self.api.catalog.return_value)
            refreshed["services"] = updated
            refreshed["sync"]["revision"] = 2
            self.api.catalog.return_value = refreshed
            self.api.services.return_value = updated
            at.session_state.clear_catalog()
            at.run()
            self.assertFalse(at.exception)
            self.assertEqual(build.call_count, 2)
            self.assertEqual((extract.call_count, read.call_count), (initial[0] + 1, initial[1] + 1))
            by_id = {r["svc"]["servId"]: r for r in at.session_state.observed_rows}
            for row in original:
                if row["svc"]["servId"] != updated[0]["servId"]:
                    self.assertEqual(by_id[row["svc"]["servId"]], row)
            self.assertEqual(by_id[updated[0]["servId"]]["svc"]["criteria"], updated[0]["criteria"])

    def test_two_sessions_share_public_rows_but_keep_answers_and_weights_private(self):
        self.set_revision(1)
        first, second = self.observed_app(), self.observed_app()
        first.session_state["test_page"] = "mine"
        second.session_state["test_page"] = "mine"
        first.run()
        second.run()
        self.assertFalse(first.exception)
        self.assertFalse(second.exception)
        public_rows = second.session_state.observed_rows
        original = deepcopy(public_rows)
        self.assertIs(first.session_state.observed_rows, public_rows)
        first.button(key="form_next").click().run()
        first.number_input(key="f_age").set_value(32)
        first.button(key="form_next").click().run()
        second.run()
        self.assertEqual(first.session_state.form["age"], "32세")
        self.assertEqual(second.session_state.form, {})
        self.assertEqual(second.session_state.fi, 0)
        self.assertEqual(public_rows, original)

        first.session_state["test_page"] = "analysis"
        first.session_state["analysis_tab"] = "점수 상세"
        first.run()
        first.slider(key="_w_no_online").set_value(0.0).run()
        self.assertEqual(first.session_state.W["no_online"], 0)
        self.assertIsNot(first.session_state.observed_rows, public_rows)
        second.session_state["test_page"] = "analysis"
        second.session_state["analysis_tab"] = "점수 상세"
        second.run()
        self.assertFalse(first.exception)
        self.assertFalse(second.exception)
        self.assertEqual(second.session_state.W, score.DEFAULT_WEIGHTS)
        self.assertEqual(second.slider(key="_w_no_online").value, score.DEFAULT_WEIGHTS["no_online"])
        self.assertIs(second.session_state.observed_rows, public_rows)
        self.assertEqual(public_rows, original)
        self.assertEqual(first.session_state.form["age"], "32세")
        self.assertEqual(second.session_state.form, {})

    def test_live_easy_explanation_does_not_modify_shared_public_row(self):
        script = (f"import runpy\napp = runpy.run_path({str(ROOT / 'app.py')!r})\n"
                  "import streamlit as st\n"
                  "row = app['ROWS'][0]\n"
                  "st.session_state['observed_row'] = row\n"
                  "app['easy_cards'](row)\n")
        easy = {"who": "지원 대상", "conditions": ["조건"], "what": "지원 내용", "steps": ["신청"]}
        with patch("llm.load_cached", return_value=None), \
                patch("chat_ai.available", return_value=True), \
                patch("llm.make_easy_short", return_value={"easy": easy}) as generated:
            at = AppTest.from_string(script, default_timeout=30).run()
            self.assertFalse(at.exception)
            generated.assert_called_once()
            self.assertIsNone(at.session_state.observed_row["ai"])
            self.assertFalse(any(k in at.session_state.observed_row for k in ("profile", "me", "form")))

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
