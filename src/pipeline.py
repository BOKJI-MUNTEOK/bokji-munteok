"""원본 → 추출(규칙/AI) → 점수 표. 화면과 명령줄이 같이 쓴다.

명령줄:
  python src/pipeline.py                   API 목록 건수·상세 캐시의 점수 상위 10개
  python src/pipeline.py --sample          고정 샘플의 규칙 계산 재현
  python src/pipeline.py --sample --ai     샘플 AI 결과 생성(.env의 LLM_API_KEY 필요)
  python src/pipeline.py --sample --template eval/gold_template.csv   검증용 빈 정답표 만들기
"""
import sys
from pathlib import Path

import evaluate
import llm
import rules
import score
from analysis_cache import features, weighted_score
from parse import load_all


def build(weights: dict | None = None, mode: str = "규칙", services: list[dict] | None = None) -> list[dict]:
    if services is None:
        from welfare_api import WelfareAPI
        api = WelfareAPI()
        services = api.services(api.catalog())
    rows = []
    for s in services:
        if not s.get("detail_loaded", True):
            rows.append({"svc": s, "score": None, "ai": None, "ext": None,
                         "reason": "상세 조회 후 문턱 점수를 계산합니다."})
            continue
        ai = llm.load_cached(s["servId"], s)
        use_ai = bool(mode == "AI" and ai)
        ext, read = features(s, rule_required=not use_ai)
        if use_ai:
            ext = ai["extract"]
        sc = weighted_score(ext, read, weights)
        rows.append({"svc": s, "ext": ext, "ext_source": "AI" if (mode == "AI" and ai) else "규칙",
                     "read": read, "ai": ai, **sc})
    rows.sort(key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    return rows


def main(argv):
    # 고정 샘플은 --sample을 명시한 경우에만 쓴다.
    if "--sample" in argv:
        services = load_all(Path(__file__).resolve().parent.parent / "data" / "sample")
    else:
        from welfare_api import WelfareAPI
        api = WelfareAPI()
        catalog = api.catalog()
        services = api.services(catalog)
        print("공식 API 목록:", len(services), "건 / 상세 조회 완료:", sum(s["detail_loaded"] for s in services), "건")
    if "--template" in argv:
        path = argv[argv.index("--template") + 1]
        evaluate.write_template(services, Path(path))
        print("정답표 틀 저장:", path)
        return
    if "--ai" in argv:
        services = [s for s in services if s.get("detail_loaded", True)]
        from concurrent.futures import ThreadPoolExecutor
        force = "--force" in argv

        def one(s):
            try:
                out = llm.run(s, force=force)
                return f"{s['servNm']}: " + ("사람 확인 필요 " + str(out["check"]["missing"]) if out["needs_review"] else "숫자 OK")
            except Exception as e:  # 한 건 실패해도 나머지는 계속
                return f"{s['servNm']}: 실패 {type(e).__name__}: {str(e)[:120]}"

        with ThreadPoolExecutor(5) as ex:
            for i, msg in enumerate(ex.map(one, services), 1):
                print(f"[{i}/{len(services)}] {msg}", flush=True)
        return
    for r in [r for r in build(services=services) if r["score"] is not None][:10]:
        print(r["score"], r["svc"]["servNm"], "-", r["reason"])


if __name__ == "__main__":
    main(sys.argv[1:])
