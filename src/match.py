"""내 조건으로 복지 고르기. 공식 목록의 지역·생애주기·대상·주제 태그를 맞추고,
지원대상 글의 'N세 이상/이하'로 나이가 분명히 안 맞는 것만 뺀다(자격 판정 아님)."""
import re

LIFE = ["영유아", "아동", "청소년", "청년", "중장년", "노년", "임신·출산"]
GROUPS = ["저소득", "장애인", "한부모·조손", "다자녀", "다문화·탈북민", "보훈대상자"]
# 이 태그가 붙은 서비스는 해당하는 사람만 받는다('장애인, 저소득'이면 저소득만으로는 안 됨)
STRICT = {"장애인", "한부모·조손", "다문화·탈북민", "보훈대상자"}
# 아이·임신 서비스는 신청하는 사람이 부모라 글에 나온 나이가 신청자 나이가 아니다
FOR_CHILD = {"영유아", "아동", "청소년", "임신·출산"}


def age_range(text: str) -> tuple[int | None, int | None]:
    """지원대상 글의 'N세 이상'은 가장 작은 값, 'N세 이하/미만'은 가장 큰 값을 범위로 본다.
    이상이 이하보다 크면('① 30세 이상 … ④ 30세 미만'처럼 예외를 나눠 적었거나 청소년·어르신을 함께 적은 글)
    아무도 못 받는 범위가 되므로 나이로 거르지 않는다."""
    lo = [int(n) for n in re.findall(r"(\d{1,3})\s*세\s*이상", text)]
    hi = [int(n) - (u == "미만") for n, u in re.findall(r"(\d{1,3})\s*세\s*(이하|미만)", text)]
    lo, hi = (min(lo) if lo else None), (max(hi) if hi else None)
    if lo is not None and hi is not None and lo > hi:
        return None, None
    return lo, hi


def match(svc: dict, sido: str = "", sgg: str = "", life=(), groups=(), themes=(), age=None) -> list[str] | None:
    """맞으면 맞은 이유(태그) 목록, 안 맞으면 None."""
    why = []
    if age is not None and not FOR_CHILD & set(svc["life"]):
        lo, hi = age_range(svc.get("target", ""))
        if (lo is not None and age < lo) or (hi is not None and age > hi):
            return None
    if svc["src"] == "지자체":  # 동네 복지는 사는 곳이 같아야 한다
        if not sido or svc["sido"] != sido or (sgg and svc["sgg"] and svc["sgg"] != sgg):
            return None
        why.append(svc["region"])
    if life and svc["life"]:  # 생애주기를 안 골랐으면 거르지 않는다
        hit = [x for x in svc["life"] if x in life]
        if not hit:
            return None
        why += hit
    if (need := STRICT & set(svc["groups"])) and not need & set(groups):
        return None
    if svc["groups"]:  # 대상이 정해진 서비스
        hit = [x for x in svc["groups"] if x in groups]
        if not hit:
            return None
        why += hit
    why += [x for x in svc["themes"] if x in themes]  # 관심 분야는 거르지 않고 순서만 올린다
    return why


def recommend(rows: list[dict], **cond) -> list[tuple[dict, list[str]]]:
    """build() 결과에서 맞는 것만, 많이 맞고 대상이 좁게 정해졌고 신청이 쉬운 순으로.
    생애주기 태그가 많거나(청년·중장년·노년) 없는 서비스는 누구에게나 걸리므로 뒤로 보낸다."""
    out = [(r, why) for r in rows if (why := match(r["svc"], **cond)) is not None]
    return sorted(out, key=lambda x: (-len(x[1]), len(x[0]["svc"]["life"]) or len(LIFE) + 1,
                                     x[0]["score"] if x[0]["score"] is not None else float("inf")))
