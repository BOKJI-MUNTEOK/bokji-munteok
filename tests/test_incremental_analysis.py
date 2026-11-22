"""변경 없는 원문·DB 분석을 재사용하면서 점수와 AI 비교 결과를 유지한다."""
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import analysis_cache
import llm
import pipeline
import rules
import score
from parse import load_all


class TestIncrementalAnalysis(unittest.TestCase):
    def setUp(self):
        analysis_cache.clear_cache()
        self.services = load_all(ROOT / "data" / "sample")[:3]

    def tearDown(self):
        analysis_cache.clear_cache()

    def stored(self, service, legacy=False):
        service = deepcopy(service)
        ext, read = rules.extract(service), score.readability(service)
        service["stored_analysis"] = {"ext": ext, "read": read,
                                      **score.score(ext, read), "weights": score.DEFAULT_WEIGHTS}
        if not legacy:
            service["stored_analysis"].update(feature_version=analysis_cache.FEATURE_VERSION,
                                               source_hash=analysis_cache.analysis_hash(service))
        return service

    def test_all_sample_scores_and_evidence_match_original_for_both_modes(self):
        services = load_all(ROOT / "data" / "sample")
        for mode in ("규칙", "AI"):
            for weights in (None, {"no_online": 0, "document": 3}):
                with self.subTest(mode=mode, weights=weights):
                    for row in pipeline.build(weights, mode, services):
                        s = row["svc"]
                        ai = llm.load_cached(s["servId"], s)
                        ext = ai["extract"] if mode == "AI" and ai else rules.extract(s)
                        read = score.readability(s)
                        expected = score.score(ext, read, weights)
                        self.assertEqual(row["ext"], ext)
                        self.assertEqual(row["read"], read)
                        self.assertEqual(row["ai"], ai)
                        self.assertEqual({k: row[k] for k in expected}, expected)

    def test_cold_start_uses_current_and_known_legacy_db_features(self):
        services = [self.stored(self.services[0]), self.stored(self.services[1], legacy=True)]
        expected = [score.score(s["stored_analysis"]["ext"], s["stored_analysis"]["read"]) for s in services]
        # 저장 당시 점수 이유가 이전 문구여도 현재 점수식으로 표시한다.
        for s in services:
            s["stored_analysis"].update(score=999, reason="이전 버전 설명")
        # 버전 없는 저장 결과는 규칙이 그 시점과 같을 때만 쓴다. 그 상황을 직접 만든다.
        with patch("analysis_cache.LEGACY_FEATURE_VERSION", analysis_cache.FEATURE_VERSION), \
                patch("rules.extract", side_effect=AssertionError("저장된 추출값 재사용 실패")), \
                patch("score.readability", side_effect=AssertionError("저장된 난이도 재사용 실패")):
            rows = pipeline.build(services=services)
        actual = {r["svc"]["servId"]: r for r in rows}
        for s, original in zip(services, expected):
            self.assertEqual({k: actual[s["servId"]][k] for k in original}, original)

    def test_legacy_db_features_are_reanalyzed_after_rule_change(self):
        service = self.stored(self.services[1], legacy=True)
        service["stored_analysis"]["ext"]["visit_required"] = not rules.extract(service)["visit_required"]
        with patch("analysis_cache.LEGACY_FEATURE_VERSION", "이전 규칙"), \
                patch("rules.extract", wraps=rules.extract) as extract:
            row = pipeline.build(services=[service])[0]
        self.assertEqual(extract.call_count, 1)
        self.assertEqual(row["ext"], rules.extract(service))

    def test_add_modify_delete_only_analyzes_new_content(self):
        services = deepcopy(self.services)
        with patch("rules.extract", wraps=rules.extract) as extract, \
                patch("score.readability", wraps=score.readability) as read:
            before = pipeline.build(services=services)
            initial = extract.call_count
            self.assertEqual(read.call_count, initial)
            changed = deepcopy(services[1])
            changed["criteria"] += " 19세 이상 취업한 청년"
            added = deepcopy(services[2])
            added["servId"] = "new-service"
            added["apply"] += " 추가된 신청 방법: 온라인 신청 후 방문"
            after = pipeline.build(services=[services[0], changed, added])
            self.assertEqual(extract.call_count, initial + 2)
            self.assertEqual(read.call_count, initial + 2)
            pipeline.build(services=[services[0], changed, added])
            self.assertEqual(extract.call_count, initial + 2)
        by_id = {r["svc"]["servId"]: r for r in after}
        self.assertNotIn(services[2]["servId"], by_id)
        original = next(r for r in before if r["svc"]["servId"] == services[0]["servId"])
        self.assertEqual(by_id[services[0]["servId"]], original)
        expected = score.score(rules.extract(changed), score.readability(changed))
        self.assertEqual(by_id[changed["servId"]]["score"], expected["score"])

    def test_catalog_check_time_and_metadata_changes_do_not_reanalyze(self):
        with patch("rules.extract", wraps=rules.extract) as extract, \
                patch("score.readability", wraps=score.readability) as read, \
                patch("score.score", wraps=score.score) as weighted:
            pipeline.build(services=self.services)
            counts = (extract.call_count, read.call_count, weighted.call_count)
            checked = deepcopy(self.services)
            for s in checked:
                s.update(detail_fetched_at=999, list_hash="new-list-metadata", summary="최신 목록 요약",
                         region="새 지역 표시")
            rows = pipeline.build(services=checked)
            self.assertEqual((extract.call_count, read.call_count, weighted.call_count), counts)
            self.assertTrue(all(r["svc"]["summary"] == "최신 목록 요약" for r in rows))

    def test_weight_change_only_recalculates_weighted_score(self):
        services = [self.stored(s) for s in self.services]
        with patch("rules.extract", side_effect=AssertionError("가중치 변경으로 원문을 다시 추출함")), \
                patch("score.readability", side_effect=AssertionError("가중치 변경으로 난이도를 다시 계산함")), \
                patch("score.score", wraps=score.score) as weighted:
            defaults = pipeline.build(services=services)
            first_count = weighted.call_count
            weights = {"no_online": 0, "condition": 4}
            changed = pipeline.build(weights, services=services)
            self.assertGreater(weighted.call_count, first_count)
            changed_count = weighted.call_count
            pipeline.build(weights, services=services)
            self.assertEqual(weighted.call_count, changed_count)
            self.assertEqual(pipeline.build(services=services), defaults)
            self.assertEqual(weighted.call_count, changed_count)
        for row in changed:
            expected = score.score(row["ext"], row["read"], weights)
            self.assertEqual({k: row[k] for k in expected}, expected)

    def test_stale_engine_or_wrong_source_recomputes_only_once(self):
        for field, value in (("feature_version", "old-engine"), ("source_hash", "other-source")):
            with self.subTest(field=field):
                analysis_cache.clear_cache()
                s = self.stored(self.services[0])
                s["stored_analysis"][field] = value
                s["stored_analysis"]["ext"]["online"] = not s["stored_analysis"]["ext"]["online"]
                with patch("rules.extract", wraps=rules.extract) as extract:
                    rows = pipeline.build(services=[s])
                    pipeline.build(services=[s])
                    self.assertEqual(extract.call_count, 1)
                self.assertEqual(rows[0]["ext"], rules.extract(s))

    def test_changed_engine_does_not_trust_unversioned_legacy_results(self):
        s = self.stored(self.services[0], legacy=True)
        with patch("analysis_cache.FEATURE_VERSION", "new-engine"), \
                patch("rules.extract", wraps=rules.extract) as extract:
            pipeline.build(services=[s])
            pipeline.build(services=[s])
            self.assertEqual(extract.call_count, 1)

    def test_incomplete_stored_analysis_falls_back_without_breaking_page(self):
        s = self.stored(self.services[0])
        del s["stored_analysis"]["ext"]["conditions"]
        with patch("rules.extract", wraps=rules.extract) as extract:
            row = pipeline.build(services=[s])[0]
            self.assertEqual(extract.call_count, 1)
        self.assertEqual(row["ext"], rules.extract(s))

    def test_output_mutation_cannot_poison_shared_feature_or_score_cache(self):
        first = pipeline.build(services=self.services)
        expected = deepcopy(first)
        first[0]["ext"]["conditions"].append({"type": "changed", "text": ""})
        first[0]["parts"].clear()
        self.assertEqual(pipeline.build(services=self.services), expected)

    def test_ai_uses_cached_readability_without_unnecessary_rule_extraction(self):
        s = self.services[0]
        ai = {"extract": rules.extract(s)}
        ai["extract"]["online"] = not ai["extract"]["online"]
        with patch("llm.load_cached", return_value=ai), \
                patch("rules.extract", wraps=rules.extract) as extract, \
                patch("score.readability", wraps=score.readability) as read:
            rows = pipeline.build(mode="AI", services=[s])
            self.assertEqual(rows[0]["ext"], ai["extract"])
            self.assertEqual(rows[0]["ext_source"], "AI")
            extract.assert_not_called()
            self.assertEqual(read.call_count, 1)
            pipeline.build(mode="AI", services=[s])
            self.assertEqual(read.call_count, 1)
            normal = pipeline.build(services=[s])
            self.assertEqual(extract.call_count, 1)
            self.assertEqual(read.call_count, 1)
            self.assertEqual(normal[0]["ext_source"], "규칙")

    def test_unloaded_details_do_not_analyze_placeholder_fields(self):
        s = {**self.services[0], "detail_loaded": False}
        with patch("rules.extract", side_effect=AssertionError("미수집 상세 분석")), \
                patch("score.readability", side_effect=AssertionError("미수집 상세 분석")):
            row = pipeline.build(services=[s])[0]
        self.assertIsNone(row["score"])
        self.assertIsNone(row["ext"])


if __name__ == "__main__":
    unittest.main()
