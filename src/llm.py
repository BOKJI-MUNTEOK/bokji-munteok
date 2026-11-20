"""AI(언어 모델) 호출: ① 조건·서류·기관 뽑기 ② 쉬운 설명 만들기.

OpenAI 호환 Chat Completions API를 표준 라이브러리로 부른다.
환경변수(.env): LLM_API_KEY(필수, 없으면 OPENAI_API_KEY), LLM_MODEL(기본 gpt-5.6-luna, 수업 자료 모델), OPENAI_BASE_URL(선택).
프롬프트는 prompts/*.txt. 결과는 data/ai_cache/<servId>.json에 저장하고 원문이 같은 경우 재사용한다.
"""
import json
import hashlib
import os
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

import numcheck
from env import load_env

load_env()

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "data" / "ai_cache"
EXTRACT_PROMPT = (ROOT / "prompts" / "extract.txt").read_text(encoding="utf-8")
EASY_PROMPT = (ROOT / "prompts" / "easy.txt").read_text(encoding="utf-8")
# 화면에서 즉석으로 만드는 짧은 요약(평가·캐시용 EASY_PROMPT와 따로 둔다)
EASY_SHORT_PROMPT = (ROOT / "prompts" / "easy_short.txt").read_text(encoding="utf-8")
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


def _chat(system: str, user: str, schema: dict, name: str, reasoning_effort: str | None = None) -> dict:
    """reasoning_effort('low' 등)를 주면 빠르게 답한다. 모델이 지원하지 않으면 옵션 없이 다시 요청한다."""
    key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError(".env에 LLM_API_KEY를 넣으세요")
    body = {
        "model": MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
    }
    if reasoning_effort:
        body["reasoning_effort"] = reasoning_effort
    req = urllib.request.Request(f"{BASE}/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.load(r)
    except urllib.error.HTTPError as exc:
        if reasoning_effort and exc.code == 400:
            return _chat(system, user, schema, name)
        raise
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


def make_easy_short(svc: dict) -> dict:
    """화면용 짧은 쉬운 설명. 빠르게(추론 강도 none, 재시도 없음) 만든다. 숫자 검사는 결과만 돌려준다."""
    src = source_text(svc)
    easy = _chat(EASY_SHORT_PROMPT, src, EASY_SCHEMA, "easy_short", reasoning_effort="none")
    return {"easy": easy, "check": numcheck.check(src, easy_text(easy)), "short": True}


def run(svc: dict, force: bool = False) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{svc['servId']}.json"
    if not force and (cached := load_cached(svc["servId"], svc)):
        return cached
    out = {"model": MODEL, "source_hash": source_hash(svc), "extract": extract(svc), **make_easy(svc)}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def source_hash(svc: dict) -> str:
    return hashlib.sha256(source_text(svc).encode("utf-8")).hexdigest()


@lru_cache(maxsize=1)
def sample_hashes() -> dict:
    """기존 AI 캐시의 원문 확인용. 운영 서비스 목록에는 샘플을 넣지 않는다."""
    from parse import load_all
    return {s["servId"]: source_hash(s) for s in load_all(ROOT / "data" / "sample")}


def load_cached(serv_id: str, svc: dict | None = None) -> dict | None:
    p = CACHE / f"{serv_id}.json"
    if not p.exists():
        return None
    cached = json.loads(p.read_text(encoding="utf-8"))
    if svc is not None:
        original = cached.get("source_hash") or sample_hashes().get(serv_id)
        if original != source_hash(svc):
            return None
    return cached
