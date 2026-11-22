"""가중치 민감도 점검이 저장된 전체 점수를 그대로 재현하는지 확인한다(가중치를 바꿔 다시 낸 비교가 의미 있으려면 필요)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import weight_check


class TestWeightCheck(unittest.TestCase):
    def test_reproduces_stored_scores_and_is_deterministic(self):
        r = weight_check.check(draws=20)
        self.assertEqual(r["reproduced"], 1.0)
        self.assertEqual(r["base"]["순위상관"], 1.0)
        self.assertEqual(r, weight_check.check(draws=20))


if __name__ == "__main__":
    unittest.main()
