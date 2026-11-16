"""내 조건으로 복지 고르기. 공식 목록의 지역·생애주기·대상·주제 태그만 맞춰 본다(자격 판정 아님)."""

LIFE = ["영유아", "아동", "청소년", "청년", "중장년", "노년", "임신·출산"]
GROUPS = ["저소득", "장애인", "한부모·조손", "다자녀", "다문화·탈북민", "보훈대상자"]
# 이 태그가 붙은 서비스는 해당하는 사람만 받는다('장애인, 저소득'이면 저소득만으로는 안 됨)
STRICT = {"장애인", "한부모·조손", "다문화·탈북민", "보훈대상자"}


def match(svc: dict, sido: str = "", sgg: str = "", life=(), groups=(), themes=()) -> list[str] | None:
    """맞으면 맞은 이유(태그) 목록, 안 맞으면 None."""
    why = []
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
    """build() 결과에서 맞는 것만, 많이 맞고 신청이 쉬운 순으로."""
    out = [(r, why) for r in rows if (why := match(r["svc"], **cond)) is not None]
    return sorted(out, key=lambda x: (-len(x[1]), x[0]["score"]))
