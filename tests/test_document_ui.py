"""사용자가 고친 정보 대신 이전 서류를 받지 않도록 하는 화면 흐름 테스트."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from streamlit.testing.v1 import AppTest
import document_ui


ROW = {"svc": {"servId": "UI_TEST", "servNm": "신청서 확인", "form_files": [
    {"name": "신청서.pdf", "url": "https://www.bokjiro.go.kr/form.pdf"},
    {"name": "위임장.pdf", "url": "https://www.bokjiro.go.kr/proxy.pdf"},
]}}


def prepared(raw, name, profile, **_):
    filled = bool(profile.get("applicant.name"))
    return {"data": b"filled:" + profile["applicant.name"].encode() if filled else raw,
            "filename": ("작성됨_" if filled else "") + name, "format": "pdf",
            "status": "filled" if filled else "manual", "filled_count": int(filled),
            "filled_labels": ["성명"] if filled else [], "manual_labels": [], "warnings": [],
            "template_status": "new", "fingerprint": "source", "preview_allowed": True,
            "analysis": {"fillable_candidate": True}}


class TestDocumentUI(unittest.TestCase):
    def setUp(self):
        self.fetch = patch.object(document_ui.packet, "download", return_value=b"original").start()
        self.prepare = patch.object(document_ui.workspace, "prepare_document", side_effect=prepared).start()
        self.viewer = patch.object(document_ui.document_viewer, "render_document", return_value={"status": "ready"}).start()
        patch.dict("os.environ", {"LLM_API_KEY": "", "OPENAI_API_KEY": "", "JUSO_API_KEY": ""}).start()
        self.addCleanup(patch.stopall)

    def app(self, row=ROW):
        code = f"""import sys
sys.path.insert(0, {str(ROOT / 'src')!r})
import streamlit as st
import document_ui
document_ui.render_document_workspace({row!r}, st.session_state.get('me'))
"""
        at = AppTest.from_string(code, default_timeout=20).run()
        self.assertFalse(at.exception)
        return at

    def open_and_fill(self):
        at = self.app()
        at.button(key="dw_open_UI_TEST").click().run()
        at.text_input(key="dw_name_UI_TEST").input("가나다").run()
        at.button(key="dw_fill_UI_TEST").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["document_workspace_UI_TEST"]["prepared"]["data"], "filled:가나다".encode())
        return at

    def test_blank_original_is_not_previewed_before_filling(self):
        at = self.app()
        at.button(key="dw_open_UI_TEST").click().run()
        self.assertEqual(self.viewer.call_args.args[0], b"original")  # 열자마자 서류 내용은 보인다
        self.assertTrue(any("아직 내 정보가 들어가지 않은 서류" in x.value for x in at.info))

    def test_open_fills_immediately_when_info_exists(self):
        at = self.app()
        at.session_state["me"] = {"name": "상담이름"}
        at.run()
        at.button(key="dw_open_UI_TEST").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state["document_workspace_UI_TEST"]["prepared"]["data"], "filled:상담이름".encode())
        self.assertEqual(self.viewer.call_args.args[0], "filled:상담이름".encode())

    def test_download_only_after_explicit_open(self):
        self.app()
        self.fetch.assert_not_called()
        self.viewer.assert_not_called()

    def test_modified_name_invalidates_old_document_then_regenerates(self):
        at = self.open_and_fill()
        views = self.viewer.call_count
        at.text_input(key="dw_name_UI_TEST").input("새이름").run()
        self.assertNotIn("prepared", at.session_state["document_workspace_UI_TEST"])
        self.assertEqual(self.viewer.call_args.args[0], b"original")  # 이전 서류 대신 원본을 보여 준다
        self.assertTrue(any("정보가 바뀌었습니다" in x.value for x in at.info))
        at.button(key="dw_fill_UI_TEST").click().run()
        self.assertEqual(self.viewer.call_args.args[0], "filled:새이름".encode())
        self.fetch.assert_called_once()

    def test_erasing_input_cannot_download_old_filled_document(self):
        at = self.open_and_fill()
        at.text_input(key="dw_name_UI_TEST").input("").run()
        self.assertNotIn("prepared", at.session_state["document_workspace_UI_TEST"])
        self.assertFalse(at.button(key="dw_fill_UI_TEST").disabled)  # 버튼은 꺼 두지 않는다
        at.button(key="dw_fill_UI_TEST").click().run()
        self.assertTrue(any("먼저 입력해 주세요" in w.value for w in at.warning))
        self.assertNotIn("prepared", at.session_state["document_workspace_UI_TEST"])

    def test_switching_document_clears_previous_preview_without_fetch(self):
        at = self.open_and_fill()
        docs = document_ui.workspace.list_documents(ROW["svc"])
        at.selectbox(key="dw_choice_UI_TEST").select(docs[1]["id"]).run()
        self.assertNotIn("original", at.session_state["document_workspace_UI_TEST"])
        self.assertNotIn("prepared", at.session_state["document_workspace_UI_TEST"])
        self.fetch.assert_called_once()

    def test_changed_consultation_profile_clears_previous_result(self):
        at = self.open_and_fill()
        at.session_state["me"] = {"name": "상담이름", "sido": "부산광역시"}
        at.run()
        self.assertNotIn("prepared", at.session_state["document_workspace_UI_TEST"])
        at.button(key="dw_open_UI_TEST").click().run()
        self.assertEqual(at.text_input(key="dw_name_UI_TEST").value, "상담이름")

    def test_failed_reopen_does_not_reuse_previous_output(self):
        at = self.open_and_fill()
        self.fetch.side_effect = RuntimeError("DO_NOT_DISPLAY_PRIVATE_DETAIL")
        at.button(key="dw_open_UI_TEST").click().run()
        self.assertNotIn("original", at.session_state["document_workspace_UI_TEST"])
        self.assertNotIn("prepared", at.session_state["document_workspace_UI_TEST"])
        self.assertTrue(at.error)
        self.assertNotIn("DO_NOT_DISPLAY", str(at.error))
        self.assertFalse(at.exception)

    def test_dead_duplicate_attachment_falls_back_to_same_name(self):
        row = {"svc": {"servId": "DUP", "servNm": "중복", "form_files": [
            {"name": "서식모음.pdf", "url": "https://www.bokjiro.go.kr/dead"},
            {"name": "서식모음.pdf", "url": "https://www.bokjiro.go.kr/alive"}]}}
        self.fetch.side_effect = lambda url: (_ for _ in ()).throw(FileNotFoundError()) if url.endswith("dead") else b"alive"
        at = self.app(row)
        at.button(key="dw_open_DUP").click().run()
        self.assertFalse(at.error)
        self.assertEqual(at.session_state["document_workspace_DUP"]["original"], b"alive")

    def test_drm_file_shows_guide_without_useless_download(self):
        self.prepare.side_effect = lambda raw, name, profile, **_: {
            "data": raw, "filename": name, "format": "unsupported", "status": "drm", "filled_count": 0,
            "filled_labels": [], "manual_labels": [], "warnings": ["기관 보안 프로그램(DRM)으로 잠긴 파일"],
            "template_status": "unsupported", "fingerprint": "x", "preview_allowed": False,
            "analysis": {"fillable_candidate": False}}
        at = self.app()
        at.button(key="dw_open_UI_TEST").click().run()
        self.assertFalse(at.exception)
        self.assertTrue(any("DRM" in w.value for w in at.warning))
        self.assertTrue(any("신청서는 이렇게 받으세요" in m.value for m in at.markdown))
        self.assertFalse([b for b in at.get("download_button")])
        self.viewer.assert_not_called()

    def test_manuals_are_reference_not_documents(self):
        row = {"svc": {"servId": "REF", "servNm": "아동수당", "form_files": [
            {"name": "아동수당서식모음.pdf", "url": "https://www.bokjiro.go.kr/f"},
            {"name": "2025년 아동수당 사업안내.pdf", "url": "https://www.bokjiro.go.kr/g"},
            {"name": "운영지침_지자체 배포.pdf", "url": "https://www.bokjiro.go.kr/h"}]}}
        at = self.app(row)
        names = at.selectbox(key="dw_choice_REF").options
        self.assertEqual(names, ["아동수당서식모음.pdf"])
        self.assertTrue(any("참고 자료 2개" in e.label for e in at.expander))
        self.assertFalse([b for b in at.button if b.key and b.key.startswith("dw_refview")])  # 참고 자료는 내려받기만
        self.assertEqual(len(at.get("link_button")), 2)
        at.button(key="dw_open_REF").click().run()
        self.assertEqual(self.fetch.call_args.args[0], "https://www.bokjiro.go.kr/f")

    def test_only_references_shows_guide_and_list(self):
        row = {"svc": {"servId": "ONLYREF", "servNm": "x", "form_files": [
            {"name": "2026년 사업 공고문.hwp", "url": "https://www.bokjiro.go.kr/a"}]}, "ext": {"documents": []}, "ai": None}
        at = self.app(row)
        self.assertFalse(at.exception)
        self.assertFalse(at.selectbox)
        self.assertTrue(any("참고 자료만 올라와" in m.value for m in at.markdown))
        self.fetch.assert_not_called()

    def test_chat_info_prefills_document_inputs(self):
        at = self.app()
        at.session_state["me"] = {"name": "상담이름", "sido": "경기도", "sgg": "성남시", "phone": "010-1234-5678",
                                  "birth": "1950-01-01", "address": "경기도 성남시 분당구 불정로 6"}
        at.run()
        at.button(key="dw_open_UI_TEST").click().run()
        self.assertEqual(at.text_input(key="dw_name_UI_TEST").value, "상담이름")
        self.assertEqual(at.text_input(key="dw_phone_UI_TEST").value, "010-1234-5678")
        self.assertEqual(at.text_input(key="dw_birth_UI_TEST").value, "1950-01-01")
        self.assertEqual(at.text_input(key="dw_address_UI_TEST").value, "경기도 성남시 분당구 불정로 6")
        self.assertFalse(at.exception)

    def test_explicit_clear_discards_input_and_output(self):
        at = self.open_and_fill()
        at.button(key="dw_clear_UI_TEST").click().run()
        self.assertFalse(at.exception)
        self.assertNotIn("prepared", at.session_state["document_workspace_UI_TEST"])
        self.assertNotIn("dw_name_UI_TEST", at.session_state)

    def opened(self):
        at = self.app()
        at.button(key="dw_open_UI_TEST").click().run()
        return at

    def test_phone_and_birth_feedback(self):
        at = self.opened()
        at.text_input(key="dw_phone_UI_TEST").input("01012345678").run()
        self.assertTrue(any("010-1234-5678" in c.value for c in at.caption))
        at.text_input(key="dw_phone_UI_TEST").input("0101").run()
        self.assertTrue(any("자릿수" in w.value for w in at.warning))
        at.text_input(key="dw_birth_UI_TEST").input("19501301").run()
        self.assertTrue(any("생년월일" in w.value for w in at.warning))
        at.text_input(key="dw_birth_UI_TEST").input("500101").run()
        self.assertTrue(any("1950년 1월 1일로 읽었습니다" in c.value for c in at.caption))
        self.assertFalse(at.exception)

    def test_address_check_needs_click_and_applies_choice(self):
        result = {"original": "서울 강남구 테헤란로1", "suggestion": "서울특별시 강남구 테헤란로 1", "detail": "",
                  "candidates": [{"road": "서울특별시 강남구 테헤란로 1", "full": "서울특별시 강남구 테헤란로 1"},
                                 {"road": "서울특별시 강남구 테헤란로 1-2", "full": "서울특별시 강남구 테헤란로 1-2"}],
                  "ai_used": True, "juso_used": True, "verified": True, "notes": []}
        with patch.object(document_ui.address_check, "check", return_value=result) as check:
            at = self.opened()
            at.text_input(key="dw_name_UI_TEST").input("가나다").run()
            at.text_input(key="dw_address_UI_TEST").input("서울 강남구 테헤란로1").run()
            check.assert_not_called()
            at.button(key="dw_addrbtn_UI_TEST").click().run()
            check.assert_called_once_with("서울 강남구 테헤란로1")
            at.radio(key="dw_addrpick_UI_TEST").set_value("서울특별시 강남구 테헤란로 1-2").run()
            at.button(key="dw_addrapply_UI_TEST").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(at.text_input(key="dw_address_UI_TEST").value, "서울특별시 강남구 테헤란로 1-2")
        self.assertNotIn("dw_addrcheck_UI_TEST", at.session_state)
        at.button(key="dw_fill_UI_TEST").click().run()
        self.assertEqual(self.prepare.call_args.args[2]["applicant.address"], "서울특별시 강남구 테헤란로 1-2")

    def test_buttons_do_not_rerun_whole_app_inside_dialog(self):
        """서류 화면은 신청 안내 대화상자 안에 있다. st.rerun()은 대화상자를 닫는다."""
        result = {"original": "서울 강남구 테헤란로1", "suggestion": "서울특별시 강남구 테헤란로 1", "detail": "",
                  "candidates": [], "ai_used": False, "juso_used": False, "verified": False, "notes": []}
        with patch.object(document_ui.address_check, "check", return_value=result), \
                patch.object(document_ui.st, "rerun", side_effect=AssertionError("dialog would close")):
            at = self.opened()
            at.text_input(key="dw_address_UI_TEST").input("서울 강남구 테헤란로1").run()
            at.button(key="dw_addrbtn_UI_TEST").click().run()
            at.button(key="dw_addrapply_UI_TEST").click().run()
            self.assertFalse(at.exception)
            self.assertEqual(at.text_input(key="dw_address_UI_TEST").value, "서울특별시 강남구 테헤란로 1")
            at.button(key="dw_clear_UI_TEST").click().run()
            self.assertFalse(at.exception)

    def test_editing_address_after_check_discards_old_suggestion(self):
        result = {"original": "서울 강남구 테헤란로1", "suggestion": "서울특별시 강남구 테헤란로 1", "detail": "",
                  "candidates": [], "ai_used": False, "juso_used": False, "verified": False, "notes": ["안내"]}
        with patch.object(document_ui.address_check, "check", return_value=result):
            at = self.opened()
            at.text_input(key="dw_address_UI_TEST").input("서울 강남구 테헤란로1").run()
            at.button(key="dw_addrbtn_UI_TEST").click().run()
            self.assertTrue(at.radio)
            at.text_input(key="dw_address_UI_TEST").input("부산 해운대구").run()
        self.assertFalse(at.radio)
        self.assertNotIn("dw_addrcheck_UI_TEST", at.session_state)

    def test_browser_search_when_server_cannot_reach_juso(self):
        """해외 서버에서 도로명주소 API가 막히면 브라우저 응답을 기다렸다가 같은 화면으로 보여 준다."""
        result = {"original": "서울 강남구 테헤란로123", "suggestion": "서울특별시 강남구 테헤란로 123", "detail": "",
                  "candidates": [], "ai_used": False, "juso_used": False, "verified": False,
                  "notes": [document_ui.address_check.NOTE_NO_JUSO],
                  "browser_search": [{"keyword": "서울특별시 강남구 테헤란로 123", "detail": ""}]}
        request = {"url": "https://example.test", "key": "k", "count": 10, "keywords": ["서울특별시 강남구 테헤란로 123"]}
        body = {"results": {"common": {"errorCode": "0"}, "juso": [
            {"roadAddr": "서울특별시 강남구 테헤란로 123 (역삼동)", "roadAddrPart1": "서울특별시 강남구 테헤란로 123",
             "rn": "테헤란로", "buldMnnm": "123", "buldSlno": "0", "zipNo": "06133", "jibunAddr": "역삼동 648-23"}]}}
        reply = {}
        with patch.object(document_ui.address_check, "check", return_value=result), \
                patch.object(document_ui.address_check, "browser_request", return_value=request), \
                patch.object(document_ui, "_juso_browser", side_effect=lambda **kw: reply) as browser:
            at = self.opened()
            at.text_input(key="dw_address_UI_TEST").input("서울 강남구 테헤란로123").run()
            at.button(key="dw_addrbtn_UI_TEST").click().run()
            self.assertFalse(at.exception)
            self.assertEqual(browser.call_args.kwargs["data"], request)
            self.assertTrue(any("찾고 있어요" in c.value for c in at.caption))
            self.assertFalse(at.radio)
            reply["reply"] = {"keyword": "서울특별시 강남구 테헤란로 123", "data": body, "error": ""}
            at.run()
            self.assertFalse(at.exception)
            self.assertTrue(any("도로명주소에서 확인한 주소입니다" in s.value for s in at.success))
            self.assertNotIn("browser_search", at.session_state["dw_addrcheck_UI_TEST"])
            calls = browser.call_count
            at.run()
            self.assertEqual(browser.call_count, calls)  # 응답을 받은 뒤에는 다시 찾지 않는다.
            at.button(key="dw_addrapply_UI_TEST").click().run()
        self.assertEqual(at.text_input(key="dw_address_UI_TEST").value, "서울특별시 강남구 테헤란로 123")

    def test_address_check_failure_does_not_break_page(self):
        with patch.object(document_ui.address_check, "check", side_effect=RuntimeError("PRIVATE")):
            at = self.opened()
            at.text_input(key="dw_address_UI_TEST").input("서울 강남구").run()
            at.button(key="dw_addrbtn_UI_TEST").click().run()
        self.assertFalse(at.exception)
        self.assertNotIn("PRIVATE", str(at.error))
        self.assertEqual(at.text_input(key="dw_address_UI_TEST").value, "서울 강남구")

    def test_address_starts_from_consultation_region_and_resets_with_it(self):
        at = self.app()
        at.session_state["me"] = {"name": "", "sido": "부산광역시", "sgg": "해운대구"}
        at.run()
        at.button(key="dw_open_UI_TEST").click().run()
        self.assertEqual(at.text_input(key="dw_address_UI_TEST").value, "부산광역시 해운대구")
        at.session_state["me"] = {"name": "", "sido": "대구광역시", "sgg": "중구"}
        at.run()
        at.button(key="dw_open_UI_TEST").click().run()
        self.assertEqual(at.text_input(key="dw_address_UI_TEST").value, "대구광역시 중구")

    def test_no_attachments_explains_how_to_get_the_form(self):
        row = {"svc": {"servId": "EMPTY", "servNm": "어르신 목욕비", "org": "가상군청", "contact": "061-000-0000",
                       "link": "https://www.bokjiro.go.kr/x", "form_files": []}}
        at = self.app(row)
        text = " ".join(m.value for m in at.markdown)
        self.assertIn("신청서는 이렇게 받으세요", text)
        self.assertIn("가상군청", text)
        self.assertIn("061-000-0000", text)
        self.assertTrue(any("함께 준비할 서류와 떼는 곳" in c.value for c in at.caption))
        self.assertFalse(at.button)

    def needed(self, row):
        code = f"""import sys
sys.path.insert(0, {str(ROOT / 'src')!r})
import document_ui
document_ui.render_needed_documents({row!r})
"""
        document_ui.cached_guide.clear()
        return AppTest.from_string(code, default_timeout=20)

    def test_official_document_list_with_where(self):
        row = {"svc": {"servId": "LIST", "servNm": "x"}, "ext": {"documents": ["주민등록등본", "통장 사본"]}, "ai": None}
        with patch.object(document_ui.doc_assist_ai, "easy_guide", side_effect=AssertionError("no AI when listed")):
            at = self.needed(row).run()
        self.assertFalse(at.exception)
        self.assertEqual([c.label for c in at.checkbox], ["주민등록등본", "통장 사본"])
        self.assertTrue(any("정부24" in c.value for c in at.caption))
        self.assertTrue(at.get("link_button"))

    def test_missing_list_ai_finds_documents_automatically(self):
        row = {"svc": {"servId": "NOLIST", "servNm": "어르신 목욕비", "target": "만 80세 이상"}, "ext": {"documents": []}, "ai": None}
        guide = {"steps": ["주민센터에 가세요."], "tip": "가족에게 부탁하셔도 돼요.", "documents": [
            {"name": "신분증", "where": "본인이 가진 주민등록증", "official": False, "online": False},
            {"name": "주민등록등본", "where": "정부24 누리집이나 주민센터 무인민원발급기", "official": False, "online": True}]}
        with patch.object(document_ui.doc_assist_ai, "available", return_value=True), \
                patch.object(document_ui.doc_assist_ai, "easy_guide", return_value=guide) as easy:
            at = self.needed(row).run()
            at.run()
        self.assertFalse(at.exception)
        self.assertEqual(easy.call_count, 1)  # 버튼 없이 자동으로, 같은 복지는 한 번만
        self.assertEqual(at.checkbox[1].label, "주민등록등본  (보통 필요 · 확인 필요)")
        self.assertTrue(any("보통 필요한 서류를 정리" in i.value for i in at.info))
        self.assertTrue(any("가족에게 부탁" in i.value for i in at.info))
        self.assertTrue(any("정부24" in b.label for b in at.get("link_button")))
        self.assertTrue(any(e.label.startswith("원문 보기") for e in at.expander))
        self.assertFalse(at.button)

    def test_missing_list_ai_failure_falls_back_to_basic(self):
        row = {"svc": {"servId": "FAIL", "servNm": "x"}, "ext": {"documents": []}, "ai": None}
        with patch.object(document_ui.doc_assist_ai, "available", return_value=True), \
                patch.object(document_ui.doc_assist_ai, "easy_guide", return_value=None):
            at = self.needed(row).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("기본 서류" in i.value for i in at.info))
        self.assertEqual(len(at.checkbox), 3)

if __name__ == "__main__":
    unittest.main()
