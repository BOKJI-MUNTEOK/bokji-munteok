"""AI 상담: OpenAI 응답(가짜)으로 정보를 모으고, 기존 규칙으로 정리해 추천까지 이어지는지."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import chat_ai
import test_all
from streamlit.testing.v1 import AppTest

PLACES = [("경기도", "성남시"), ("전라남도", "해남군")]


def reply(**kw):
    base = {"reply": "말씀 고마워요.", "place": "", "age": "", "job": "", "family": "", "special": "", "need": "", "name": "", "done": False}
    return {**base, **kw}


class Turn(unittest.TestCase):
    def test_fields_are_normalized_by_rules(self):
        seen = []

        def chat(system, user, schema, name):
            seen.append(user)
            return reply(place="성남", age="72", family="혼자 살아요", special="저소득", need="병원비", done=True,
                         reply="정리했어요. 아래에서 찾은 복지를 보여 드릴게요.")
        out = chat_ai.turn([{"role": "assistant", "content": chat_ai.GREETING},
                            {"role": "user", "content": "성남 사는 72살이고 혼자 살아요"}], PLACES, chat=chat)
        p = out["profile"]
        self.assertEqual((p["sido"], p["sgg"], p["age"]), ("경기도", "성남시", 72))
        self.assertIn("노년", p["life"])
        self.assertIn("저소득", p["groups"])
        self.assertIn("신체건강", p["themes"])
        self.assertTrue(out["done"])
        self.assertIn("사용자: 성남 사는 72살", seen[0])

    def test_collects_form_info_and_drops_bad_values(self):
        out = chat_ai.turn([{"role": "user", "content": "가상시험이고 1950년 1월 1일생, 01012345678, 성남시 불정로 6 101동"}], PLACES,
                           chat=lambda *a: reply(place="성남", name="가상시험", birth="1950년 1월 1일", phone="01012345678",
                                                 address="경기도 성남시 분당구 불정로 6 101동", reply="고마워요."))
        p = out["profile"]
        self.assertEqual((p["name"], p["birth"], p["phone"]), ("가상시험", "1950-01-01", "010-1234-5678"))
        self.assertEqual(p["address"], "경기도 성남시 분당구 불정로 6, 101동")  # 띄어쓰기 정리 규칙을 거친다
        self.assertIsNotNone(p["age"])  # 생년월일로 나이를 계산한다
        bad = chat_ai.turn([{"role": "user", "content": "x"}], PLACES,
                           chat=lambda *a: reply(birth="모름", phone="12", reply="네"))["profile"]
        self.assertEqual((bad["birth"], bad["phone"]), ("", ""))

    def test_cannot_finish_without_place_and_age(self):
        out = chat_ai.turn([{"role": "user", "content": "결과 보여 줘"}], PLACES,
                           chat=lambda *a: reply(need="생활비", done=True, reply="보여 드릴게요"))
        self.assertFalse(out["done"])
        self.assertEqual(out["missing"], ["지역", "나이"])
        self.assertIn("지역", out["reply"])
        self.assertIn("나이", out["reply"])

    def test_bad_replies_raise(self):
        for bad in ("text", {"reply": ""}, None):
            with self.assertRaises(Exception):
                chat_ai.turn([{"role": "user", "content": "안녕"}], PLACES, chat=lambda *a, b=bad: b)

    def test_pick_keeps_only_candidates(self):
        cands = [{"servId": f"S{i}", "servNm": f"복지{i}", "target": "", "summary": ""} for i in range(20)]
        out = chat_ai.pick([{"role": "user", "content": "70살"}], cands, chat=lambda *a: {"picks": [
            {"id": "S3", "reason": "어르신 대상"}, {"id": "X", "reason": ""}, {"id": "S3", "reason": "중복"},
            {"id": "S16", "reason": "15개 밖"}, {"id": "S1", "reason": "생활비"}, {"id": "S2", "reason": ""}, {"id": "S4", "reason": ""}]})
        self.assertEqual(out, [("S3", "어르신 대상"), ("S1", "생활비"), ("S2", "")])

        def boom(*a):
            raise TimeoutError
        self.assertEqual(chat_ai.pick([], cands, chat=boom), [])
        self.assertEqual(chat_ai.pick([], [], chat=boom), [])

    def test_transcript_is_bounded(self):
        msgs = [{"role": "user", "content": "가" * 2000}] * 40
        t = chat_ai.transcript(msgs)
        self.assertEqual(t.count("사용자:"), 24)
        self.assertLess(len(t), 24 * 620)


class Screen(unittest.TestCase):
    def setUp(self):
        test_all.TestApp.setUp(self)
        patch("chat_ai.available", return_value=True).start()
        self.addCleanup(patch.stopall)

    def app(self):
        script = f"import runpy\napp = runpy.run_path({str(test_all.ROOT / 'app.py')!r})\napp['mine']()"
        at = AppTest.from_string(script, default_timeout=30)
        at.session_state["mode"] = "상담으로 찾기"
        return at.run()

    def test_conversation_to_results_and_restart(self):
        answers = [reply(place="해남", reply="해남에 사시는군요. 나이가 어떻게 되세요?"),
                   reply(place="해남", age="70", need="생활비", done=True, reply="정리했어요. 아래에서 찾은 복지를 보여 드릴게요."),
                   {"picks": [{"id": "없는서비스", "reason": "무시돼야 함"}]}]
        with patch("llm._chat", side_effect=answers) as chat:
            at = self.app()
            self.assertFalse(at.exception)
            self.assertTrue(any("AI 상담" in m.value for m in at.chat_message[0].markdown))
            at.chat_input(key="ai_answer").set_value("해남에 살아요").run()
            self.assertIn("나이가 어떻게", at.chat_message[-1].markdown[0].value)
            at.chat_input(key="ai_answer").set_value("70살이고 생활비가 필요해요").run()
        self.assertFalse(at.exception)
        self.assertEqual(chat.call_count, 3)  # 대화 2번 + 먼저 볼 복지 고르기 1번
        self.assertTrue(at.session_state.ai_done)
        self.assertNotIn("없는서비스", at.session_state.ai_picks)  # 후보에 없는 id는 버리고 규칙 순서로
        self.assertEqual(len(at.session_state.ai_picks), 3)
        self.assertEqual(at.session_state.me["age"], 70)
        self.assertEqual(at.session_state.me["sgg"], "해남군")
        self.assertTrue(any("맞춤 복지" in m.value for m in at.markdown))
        guide = at.session_state.ai_msgs[-1]["content"]  # AI가 먼저 잘 맞는 복지를 짚고 신청서 채우기로 안내한다
        self.assertIn("찾았어요", guide)
        self.assertIn("1. **", guide)
        self.assertIn("서류 작성·인쇄", guide)
        next(b for b in at.button if b.key == "ai_restart").click().run()
        self.assertEqual(len(at.session_state.ai_msgs), 1)
        self.assertFalse(at.exception)

    def test_failure_offers_retry_and_switch(self):
        with patch("llm._chat", side_effect=[TimeoutError("SECRET"), reply(place="해남", reply="해남이군요.")]) as chat:
            at = self.app()
            at.chat_input(key="ai_answer").set_value("해남이요").run()
            self.assertTrue(at.error)
            self.assertNotIn("SECRET", str(at.error))
            at.button(key="ai_retry_btn").click().run()
        self.assertFalse(at.exception)
        self.assertEqual(chat.call_count, 2)
        self.assertFalse(at.error)
        self.assertEqual(sum(m["role"] == "user" for m in at.session_state.ai_msgs), 1)  # 다시 시도해도 같은 말을 두 번 넣지 않는다

    def test_switch_to_steps_on_failure(self):
        with patch("llm._chat", side_effect=TimeoutError()):
            at = self.app()
            at.chat_input(key="ai_answer").set_value("안녕").run()
            at.button(key="ai_to_steps").click().run()
        self.assertEqual(at.session_state.mode, "질문에 답하기")
        self.assertFalse(at.exception)

    def test_without_key_uses_rule_questions(self):
        with patch("chat_ai.available", return_value=False), patch("llm._chat", side_effect=AssertionError("no call")):
            at = self.app()
            at.chat_input(key="chat_answer").set_value("해남").run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state.qi, 1)


if __name__ == "__main__":
    unittest.main()
