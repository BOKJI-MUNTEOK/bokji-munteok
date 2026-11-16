"""단순 규칙(키워드) 추출기. AI 추출과 같은 모양의 결과를 내서 정확도를 비교하는 기준선으로 쓴다.

일부러 단순하게 만든다. 예: 글에 '방문'이 있으면 방문 필수로 본다.
"""
import re

COND_PATTERNS = {
    "나이": r"\d+\s*세|연령|만\s*\d+|노인|어르신|청년|아동|영유아|영아|청소년",
    "소득": r"소득|중위|수급자|차상위|저소득",
    "거주지": r"거주|주민등록|주소",
    "가구 형태": r"가구|가정|부부|한부모|다자녀|혼인|신혼|자녀|출산",
    "장애": r"장애",
    "질환·건강": r"질환|질병|진단|암|접종",
    "재산·주택": r"재산|무주택|주택|임차|전세",
    "국적": r"국적|외국인|다문화|탈북",
    "취업·근로": r"취업|근로|재직|실업|구직|산재",
    "보훈": r"유공자|보훈",
}

DOC_HEADER = re.compile(r"(?:신청)?(?:구비|제출|필요|신청)\s*서류\s*[:：\]]?")
DOC_ITEM = re.compile(r"(신청서|동의서|증명서|확인서|진단서|소견서|처방전|영수증|등본|초본|사본|신분증|통장|계약서|증서|증|서약서)")
NOT_DOC = re.compile(r"검토|및 지급|제출|받고")  # 서류 이름이 아니라 절차 문장


def detect_online(svc: dict) -> bool:
    if svc["src"] == "중앙":
        return bool(svc["online_flag"])
    text = svc["apply_method_name"] + " " + svc["apply"]
    return bool(re.search(r"인터넷|온라인|정부24|홈페이지", text))


def detect_visit(svc: dict) -> bool:
    return "방문" in (svc["apply_method_name"] + " " + svc["apply"])


def detect_agencies(svc: dict) -> list[str]:
    stages = []
    if svc["src"] == "중앙":
        names = " ".join(a["stage"] for a in svc["agencies"])
        for key, label in (("신청", "신청"), ("조사", "조사"), ("결정", "결정")):
            if key in names:
                stages.append(label)
        return stages or ["신청"]
    text = svc["apply"]
    stages.append("신청")
    if re.search(r"조사|심사|검토|확인", text):
        stages.append("조사")
    if re.search(r"결정|선정|통보", text):
        stages.append("결정")
    return stages


def detect_conditions(svc: dict) -> list[str]:
    text = svc["target"] + " " + svc["criteria"]
    return [k for k, p in COND_PATTERNS.items() if re.search(p, text)]


def detect_documents(svc: dict) -> list[str] | None:
    """'구비서류:' 같은 머리말 뒤의 목록만 읽는다. 머리말이 없으면 None(서류 정보 없음)."""
    text = svc["apply"] + "\n" + svc["criteria"]
    m = DOC_HEADER.search(text)
    if not m:
        return None
    tail = text[m.end(): m.end() + 300]
    parts = re.split(r"\d+\s*[.)]\s*|[,，、/\n]|\s-\s|○|\*", tail)
    docs = []
    for p in parts:
        p = re.sub(r"\s*\d+\s*부\.?$|\(.*?\)", "", p)
        p = re.sub(r"^.*서류\s*[:：]\s*", "", p).strip(" .·:-")  # '신청서류 : 신청서' → '신청서'
        if p and len(p) <= 30 and DOC_ITEM.search(p) and not NOT_DOC.search(p) and p not in docs:
            docs.append(p)
    return docs or None


def extract(svc: dict) -> dict:
    return {
        "online": detect_online(svc),
        "visit_required": detect_visit(svc),
        "agencies": detect_agencies(svc),
        "conditions": [{"type": c, "text": ""} for c in detect_conditions(svc)],
        "documents": detect_documents(svc),
    }
