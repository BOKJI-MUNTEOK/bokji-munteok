"""가중치 민감도 점검: 전체 분석 결과(scores.csv)의 항목 값으로 가중치를 바꿔 점수를 다시 내고,
서비스 순위와 높음·보통·낮음 구간이 얼마나 유지되는지 본다. DB·외부 호출 없음.

실행: python src/weight_check.py  → eval/results/weight_check.json, weight_check.md
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

import score
from parse import ROOT

RESULTS = ROOT / "eval" / "results"
CAP = {"no_online": 1, "visit": 1, **score.CAPS}  # 항목별 최댓값(0~100 환산 분모)
LABEL = {"no_online": "온라인 신청 불가", "visit": "방문 필수", "agency": "거치는 기관", "condition": "받을 조건",
         "document": "내야 할 서류", "readability": "안내문 난이도"}


def features(csv_path: Path) -> tuple[pd.DataFrame, pd.Series]:
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    x = pd.DataFrame({
        "no_online": (df["온라인"] == "N").astype(float),
        "visit": (df["방문필수"] == "Y").astype(float),
        "agency": df["기관"].clip(upper=CAP["agency"]),
        "condition": df["조건"].clip(upper=CAP["condition"]),
        "document": pd.to_numeric(df["서류"], errors="coerce").fillna(0).clip(upper=CAP["document"]),  # 정보 없음 = 가산 없음
        "readability": df["난이도"].clip(upper=CAP["readability"]),
    })
    return x, df["문턱점수"]


def rescore(x: pd.DataFrame, w: dict) -> pd.Series:
    raw = sum(x[k] * w[k] for k in w)
    return (100 * raw / sum(w[k] * CAP[k] for k in w)).round()


def compare(base: pd.Series, s: pd.Series) -> dict:
    top_n = len(base) // 10
    top0, top = set(base.nlargest(top_n).index), set(s.nlargest(top_n).index)
    high0, high = set(base[base >= 60].index), set(s[s >= 60].index)
    return {"순위상관": round(float(np.corrcoef(base.rank(), s.rank())[0, 1]), 3),
            "상위10%_유지": round(len(top & top0) / top_n, 3),
            "높음_일치": round(len(high & high0) / max(1, len(high | high0)), 3),
            "낮음%": round(float((s < 40).mean() * 100), 1), "보통%": round(float(((s >= 40) & (s < 60)).mean() * 100), 1),
            "높음%": round(float((s >= 60).mean() * 100), 1)}


def check(csv_path: Path = RESULTS / "full" / "scores.csv", draws: int = 1000) -> dict:
    x, stored = features(csv_path)
    w0 = dict(score.DEFAULT_WEIGHTS)
    base = rescore(x, w0)
    raw0 = sum(x[k] * w0[k] for k in w0)
    rng = np.random.default_rng(1124)  # 결과 재현용 고정 시드
    rand = [compare(base, rescore(x, {k: v * rng.uniform(0.5, 1.5) for k, v in w0.items()})) for _ in range(draws)]
    pick = lambda key, q: round(float(np.percentile([r[key] for r in rand], q)), 3)
    return {
        "n": len(x), "weights": w0, "caps": score.CAPS,
        "reproduced": round(float((base.values == stored.values).mean()), 4),
        "base": compare(base, base),
        "점수_기여%": {LABEL[k]: round(float((x[k] * w0[k]).mean() / raw0.mean() * 100), 1) for k in w0},
        "환산_최대치%": {LABEL[k]: round(100 * w0[k] * CAP[k] / sum(w0[j] * CAP[j] for j in w0), 1) for k in w0},
        "하나씩": {f"{LABEL[k]} ×{f}": compare(base, rescore(x, {**w0, k: w0[k] * f})) for k in w0 for f in (0, 0.5, 1.5)},
        "같은_가중치": compare(base, rescore(x, {k: 1.0 for k in w0})),
        "무작위_±50%": {"횟수": draws, "순위상관_최소": min(r["순위상관"] for r in rand), "순위상관_하위5%": pick("순위상관", 5),
                       "순위상관_중앙값": pick("순위상관", 50), "상위10%_유지_하위5%": pick("상위10%_유지", 5),
                       "상위10%_유지_중앙값": pick("상위10%_유지", 50)},
    }


def markdown(r: dict) -> str:
    rnd = r["무작위_±50%"]
    lines = ["# 가중치 민감도 점검", "",
             f"대상: [전체 분석 결과](full/report.md)의 {r['n']:,}건. 재현: `python src/weight_check.py` (DB·외부 호출 없음).",
             f"확정 가중치 {json.dumps(r['weights'], ensure_ascii=False)}, 상한 {json.dumps(r['caps'], ensure_ascii=False)}. "
             f"저장된 점수 재현율 {r['reproduced'] * 100:.1f}%.", "",
             "## 결론", "",
             f"- 6개 가중치를 동시에 0.5~1.5배로 {rnd['횟수']:,}번 바꿔도 서비스 순위상관은 중앙값 {rnd['순위상관_중앙값']}, "
             f"하위 5% {rnd['순위상관_하위5%']}입니다. 상위 10% 서비스는 중앙값 {rnd['상위10%_유지_중앙값'] * 100:.0f}%가 그대로 남습니다.",
             "- 순위는 가중치 선택에 크게 좌우되지 않습니다. 반면 높음(60점 이상)·보통·낮음 비율은 고정 점수선이라 가중치에 따라 크게 움직입니다.",
             "- 서류는 정보가 있는 서비스가 적어 점수 기여는 작지만 0~100 환산 최대치에서는 큰 몫을 차지합니다. "
             "서류 가중치를 바꾸면 순위는 거의 그대로인데 구간 비율이 크게 바뀝니다.", "",
             "## 항목별 몫", "", "| 항목 | 점수 기여(%) | 환산 최대치(%) |", "| --- | ---: | ---: |"]
    lines += [f"| {k} | {v} | {r['환산_최대치%'][k]} |" for k, v in r["점수_기여%"].items()]
    lines += ["", "## 가중치를 하나씩 바꿨을 때", "",
              "| 바꾼 가중치 | 순위상관 | 상위 10% 유지 | 높음 서비스 일치 | 낮음 % | 보통 % | 높음 % |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
              "| (확정값) | 1 | 1 | 1 | {낮음%} | {보통%} | {높음%} |".format(**r["base"])]
    for k, v in list(r["하나씩"].items()) + [("모두 1", r["같은_가중치"])]:
        lines.append(f"| {k} | {v['순위상관']} | {v['상위10%_유지']} | {v['높음_일치']} | {v['낮음%']} | {v['보통%']} | {v['높음%']} |")
    lines += ["", "높음 서비스 일치 = 두 계산에서 60점 이상인 서비스의 교집합 ÷ 합집합.", ""]
    return "\n".join(lines)


def main() -> None:
    r = check()
    (RESULTS / "weight_check.json").write_text(json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (RESULTS / "weight_check.md").write_text(markdown(r), encoding="utf-8")
    print(f"{r['n']:,}건 / 재현율 {r['reproduced'] * 100:.1f}% / 무작위 순위상관 중앙값 {r['무작위_±50%']['순위상관_중앙값']} → {RESULTS}")


if __name__ == "__main__":
    main()
