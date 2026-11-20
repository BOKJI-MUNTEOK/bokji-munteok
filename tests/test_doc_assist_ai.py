"""서류 AI 도움: 쉬운 발급 안내. OpenAI 호출은 가짜로 대신한다."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import doc_assist_ai as ai

def fake(result, seen=None):
    def chat(system, user, schema, name):
        if seen is not None:
            seen.append(user)
        return result
    return chat


class EasyGuide(unittest.TestCase):
    def test_sends_public_text_only(self):
        seen = []
        g = ai.easy_guide({"servNm": "목욕비", "org": "가상군", "apply": "주민센터 방문", "form_files": [{"name": "신청서.hwp"}]},
                          chat=fake({"steps": ["주민센터에 가세요."] * 8, "documents": [{"name": "신분증", "where": "본인"}], "tip": "챙기세요"}, seen))
        self.assertEqual(len(g["steps"]), 6)
        self.assertIn("신청서.hwp", seen[0])

    def test_empty_or_failed(self):
        self.assertIsNone(ai.easy_guide({"servNm": "x"}, chat=fake({"steps": [], "documents": [], "tip": ""})))

        def boom(*a):
            raise OSError
        self.assertIsNone(ai.easy_guide({"servNm": "x"}, chat=boom))


if __name__ == "__main__":
    unittest.main()
