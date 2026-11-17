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
from welfare_api import APIError, DETAIL_TTL, LIST_TTL, STALE_TTL, WelfareAPI


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


class TestWelfareAPI(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.api = WelfareAPI(key="test-key", cache_dir=temp.name)
        self.local = parse_list_item(next(listing(["L1"]).iter("servList")), "local")

    def test_all_pages_both_sources_and_cache(self):
        def request(source, **params):
            if source == "central":
                ids = [f"C{i}" for i in range(500)] if params["pageNo"] == 1 else ["C500"]
                return listing(ids, total=501, page=params["pageNo"])
            return listing(["L1", "L2"])
        with patch.object(self.api, "_request", side_effect=request) as req:
            catalog = self.api.catalog()
            again = self.api.catalog()
        self.assertEqual(catalog["counts"], {"central": 501, "local": 2})
        self.assertEqual(len(catalog["services"]), 503)
        self.assertEqual(req.call_count, 3)
        self.assertEqual(catalog, again)
        self.assertTrue(all(not s["detail_loaded"] for s in catalog["services"]))
        self.assertEqual(catalog["services"][-1]["life"], ["청년", "중장년"])

    def test_missing_page_is_not_published_as_complete(self):
        with patch.object(self.api, "_request", return_value=listing(["C1"], total=2)) as req:
            with self.assertRaises(APIError):
                self.api.catalog()
        self.assertEqual(req.call_count, 2)
        self.assertFalse((self.api.cache_dir / "catalog-central.json").exists())

    def test_catalog_failure_does_not_use_samples(self):
        with patch.object(self.api, "_request", side_effect=APIError("점검 중")):
            with self.assertRaisesRegex(APIError, "점검 중"):
                self.api.catalog()

    def test_last_successful_catalog_is_labelled_when_refresh_fails(self):
        now = time.time()
        with patch("welfare_api.time.time", return_value=now - LIST_TTL - 10):
            self.api._write("catalog-central", [])
            self.api._write("catalog-local", [self.local])
        with patch.object(self.api, "_request", side_effect=APIError("점검 중")):
            catalog = self.api.catalog()
        self.assertEqual(len(catalog["warnings"]), 2)
        self.assertEqual(catalog["services"], [self.local])
        self.assertLess(catalog["updated_at"], now - LIST_TTL)
        with patch("welfare_api.time.time", return_value=now + STALE_TTL):
            with patch.object(self.api, "_request", side_effect=APIError("점검 중")):
                with self.assertRaises(APIError):
                    self.api.catalog()

    def test_detail_keeps_metadata_and_is_cached(self):
        with patch.object(self.api, "_request", return_value=detail_xml()) as req:
            svc = self.api.detail(self.local)
            again = self.api.detail(self.local)
        self.assertEqual(req.call_count, 1)
        self.assertEqual(svc, again)
        self.assertTrue(svc["detail_loaded"])
        self.assertEqual(svc["target"], "19세 이상")
        self.assertEqual(svc["region"], "경기도 성남시")
        self.assertEqual(svc["apply_method_name"], "방문")
        self.assertEqual(svc["summary"], "공식 요약")
        self.assertEqual(svc["forms"], ["신청서"])
        self.assertEqual(svc["life"], self.local["life"])
        self.assertIsInstance(build(services=[svc])[0]["score"], int)

    def test_central_detail_keeps_online_flag(self):
        item = parse_list_item(next(listing(["C1"]).iter("servList")), "central")
        with patch.object(self.api, "_request", return_value=detail_xml("C1", "central")):
            svc = self.api.detail(item)
        self.assertTrue(svc["online_flag"])
        self.assertEqual(svc["region"], "전국")
        self.assertEqual(svc["forms"], ["신청서"])
        self.assertTrue(build(services=[svc])[0]["ext"]["online"])

    def test_wrong_detail_id_is_rejected(self):
        with patch.object(self.api, "_request", return_value=detail_xml("wrong")):
            with self.assertRaises(APIError):
                self.api.detail(self.local)
        self.assertIsNone(self.api.cached_detail(self.local))

    def test_changed_service_invalidates_detail_cache(self):
        with patch.object(self.api, "_request", return_value=detail_xml()) as req:
            self.api.detail(self.local)
            self.api.detail({**self.local, "last_modified": "20261118"})
        self.assertEqual(req.call_count, 2)

    def test_stale_detail_is_labelled_not_silently_reused(self):
        with patch("welfare_api.time.time", return_value=time.time() - DETAIL_TTL - 10):
            with patch.object(self.api, "_request", return_value=detail_xml()):
                self.api.detail(self.local)
        self.assertIsNone(self.api.cached_detail(self.local))
        with patch.object(self.api, "_request", side_effect=APIError("점검 중")):
            svc = self.api.detail(self.local)
        self.assertIn("마지막 조회", svc["detail_warning"])

    def test_encoded_and_decoded_keys_are_encoded_once(self):
        for key in ("a+b/==", "a%2Bb%2F%3D%3D"):
            self.api.key = urllib.parse.unquote(key)
            with patch("welfare_api.urllib.request.urlopen", return_value=io.BytesIO(ET.tostring(listing([])))) as req:
                self.api._request("central", pageNo=1, numOfRows=500)
            parsed = urllib.parse.parse_qs(urllib.parse.urlsplit(req.call_args.args[0]).query)
            self.assertEqual(parsed["serviceKey"], ["a+b/=="])
            self.assertEqual(parsed["callTp"], ["L"])

    def test_missing_key_has_no_network_call(self):
        self.api.key = ""
        with patch("welfare_api.urllib.request.urlopen") as req:
            with self.assertRaisesRegex(APIError, "DATA_GO_KR_KEY"):
                self.api.catalog()
            req.assert_not_called()

    def test_portal_error_is_redacted_and_not_cached(self):
        error = b"<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>test-key</errMsg><returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
        with patch("welfare_api.urllib.request.urlopen", return_value=io.BytesIO(error)):
            with self.assertRaises(APIError) as raised:
                self.api.catalog()
        self.assertNotIn("test-key", str(raised.exception))
        self.assertIn("22", str(raised.exception))
        self.assertFalse((self.api.cache_dir / "catalog-central.json").exists())

    def test_rate_limit_does_not_retry_or_expose_url(self):
        error = urllib.error.HTTPError("https://example.invalid/?serviceKey=test-key", 429, "limited", {}, None)
        with patch("welfare_api.urllib.request.urlopen", side_effect=error) as req:
            for _ in range(2):
                try:
                    self.api._request("local", servId="L1", detail=True)
                except APIError:
                    self.assertNotIn("test-key", traceback.format_exc())
                else:
                    self.fail("Expected a safe APIError")
        self.assertEqual(req.call_count, 1)

    def test_transient_network_error_retries(self):
        with patch("welfare_api.urllib.request.urlopen", side_effect=[TimeoutError(), io.BytesIO(ET.tostring(listing([])))]) as req:
            with patch("welfare_api.time.sleep"):
                self.api._request("local", pageNo=1, numOfRows=500)
        self.assertEqual(req.call_count, 2)

    def test_malformed_xml_is_safe_error(self):
        with patch("welfare_api.urllib.request.urlopen", return_value=io.BytesIO(b"<broken")):
            with self.assertRaises(APIError):
                self.api._request("local")

    def test_pending_rows_are_not_scored_and_empty_list_stays_empty(self):
        with patch("pipeline.rules.extract") as extract:
            rows = build(services=[self.local])
            self.assertEqual(build(services=[]), [])
        extract.assert_not_called()
        self.assertIsNone(rows[0]["score"])
        self.assertIsNone(rows[0]["ext"])
        ranked = match.recommend([rows[0], {**rows[0], "score": 20}], sido="경기도", life=["청년"])
        self.assertEqual([r["score"] for r, _ in ranked], [20, None])

    def test_old_ai_is_only_reused_for_same_source(self):
        svc = next(s for s in load_all(ROOT / "data" / "sample") if (llm.CACHE / f"{s['servId']}.json").exists())
        self.assertIsNotNone(llm.load_cached(svc["servId"], svc))
        self.assertIsNone(llm.load_cached(svc["servId"], {**svc, "criteria": svc["criteria"] + " 변경"}))


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
        self.assertTrue(any("불러오지 못했어요" in e.value for e in at.error))
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
        self.assertTrue(any("신청 방법 보기" in i.value for i in at.info))

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
