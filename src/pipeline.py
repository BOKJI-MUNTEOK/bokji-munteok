"""원본 → 추출(규칙/AI) → 점수 표. 화면과 명령줄이 같이 쓴다.

명령줄:
  python src/pipeline.py            규칙 점수 상위 10개 출력
  python src/pipeline.py --ai       AI 추출·쉬운 설명을 만들어 data/ai_cache에 저장(.env의 LLM_API_KEY 필요)
  python src/pipeline.py --template eval/gold_template.csv   검증용 빈 정답표 만들기
"""
import sys

import evaluate
import llm
import rules
import score
from parse import load_all


def build(weights: dict | None = None, mode: str = "규칙", services: list[dict] | None = None) -> list[dict]:
    services = services or load_all()
    rows = []
    for s in services:
        ai = llm.load_cached(s["servId"])
        ext = ai["extract"] if (mode == "AI" and ai) else rules.extract(s)
        read = score.readability(s)
        sc = score.score(ext, read, weights)
        rows.append({"svc": s, "ext": ext, "ext_source": "AI" if (mode == "AI" and ai) else "규칙",
                     "read": read, "ai": ai, **sc})
    rows.sort(key=lambda r: -r["score"])
    return rows


def main(argv):
    services = load_all()
    if "--template" in argv:
        path = argv[argv.index("--template") + 1]
        evaluate.write_template(services, __import__("pathlib").Path(path))
        print("정답표 틀 저장:", path)
        return
    if "--ai" in argv:
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
    for r in build(services=services)[:10]:
        print(r["score"], r["svc"]["servNm"], "-", r["reason"])


if __name__ == "__main__":
    main(sys.argv[1:])
