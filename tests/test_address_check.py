"""주소 확인: 띄어쓰기 정리, AI 검수, 도로명주소 검색. 외부 호출은 모두 가짜로 대신한다."""
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import address_check as ac


def juso_reply(rows, code="0"):
    def item(r):
        m = ac.re.search(r"(\S+(?:로|길))\s+(\d+)(?:-(\d+))?$", r)
        return {"roadAddr": r + " (동)", "roadAddrPart1": r, "jibunAddr": "지번 " + r, "zipNo": "06234",
                "rn": m.group(1) if m else "", "buldMnnm": m.group(2) if m else "", "buldSlno": (m.group(3) or "0") if m else "0"}
    body = {"results": {"common": {"errorCode": code, "totalCount": str(len(rows))}, "juso": [item(r) for r in rows]}}

    def opener(url, timeout=0):
        opener.urls.append(url)
        return io.BytesIO(json.dumps(body).encode())
    opener.urls = []
    return opener


NO_KEYS = {"LLM_API_KEY": "", "OPENAI_API_KEY": "", "JUSO_API_KEY": ""}


class Tidy(unittest.TestCase):
    def test_spacing(self):
        cases = {
            "서울특별시강남구테헤란로123 101동202호": "서울특별시 강남구 테헤란로 123, 101동 202호",
            "경기 성남시분당구 불정로6": "경기도 성남시 분당구 불정로 6",
            "서울시 강남구 테헤란로 123": "서울특별시 강남구 테헤란로 123",
            "부산시해운대구센텀중앙로 79": "부산광역시 해운대구 센텀중앙로 79",
            "경상북도의성군의성읍중앙길12번길3, 2층": "경상북도 의성군 의성읍 중앙길12번길 3, 2층",
            "충남 천안시동남구 목천읍교촌리 123번지": "충청남도 천안시 동남구 목천읍 교촌리 123번지",
            "서울 강서구 마곡중앙로161-8": "서울특별시 강서구 마곡중앙로 161-8",
            "  서울특별시   종로구  세종대로 209 ": "서울특별시 종로구 세종대로 209",
        }
        for raw, want in cases.items():
            self.assertEqual(ac.tidy(raw), want, raw)

    def test_road_and_district_names_starting_with_region_names_are_kept(self):
        cases = {
            "세종대로 209": "세종대로 209", "대구로 1": "대구로 1", "인천대로 1": "인천대로 1", "경기대로 1": "경기대로 1",
            "제주대학로 102": "제주대학로 102", "서울시립대로 163": "서울시립대로 163",
            "부산진구 중앙대로 1": "부산진구 중앙대로 1", "부산부산진구중앙대로1": "부산광역시 부산진구 중앙대로 1",
            "대구중구공평로88": "대구광역시 중구 공평로 88", "경북예천군예천읍충효로 102": "경상북도 예천군 예천읍 충효로 102",
            "경기도시흥시시청로20": "경기도 시흥시 시청로 20", "서울종로구종로1": "서울특별시 종로구 종로 1",
            "대구광역시중구공평로88": "대구광역시 중구 공평로 88",
        }
        for raw, want in cases.items():
            self.assertEqual(ac.tidy(raw), want, raw)

    def test_city_names_that_are_not_provinces_are_kept(self):
        self.assertEqual(ac.tidy("제주시 연동 312-1"), "제주시 연동 312-1")
        self.assertEqual(ac.tidy("광주시 오포읍 문형리 1"), "광주시 오포읍 문형리 1")

    def test_never_drops_or_adds_digits(self):
        for raw in ("서울특별시강남구테헤란로123 101동202호", "경기 성남시분당구 불정로6", "아무 글 12 34"):
            self.assertEqual(sorted(ac.re.findall(r"\d+", raw)), sorted(ac.re.findall(r"\d+", ac.tidy(raw))))

    def test_empty_and_long_input(self):
        self.assertEqual(ac.tidy(""), "")
        self.assertEqual(ac.tidy(None), "")
        self.assertLessEqual(len(ac.tidy("가" * 1000)), ac.MAX_LEN)

    def test_split_detail(self):
        self.assertEqual(ac.split_detail("서울특별시강남구테헤란로123 101동202호"), ("서울특별시 강남구 테헤란로 123", "101동 202호"))
        self.assertEqual(ac.split_detail("경상북도 의성군 의성읍 중앙길12번길 3, 2층"), ("경상북도 의성군 의성읍 중앙길12번길 3", "2층"))
        self.assertEqual(ac.split_detail("충청남도 천안시 동남구 목천읍 교촌리 123번지 2층"), ("충청남도 천안시 동남구 목천읍 교촌리 123번지", "2층"))
        self.assertEqual(ac.split_detail("서울특별시 종로구"), ("서울특별시 종로구", ""))


class AIReview(unittest.TestCase):
    def test_sends_only_address_and_tidies_result(self):
        seen = []

        def chat(system, user, schema, name):
            seen.append(user)
            return {"is_address": True, "corrected": "서울특별시 강남구 테헤란로 123, 101동 202호",
                    "road_keyword": "서울특별시 강남구 테헤란로 123", "detail": "101동 202호", "changed": True}
        out = ac.ai_review("서울 강남구 테헤란로123 101동 202호", chat=chat)
        self.assertEqual(seen, ["서울 강남구 테헤란로123 101동 202호"])
        self.assertEqual(out["corrected"], "서울특별시 강남구 테헤란로 123, 101동 202호")
        self.assertEqual(out["detail"], "101동 202호")

    def test_rejects_invented_numbers(self):
        chat = lambda *a: {"is_address": True, "corrected": "서울특별시 강남구 테헤란로 123, 101동 202호",
                           "road_keyword": "서울특별시 강남구 테헤란로 123", "detail": "101동 202호", "changed": True}
        self.assertIsNone(ac.ai_review("서울 강남구 테헤란로123", chat=chat))

    def test_rejects_invented_province(self):
        chat = lambda *a: {"is_address": True, "corrected": "세종특별자치시 세종대로 209",
                           "road_keyword": "세종특별자치시 세종대로 209", "detail": "", "changed": True}
        self.assertIsNone(ac.ai_review("세종대로 209", chat=chat))
        ok = lambda *a: {"is_address": True, "corrected": "서울특별시 종로구 세종대로 209",
                         "road_keyword": "서울특별시 종로구 세종대로 209", "detail": "", "changed": True}
        self.assertIsNotNone(ac.ai_review("서울 종로구 세종대로209", chat=ok))
        self.assertIsNotNone(ac.ai_review("서울특별시종로구세종대로209", chat=ok))

    def test_not_an_address_or_failure(self):
        self.assertIsNone(ac.ai_review("안녕하세요", chat=lambda *a: {"is_address": False, "corrected": "", "road_keyword": "",
                                                                    "detail": "", "changed": False}))

        def boom(*a):
            raise TimeoutError("network")
        self.assertIsNone(ac.ai_review("서울 강남구", chat=boom))
        self.assertIsNone(ac.ai_review("서울 강남구", chat=lambda *a: "not json"))

    def test_without_key_makes_no_call(self):
        with patch.dict(os.environ, NO_KEYS), patch("address_check.load_env"), \
                patch("urllib.request.urlopen", side_effect=AssertionError("must not call")):
            self.assertIsNone(ac.ai_review("서울 강남구 테헤란로 123"))


class Juso(unittest.TestCase):
    def test_search_builds_query_and_reads_rows(self):
        opener = juso_reply(["서울특별시 강남구 테헤란로 123"])
        with patch.dict(os.environ, {"JUSO_API_KEY": "test-key"}), patch("address_check.load_env"):
            rows = ac.search_juso("서울특별시 강남구 테헤란로 123", opener=opener)
        self.assertEqual(rows, [{"road": "서울특별시 강남구 테헤란로 123", "jibun": "지번 서울특별시 강남구 테헤란로 123", "zip": "06234",
                                 "rn": "테헤란로", "number": "123"}])
        url = opener.urls[0]
        self.assertTrue(url.startswith(ac.JUSO_URL))
        self.assertIn("resultType=json", url)
        self.assertNotIn("%25", url)

    def test_api_error_or_no_key_returns_none(self):
        with patch.dict(os.environ, {"JUSO_API_KEY": "test-key"}), patch("address_check.load_env"):
            self.assertIsNone(ac.search_juso("서울 강남구", opener=juso_reply([], code="E0001")))

            def broken(url, timeout=0):
                return io.BytesIO(b"<html>")
            self.assertIsNone(ac.search_juso("서울 강남구", opener=broken))
            self.assertIsNone(ac.search_juso("서", opener=juso_reply(["x"])))
        with patch.dict(os.environ, NO_KEYS), patch("address_check.load_env"):
            self.assertIsNone(ac.search_juso("서울 강남구", opener=juso_reply(["x"])))

    def test_special_characters_are_removed_from_keyword(self):
        opener = juso_reply([])
        with patch.dict(os.environ, {"JUSO_API_KEY": "k"}), patch("address_check.load_env"):
            ac.search_juso("서울%강남구<script>", opener=opener)
        self.assertNotIn("%25", opener.urls[0])
        self.assertNotIn("%3C", opener.urls[0])


class Check(unittest.TestCase):
    def test_without_keys_gives_local_tidy_and_says_so(self):
        with patch.dict(os.environ, NO_KEYS), patch("address_check.load_env"), \
                patch("urllib.request.urlopen", side_effect=AssertionError("must not call")):
            r = ac.check("서울특별시강남구테헤란로123 101동202호")
        self.assertEqual(r["suggestion"], "서울특별시 강남구 테헤란로 123, 101동 202호")
        self.assertFalse(r["verified"])
        self.assertEqual(len(r["notes"]), 2)

    def test_single_juso_match_keeps_detail(self):
        with patch.dict(os.environ, {**NO_KEYS, "JUSO_API_KEY": "k"}), patch("address_check.load_env"):
            r = ac.check("서울 강남구 테헤란로123 101동 202호", opener=juso_reply(["서울특별시 강남구 테헤란로 123"]))
        self.assertTrue(r["verified"])
        self.assertEqual(r["suggestion"], "서울특별시 강남구 테헤란로 123 101동 202호")

    def test_several_matches_are_offered_not_chosen(self):
        rows = ["서울특별시 중구 중앙로 1", "인천광역시 중구 중앙로 1"]
        with patch.dict(os.environ, {**NO_KEYS, "JUSO_API_KEY": "k"}), patch("address_check.load_env"):
            r = ac.check("중앙로 1", opener=juso_reply(rows))
        self.assertEqual([c["road"] for c in r["candidates"]], rows)
        self.assertEqual(r["suggestion"], "중앙로 1")
        self.assertTrue(r["verified"])

    def test_only_similar_addresses_are_not_called_verified(self):
        rows = ["서울특별시 강남구 테헤란로 313", "서울특별시 강남구 테헤란로1길 10"]
        with patch.dict(os.environ, {**NO_KEYS, "JUSO_API_KEY": "k"}), patch("address_check.load_env"):
            r = ac.check("테헤란로 1", opener=juso_reply(rows))
        self.assertFalse(r["verified"])
        self.assertEqual(r["suggestion"], "테헤란로 1")
        self.assertEqual(len(r["candidates"]), 2)
        self.assertTrue(any("똑같은 주소는 없어요" in n for n in r["notes"]))

    def test_exact_road_and_number_win_over_fuzzy_results(self):
        rows = ["경기도 성남시 분당구 불정로 195", "경기도 성남시 분당구 불정로 6", "경기도 성남시 분당구 불정로 6-1"]
        with patch.dict(os.environ, {**NO_KEYS, "JUSO_API_KEY": "k"}), patch("address_check.load_env"):
            r = ac.check("성남시 분당구 불정로6 3층", opener=juso_reply(rows))
        self.assertEqual([c["road"] for c in r["candidates"]], ["경기도 성남시 분당구 불정로 6"])
        self.assertEqual(r["suggestion"], "경기도 성남시 분당구 불정로 6 3층")

    def test_duplicate_rows_collapse_and_region_only_is_not_verified(self):
        rows = ["경기도 성남시 수정구 산성대로 451", "경기도 성남시 수정구 산성대로 451"]
        with patch.dict(os.environ, {**NO_KEYS, "JUSO_API_KEY": "k"}), patch("address_check.load_env"):
            r = ac.check("성남시 수정구 산성대로 451", opener=juso_reply(rows))
            self.assertEqual((len(r["candidates"]), r["verified"], r["suggestion"]), (1, True, rows[0]))
            r = ac.check("서울시 강남구", opener=juso_reply(["서울특별시 강남구 가로수길 5"]))
        self.assertFalse(r["verified"])
        self.assertTrue(any("덜 적혔어요" in n for n in r["notes"]))

    def test_juso_without_ai_key_hides_ai_note(self):
        with patch.dict(os.environ, {**NO_KEYS, "JUSO_API_KEY": "k"}), patch("address_check.load_env"):
            r = ac.check("서울강남구테헤란로123", opener=juso_reply(["서울특별시 강남구 테헤란로 123"]))
        self.assertTrue(r["verified"])
        self.assertEqual(r["notes"], [])

    def test_no_match_is_reported(self):
        with patch.dict(os.environ, {**NO_KEYS, "JUSO_API_KEY": "k"}), patch("address_check.load_env"):
            r = ac.check("없는시 없는로 999", opener=juso_reply([]))
        self.assertFalse(r["verified"])
        self.assertTrue(any("찾지 못했습니다" in n for n in r["notes"]))

    def test_ai_then_juso(self):
        chat = lambda *a: {"is_address": True, "corrected": "서울특별시 강남구 테헤란로 123 101동",
                           "road_keyword": "서울특별시 강남구 테헤란로 123", "detail": "101동", "changed": True}
        opener = juso_reply(["서울특별시 강남구 테헤란로 123"])
        with patch.dict(os.environ, {**NO_KEYS, "JUSO_API_KEY": "k"}), patch("address_check.load_env"):
            r = ac.check("서울강남구테헤란로 123 101동", chat=chat, opener=opener)
        self.assertTrue(r["ai_used"] and r["juso_used"] and r["verified"])
        self.assertIn("keyword=%EC%84%9C%EC%9A%B8%ED%8A%B9%EB%B3%84%EC%8B%9C", opener.urls[0])
        self.assertEqual(r["suggestion"], "서울특별시 강남구 테헤란로 123 101동")

    def test_empty(self):
        r = ac.check("   ")
        self.assertEqual((r["suggestion"], r["candidates"]), ("", []))


if __name__ == "__main__":
    unittest.main()
