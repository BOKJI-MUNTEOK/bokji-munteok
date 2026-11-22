"""고정 샘플·정답표·기존 AI 캐시로 비교 보고서를 만든다. 네트워크 호출 없음.

실행: python src/sample_report.py [--output-dir eval/results]
"""
import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median

import evaluate
import llm
import numcheck
import rules
import score
from parse import ROOT, load_all


def build_report(services: list[dict] | None = None) -> dict:
    services = load_all(ROOT / "data" / "sample") if services is None else services
    gold = evaluate.load_gold()
    original_hashes = llm.sample_hashes()
    predictions, cases, checks, scores = {}, [], [], []
    eligible = {}
    models = Counter()
    attempts = Counter()
    for svc in sorted(services, key=lambda s: s["servId"]):
        sid = svc["servId"]
        rule = rules.extract(svc)
        rule_summary = evaluate.summarize(rule)
        cached = llm.load_cached(sid, svc)
        ai_summary = evaluate.summarize(cached["extract"]) if cached else None
        predictions[sid] = {"rule": rule_summary, "ai": ai_summary}
        if sid in gold and original_hashes.get(sid) == llm.source_hash(svc):
            eligible[sid] = gold[sid]
        sc = score.score(rule, score.readability(svc))
        scores.append({"servId": sid, "service": svc["servNm"], "source": svc["src"], "score": sc["score"]})
        for field in evaluate.FIELDS:
            cases.append({"servId": sid, "service": svc["servNm"], "source": svc["src"],
                          "field": field,
                          "gold": eligible.get(sid, {}).get(field), "rule": rule_summary[field],
                          "ai": ai_summary[field] if ai_summary else None,
                          "gold_eligible": sid in eligible, "ai_available": cached is not None})
        if cached:
            # 저장된 판정 플래그 대신 현재 숫자 검사로 다시 계산한다.
            chk = numcheck.check(llm.source_text(svc), llm.easy_text(cached["easy"]))
            checks.append({"servId": sid, **chk})
            models[cached.get("model", "unknown")] += 1
            attempts[str(cached.get("attempts", "unknown"))] += 1

    # AI 누락·원문 불일치는 규칙으로 대신하지 않는다. 두 방법을 같은 서비스로 비교한다.
    shared = {sid: value for sid, value in eligible.items() if predictions[sid]["ai"] is not None}
    cohorts = {"전체": {"gold_n": len(eligible), "compared_n": len(shared),
                        "excluded_ai_ids": sorted(set(eligible) - set(shared)),
                        **{method: evaluate.compare({sid: predictions[sid][method] for sid in shared}, shared)
                           for method in ("rule", "ai")}}}

    sources = Counter(s["src"] for s in services)
    input_paths = [*sorted((ROOT / "data" / "sample").glob("*.xml")),
                   *sorted((ROOT / "data" / "ai_cache").glob("*.json")),
                   *sorted(evaluate.EVAL.glob("gold*.csv"))]
    inputs = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in input_paths}
    score_summary = {}
    for name in ("전체", *sorted(sources)):
        vals = [s["score"] for s in scores if name == "전체" or s["source"] == name]
        score_summary[name] = {"n": len(vals), "mean": round(mean(vals), 2) if vals else None,
                               "median": median(vals) if vals else None,
                               "min": min(vals) if vals else None, "max": max(vals) if vals else None}
    return {"dataset": "2026-11-10 고정 샘플 (운영 전체 데이터 통계 아님)",
            "sample_n": len(services), "sources": dict(sources), "gold_n": len(gold),
            "eligible_gold_n": len(eligible), "ai_n": sum(v["ai"] is not None for v in predictions.values()),
            "cohorts": cohorts, "score_weights": score.DEFAULT_WEIGHTS, "score_caps": score.CAPS,
            "score_summary": score_summary, "scores": scores, "models": dict(sorted(models.items())),
            "generation_attempts": dict(sorted(attempts.items())),
            "number_check": {"n": len(checks), "passed": sum(c["ok"] for c in checks),
                             "total_numbers": sum(c["total"] for c in checks),
                             "kept_numbers": sum(c["kept"] for c in checks), "details": checks},
            "cases": cases, "input_sha256": inputs}


def display(value) -> str:
    return "정보 없음" if value is None else str(value)


def markdown(report: dict) -> str:
    sources = " · ".join(f"{name} {n}건" for name, n in report["sources"].items())
    lines = ["# 고정 샘플 분석·검증 결과", "", f"대상: {report['dataset']}. {sources}을 보관한 편의 표본입니다.",
             "운영 API 전체 목록과 통계 범위를 구분합니다. 대표 표본이나 실제 신청 난이도에 대한 검증이 아닙니다.", "",
             "재현: `python src/sample_report.py` (API 키·DB·유료 AI 호출 불필요).", "",
             "## 비교 방법과 한계", "",
             "- 보관 원문·정답표·원문이 일치하는 AI 캐시가 있는 동일 서비스만 규칙과 AI 양쪽에 씁니다.",
             "- 일치율은 개수까지 정확히 같은 비율입니다. 서류의 `정보 없음`끼리는 일치하며, `0개`와 구분합니다. 서류 이름의 정확도를 뜻하지 않습니다.",
             "- 평균 절대 오차(MAE)는 양쪽에 숫자가 있는 쌍만 계산하고 그 분모를 표시합니다. 정보 없음은 0으로 대체하지 않습니다.",
             "- 서류 수 MAE는 방법별로 숫자 쌍의 분모가 다를 수 있어 직접적인 우열 비교에 쓰지 않습니다. 정보 없음 판단은 일치율과 불일치 목록에서 함께 확인합니다.",
             "- AI 모델명은 캐시에 기록된 값입니다. 전체 서비스로의 일반화 검증은 남아 있습니다.", ""]
    for name, cohort in report["cohorts"].items():
        if not cohort["gold_n"]:
            continue
        lines.extend([f"## {name} 비교", "", f"정답표 {cohort['gold_n']}건 중 같은 표본 {cohort['compared_n']}건 비교. AI 캐시 누락·원문 불일치 제외 {len(cohort['excluded_ai_ids'])}건.", "",
                      "| 항목 | 규칙 일치율 (%) | AI 일치율 (%) | 규칙 MAE (건수) | AI MAE (건수) |",
                      "| --- | ---: | ---: | ---: | ---: |"])
        for field in evaluate.FIELDS:
            r, a = cohort["rule"], cohort["ai"]
            mae = lambda m: f"{display(m[field + '_mae'])} ({m[field + '_mae_n']})" if field.startswith("n_") else "—"
            lines.append(f"| {evaluate.LABELS[field]} | {display(r[field])} | {display(a[field])} | {mae(r)} | {mae(a)} |")
        lines.append("")
    lines.extend(["## 문턱 점수 분포", "", "확정 가중치와 상한으로 계산한 규칙 점수입니다. 서류 정보 없음은 점수에 가산하지 않으므로 실제 부담을 과소평가할 수 있습니다.", "",
                  "| 구분 | 건수 | 평균 | 중앙값 | 최솟값 | 최댓값 |", "| --- | ---: | ---: | ---: | ---: | ---: |"])
    for name, row in report["score_summary"].items():
        lines.append(f"| {name} | {row['n']} | {display(row['mean'])} | {display(row['median'])} | {display(row['min'])} | {display(row['max'])} |")
    chk = report["number_check"]
    lines.extend(["", "## 쉬운 설명 숫자 검사", "",
                  f"기존 캐시 {chk['n']}건 중 {chk['passed']}건 통과. 서비스별로 중복 제거한 원문의 단위 포함 숫자 {chk['total_numbers']}개 중 {chk['kept_numbers']}개가 설명에 유지됐습니다.",
                  f"캐시 모델명: {json.dumps(report['models'], ensure_ascii=False)}. 생성 시도 횟수별 건수: {json.dumps(report['generation_attempts'], ensure_ascii=False)}.",
                  "숫자 유지 검사 통과는 문장 의미·자격 판정·서류의 정확성을 보장하지 않습니다. 최초 시도 답변은 저장돼 있지 않아 그 내용은 재검증하지 않았습니다.", "",
                  "## 해석과 다음 작업", "",
                  "방문 필수 판단과 조건 수의 불일치 원문을 `mismatches.csv`에서 검토합니다. 이 결과만으로 AI 우수성을 단정하지 않습니다.",
                  "2026-11-22 방문 필수 규칙을 정답표 기준(방문 말고 다른 신청 수단이 없을 때만)으로 고쳐 같은 표본으로 다시 비교했습니다(이전 48%). 이 50건의 불일치를 보고 고쳤으므로 새 표본의 정확도는 아닙니다. 조건 수는 점수에 조건 범주 수를 쓰고 정답표는 요건 수를 세므로 일치율이 낮습니다. 가중치 확정 근거는 [가중치 민감도 점검](weight_check.md)에 있습니다.", "",
                  "## 산출물", "",
                  "- `summary.json`: 입력 SHA-256, 점수식 설정, 분모·지표·숫자 검사 내역.",
                  "- `predictions.csv`: 서비스·항목별 정답/규칙/AI와 비교 가능 여부. 빈 값은 정보 없음입니다.",
                  "- `mismatches.csv`: 양쪽 비교가 가능한 정답표에서 하나라도 불일치한 항목. 원문은 `data/sample/`, 작성 기준은 `eval/README.md`.", ""])
    return "\n".join(lines)


def artifacts(report: dict) -> dict[str, str]:
    out = {"report.md": markdown(report), "summary.json": json.dumps(report, ensure_ascii=False, indent=2) + "\n"}
    fields = ["servId", "service", "source", "field", "gold", "rule", "ai", "gold_eligible", "ai_available"]
    for name in ("predictions.csv", "mismatches.csv"):
        buf = io.StringIO(newline="")
        writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in report["cases"]:
            if name == "mismatches.csv" and (not row["gold_eligible"] or not row["ai_available"] or
                                              row["gold"] == row["rule"] == row["ai"]):
                continue
            writer.writerow(row)
        out[name] = "\ufeff" + buf.getvalue()
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "eval" / "results")
    args = parser.parse_args()
    report = build_report()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, content in artifacts(report).items():
        (args.output_dir / name).write_bytes(content.encode("utf-8"))
    print(f"샘플 {report['sample_n']}건 / 동일 표본 비교 {report['cohorts']['전체']['compared_n']}건 → {args.output_dir}")


if __name__ == "__main__":
    main()
