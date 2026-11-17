import io
import sys
import tempfile
import time
import traceback
import unittest
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import llm
import match
from parse import load_all, merge_detail, parse_list_item
from pipeline import build
from welfare_api import APIError, WelfareAPI


def listing(ids, total=None, page=1):
    root = ET.Element("wantedList")
    for key, value in {"resultCode": 0, "totalCount": len(ids) if total is None else total,
                       "pageNo": page, "numOfRows": 500}.items():
        ET.SubElement(root, key).text = str(value)
    for sid in ids:
        item = ET.SubElement(root, "servList")
        for key, value in {"servId": sid, "servNm": f"복지 {sid}", "servDgst": "공식 요약",
                           "ctpvNm": "경기도", "sggNm": "성남시", "lifeNmArray": "청년, 중장년",
                           "onapPsbltYn": "Y", "aplyMtdNm": "방문", "lastModYmd": "20261117",
                           "servDtlLink": "https://www.bokjiro.go.kr/"}.items():
            ET.SubElement(item, key).text = value
    return root


def detail_xml(sid="L1", source="local"):
    root = ET.Element("wantedDtl")
    values = {"resultCode": "0", "servId": sid, "servNm": f"복지 {sid}", "slctCritCn": "중위소득 100% 이하",
              "alwServCn": "월 10만원", "sprtTrgtCn": "19세 이상", "aplyMtdCn": "주민센터 방문 신청",
              "tgtrDtlCn": "19세 이상", "wlfareInfoOutlCn": "공식 상세 요약"}
    for key, value in values.items():
        ET.SubElement(root, key).text = value
    form = ET.SubElement(root, "basfrmList")
    ET.SubElement(form, "wlfareInfoReldNm" if source == "local" else "servSeDetailNm").text = "신청서"
    ET.SubElement(form, "wlfareInfoReldCn" if source == "local" else "servSeDetailLink").text = "https://www.bokjiro.go.kr/form.hwp"
    return root


class TestLiveCatalogApp(unittest.TestCase):
    def setUp(self):
        import streamlit as st
        st.cache_resource.clear()
        st.cache_data.clear()
        self.addCleanup(st.cache_resource.clear)
        self.addCleanup(st.cache_data.clear)
        self.items = [parse_list_item(e, "central") for e in listing([f"C{i}" for i in range(73)]).iter("servList")]
        patched = patch("welfare_api.WelfareAPI")
        self.api = patched.start().return_value
        self.addCleanup(patched.stop)
        self.api.catalog.return_value = {"services": self.items, "counts": {"central": 73, "local": 0},
                                         "updated_at": time.time(), "warnings": []}
        self.api.services.return_value = self.items

    def test_daily_sync_status_is_displayed_and_polled(self):
        from streamlit.testing.v1 import AppTest
        state = {"revision": 1, "running": True, "sources": {
            "central": {"ready": 10, "total": 73, "status": "collecting"},
            "local": {"ready": 0, "total": 0, "status": "listing"}}}
        self.api.catalog.return_value["sync"] = state
        self.api.refresh_status.return_value = state
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(any("10/73건" in c.value for c in at.caption))
        self.api.refresh_status.assert_called_once()

    def test_first_collection_shows_pending_state_without_sample_data(self):
        from streamlit.testing.v1 import AppTest
        state = {"revision": 0, "running": True, "sources": {
            name: {"ready": 0, "total": 0, "status": "listing"} for name in ("central", "local")}}
        self.api.catalog.return_value.update(services=[], counts={"central": 0, "local": 0}, sync=state)
        self.api.services.return_value = []
        self.api.refresh_status.return_value = state
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        self.assertFalse(at.error, at.error)
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(any("자동 수집" in i.value for i in at.info))

    def test_pending_catalog_home_has_actual_count_without_detail_calls(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        self.assertFalse(at.exception, at.exception)
        self.assertFalse(at.error, at.error)
        self.assertTrue(any("73" in m.value and "공식" in m.value for m in at.markdown))
        self.api.detail.assert_not_called()

    def test_api_failure_shows_error_instead_of_sample_home(self):
        from streamlit.testing.v1 import AppTest
        self.api.catalog.side_effect = APIError("인증 확인 필요")
        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        self.assertTrue(any("인증 확인 필요" in e.value for e in at.error))
        self.assertFalse(any("질문 몇 개면" in m.value for m in at.markdown))

    def test_browse_paginates_and_unknown_scores_do_not_crash(self):
        from streamlit.testing.v1 import AppTest
        script = f"import runpy\napp = runpy.run_path({str(ROOT / 'app.py')!r})\napp['browse']()"
        at = AppTest.from_string(script, default_timeout=30).run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(sum(b.label == "신청 방법 보기" for b in at.button), 24)
        at.number_input(key="all_page").set_value(4).run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(sum(b.label == "신청 방법 보기" for b in at.button), 1)
        self.api.detail.assert_not_called()

    def test_analysis_without_details_explains_scope(self):
        from streamlit.testing.v1 import AppTest
        script = f"import runpy\napp = runpy.run_path({str(ROOT / 'app.py')!r})\napp['analysis']()"
        at = AppTest.from_string(script, default_timeout=30).run()
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(any("자동 수집" in i.value for i in at.info))

    def test_detail_click_loads_real_fields_and_calculates_score(self):
        from streamlit.testing.v1 import AppTest
        svc = merge_detail(self.items[0], detail_xml("C0", "central"))
        svc["detail_fetched_at"] = time.time()
        self.api.detail.return_value = svc
        script = f"import runpy\napp = runpy.run_path({str(ROOT / 'app.py')!r})\napp['browse']()"
        at = AppTest.from_string(script, default_timeout=30).run()
        at.button(key="all_C0").click().run()
        self.assertFalse(at.exception, at.exception)
        self.assertFalse(at.error, at.error)
        self.api.detail.assert_called_once()
        self.assertTrue(any("19세 이상" in t.value for t in at.text))
        self.assertTrue(any("문턱" in m.value and "점</span>" in m.value for m in at.markdown))

    def test_recommendation_can_include_pending_and_scored_services(self):
        from streamlit.testing.v1 import AppTest
        svc = merge_detail(self.items[0], detail_xml("C0", "central"))
        self.api.services.return_value = [svc, *self.items[1:]]
        script = f"import runpy\napp = runpy.run_path({str(ROOT / 'app.py')!r})\napp['report'](app['intake'].new_profile())"
        at = AppTest.from_string(script, default_timeout=30).run()
        self.assertFalse(at.exception, at.exception)
        self.assertTrue(any("상세를 확인한 1건" in i.value for i in at.info))

    def test_analysis_with_one_detail_uses_only_that_service(self):
        from streamlit.testing.v1 import AppTest
        svc = merge_detail(self.items[0], detail_xml("C0", "central"))
        self.api.services.return_value = [svc, *self.items[1:]]
        script = f"import runpy\napp = runpy.run_path({str(ROOT / 'app.py')!r})\napp['analysis']()"
        at = AppTest.from_string(script, default_timeout=30).run()
        self.assertFalse(at.exception, at.exception)
        self.assertEqual(at.metric[0].value, "1건")
        self.assertTrue(any("전체 복지서비스의 통계가 아닙니다" in c.value for c in at.caption))


if __name__ == "__main__":
    unittest.main()
