"""MongoDB에 공식 목록, 상세·분석 결과, 갱신 잠금과 일일 호출량을 저장한다."""
import hashlib
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache

from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

KST = timezone(timedelta(hours=9))
LIMITS = {"central": 100, "local": 1000}
LEASE_SECONDS = 300


def today(now=None):
    return datetime.fromtimestamp(time.time() if now is None else now, KST).date().isoformat()


def key_id(key):
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def detail_id(item):
    return f"{item['api_source']}:{item['servId']}:{item['list_hash']}"


class StoreError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def mongo_client(uri):
    # 프로세스 안에서 연결 풀을 공유한다. 재배포 후에도 데이터는 Atlas에 남는다.
    return MongoClient(uri, appname="bokji-munteok", maxPoolSize=16,
                       serverSelectionTimeoutMS=10000, connectTimeoutMS=10000,
                       socketTimeoutMS=30000)


def initial_source():
    return dict(generation="", previous_generation="", catalog_day="", catalog_at=0,
                run_day="", status="pending", retry_at=0, note="", added=0, changed=0, removed=0)


class WelfareStore:
    def __init__(self, uri, database=None):
        self.uri = uri
        self.database = database or os.environ.get("MONGODB_DATABASE", "bokji_munteok")
        self.db = None
        self.catalog_cache = {}

    def __enter__(self):
        if not self.uri:
            raise StoreError("복지 데이터 DB가 설정되지 않았어요. 운영자는 MONGODB_URI를 확인해 주세요.")
        try:
            self.db = mongo_client(self.uri)[self.database]
        except (PyMongoError, ValueError):
            raise StoreError("MongoDB 연결 설정을 확인해 주세요.") from None
        return self

    def __exit__(self, kind, value, traceback):
        # 드라이버 예외의 URI/호스트/인증 정보를 화면이나 로그에 노출하지 않는다.
        if kind is not None and issubclass(kind, PyMongoError):
            raise StoreError("복지 데이터 DB에 연결하지 못했어요. 잠시 후 다시 확인해 주세요.") from None

    def initialize(self):
        self.db.catalog.create_index([("source", 1), ("generation", 1)])
        self.db.details.create_index([("source", 1), ("serv_id", 1)])
        self.db.quota.create_index([("day", 1), ("source", 1)])
        try:
            self.db.meta.update_one({"_id": "sync"}, {"$setOnInsert": {
                "revision": 0, "owner": "", "expires": 0,
                "sources": {s: initial_source() for s in LIMITS}}}, upsert=True)
        except DuplicateKeyError:
            pass

    def acquire(self, owner, now=None):
        now = time.time() if now is None else now
        return self.db.meta.find_one_and_update(
            {"_id": "sync", "$or": [{"expires": {"$lte": now}}, {"owner": owner}]},
            {"$set": {"owner": owner, "expires": now + LEASE_SECONDS}}) is not None

    def renew(self, owner):
        now = time.time()
        if self.db.meta.update_one({"_id": "sync", "owner": owner, "expires": {"$gt": now}},
                                   {"$set": {"expires": now + LEASE_SECONDS}}).matched_count != 1:
            raise StoreError("다른 갱신 작업이 이어받았습니다.")

    def release(self, owner):
        self.db.meta.update_one({"_id": "sync", "owner": owner}, {"$set": {"owner": "", "expires": 0}})

    def source_state(self, source):
        return self.db.meta.find_one({"_id": "sync"})["sources"][source]

    def state(self, source, status, note="", retry_at=0):
        values = dict(run_day=today(), status=status, note=note, retry_at=retry_at)
        self.db.meta.update_one({"_id": "sync"}, {"$set": {f"sources.{source}.{k}": v for k, v in values.items()}})

    def _catalog(self, source, state=None):
        state = self.source_state(source) if state is None else state
        generation = state["generation"]
        if not generation:
            return []
        if generation not in self.catalog_cache:
            self.catalog_cache[generation] = [d["item"] for d in self.db.catalog.find(
                {"source": source, "generation": generation}, {"item": 1}).sort("serv_id", 1)]
        return self.catalog_cache[generation]

    def publish_catalog(self, source, items, owner):
        """전체 목록 저장 후 공개 포인터를 원자적으로 교체한다.

        페이지 실패/저장 중단 때 기존 목록을 유지하고, 완성된 목록에서 빠진 항목만
        비활성화한다. 단일 문서 갱신이라 수천 건짜리 트랜잭션이 필요 없다.
        """
        state = self.source_state(source)
        old = {s["servId"]: s["list_hash"] for s in self._catalog(source, state)}
        new = {s["servId"]: s["list_hash"] for s in items}
        if len(new) != len(items) or any(s["api_source"] != source for s in items):
            raise StoreError("목록의 서비스 식별 정보가 올바르지 않습니다.")
        changes = dict(added=len(new.keys() - old.keys()), removed=len(old.keys() - new.keys()),
                       changed=sum(old[sid] != new[sid] for sid in old.keys() & new.keys()))
        generation, now = uuid.uuid4().hex, time.time()
        for offset in range(0, len(items), 500):
            self.renew(owner)
            self.db.catalog.insert_many([
                {"_id": f"{generation}:{s['servId']}", "source": source, "serv_id": s["servId"],
                 "generation": generation, "detail_ref": detail_id(s), "created_at": now, "item": s}
                for s in items[offset:offset + 500]])
        values = dict(generation=generation, previous_generation=state["generation"], catalog_day=today(),
                      catalog_at=now, run_day=today(), status="collecting", note="", retry_at=0, **changes)
        result = self.db.meta.update_one(
            {"_id": "sync", "owner": owner, "expires": {"$gt": time.time()}},
            {"$set": {f"sources.{source}": values}, "$inc": {"revision": 1}})
        if result.matched_count != 1:
            raise StoreError("갱신 잠금이 만료되어 기존 목록을 유지합니다.")
        # 현재/직전 세대와 최근 이틀 자료를 유지하고 중단된 미공개 세대도 정리한다.
        self.db.catalog.delete_many({"source": source, "generation": {"$nin": [generation, state["generation"]]},
                                     "created_at": {"$lt": now - 2 * 86400}})
        self.catalog_cache = {generation: items}
        return changes

    def _ready(self, source):
        return {d["_id"] for d in self.db.details.find({"source": source}, {"_id": 1})}

    def pending(self, source, limit=24, include_deferred=False):
        ready = self._ready(source)
        deferred = set() if include_deferred else {d["_id"] for d in self.db.retries.find(
            {"source": source, "until": {"$gt": time.time()}}, {"_id": 1})}
        skipped = ready | deferred
        candidates = [s for s in self._catalog(source) if detail_id(s) not in skipped]
        known = {d["serv_id"] for d in self.db.details.find({"source": source}, {"serv_id": 1})}
        return sorted(candidates, key=lambda s: (s["servId"] not in known, s["servId"]))[:limit]

    def failed_item(self, item):
        self.db.retries.update_one({"_id": detail_id(item)}, {"$set": {
            "source": item["api_source"], "until": time.time() + 3600}}, upsert=True)

    def save_detail(self, item, detail):
        import rules
        import score
        ext, read = rules.extract(detail), score.readability(detail)
        analysis = {"ext": ext, "read": read, **score.score(ext, read), "weights": score.DEFAULT_WEIGHTS}
        # 목록 해시별 저장: 늦게 끝난 응답이 새 상세를 덮어쓰지 않는다.
        result = self.db.details.update_one({"_id": detail_id(item)}, {"$setOnInsert": {
            "source": item["api_source"], "serv_id": item["servId"], "list_hash": item["list_hash"],
            "detail": detail, "analysis": analysis, "fetched_at": time.time()}}, upsert=True)
        if result.upserted_id:
            self.db.meta.update_one({"_id": "sync"}, {"$inc": {"revision": 1}})
        return result.upserted_id is not None

    def quota(self, source, keys, day=None):
        usage = {d["key_id"]: d for d in self.db.quota.find({"day": day or today(), "source": source})}
        return {key_id(key): usage.get(key_id(key), {"used": 0, "blocked": False}) for key in set(keys)}

    def remaining(self, source, keys):
        return sum(0 if r["blocked"] else max(0, LIMITS[source] - r["used"])
                   for r in self.quota(source, keys).values())

    def reserve_key(self, source, keys):
        """요청 직전에 1회를 원자적으로 예약한다. 재시도도 별도 호출이다."""
        day = today()
        usage = self.quota(source, keys, day)
        for key in sorted(set(keys), key=lambda key: usage[key_id(key)]["used"]):
            kid = key_id(key)
            identity = f"{day}:{source}:{kid}"
            if "_id" not in usage[kid]:
                try:
                    self.db.quota.insert_one(dict(_id=identity, day=day, source=source,
                                                  key_id=kid, used=0, blocked=False))
                except DuplicateKeyError:
                    pass
            if self.db.quota.find_one_and_update(
                    {"_id": identity, "blocked": False, "used": {"$lt": LIMITS[source]}},
                    {"$inc": {"used": 1}}, return_document=ReturnDocument.AFTER):
                return key, day
        return None

    def block_key(self, source, key, day):
        self.db.quota.update_one({"_id": f"{day}:{source}:{key_id(key)}"}, {"$set": {"blocked": True}})

    def progress(self, keys=()):
        meta = self.db.meta.find_one({"_id": "sync"})
        result = {"revision": meta["revision"], "running": meta["expires"] > time.time(), "sources": {}}
        for source, state in meta["sources"].items():
            # 30초 폴링에서 수천 건의 원문을 내려받지 않고 DB에서 건수만 집계한다.
            counts = list(self.db.catalog.aggregate([
                {"$match": {"source": source, "generation": state["generation"]}},
                {"$lookup": {"from": self.db.details.name, "localField": "detail_ref",
                             "foreignField": "_id", "as": "stored"}},
                {"$group": {"_id": None, "total": {"$sum": 1}, "ready": {"$sum": {
                    "$cond": [{"$gt": [{"$size": "$stored"}, 0]}, 1, 0]}}}},
            ], maxTimeMS=15000))
            count = counts[0] if counts else {"total": 0, "ready": 0}
            result["sources"][source] = {**state, "total": count["total"], "ready": count["ready"],
                                         "remaining_calls": self.remaining(source, keys)}
        return result

    def revision(self):
        return self.db.meta.find_one({"_id": "sync"}, {"revision": 1})["revision"]

    def needs_sync(self, keys):
        for source in LIMITS:
            state = self.source_state(source)
            if state["retry_at"] > time.time() or not self.remaining(source, keys):
                continue
            if state["catalog_day"] != today() or self.pending(source, 1):
                return True
        return False

    @staticmethod
    def _service(item, detail):
        if not detail:
            return item
        merged = {**item, **detail["detail"], "detail_fetched_at": detail["fetched_at"],
                  "stored_analysis": detail["analysis"]}
        for field in ("life", "groups", "themes", "region", "sido", "sgg", "link", "online_flag"):
            merged[field] = item[field]
        return merged

    def service(self, source, sid):
        state = self.source_state(source)
        row = self.db.catalog.find_one({"source": source, "generation": state["generation"], "serv_id": sid})
        return self._service(row["item"], self.db.details.find_one({"_id": detail_id(row["item"])})) if row else None

    def snapshot(self, keys=()):
        meta = self.db.meta.find_one({"_id": "sync"})
        services, counts = [], {}
        for source, state in meta["sources"].items():
            items = self._catalog(source, state)
            refs = [detail_id(s) for s in items]
            details = {d["_id"]: d for d in self.db.details.find({"_id": {"$in": refs}})}
            services.extend(self._service(s, details.get(detail_id(s))) for s in items)
            counts[source] = len(items)
        # 복사 중 저장된 상세도 다음 폴링에서 갱신되도록 읽기 시작 시의 revision을 쓴다.
        progress = self.progress(keys)
        progress["revision"] = meta["revision"]
        states = meta["sources"].values()
        return {"services": services, "counts": counts,
                "updated_at": min((s["catalog_at"] for s in states), default=0), "sync": progress,
                "warnings": [s["note"] for s in states if s["note"]]}
