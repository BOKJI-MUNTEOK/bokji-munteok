"""Vercel/로컬 공용 웹 앱. 실행: python -m uvicorn main:app --reload

공개 샘플만 메모리에 캐시한다. 상담 답과 작성 도우미 입력은 요청 안에서만 사용한다.
기존 Streamlit app.py 및 src의 계산·추천 규칙을 그대로 재사용한다.
"""
from functools import lru_cache
from pathlib import Path
import sys
import time
from typing import Annotated, Literal
from urllib.parse import quote
import urllib.request

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

import evaluate
import intake
import llm
import match
import numcheck
import packet
import paperwork
import rules
import score
from parse import load_all
from pipeline import build

app = FastAPI(title="복지문턱", docs_url=None, redoc_url=None, openapi_url=None)
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
QUICK = {
    "청년": {"life": ["청년"]}, "어르신": {"life": ["노년"]},
    "임신·출산": {"life": ["임신·출산"]}, "아이 키우기": {"life": ["영유아", "아동"]},
    "장애인": {"groups": ["장애인"]}, "저소득": {"groups": ["저소득"]},
}
LABELS = {"no_online": "온라인 신청 불가", "visit": "방문 필수", "agency": "기관 1곳당",
          "condition": "조건 1개당", "document": "서류 1개당", "readability": "난이도 1점당"}
AnswerKey = Literal["place", "age", "job", "family", "special", "need"]
ShortText = Annotated[str, StringConstraints(max_length=300)]
Weight = Annotated[float, Field(ge=0, le=5, allow_inf_nan=False)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindInput(Input):
    answers: dict[AnswerKey, ShortText] = Field(default_factory=dict)
    quick: Literal["", "청년", "어르신", "임신·출산", "아이 키우기", "장애인", "저소득"] = ""


class Extra(Input):
    name: Annotated[str, StringConstraints(max_length=10)] = ""
    phone: Annotated[str, StringConstraints(max_length=40)] = ""
    address: ShortText = ""
    proxy: Annotated[str, StringConstraints(max_length=40)] = ""
    relation: Annotated[str, StringConstraints(max_length=40)] = ""


class PacketInput(FindInput):
    service_id: Annotated[str, StringConstraints(pattern=r"^WLF\d{8}$")]
    extra: Extra = Field(default_factory=Extra)


class AnalysisInput(Input):
    weights: dict[Literal["no_online", "visit", "agency", "condition", "document", "readability"], Weight] = Field(default_factory=dict)


@app.exception_handler(RequestValidationError)
async def bad_input(request, exc):
    # FastAPI의 기본 응답은 입력 원문을 되돌려준다. 개인정보를 오류 응답에 넣지 않는다.
    return JSONResponse({"detail": "입력 형식이나 길이를 확인해 주세요."}, status_code=422, headers=NO_STORE)


@app.exception_handler(HTTPException)
async def expected_error(request, exc):
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=NO_STORE)


@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    return JSONResponse({"detail": "처리하지 못했어요. 잠시 후 다시 시도해 주세요."}, status_code=500, headers=NO_STORE)


@lru_cache(maxsize=1)
def dataset():
    services = load_all()
    return services, build(services=services)


def places():
    return sorted({(s["sido"], s["sgg"]) for s in dataset()[0] if s["src"] == "지자체" and s["sido"]})


def profile(body: FindInput):
    p = intake.new_profile()
    for key, _, _ in intake.QUESTIONS:
        if key in body.answers:
            error = intake.take(p, key, body.answers[key], places())
            if error:
                raise HTTPException(422, error)
    if body.quick:
        p.update(QUICK[body.quick])
    return p


def present(row):
    docs, listed = paperwork.documents(row)
    ai = row["ai"]
    return {**row, "documents": [{"name": d, "where": paperwork.where(d)} for d in docs],
            "documents_listed": listed, "call_script": paperwork.call_script(row["svc"]),
            "number_check": numcheck.check(llm.source_text(row["svc"]), llm.easy_text(ai["easy"])) if ai else None}


@app.get("/api/health")
def health():
    return {"status": "ok", "services": len(dataset()[0])}


@app.get("/api/bootstrap")
def bootstrap():
    return JSONResponse({"rows": [present(r) for r in dataset()[1]], "places": places(),
                         "questions": intake.QUESTIONS, "quick": list(QUICK),
                         "weights": score.DEFAULT_WEIGHTS, "weight_labels": LABELS, "caps": score.CAPS},
                        headers={"Cache-Control": "public, max-age=300"})


@app.post("/api/recommend")
def recommend(body: FindInput):
    p = profile(body)
    recs = match.recommend(dataset()[1], **intake.conditions(p))
    return JSONResponse({"profile": p, "summary": intake.summary(p),
                         "results": [{"id": r["svc"]["servId"], "why": why} for r, why in recs]}, headers=NO_STORE)


@app.post("/api/analysis")
def analysis(body: AnalysisInput):
    services, _ = dataset()
    rows = build(body.weights, services=services)
    rule_sum = {s["servId"]: evaluate.summarize(rules.extract(s)) for s in services}
    ai_sum = {r["svc"]["servId"]: evaluate.summarize(r["ai"]["extract"]) for r in rows if r["ai"]}
    gold = evaluate.load_gold()
    return JSONResponse({
        "rows": [{"id": r["svc"]["servId"], "name": r["svc"]["servNm"], "src": r["svc"]["src"],
                  "score": r["score"], "reason": r["reason"], "online": r["ext"]["online"],
                  "parts": {k: round(v * r["score"] / r["raw"], 2) if r["raw"] else 0 for k, v in r["parts"].items()}}
                 for r in rows],
        "gold_count": len(gold), "gold_mix": evaluate.gold_mix(), "labels": evaluate.LABELS,
        "rule_gold": evaluate.compare(rule_sum, gold) if gold else {},
        "ai_gold": evaluate.compare(ai_sum, gold) if gold else {},
        "rule_ai": evaluate.compare(rule_sum, ai_sum) if ai_sum else {},
        "number_ok": sum(not r["ai"]["needs_review"] for r in rows if r["ai"]), "ai_count": len(ai_sum),
    }, headers=NO_STORE)


def limited_downloads():
    """Vercel 응답 크기·실행 시간 안에서 원본을 받는다. 초과하면 기존 ZIP의 링크 안내로 대체."""
    deadline, remaining = time.monotonic() + 30, 2_500_000

    def fetch(url):
        nonlocal remaining
        seconds = deadline - time.monotonic()
        if seconds <= 0 or remaining <= 0:
            raise TimeoutError("다운로드 한도")
        # URL은 클라이언트 입력을 받지 않고 저장소의 공식 첨부 목록에서만 가져온다.
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=min(5, seconds)) as response:
            chunks, size = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("다운로드 시간 초과")
                chunk = response.read(min(65536, remaining - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > remaining:
                    raise ValueError("다운로드 용량 초과")
                chunks.append(chunk)
        data = b"".join(chunks)
        if data[:1] == b"{" and b'"ERROR"' in data[:200]:
            raise FileNotFoundError("원본 파일 없음")
        remaining -= len(data)
        return data
    return fetch


@app.post("/api/packet")
def make_packet(body: PacketInput):
    row = next((r for r in dataset()[1] if r["svc"]["servId"] == body.service_id), None)
    if row is None:
        raise HTTPException(404, "서비스를 찾을 수 없어요.")
    p = profile(body)
    p["name"] = body.extra.name.strip()
    data, failed = packet.build_zip(row, p, body.extra.model_dump(), fetch=limited_downloads())
    if len(data) > 4_000_000:
        raise HTTPException(413, "꾸러미가 너무 커요. 기관 첨부 파일을 따로 내려받아 주세요.")
    filename = quote(f"신청준비_{row['svc']['servNm'][:30]}.zip")
    return Response(data, media_type="application/zip", headers={**NO_STORE,
        "Content-Disposition": f"attachment; filename=application.zip; filename*=UTF-8''{filename}",
        "X-Missing-Files": str(len(failed))})


@app.get("/", include_in_schema=False)
@app.get("/find", include_in_schema=False)
@app.get("/all", include_in_schema=False)
@app.get("/analysis", include_in_schema=False)
@app.get("/guide", include_in_schema=False)
def page():
    return FileResponse(ROOT / "web" / "index.html", headers={"Cache-Control": "no-cache"})


# 두 실행 환경 모두 같은 URL로 정적 자산을 제공한다.
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="web")
app.mount("/assets", StaticFiles(directory=ROOT / "assets"), name="assets")
