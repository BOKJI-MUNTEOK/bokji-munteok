"""AI(언어 모델) 호출: ① 조건·서류·기관 뽑기 ② 쉬운 설명 만들기.

OpenAI 호환 Chat Completions API를 표준 라이브러리로 부른다.
환경변수(.env): LLM_API_KEY(필수, 없으면 OPENAI_API_KEY), LLM_MODEL(기본 gpt-5.6-luna, 수업 자료 모델), OPENAI_BASE_URL(선택).
프롬프트는 prompts/*.txt. 결과는 data/ai_cache/<servId>.json에 저장해 화면은 API 없이도 열린다.
"""
import json
import os
import urllib.request
from pathlib import Path

import numcheck
from env import load_env

load_env()

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "ai_cache"
EXTRACT_PROMPT = (ROOT / "prompts" / "extract.txt").read_text(encoding="utf-8")
EASY_PROMPT = (ROOT / "prompts" / "easy.txt").read_text(encoding="utf-8")
MODEL = os.environ.get("LLM_MODEL", "gpt-5.6-luna")
BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")



EXTRACT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["online", "visit_required", "agencies", "conditions", "documents"],
    "properties": {
        "online": {"type": "boolean"},
        "visit_required": {"type": "boolean"},
        "agencies": {"type": "array", "items": {"type": "string", "enum": ["신청", "조사", "결정"]}},
        "conditions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["type", "text"],
            "properties": {"type": {"type": "string"}, "text": {"type": "string"}}}},
        "documents": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}]},
    },
}
EASY_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["who", "conditions", "what", "steps"],
    "properties": {
        "who": {"type": "string"},
        "conditions": {"type": "array", "items": {"type": "string"}},
        "what": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
    },
}


def source_text(svc: dict) -> str:
    return (f"[서비스명] {svc['servNm']}\n[지원대상] {svc['target']}\n[선정기준] {svc['criteria']}\n"
            f"[지원내용] {svc['benefit']}\n[신청방법] {svc['apply_method_name']} {svc['apply']}")


def easy_text(easy: dict) -> str:
    return "\n".join([easy["who"], *easy["conditions"], easy["what"], *easy["steps"]])


def _chat(system: str, user: str, schema: dict, name: str) -> dict:
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(".env에 LLM_API_KEY를 넣으세요")
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
    }
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    return json.loads(data["choices"][0]["message"]["content"])


def extract(svc: dict) -> dict:
    return _chat(EXTRACT_PROMPT, source_text(svc), EXTRACT_SCHEMA, "extract")


def make_easy(svc: dict, retries: int = 1) -> dict:
    """쉬운 설명을 만들고 숫자 검사. 빠지면 빠진 숫자를 알려 주고 다시 만든다."""
    src = source_text(svc)
    user = src
    for attempt in range(retries + 1):
        easy = _chat(EASY_PROMPT, user, EASY_SCHEMA, "easy")
        chk = numcheck.check(src, easy_text(easy))
        if chk["ok"]:
            break
        user = src + "\n\n[다시 쓰기] 앞 답에서 다음 숫자가 빠졌어요. 모두 넣어 다시 써 주세요: " + ", ".join(chk["missing"])
    return {"easy": easy, "check": chk, "attempts": attempt + 1, "needs_review": not chk["ok"]}


def run(svc: dict, force: bool = False) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{svc['servId']}.json"
    if path.exists() and not force:
        return json.loads(path.read_text(encoding="utf-8"))
    out = {"model": MODEL, "extract": extract(svc), **make_easy(svc)}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def load_cached(serv_id: str) -> dict | None:
    p = CACHE / f"{serv_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None
