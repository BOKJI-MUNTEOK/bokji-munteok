"""서류 화면의 AI 도움: 신청서·서류를 받는 방법을 쉬운 말로 설명한다.

- 공개된 복지서비스 안내문만 보낸다(개인정보 없음).
- 키가 없거나 호출이 실패하면 None을 돌려주고, 화면은 규칙 안내로 대신한다.
"""
from __future__ import annotations

import os

from env import load_env

GUIDE_PROMPT = """너는 어르신도 이해하기 쉽게 복지 신청 방법과 필요한 서류를 알려 주는 안내원이다.
공식 안내문을 읽고, 신청할 때 낼 서류를 어디서 어떻게 떼는지 차례대로 설명한다.
규칙:
- steps는 2~3개, 각 짧은 한 문장, 쉬운 존댓말. 첫 단계는 신청서를 받는 방법(온라인/방문/전화).
- documents: 안내문에 나온 서류는 official=true로 모두 넣는다. 안내문에 서류가 없거나 부족하면, 이런 복지를 신청할 때
  보통 내는 서류(신청서, 신분증, 본인 명의 통장 사본, 대상임을 보여 주는 증명서 — 예: 장애인증명서·가족관계증명서·주민등록등본·
  소득 증빙 등 이 복지의 대상에 맞는 것)를 official=false로 넣는다. 3~5개.
- where에는 어디서 어떻게 떼는지 어르신이 바로 따라 할 수 있게 짧은 한 문장으로 쓴다
  (예: '정부24 누리집이나 가까운 주민센터 무인민원발급기에서 뗄 수 있어요. 어려우면 주민센터 창구에 신분증을 내면 떼 줘요.').
  online은 정부24·홈택스·은행 앱 등 온라인으로 뗄 수 있으면 true.
- 안내문에 없는 사실(금액·기한·전화번호)은 지어내지 않는다.
- tip에는 혼자 하기 어려운 어르신을 위한 방법(가족에게 부탁, 주민센터 방문 상담, 전화 문의 등)을 한 문장으로."""
GUIDE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["steps", "documents", "tip"],
    "properties": {
        "steps": {"type": "array", "items": {"type": "string"}},
        "documents": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["name", "where", "official", "online"],
            "properties": {"name": {"type": "string"}, "where": {"type": "string"},
                           "official": {"type": "boolean"}, "online": {"type": "boolean"}}}},
        "tip": {"type": "string"},
    },
}


def available() -> bool:
    load_env()
    return bool(os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def _chat():
    import llm
    return llm._chat


def easy_guide(svc: dict, *, chat=None) -> dict | None:
    """공식 안내문으로 서류 받는 방법을 쉽게 설명한다. 실패하면 None."""
    if chat is None and not available():
        return None
    text = "\n".join(f"[{label}] {str(svc.get(key) or '')[:1500]}" for key, label in (
        ("servNm", "서비스명"), ("org", "담당 기관"), ("apply_method_name", "신청 방법"), ("apply", "신청 절차"),
        ("target", "지원 대상"), ("benefit", "지원 내용"), ("contact", "문의")))
    files = ", ".join(f.get("name", "") for f in (svc.get("form_files") or [])[:6])
    if files:
        text += f"\n[첨부 파일] {files}"
    try:
        result = (chat or _chat())(GUIDE_PROMPT, text, GUIDE_SCHEMA, "document_guide", **({} if chat else {"reasoning_effort": "none"}))
    except Exception:
        return None
    if not isinstance(result, dict) or not result.get("steps"):
        return None
    return {"steps": [str(s)[:200] for s in result["steps"][:6]],
            "documents": [{"name": str(d.get("name", ""))[:60], "where": str(d.get("where", ""))[:200],
                           "official": bool(d.get("official")), "online": bool(d.get("online"))}
                          for d in (result.get("documents") or [])[:10] if str(d.get("name", "")).strip()],
            "tip": str(result.get("tip") or "")[:200]}
