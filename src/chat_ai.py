"""AI 상담: OpenAI와 자유롭게 대화하면서 복지 추천에 필요한 정보를 모은다.

모델은 매 턴 대화 전체를 보고 ① 다음에 할 말 ② 지금까지 알게 된 정보(지역·나이·직업·가족·해당 사항·필요한 도움)를
돌려준다. 정보는 intake.build()에 그대로 넣어 기존 규칙(지역 이름 정리, 생애주기 계산)으로 다시 정리한다.
대화는 화면 세션에만 두고 저장하지 않는다. 키가 없거나 호출이 실패하면 화면이 규칙 상담으로 대신한다.
"""
from __future__ import annotations

import os

import address_check
import field_format
import intake
from env import load_env

MAX_TURNS = 12
GREETING = ("안녕하세요, 복지문턱 AI 상담이에요. 지금 상황을 편하게 말씀해 주세요.\n\n"
            "예: “성남시에 사는 72살이고 혼자 살아요. 병원비가 걱정이에요.”")

SYSTEM = """너는 한국 복지 상담사다. 어르신도 알아듣게 짧고 다정한 존댓말로 말한다.
목표: 복지 추천에 필요한 정보를 대화로 모은다 — 사는 지역(시·도와 시·군·구), 나이, 직업, 함께 사는 가족,
해당 사항(임신, 장애, 저소득(기초·차상위), 한부모, 보훈, 다문화·탈북), 요즘 가장 필요한 도움(집·월세, 일자리, 병원비·건강, 아이 돌봄, 생활비, 학비 등).
규칙:
- reply는 2~3문장 이내. 한 번에 하나만 묻는다. 이미 들은 것은 다시 묻지 않는다. 공감 한마디를 먼저 해도 좋다.
- 같은 질문은 한 번만 한다. 사용자가 답하지 않고 다른 이야기를 하면 그 질문은 '모름'으로 두고 넘어간다.
- 직업·가족·해당 사항은 선택 질문이다. 지역·나이·필요한 도움을 알면 더 묻지 말고 done=true로 결과를 보여 준다.
- 주민등록번호와 계좌번호는 절대 묻지 않는다. 사용자가 말해도 기록하지 않고, 필요 없다고 짧게 안내한다.
- 추천 결과를 보여 준 뒤(대화에 '찾은 복지' 안내가 나온 뒤)에는 신청서를 미리 채워 드리기 위해 이름 → 생년월일 → 연락처 →
  도로명 주소(동·호수까지)를 한 번에 하나씩 물을 수 있다. 사용자가 원하지 않거나 건너뛰자고 하면 더 묻지 않는다.
  다 받으면 '이제 신청 방법 보기 → 서류 작성·인쇄에서 내 정보가 들어간 신청서를 볼 수 있어요'라고 안내한다.
- 받을 수 있다고 단정하거나 금액을 약속하지 않는다.
- 필드는 대화 전체에서 사용자가 실제로 말한 내용만 짧게 적는다. 모르면 빈 문자열.
  place: 예 '경기도 성남시', age: 숫자만(예 '72', '70대'면 '70'), job, family, special: 쉼표로 구분(없다고 하면 '없음'),
  need: 쉼표로 구분, name: 사용자가 스스로 밝힌 이름만, birth: 생년월일(예 '1950-01-01'), phone: 연락처 숫자,
  address: 사용자가 말한 전체 주소(도로명·동호수 포함, 모르면 빈 문자열).
- 지역과 나이를 알고, 필요한 도움까지 들었거나 사용자가 결과를 보고 싶다고 하면 done=true로 하고
  reply에 들은 내용을 한 문장으로 정리한 뒤 '아래에서 찾은 복지를 보여 드릴게요'라고 말한다."""
SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["reply", "place", "age", "job", "family", "special", "need", "name", "birth", "phone", "address", "done"],
    "properties": {k: {"type": "string"} for k in ("reply", "place", "age", "job", "family", "special", "need", "name",
                                                   "birth", "phone", "address")}
    | {"done": {"type": "boolean"}},
}
FIELDS = ("place", "age", "job", "family", "special", "need")

PICK_PROMPT = """너는 복지 상담사다. 상담 내용과 후보 복지 목록을 보고, 이 사람에게 가장 먼저 권할 복지 3개를 고른다.
규칙:
- 나이·가족·지역·필요한 도움에 맞지 않는 것(예: 40대에게 청소년부모 지원)은 고르지 않는다.
- 반드시 후보에 있는 id만 쓴다. reason은 이 사람에게 왜 맞는지 쉬운 말 한 문장(받을 수 있다고 단정하지 않는다)."""
PICK_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["picks"],
    "properties": {"picks": {"type": "array", "items": {
        "type": "object", "additionalProperties": False, "required": ["id", "reason"],
        "properties": {"id": {"type": "string"}, "reason": {"type": "string"}}}}},
}


def _fast(chat) -> dict:
    """실제 OpenAI 호출이면 빠른 응답 옵션을 붙인다(테스트용 가짜 함수에는 붙이지 않는다)."""
    import llm
    return {"reasoning_effort": "low"} if chat is llm._chat else {}


def available() -> bool:
    load_env()
    return bool(os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def transcript(messages: list[dict]) -> str:
    who = {"assistant": "상담사", "user": "사용자"}
    return "\n".join(f"{who.get(m['role'], m['role'])}: {str(m['content'])[:600]}" for m in messages[-24:])


def pick(messages: list[dict], candidates: list[dict], *, chat=None) -> list[tuple[str, str]]:
    """규칙으로 고른 후보 중 상담 내용에 가장 맞는 3개를 AI가 고른다. [(servId, 이유)]. 실패하면 빈 목록."""
    if not candidates:
        return []
    if chat is None:
        import llm
        chat = llm._chat
    lines = "\n".join(f"- id={c['servId']} | {c['servNm']} | 대상: {str(c.get('target') or '')[:120]} | 내용: {str(c.get('summary') or '')[:80]}"
                      for c in candidates[:15])
    try:
        result = chat(PICK_PROMPT, f"상담 내용:\n{transcript(messages)}\n\n후보 복지:\n{lines}", PICK_SCHEMA, "welfare_pick", **_fast(chat))
    except Exception:
        return []
    ids = {c["servId"] for c in candidates[:15]}
    out = []
    for item in (result or {}).get("picks", []) if isinstance(result, dict) else []:
        if item.get("id") in ids and item["id"] not in {x for x, _ in out}:
            out.append((item["id"], str(item.get("reason") or "").strip()[:120]))
    return out[:3]


def turn(messages: list[dict], places: list[tuple[str, str]], *, chat=None) -> dict:
    """대화 한 턴. {'reply', 'answers', 'profile', 'done', 'missing'}를 돌려준다. 호출 실패는 예외로 올린다."""
    if chat is None:
        import llm
        chat = llm._chat
    result = chat(SYSTEM, "지금까지의 대화:\n" + transcript(messages), SCHEMA, "welfare_intake", **_fast(chat))
    if not isinstance(result, dict) or not str(result.get("reply") or "").strip():
        raise ValueError("bad_reply")
    answers = {k: str(result.get(k) or "").strip()[:100] for k in FIELDS if str(result.get(k) or "").strip()}
    profile = intake.build(answers, places)
    profile["notes"] = []  # AI가 정리한 표현은 '직접 적음' 메모가 아니다(추천에는 쓰이지 않는다).
    name = str(result.get("name") or "").strip()[:10]
    if name:
        profile["name"] = name
    # 서류에 미리 넣을 정보. 읽을 수 없는 값은 버린다(주민번호·계좌번호는 모델이 기록하지 않게 했다).
    birth = field_format.parse_date(str(result.get("birth") or ""))
    profile["birth"] = birth.isoformat() if birth else ""
    phone = str(result.get("phone") or "")
    profile["phone"] = field_format.format_phone(phone) if field_format.phone_parts(phone) else ""
    profile["address"] = address_check.tidy(str(result.get("address") or ""))[:120]
    if profile["age"] is None and birth:
        # 생년월일만 말했으면 나이를 계산해 둔다.
        from datetime import date
        today = date.today()
        profile["age"] = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
        profile["life"] = [intake.age_to_life(profile["age"])]
    missing = [label for ok, label in ((bool(profile["sido"]), "지역"), (profile["age"] is not None, "나이")) if not ok]
    user_turns = sum(m["role"] == "user" for m in messages)
    done = (bool(result.get("done")) and not missing) or (user_turns >= MAX_TURNS and not missing)
    reply = str(result["reply"]).strip()[:600]
    if result.get("done") and missing:
        # 모델이 끝내려 해도 추천에 꼭 필요한 정보가 없으면 한 번 더 묻는다.
        reply = f"추천을 위해 {', '.join(missing)}만 더 알려 주세요."
    return {"reply": reply, "answers": answers, "profile": profile, "done": done, "missing": missing}
