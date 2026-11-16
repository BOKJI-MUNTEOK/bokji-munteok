"""정답표(사람이 직접 읽고 센 값)와 규칙·AI 추출 결과를 비교한다.

eval/gold.csv 열: servId, online(Y/N), visit_required(Y/N), n_agencies, n_conditions, n_documents(빈칸=서류 정보 없음)
정답표가 없으면 규칙과 AI가 서로 얼마나 일치하는지만 보여 준다.
"""
import csv
from pathlib import Path

EVAL = Path(__file__).resolve().parent.parent / "eval"
GOLD = EVAL / "gold.csv"
FIELDS = ["online", "visit_required", "n_agencies", "n_conditions", "n_documents"]
LABELS = {"online": "온라인 가능", "visit_required": "방문 필수", "n_agencies": "기관 수",
          "n_conditions": "조건 수", "n_documents": "서류 수"}


def summarize(ext: dict) -> dict:
    docs = ext.get("documents")
    return {"online": bool(ext["online"]), "visit_required": bool(ext["visit_required"]),
            "n_agencies": len(ext["agencies"]), "n_conditions": len(ext["conditions"]),
            "n_documents": len(docs) if docs else None}


def load_gold(path: Path | None = None) -> dict:
    """path를 주면 그 파일만, 안 주면 eval/gold.csv와 사람별 eval/gold_*.csv(틀 제외)를 합쳐 읽는다."""
    paths = [path] if path else sorted(p for p in EVAL.glob("gold*.csv") if p.name != "gold_template.csv")
    gold = {}
    for p in paths:
        if p.exists():
            gold.update(_read_gold(p))
    return gold


def _read_gold(path: Path) -> dict:
    gold = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if not row.get("online", "").strip() or "AI 초안(검수 전)" in row.get("메모", ""):
                continue  # 아직 안 채운 줄, 사람이 검수하기 전인 AI 초안은 정답으로 안 씀
            yn = lambda v: v.strip().upper() in ("Y", "TRUE", "1", "O")
            num = lambda v: int(float(v)) if v.strip() else None
            gold[row["servId"]] = {"online": yn(row["online"]), "visit_required": yn(row["visit_required"]),
                                   "n_agencies": num(row["n_agencies"]), "n_conditions": num(row["n_conditions"]),
                                   "n_documents": num(row.get("n_documents", ""))}
    return gold


def compare(pred: dict, ref: dict) -> dict:
    """pred, ref: {servId: summarize() 결과}. 항목별 일치율(%)과 수치 항목의 평균 오차."""
    ids = [i for i in ref if i in pred]
    out = {"n": len(ids)}
    for f in FIELDS:
        pairs = [(pred[i][f], ref[i][f]) for i in ids]
        out[f] = round(100 * sum(a == b for a, b in pairs) / len(pairs), 1) if pairs else None
        if f.startswith("n_"):
            nums = [(a or 0, b or 0) for a, b in pairs]
            out[f + "_mae"] = round(sum(abs(a - b) for a, b in nums) / len(nums), 2) if nums else None
    return out


def write_template(services: list[dict], path: Path) -> None:
    """검증 담당이 채울 빈 정답표. 원문을 옆에 붙여 바로 읽고 셀 수 있게 한다."""
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["servId", "servNm", *FIELDS, "documents(쉼표로)", "메모", "원문"])
        for s in services:
            raw = f"[지원대상] {s['target']}\n[선정기준] {s['criteria']}\n[신청방법] {s['apply_method_name']} {s['apply']}"
            w.writerow([s["servId"], s["servNm"], "", "", "", "", "", "", "", raw[:3000]])
