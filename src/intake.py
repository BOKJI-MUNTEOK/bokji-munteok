"""상담 대화: 하나씩 물어보고 답에서 지역·생애주기·대상·관심 주제를 뽑는다.

뽑은 값은 match.recommend()에 그대로 넣는다. 정해진 질문의 짧은 답이라 키워드 규칙으로 충분하다.
답은 화면 세션에만 두고 저장하지 않는다.
"""
import re

import match

# 시·도 줄임말. 샘플에 없는 지역도 알아듣게 넉넉히 둔다.
SIDO_ALIAS = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시", "대전": "대전광역시",
    "울산": "울산광역시", "세종": "세종특별자치시", "경기": "경기도", "강원": "강원특별자치도",
    "충북": "충청북도", "충남": "충청남도", "전북": "전북특별자치도", "경북": "경상북도", "경남": "경상남도",
    "제주": "제주특별자치도", "전남": "전남광주통합특별시", "광주": "전남광주통합특별시",
}
GROUP_WORDS = {
    "저소득": r"저소득|기초|수급|차상위|형편|어려",
    "장애인": r"장애",
    "한부모·조손": r"한부모|조손|혼자 키우",
    "다자녀": r"다자녀|셋|3명|넷|4명|[3-9]\s*명 이상",
    "다문화·탈북민": r"다문화|결혼이민|외국|탈북|북한이탈",
    "보훈대상자": r"보훈|유공자",
}
THEME_WORDS = {
    "주거": r"월세|전세|이사|집|주거|임대",
    "일자리": r"일자리|취업|구직|직장",
    "신체건강": r"병원|건강|치료|수술|질환|아파",
    "정신건강": r"우울|상담|마음|스트레스",
    "보육": r"어린이집|육아|보육|돌봄",
    "보호·돌봄": r"돌봄|간병|요양",
    "교육": r"학비|장학|교육|학원|학생",
    "서민금융": r"대출|빚|이자|자금",
    "생활지원": r"생활비|생계",
    "임신·출산": r"임신|출산",
}
NONE = r"^(없|아니|해당 ?없|모르|건너)"

ETC = "기타(직접 입력)"
# (키, 질문, 빠른 답). 복지를 거르는 데 바로 쓰는 지역·나이부터 묻는다. 이름은 신청 준비 꾸러미에서 받는다.
# 빠른 답의 ETC를 고르면 직접 적는다.
QUESTIONS = [
    ("place", "안녕하세요, 복지온길이에요. 몇 가지만 여쭤보고 받을 수 있을 만한 복지를 찾아 드릴게요.\n\n"
              "먼저 **살고 계신 지역**은 어디세요? 시·군·구까지 알려 주시면 동네 복지도 찾아 드려요.", []),
    ("age", "**나이**가 어떻게 되세요?", ["20대", "30대", "40대", "50대", "60대", "70대 이상"]),
    ("job", "**직업**은 무엇인가요?", ["학생", "직장인", "구직 중", "자영업", "농어업", "은퇴·무직", ETC]),
    ("family", "**함께 사는 가족**은 어떻게 되세요?",
     ["혼자 살아요", "배우자", "아이 1~2명", "아이 3명 이상", "부모님(어르신)", ETC]),
    # 임신은 함께 사는 가족이 아니라 본인·배우자의 상황이라 여기서 묻는다
    ("special", "아래 중 **해당되는 것**이 있나요?", ["임신 중", "장애", "저소득(기초·차상위)", "한부모", "보훈", "다문화·탈북", "없음", ETC]),
    ("need", "**요즘 가장 도움이 필요한 건** 뭔가요?",
     ["집·월세", "일자리", "병원비·건강", "아이 돌봄", "생활비", "학비", ETC]),
]


def new_profile() -> dict:
    return {"name": "", "sido": "", "sgg": "", "age": None, "job": "", "family": "",
            "life": [], "groups": [], "themes": [], "notes": [], "answers": {}}


def age_to_life(age: int) -> str:
    return ("영유아" if age < 7 else "아동" if age < 13 else "청소년" if age < 19
            else "청년" if age < 40 else "중장년" if age < 65 else "노년")


def find_place(text: str, places: list[tuple[str, str]]) -> tuple[str, str]:
    """places: 샘플에 있는 (시·도, 시·군·구). 시·도를 먼저 정하고 그 안에서 시·군·구(해남군, 해남)를 찾는다."""
    sido = next((s for s, _ in places if s in text), "") or next((v for k, v in SIDO_ALIAS.items() if k in text), "")
    for s, g in places:
        short = re.sub(r"[시군구]$", "", g)
        if g and (not sido or s == sido) and (g in text or (len(short) >= 2 and short in text)):
            return s, g
    return sido, ""


def find_age(text: str) -> int | None:
    m = re.search(r"\d{1,3}", text)
    return int(m.group()) if m else None


def _add(lst: list, *items):
    lst += [x for x in items if x not in lst]


def take(p: dict, key: str, text: str, places: list[tuple[str, str]]) -> str | None:
    """답 하나를 프로필에 넣는다. 다시 물어야 하면 그 안내 문구를 돌려준다."""
    text = text.strip()
    p["answers"][key] = text
    if key == "name":
        p["name"] = "" if re.search(NONE, text) else text[:10]
    elif key == "place":
        p["sido"], p["sgg"] = find_place(text, places)
        if not p["sido"] and not re.search(NONE, text):
            return "어느 시·도인지 알아듣지 못했어요. 예: 경기도 성남시, 해남군, 부산"
    elif key == "age":
        age = find_age(text)
        if age is None or not 0 <= age <= 120:
            return "숫자로 알려 주세요. 예: 32살, 70대"
        p["age"] = age
        _add(p["life"], age_to_life(age))
    elif key == "job":
        p["job"] = "" if re.search(NONE, text) else text
        if re.search(r"구직|실업|취준|일자리", text):
            _add(p["themes"], "일자리")
        if re.search(r"학생", text):
            _add(p["themes"], "교육")
    elif key == "family":
        p["family"] = text
        if re.search(r"아이|자녀|아들|딸|아기", text):
            _add(p["life"], "영유아", "아동")
        if re.search(r"부모|어르신|할머니|할아버지", text):
            _add(p["life"], "노년")
    if key in ("family", "special"):
        if re.search(r"임신|출산", text):
            _add(p["life"], "임신·출산")
        _add(p["groups"], *[k for k, pat in GROUP_WORDS.items() if not re.search(NONE, text) and re.search(pat, text)])
    if key == "need":
        _add(p["themes"], *[k for k, pat in THEME_WORDS.items() if re.search(pat, text)])
    if key in ("special", "need") and (etc := typed(text, key)):
        _add(p["notes"], etc)
    return None


def typed(text: str, key: str) -> str:
    """빠른 답에 없는, 직접 적은 부분만."""
    quick = next(q for k, _, q in QUESTIONS if k == key)
    rest = [x.strip() for x in re.split(r"[,，]", text) if x.strip() and x.strip() not in quick]
    return ", ".join(x for x in rest if not re.search(NONE, x))


def conditions(p: dict) -> dict:
    return {k: p[k] for k in ("sido", "sgg", "life", "groups", "themes", "age")}


def summary(p: dict) -> list[str]:
    out = [" ".join(x for x in (p["sido"], p["sgg"]) if x) or "지역 모름"]
    if p["age"] is not None:
        out.append(f"{p['age']}세")
    if p["job"]:
        out.append(p["job"])
    if p["family"] and not re.search(NONE, p["family"]):
        out.append(f"가족: {p['family']}")
    return out + p["groups"] + [f"관심: {t}" for t in p["themes"]] + [f"직접 적음: {n}" for n in p["notes"]]


def build(answers: dict, places: list[tuple[str, str]]) -> dict:
    """답 모음으로 프로필을 처음부터 다시 만든다(질문에 답하기에서 이전으로 돌아가 고쳐도 꼬이지 않게)."""
    p = new_profile()
    for key, _, _ in QUESTIONS:
        if key in answers:
            take(p, key, answers[key], places)
    return p
