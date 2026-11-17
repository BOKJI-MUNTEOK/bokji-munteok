import io
import os
import sys
import threading
import time
import traceback
import unittest
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import mongomock
from pymongo.errors import ConnectionFailure

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from parse import merge_detail, parse_list_item
from pipeline import build
import llm
from parse import load_all
from welfare_api import APIError, DataClient, QuotaExceeded, WelfareAPI, api_keys
from welfare_store import StoreError, WelfareStore, key_id, sync_slot, today
from welfare_sync import main, sync
from test_welfare_api import listing, detail_xml


def items(ids, source="local"):
    return [parse_list_item(e, source) for e in listing(ids).iter("servList")]


class TestMongoSync(unittest.TestCase):
    def setUp(self):
        self.mongo = mongomock.MongoClient()
        mock = patch("welfare_store.mongo_client", return_value=self.mongo)
        mock.start()
        self.addCleanup(mock.stop)
        self.store = WelfareStore("mongodb://test").__enter__()
        self.store.initialize()
        self.keys = ["test-one", "test-two", "test-three"]
        self.client = DataClient(self.store, self.keys, MagicMock())
        self.local = items(["L1"])[0]

    def publish(self, data, source="local"):
        self.assertTrue(self.store.acquire("test-owner"))
        return self.store.publish_catalog(source, data, "test-owner")

    def save(self, item):
        detail = merge_detail(item, detail_xml(item["servId"], item["api_source"]))
        self.store.save_detail(item, detail)
        return detail

    def test_korean_midnight_resets_daily_budget(self):
        before = datetime(2026, 11, 17, 14, 59, 59, tzinfo=timezone.utc).timestamp()
        self.assertEqual(today(before), "2026-11-17")
        self.assertEqual(today(before + 1), "2026-11-18")
        with patch("welfare_store.today", return_value="2026-11-17"):
            key, day = self.store.reserve_key("central", [self.keys[0]])
            self.store.block_key("central", key, day)
            self.assertEqual(self.store.remaining("central", [key]), 0)
        with patch("welfare_store.today", return_value="2026-11-18"):
            self.assertEqual(self.store.remaining("central", [key]), 100)

    def test_duplicate_keys_do_not_expand_limit(self):
        self.assertEqual(self.store.remaining("central", [*self.keys, self.keys[0]]), 300)
        self.assertEqual(self.store.remaining("local", self.keys), 3000)
        with patch.dict(os.environ, {"DATA_GO_KR_KEY": "a+b/==", "DATA_GO_KR_KEY_2": "a%2Bb%2F%3D%3D",
                                     "DATA_GO_KR_KEY_3": "second"}), patch("welfare_api.load_env"):
            self.assertEqual(api_keys(), ["a+b/==", "second"])

    def test_concurrent_requests_cannot_exceed_three_budgets(self):
        # mongomock은 findOneAndUpdate의 서버 원자성을 구현하지 않으므로 그 한 연산만 잠근다.
        # 실제 Atlas에서도 별도의 통합 검사로 병렬 예약 한도를 확인한다.
        lock = threading.Lock()
        original = self.store.db.quota.find_one_and_update
        def atomic(*args, **kwargs):
            with lock:
                return original(*args, **kwargs)
        with patch.object(self.store.db.quota, "find_one_and_update", side_effect=atomic):
            with ThreadPoolExecutor(max_workers=12) as pool:
                reserved = list(pool.map(lambda _: self.store.reserve_key("central", self.keys), range(350)))
        self.assertEqual(sum(r is not None for r in reserved), 300)
        self.assertEqual(self.store.remaining("central", self.keys), 0)
        self.assertEqual([r["used"] for r in self.store.quota("central", self.keys).values()], [100] * 3)
        self.assertNotIn("test-one", str(list(self.store.db.quota.find())))

    def test_live_lease_prevents_duplicate_job_and_expired_lease_resumes(self):
        now = time.time()
        self.assertTrue(self.store.acquire("first", now))
        self.assertFalse(self.store.acquire("second", now + 1))
        self.assertTrue(self.store.acquire("second", now + 301))
        with self.assertRaises(StoreError):
            self.store.renew("first")
        self.store.release("first")
        self.assertEqual(self.store.db.meta.find_one({"_id": "sync"})["owner"], "second")

    def test_add_change_delete_only_after_complete_catalog(self):
        first = items(["L1", "L2", "L3"])
        self.publish(first)
        for item in first:
            self.save(item)
        updated = [first[0], {**first[1], "list_hash": "changed", "summary": "변경"}, *items(["L4"])]
        self.assertEqual(self.publish(updated), {"added": 1, "changed": 1, "removed": 1})
        self.assertEqual([s["servId"] for s in self.store.pending("local")], ["L2", "L4"])
        self.assertTrue(self.store.service("local", "L1")["detail_loaded"])
        self.assertFalse(self.store.service("local", "L2")["detail_loaded"])
        self.assertIsNone(self.store.service("local", "L3"))
        self.assertEqual(self.store.progress(self.keys)["sources"]["local"]["ready"], 1)

    def test_partial_database_write_does_not_publish_or_delete(self):
        self.publish([self.local])
        self.save(self.local)
        original = self.store.db.catalog.insert_many
        def interrupted(docs):
            original(docs[:1])
            raise ConnectionFailure("interrupted")
        with patch.object(self.store.db.catalog, "insert_many", side_effect=interrupted):
            with self.assertRaises(ConnectionFailure):
                self.store.publish_catalog("local", items(["NEW", "NEXT"]), "test-owner")
        self.assertTrue(self.store.service("local", "L1")["detail_loaded"])
        self.assertIsNone(self.store.service("local", "NEW"))

    def test_late_detail_cannot_overwrite_changed_version(self):
        self.publish([self.local])
        updated = {**self.local, "list_hash": "new"}
        self.publish([updated])
        current = self.save(updated)
        old = {**merge_detail(self.local, detail_xml()), "criteria": "old response"}
        self.store.save_detail(self.local, old)
        self.assertEqual(self.store.service("local", "L1")["criteria"], current["criteria"])

    def test_restart_keeps_details_analysis_and_quota(self):
        self.publish([self.local])
        detailed = self.save(self.local)
        self.store.reserve_key("central", self.keys)
        with WelfareStore("mongodb://test") as restarted:
            restarted.initialize()
            svc = restarted.service("local", "L1")
            self.assertEqual(svc["target"], "19세 이상")
            self.assertEqual(svc["stored_analysis"]["score"], build(services=[detailed])[0]["score"])
            self.assertEqual(restarted.remaining("central", self.keys), 299)

    def test_same_day_has_no_calls_and_next_day_only_lists_unchanged(self):
        def catalog(client, source):
            return [self.local] if source == "local" else []
        def detail(client, item):
            return merge_detail(item, detail_xml())
        with patch.object(DataClient, "catalog", autospec=True, side_effect=catalog) as cat, \
                patch.object(DataClient, "detail", autospec=True, side_effect=detail) as dtl:
            sync("mongodb://test", self.keys)
            self.assertEqual((cat.call_count, dtl.call_count), (2, 1))
            sync("mongodb://test", self.keys)
            self.assertEqual((cat.call_count, dtl.call_count), (2, 1))
            self.store.db.meta.update_one({"_id": "sync"}, {"$set": {
                "sources.central.catalog_day": "yesterday", "sources.local.catalog_day": "yesterday"}})
            sync("mongodb://test", self.keys)
            self.assertEqual((cat.call_count, dtl.call_count), (4, 1))

    def test_failed_list_keeps_previous_catalog_and_detail(self):
        self.publish([self.local])
        self.save(self.local)
        self.store.release("test-owner")
        self.store.db.meta.update_one({"_id": "sync"}, {"$set": {"sources.local.catalog_day": "yesterday"}})
        with patch.object(DataClient, "catalog", side_effect=APIError("목록 실패")):
            sync("mongodb://test", self.keys)
        self.assertTrue(self.store.service("local", "L1")["detail_loaded"])
        self.assertEqual(self.store.source_state("local")["status"], "error")

    def test_list_hash_ignores_views_but_tracks_modified_fields(self):
        element = next(listing(["L1"]).iter("servList"))
        before = parse_list_item(element, "local")["list_hash"]
        ET.SubElement(element, "inqNum").text = "99999"
        self.assertEqual(parse_list_item(element, "local")["list_hash"], before)
        element.find("lastModYmd").text = "20261118"
        self.assertNotEqual(parse_list_item(element, "local")["list_hash"], before)

    def test_catalog_fetches_every_page_and_rejects_incomplete_result(self):
        with patch.object(self.client, "request", side_effect=[listing(["L1"], 2), listing(["L2"], 2, 2)]):
            self.assertEqual(len(self.client.catalog("local")), 2)
        for responses in ([listing(["L1"], 2), listing(["L1"], 2, 2)],
                          [listing(["L1"], 2), listing(["L2"], 3, 2)]):
            with patch.object(self.client, "request", side_effect=responses):
                with self.assertRaises(APIError):
                    self.client.catalog("local")

    def test_quota_error_blocks_only_rejected_key_and_reserves_retries(self):
        error = b"<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>test-one</errMsg><returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
        with patch("welfare_api.urllib.request.urlopen", side_effect=[io.BytesIO(error), io.BytesIO(ET.tostring(listing([])))]) as req:
            self.client.request("central", pageNo=1)
        self.assertEqual(req.call_count, 2)
        quota = list(self.store.quota("central", self.keys).values())
        self.assertEqual(sum(r["used"] for r in quota), 2)
        self.assertEqual(sum(r["blocked"] for r in quota), 1)

    def test_retry_counts_and_errors_never_expose_request_credentials(self):
        error = urllib.error.HTTPError("https://invalid/?serviceKey=test-one", 429, "limited", {}, None)
        with patch("welfare_api.urllib.request.urlopen", side_effect=error) as req, patch("welfare_api.time.sleep"):
            try:
                self.client.request("local")
            except APIError:
                self.assertNotIn("test-one", traceback.format_exc())
            else:
                self.fail("Expected APIError")
        self.assertEqual(req.call_count, 3)
        self.assertEqual(self.store.remaining("local", self.keys), 2997)

    def test_exhaustion_stops_requests_until_tomorrow(self):
        for key in self.keys:
            reserved, day = self.store.reserve_key("central", [key])
            self.store.block_key("central", reserved, day)
        with patch("welfare_api.urllib.request.urlopen") as req:
            with self.assertRaises(QuotaExceeded):
                self.client.request("central")
            req.assert_not_called()

    def test_pending_detail_does_not_spend_an_extra_call(self):
        self.publish([self.local])
        api = WelfareAPI("mongodb://test")
        with patch("welfare_api.urllib.request.urlopen") as req, patch("welfare_sync.sync") as refresh:
            with self.assertRaisesRegex(APIError, "자동 수집 대기"):
                api.detail(self.local)
            req.assert_not_called()
            refresh.assert_not_called()
        self.assertIsNone(build(services=[self.local])[0]["score"])

    def test_wrong_detail_and_malformed_xml_are_rejected(self):
        with patch.object(self.client, "request", return_value=detail_xml("wrong")):
            with self.assertRaises(APIError):
                self.client.detail(self.local)
        with patch("welfare_api.urllib.request.urlopen", return_value=io.BytesIO(b"<broken")):
            with self.assertRaises(APIError):
                self.client.request("local")

    def test_http_key_encoding_and_retry_both_reserve_calls(self):
        client = DataClient(self.store, ["a+b/=="], MagicMock())
        with patch("welfare_api.urllib.request.urlopen", side_effect=[TimeoutError(), io.BytesIO(ET.tostring(listing([])))]) as req, \
                patch("welfare_api.time.sleep"):
            client.request("central", pageNo=1)
        parsed = urllib.parse.parse_qs(urllib.parse.urlsplit(req.call_args.args[0]).query)
        self.assertEqual(parsed["serviceKey"], ["a+b/=="])
        self.assertEqual(parsed["callTp"], ["L"])
        self.assertEqual(self.store.remaining("central", ["a+b/=="]), 98)

    def test_missing_key_has_no_network_call(self):
        with patch("welfare_api.urllib.request.urlopen") as req:
            with self.assertRaisesRegex(APIError, "인증 키"):
                DataClient(self.store, [], MagicMock()).request("local")
            req.assert_not_called()

    def test_old_ai_is_only_reused_for_same_source(self):
        svc = next(s for s in load_all(ROOT / "data" / "sample") if (llm.CACHE / f"{s['servId']}.json").exists())
        self.assertIsNotNone(llm.load_cached(svc["servId"], svc))
        self.assertIsNone(llm.load_cached(svc["servId"], {**svc, "criteria": svc["criteria"] + " 변경"}))

    def test_unchanged_revision_reuses_database_snapshot(self):
        self.publish([self.local])
        api = WelfareAPI("mongodb://test")
        with patch.object(WelfareStore, "snapshot", autospec=True,
                side_effect=WelfareStore.snapshot) as snapshot:
            api.catalog()
            api.catalog()
            self.assertEqual(snapshot.call_count, 1)
            self.save(self.local)
            api.catalog()
            self.assertEqual(snapshot.call_count, 2)

    def test_four_schedules_use_korean_time(self):
        midnight = datetime(2026, 11, 17, 15, tzinfo=timezone.utc).timestamp()
        self.assertEqual(sync_slot(midnight - 1), "2026-11-17T18")
        for hour in (0, 6, 12, 18):
            self.assertEqual(sync_slot(midnight + hour * 3600), f"2026-11-18T{hour:02d}")
            self.assertEqual(sync_slot(midnight + (hour + 6) * 3600 - 1), f"2026-11-18T{hour:02d}")

    def test_four_runs_compare_lists_and_share_the_same_daily_budget(self):
        def catalog(client, source):
            self.assertIsNotNone(client.store.reserve_key(source, client.keys))
            return [self.local] if source == "local" else []
        def detail(client, item):
            self.assertIsNotNone(client.store.reserve_key(item["api_source"], client.keys))
            return merge_detail(item, detail_xml())
        with patch.object(DataClient, "catalog", autospec=True, side_effect=catalog) as cat, \
                patch.object(DataClient, "detail", autospec=True, side_effect=detail) as dtl:
            for hour in (0, 6, 12, 18):
                with patch("welfare_sync.sync_slot", return_value=f"{today()}T{hour:02d}"):
                    sync("mongodb://test", self.keys)
                    sync("mongodb://test", self.keys)  # 같은 구간 수동 재실행
            self.assertEqual(cat.call_count, 8)
            self.assertEqual(dtl.call_count, 1)
        self.assertEqual(self.store.remaining("central", self.keys), 296)
        self.assertEqual(self.store.remaining("local", self.keys), 2995)

    def test_next_schedule_does_not_call_api_with_exhausted_daily_budget(self):
        self.publish([self.local])
        self.save(self.local)
        self.store.release("test-owner")
        for source in ("central", "local"):
            for key in self.keys:
                _, day = self.store.reserve_key(source, [key])
                self.store.block_key(source, key, day)
        with patch("welfare_sync.sync_slot", return_value="next-slot"), \
                patch("welfare_api.urllib.request.urlopen") as req:
            result = sync("mongodb://test", self.keys)
        req.assert_not_called()
        self.assertEqual(result["saved"], {"central": 0, "local": 0})
        self.assertTrue(self.store.service("local", "L1")["detail_loaded"])
        self.assertTrue(all(s["status"] == "quota" for s in result["progress"]["sources"].values()))

    def test_web_reads_do_not_initialize_write_or_query_sync_progress(self):
        self.publish([self.local, *items(["L2"])])
        self.save(self.local)
        with patch.object(WelfareStore, "initialize") as initialize, \
                patch.object(WelfareStore, "progress") as progress, \
                patch.object(WelfareStore, "quota") as quota, \
                patch("welfare_sync.sync") as refresh, patch("welfare_api.urllib.request.urlopen") as request:
            api = WelfareAPI("mongodb://test")
            snapshot = api.catalog()
            api.catalog()
            api.detail(self.local)
        self.assertEqual(snapshot["sync"]["sources"]["local"]["ready"], 1)
        self.assertEqual(snapshot["sync"]["sources"]["local"]["total"], 2)
        for mock in (initialize, progress, quota, refresh, request):
            mock.assert_not_called()

    def test_scheduled_command_reports_incomplete_work_as_failure(self):
        for status, expected in (("complete", 0), ("quota", 0), ("error", 1), ("waiting", 1), ("collecting", 1)):
            result = {"status": "finished", "saved": {"central": 0, "local": 0}, "progress": {"sources": {
                s: {"total": 1, "ready": 0, "remaining_calls": 0, "status": status}
                for s in ("central", "local")}}}
            with self.subTest(status=status), patch.dict(os.environ, {"MONGODB_URI": "mongodb://test"}), \
                    patch("welfare_sync.api_keys", return_value=self.keys), patch("welfare_sync.sync", return_value=result), \
                    patch("sys.stdout", new=io.StringIO()):
                self.assertEqual(main(["--seconds", "5400"]), expected)

    def test_scheduled_command_does_not_print_sensitive_exception_details(self):
        output = io.StringIO()
        with patch.dict(os.environ, {"MONGODB_URI": "mongodb://test"}), \
                patch("welfare_sync.api_keys", return_value=self.keys), \
                patch("welfare_sync.sync", side_effect=RuntimeError("private-password")), \
                patch("sys.stdout", new=output):
            self.assertEqual(main([]), 1)
        self.assertNotIn("private-password", output.getvalue())

    def test_database_error_is_redacted(self):
        with self.assertRaises(StoreError) as caught:
            with WelfareStore("mongodb://test"):
                raise ConnectionFailure("mongodb://user:private-password@host")
        self.assertNotIn("private-password", str(caught.exception))

    def test_deferred_failure_is_not_reported_complete(self):
        with patch.object(DataClient, "catalog", side_effect=lambda source: [self.local] if source == "local" else []), \
                patch.object(DataClient, "detail", side_effect=APIError("일시 오류")):
            sync("mongodb://test", self.keys)
        self.assertEqual(self.store.source_state("local")["status"], "error")
        self.store.state("local", "pending")
        sync("mongodb://test", self.keys)
        self.assertEqual(self.store.source_state("local")["status"], "waiting")
        self.assertEqual(self.store.pending("local"), [])
        self.assertEqual(len(self.store.pending("local", include_deferred=True)), 1)


if __name__ == "__main__":
    unittest.main()
