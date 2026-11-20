"""운영 DB의 모든 첨부파일을 내려받아 내용으로 서류/참고 자료를 판정하고 data/attachment_kinds.json에 저장한다.

실행: python scripts/build_attachment_kinds.py [--cache 폴더]
--cache를 주면 내려받은 파일을 그 폴더에 두고 다시 쓴다(키: URL sha1 앞 16자).
판정 기준은 document_workspace.decide_kind 참고. 표에 없는 새 첨부는 화면에서 이름 규칙으로 판단한다.
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def classify(args):
    path, name = args
    import document_workspace as dw
    if not path:
        return None
    raw = Path(path).read_bytes()
    try:
        content, pages = dw.content_check(raw, name)
    except Exception:
        content, pages = "unsupported", None
    return dw.decide_kind(name, content, pages=pages, size_kb=len(raw) / 1024), content


def main():
    import document_workspace as dw
    import packet
    from welfare_api import WelfareAPI
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=ROOT / ".cache" / "attachments")
    args = ap.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    items = {}
    for svc in WelfareAPI().catalog()["services"]:
        for f in svc.get("form_files") or []:
            items.setdefault(f["url"], f["name"])

    def fetch(url):
        path = args.cache / dw.attachment_key(url)
        if not path.exists():
            try:
                path.write_bytes(packet.download(url))
            except Exception:
                return None
        return str(path)
    urls = list(items)
    with ThreadPoolExecutor(12) as ex:
        paths = list(ex.map(fetch, urls))
    with ProcessPoolExecutor(max(2, (os.cpu_count() or 4) - 1)) as ex:
        results = list(ex.map(classify, [(p, items[u]) for p, u in zip(paths, urls)], chunksize=8))
    kinds, stats = {}, {}
    for url, res in zip(urls, results):
        if res is None:
            continue
        kind, content = res
        kinds[dw.attachment_key(url)] = kind
        stats[f"{kind}/{content}"] = stats.get(f"{kind}/{content}", 0) + 1
    dw.ATTACHMENT_KINDS_PATH.write_text(json.dumps(
        {"version": 1, "built_at": time.strftime("%Y-%m-%d"), "count": len(kinds), "stats": stats, "kinds": kinds},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(len(kinds), stats)


if __name__ == "__main__":
    main()
