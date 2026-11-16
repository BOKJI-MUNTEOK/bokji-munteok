"""문턱 점수: 정해 둔 항목에 점수를 매겨 더한 뒤 0~100으로 바꾼다(예측 모델 아님)."""
import re

# 팀이 정할 가중치(안). 화면에서 바꿔 볼 수 있다.
DEFAULT_WEIGHTS = {
    "no_online": 3.0,   # 온라인 신청 불가
    "visit": 2.0,       # 방문 필수
    "agency": 1.0,      # 거치는 기관 한 곳마다
    "condition": 1.0,   # 받을 조건 하나마다
    "document": 0.5,    # 서류 하나마다
    "readability": 1.0, # 읽기 어려움 0~3점에 곱함
}
# 0~100으로 바꿀 때 쓰는 항목별 상한(이보다 많아도 더 오르지 않음)
CAPS = {"agency": 3, "condition": 6, "document": 8, "readability": 3}

HARD_WORDS = [
    "기준중위소득", "기준 중위소득", "차상위", "수급자", "본인부담", "비급여", "상병", "직계존비속",
    "소요자", "해당하는", "의거", "준용", "산정", "고지액", "적격", "부적격", "내구연한", "가구원",
    "소득인정액", "급여", "보장", "조례", "시행", "익월", "당해", "통보", "구비", "지참", "말소",
]


def readability(svc: dict) -> dict:
    text = " ".join(svc[k] for k in ("target", "criteria", "apply"))
    sents = [s for s in re.split(r"[.。!?]\s|\n|○|※|·|- ", text) if len(s.strip()) > 3]
    avg = sum(len(s) for s in sents) / len(sents) if sents else 0
    hard = sorted({w for w in HARD_WORDS if w in text})
    pts = (avg > 60) + (avg > 100) + (len(hard) >= 3)
    return {"points": int(pts), "avg_len": round(avg), "hard_words": hard}


def score(ext: dict, read: dict, w: dict | None = None) -> dict:
    """ext: rules.extract() 또는 AI 추출 결과(같은 모양)."""
    w = {**DEFAULT_WEIGHTS, **(w or {})}
    n_ag = min(len(ext["agencies"]), CAPS["agency"])
    n_cond = min(len(ext["conditions"]), CAPS["condition"])
    docs = ext.get("documents")
    n_doc = min(len(docs), CAPS["document"]) if docs else 0
    parts = {
        "온라인 신청 불가": w["no_online"] * (not ext["online"]),
        "방문 필수": w["visit"] * bool(ext["visit_required"]),
        "거치는 기관": w["agency"] * n_ag,
        "받을 조건": w["condition"] * n_cond,
        "내야 할 서류": w["document"] * n_doc,
        "안내문 난이도": w["readability"] * read["points"],
    }
    max_raw = (w["no_online"] + w["visit"] + w["agency"] * CAPS["agency"] + w["condition"] * CAPS["condition"]
               + w["document"] * CAPS["document"] + w["readability"] * CAPS["readability"])
    raw = sum(parts.values())
    total = round(100 * raw / max_raw) if max_raw else 0

    reasons = []
    if not ext["online"]:
        reasons.append("방문만 가능" if ext["visit_required"] else "온라인 신청 불가")
    elif ext["visit_required"]:
        reasons.append("방문 필요")
    reasons.append(f"조건 {len(ext['conditions'])}개")
    reasons.append(f"기관 {len(ext['agencies'])}단계")
    reasons.append(f"서류 {len(docs)}개" if docs else "서류 정보 없음")
    if read["points"] >= 2:
        reasons.append("안내문 어려움")
    return {"score": total, "raw": round(raw, 1), "parts": parts, "reason": ", ".join(reasons) + f"라서 {total}점"}
