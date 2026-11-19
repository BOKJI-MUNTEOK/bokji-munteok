"""매일 한국시간 04시 증분 수집. DB 잠금·일일 호출 예산·진행 지점으로 재개한다."""
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from env import load_env
from welfare_api import APIError, DataClient, QuotaExceeded, RequestGate, api_keys
from welfare_store import StoreError, WelfareStore, sync_slot, today


def sync(database_url, keys, max_seconds=210, progress=None):
    owner = uuid.uuid4().hex
    started = time.monotonic()
    slot = sync_slot()
    gate = RequestGate()
    saved = {"central": 0, "local": 0}
    with WelfareStore(database_url) as store:
        if not store.acquire(owner):
            return {"status": "already_running", "saved": saved}
        try:
            client = DataClient(store, keys, gate, owner)
            for source in ("central", "local"):
                state = store.source_state(source)
                if (state["catalog_day"] == today() and state.get("catalog_slot") == slot) or state["retry_at"] > time.time():
                    continue
                try:
                    store.state(source, "listing")
                    items = client.catalog(source)
                    store.publish_catalog(source, items, owner, slot)
                except QuotaExceeded as exc:
                    store.state(source, "quota", str(exc))
                except APIError as exc:
                    store.state(source, "error", str(exc), time.time() + 300)

            def one(item):
                with WelfareStore(database_url) as worker:
                    try:
                        detail = DataClient(worker, keys, gate, owner).detail(item)
                        return ("saved" if worker.save_detail(item, detail) else "changed"), ""
                    except QuotaExceeded as exc:
                        return "quota", str(exc)
                    except APIError as exc:
                        worker.failed_item(item)
                        return "error", str(exc)

            with ThreadPoolExecutor(max_workers=4) as pool:
                while time.monotonic() - started < max_seconds:
                    worked = False
                    for source in ("central", "local"):
                        state = store.source_state(source)
                        if state.get("catalog_slot") != slot or state["catalog_day"] != today() or state["retry_at"] > time.time():
                            continue
                        remaining = store.remaining(source, keys)
                        items = store.pending(source, min(24, max(1, remaining)))
                        if not items:
                            deferred = store.pending(source, 1, include_deferred=True)
                            store.state(source, "waiting" if deferred else "complete",
                                        "조회에 실패한 상세는 잠시 후 다시 확인합니다." if deferred else "")
                            continue
                        if remaining <= 0:
                            label = "중앙부처" if source == "central" else "지자체"
                            store.state(source, "quota", f"{label} 오늘 호출 한도에 도달했습니다. 다음날 예약 갱신에서 이어서 수집합니다.")
                            continue
                        worked = True
                        store.renew(owner)
                        store.state(source, "collecting")
                        results = list(pool.map(one, items))
                        saved[source] += sum(status == "saved" for status, _ in results)
                        errors = [(status, msg) for status, msg in results if status in ("quota", "error")]
                        if errors:
                            status, message = errors[0]
                            store.state(source, status, message, time.time() + 300 if status == "error" else 0)
                        if progress:
                            progress(store.progress(keys))
                        if time.monotonic() - started >= max_seconds:
                            break
                    if not worked:
                        break
            return {"status": "finished", "saved": saved, "progress": store.progress(keys)}
        finally:
            store.release(owner)


def main(argv=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(description="복지 API 변경분과 미수집 상세를 일일 한도 안에서 DB에 저장")
    parser.add_argument("--seconds", type=int, default=210)
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 5400:
        parser.error("--seconds는 1~5400 사이여야 합니다.")
    load_env()
    url = os.environ.get("MONGODB_URI", "")
    keys = api_keys()
    if not url or not keys:
        print("MongoDB 연결 정보와 공공데이터 API 키를 설정해 주세요.", flush=True)
        return 1

    def report(state):
        print(json.dumps({s: {k: v[k] for k in ("total", "ready", "remaining_calls", "status")}
                          for s, v in state["sources"].items()}, ensure_ascii=False), flush=True)

    try:
        with WelfareStore(url) as store:
            store.initialize()
        result = sync(url, keys, args.seconds, report)
    except (APIError, StoreError) as exc:
        print(str(exc), flush=True)
        return 1
    except Exception as exc:
        # 드라이버·네트워크 예외 본문에 인증 정보가 포함될 수 있다.
        print(f"예약 갱신 중단: {type(exc).__name__}", flush=True)
        return 1
    print(json.dumps({"status": result["status"], "saved": result["saved"]}, ensure_ascii=False), flush=True)
    if result["status"] == "already_running":
        return 0
    report(result["progress"])
    incomplete = any(s["status"] not in ("complete", "quota") for s in result["progress"]["sources"].values())
    if incomplete:
        print("갱신이 일부 완료되지 않았습니다. 저장 자료는 유지하며 다음 예약 실행에서 이어받습니다.", flush=True)
    return int(incomplete)


if __name__ == "__main__":
    raise SystemExit(main())
