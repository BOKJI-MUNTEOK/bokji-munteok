"""Vercel 웹 경계의 회귀 검사. 외부 기관 다운로드는 테스트에서 대체한다."""
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
import main


class TestWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def test_deep_links_and_assets(self):
        for path in ("/", "/find", "/all", "/analysis", "/guide"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn('lang="ko"', response.text)
        for path in ("/static/app.js", "/static/style.css", "/assets/logo/mark.svg"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertEqual(self.client.get("/not-a-page").status_code, 404)
        self.assertEqual(self.client.get("/.env").status_code, 404)

    def test_bootstrap_matches_existing_pipeline(self):
        data = self.client.get("/api/bootstrap").json()
        expected = main.build()
        self.assertEqual(len(data["rows"]), 50)
        self.assertEqual([(r["svc"]["servId"], r["score"]) for r in data["rows"]],
                         [(r["svc"]["servId"], r["score"]) for r in expected])
        self.assertTrue(all(r["number_check"] is not None for r in data["rows"]))

    def test_recommendation_matches_existing_logic_and_is_private(self):
        answers = {"place": "경기도", "age": "32세", "job": "구직 중", "family": "아이 3명 이상", "special": "없음", "need": "집·월세"}
        response = self.client.post("/api/recommend", json={"answers": answers})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        p = main.intake.build(answers, main.places())
        expected = main.match.recommend(main.dataset()[1], **main.intake.conditions(p))
        self.assertEqual(response.json()["results"], [{"id": r["svc"]["servId"], "why": why} for r, why in expected])
        fresh = self.client.post("/api/recommend", json={}).json()["profile"]
        self.assertEqual(fresh, main.intake.new_profile())

    def test_invalid_input_does_not_echo_personal_values(self):
        for body in ({"answers": {"age": "130"}}, {"answers": {"name": "민감한입력"}},
                     {"answers": {"place": "X" * 301}}, {"quick": "unknown"}):
            response = self.client.post("/api/recommend", json=body)
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("민감한입력", response.text)

    def test_weights_are_per_request_and_validated(self):
        default = self.client.post("/api/analysis", json={}).json()
        zeros = self.client.post("/api/analysis", json={"weights": {k: 0 for k in main.score.DEFAULT_WEIGHTS}}).json()
        self.assertTrue(all(r["score"] == 0 for r in zeros["rows"]))
        self.assertEqual(self.client.post("/api/analysis", json={}).json(), default)
        self.assertEqual(default["gold_count"], 50)
        self.assertEqual(self.client.post("/api/analysis", json={"weights": {"visit": -1}}).status_code, 422)

    def test_packet_contains_pdf_and_failed_attachment_links(self):
        row = next(r for r in main.dataset()[1] if r["svc"]["form_files"])
        with patch("main.limited_downloads", return_value=lambda url: (_ for _ in ()).throw(TimeoutError())):
            response = self.client.post("/api/packet", json={"service_id": row["svc"]["servId"], "answers": {"age": "32"}, "extra": {"name": "시연"}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertGreater(int(response.headers["x-missing-files"]), 0)
        self.assertLess(len(response.content), 4_000_000)
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            pdf = next(n for n in archive.namelist() if n.endswith(".pdf"))
            self.assertTrue(archive.read(pdf).startswith(b"%PDF"))
            self.assertIn("내려받지_못한_파일.txt", archive.namelist())
        self.assertEqual(self.client.post("/api/packet", json={"service_id": "WLF99999999"}).status_code, 404)
        self.assertEqual(self.client.post("/api/packet", json={"service_id": "../../.env"}).status_code, 422)

    def test_attachment_budget_rejects_oversized_files(self):
        class FakeResponse(io.BytesIO):
            pass
        with patch("main.urllib.request.urlopen", return_value=FakeResponse(b"x" * 2_500_001)):
            with self.assertRaises(ValueError):
                main.limited_downloads()("https://www.bokjiro.go.kr/test")


if __name__ == "__main__":
    unittest.main()
