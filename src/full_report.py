"""운영 DB 전체 자료로 문턱 점수 분석 보고서를 만든다. 앱 분석 탭과 같은 계산(pipeline.build, 기본 가중치).

실행: python src/full_report.py [--output-dir eval/results/full]
필요: MONGODB_URI(읽기 권한이면 충분). 외부 API·유료 AI 호출 없음. DB에 쓰지 않는다.
"""
import argparse
import csv
import io
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median, pstdev

import packet
import score
from parse import ROOT
from pipeline import build

KST = timezone(timedelta(hours=9))
BANDS = [("낮음", 0, 40), ("보통", 40, 60), ("높음", 60, 101)]  # app.py BANDS와 같은 경계
MIN_GROUP = 30  # 이보다 적은 집단은 평균을 비교하지 않는다


def band(s: int) -> str:
    return next(name for name, lo, hi in BANDS if lo <= s < hi)


def stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    q = sorted(vals)
    pick = lambda p: q[min(len(q) - 1, int(p * len(q)))]
    bands = Counter(band(v) for v in vals)
    return {"n": len(vals), "mean": round(mean(vals), 1), "median": float(median(vals)), "sd": round(pstdev(vals), 1),
            "q1": pick(0.25), "q3": pick(0.75), "min": q[0], "max": q[-1],
            **{f"band_{b}": round(100 * bands[b] / len(vals), 1) for b, _, _ in BANDS}}


def scaled_parts(r: dict) -> dict:
    """항목 점수를 문턱 점수와 같은 100점 환산으로 맞춘다(앱 분석 탭과 같은 방식)."""
    return {k: (v * r["score"] / r["raw"] if r["raw"] else 0) for k, v in r["parts"].items()}


def summarize(rows: list[dict], counts: dict, updated_at: float, now: datetime) -> dict:
    scored = [r for r in rows if r["score"] is not None]
    by_src = defaultdict(list)
    for r in scored:
        by_src[r["svc"]["src"]].append(r)
    items = list(scored[0]["parts"]) if scored else []

    def part_avg(group):
        sp = [scaled_parts(r) for r in group]
        return {k: round(mean(p[k] for p in sp), 2) for k in items} if sp else {}

    def facts(group):
        n = len(group) or 1
        docs = [r for r in group if r["ext"].get("documents")]
        return {"온라인 신청 불가(%)": round(100 * sum(not r["ext"]["online"] for r in group) / n, 1),
                "방문 필수(%)": round(100 * sum(bool(r["ext"]["visit_required"]) for r in group) / n, 1),
                "평균 기관 단계": round(mean(len(r["ext"]["agencies"]) for r in group), 2) if group else None,
                "평균 조건 수": round(mean(len(r["ext"]["conditions"]) for r in group), 2) if group else None,
                "서류 정보 있음(%)": round(100 * len(docs) / n, 1),
                "안내문 어려움 2점 이상(%)": round(100 * sum(r["read"]["points"] >= 2 for r in group) / n, 1)}

    def grouped(key, group, multi=False):
        g = defaultdict(list)
        for r in group:
            vals = r["svc"].get(key) or []
            for v in (vals if multi else [vals]):
                if v:
                    g[v].append(r["score"])
        out = [{"이름": k, **stats(v)} for k, v in g.items() if len(v) >= MIN_GROUP]
        return sorted(out, key=lambda x: -x["mean"])

    with_form = [r for r in scored if any(packet.is_form(f.get("name", "")) for f in r["svc"].get("form_files") or [])]
    form_ids = {id(r) for r in with_form}
    top = [{"서비스": r["svc"]["servNm"], "구분": r["svc"]["src"], "지역": r["svc"].get("region") or "전국",
            "점수": r["score"], "이유": r["reason"]} for r in scored[:10]]
    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M KST"),
        "catalog_updated_at": datetime.fromtimestamp(updated_at, KST).strftime("%Y-%m-%d %H:%M KST") if updated_at else None,
        "list_counts": counts, "services": len(rows), "scored": len(scored), "unscored": len(rows) - len(scored),
        "weights": score.DEFAULT_WEIGHTS, "caps": score.CAPS,
        "overall": stats([r["score"] for r in scored]),
        "by_source": {k: {**stats([r["score"] for r in v]), "parts": part_avg(v), "facts": facts(v)} for k, v in sorted(by_src.items())},
        "parts_all": part_avg(scored), "facts_all": facts(scored),
        "histogram": dict(sorted(Counter(r["score"] // 5 * 5 for r in scored).items())),
        "by_sido": grouped("sido", by_src.get("지자체", [])),
        "by_life": grouped("life", scored, multi=True),
        "by_group": grouped("groups", scored, multi=True),
        "forms": {"with_form": stats([r["score"] for r in with_form]),
                  "without_form": stats([r["score"] for r in scored if id(r) not in form_ids])},
        "top10": top,
    }


HEAD = {"n": "건수", "mean": "평균", "median": "중앙값", "sd": "표준편차", "q1": "1사분위", "q3": "3사분위",
        "min": "최소", "max": "최대", "band_낮음": "낮음(%)", "band_보통": "보통(%)", "band_높음": "높음(%)"}
SRC = {"central": "중앙", "local": "지자체"}


def cell(v) -> str:
    if isinstance(v, float):
        return f"{v:,.1f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def table(rows: list[dict], cols: list[str]) -> list[str]:
    num = [all(isinstance(r.get(c), (int, float)) for r in rows) for c in cols]
    out = ["| " + " | ".join(HEAD.get(c, c) for c in cols) + " |", "| " + " | ".join("---:" if n else "---" for n in num) + " |"]
    out += ["| " + " | ".join(cell(r.get(c, "")) for c in cols) + " |" for r in rows]
    return out


def markdown(s: dict) -> str:
    o, src = s["overall"], s["by_source"]
    L = ["# 전체 데이터 분석 결과 (운영 DB)", "",
         f"기준: 공식 목록 {s['services']:,}건(" + " · ".join(f"{SRC.get(k, k)} {v:,}" for k, v in s["list_counts"].items())
         + f"), 상세 저장·점수 계산 {s['scored']:,}건, 미계산 {s['unscored']:,}건. 목록 갱신 {s['catalog_updated_at']}, 집계 {s['generated_at']}.",
         "계산: 앱 분석 탭과 같은 규칙 추출과 기본 가중치(팀 확정 전 안). 재현: `python src/full_report.py`(읽기 권한 MongoDB, 외부 API·AI 호출 없음).", "",
         "## 1. 문턱 점수 분포", "",
         *table([{"구분": "전체", **o}, *({"구분": k, **v} for k, v in src.items())],
                ["구분", "n", "mean", "median", "sd", "q1", "q3", "min", "max", "band_낮음", "band_보통", "band_높음"]), "",
         "구간: 낮음 40점 미만(신청 쉬움), 보통 40~59점, 높음 60점 이상(신청 까다로움). 앱과 같은 기준입니다.", "",
         "5점 구간별 서비스 수: " + ", ".join(f"{k}~{k + 4}점 {v:,}" for k, v in s["histogram"].items()), "",
         "## 2. 무엇이 문턱을 높이나", "",
         "항목별 평균 점수(100점 환산, 합이 평균 문턱 점수):", "",
         *table([{"구분": "전체", **s["parts_all"]}, *({"구분": k, **v["parts"]} for k, v in src.items())], ["구분", *s["parts_all"].keys()]), "",
         "규칙으로 뽑은 신청 조건 현황:", "",
         *table([{"구분": "전체", **s["facts_all"]}, *({"구분": k, **v["facts"]} for k, v in src.items())], ["구분", *s["facts_all"].keys()]), "",
         f"## 3. 시도별(지자체, {MIN_GROUP}건 이상)", "",
         *table(s["by_sido"], ["이름", "n", "mean", "median", "band_높음"]), "",
         f"## 4. 생애주기별·대상별({MIN_GROUP}건 이상, 한 서비스가 여러 태그에 들어감)", "",
         *table(s["by_life"], ["이름", "n", "mean", "median", "band_높음"]), "",
         *table(s["by_group"], ["이름", "n", "mean", "median", "band_높음"]), "",
         "## 5. 작성 서식 첨부 여부", "",
         *table([{"구분": "작성 서식 있음", **s["forms"]["with_form"]}, {"구분": "없음", **s["forms"]["without_form"]}],
                ["구분", "n", "mean", "median", "band_높음"]), "",
         "## 6. 문턱 점수 상위 10개", "",
         *table(s["top10"], ["서비스", "구분", "지역", "점수", "이유"]), "",
         "## 7. 해석할 때 주의할 점", "",
         "- 점수는 규칙 추출 결과입니다. 고정 샘플 50건 비교에서 조건 수 일치율은 규칙 26%였으므로([샘플 비교](../report.md)) 조건 항목은 오차가 큽니다.",
         "- 서류 정보가 없는 서비스는 서류 점수를 더하지 않아 실제 부담보다 낮게 나올 수 있습니다(위 '서류 정보 있음' 비율 참고).",
         "- 가중치는 팀 확정 전 기본안입니다. 가중치가 바뀌면 순위와 구간 비율이 달라집니다.",
         "- 공식 목록·상세 원문 기준이며, 실제 신청에 걸린 시간이나 이용자 경험을 잰 값이 아닙니다.",
         "- 집단 간 평균 차이에 통계적 유의성 검정은 하지 않았습니다. 표준편차(약 12점)에 비해 작은 차이는 우연일 수 있습니다.", ""]
    return "\n".join(L)


def scores_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["servId", "서비스", "구분", "시도", "시군구", "문턱점수", "구간", "온라인", "방문필수", "기관", "조건", "서류", "난이도"])
    for r in rows:
        if r["score"] is None:
            continue
        s, e = r["svc"], r["ext"]
        w.writerow([s["servId"], s["servNm"], s["src"], s.get("sido", ""), s.get("sgg", ""), r["score"], band(r["score"]),
                    "Y" if e["online"] else "N", "Y" if e["visit_required"] else "N", len(e["agencies"]),
                    len(e["conditions"]), len(e["documents"]) if e.get("documents") else "", r["read"]["points"]])
    return "﻿" + buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output-dir", type=Path, default=ROOT / "eval" / "results" / "full")
    args = ap.parse_args()
    from welfare_api import WelfareAPI
    api = WelfareAPI()
    catalog = api.catalog()
    rows = build(score.DEFAULT_WEIGHTS, "규칙", api.services(catalog))
    s = summarize(rows, catalog["counts"], catalog["updated_at"], datetime.now(KST))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(json.dumps(s, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (args.output_dir / "report.md").write_text(markdown(s), encoding="utf-8")
    (args.output_dir / "scores.csv").write_bytes(scores_csv(rows).encode("utf-8"))
    print(f"서비스 {s['services']:,}건 / 점수 {s['scored']:,}건 → {args.output_dir}")


if __name__ == "__main__":
    main()
