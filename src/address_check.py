"""서류에 적을 주소를 다듬고 실제 주소인지 확인한다.

1. 로컬 정리: 붙여 쓴 '시·도 / 시·군·구 / 도로명 / 건물번호 / 동·호'를 띄어 쓴다.
2. AI 검수(선택, LLM_API_KEY): 띄어쓰기·오타만 고친다. 없는 동·호수를 만들지 않는다.
3. 도로명주소 검색(선택, JUSO_API_KEY): 행정안전부 도로명주소 API로 실제 주소 후보를 찾는다.

외부 호출은 사용자가 '주소 확인하기'를 눌렀을 때 주소 한 줄만 보낸다. 이름·연락처는 보내지 않는다.
키가 없거나 호출이 실패하면 그 단계만 건너뛰고 로컬 정리 결과를 돌려준다.
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

from env import load_env

JUSO_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"
MAX_LEN = 200

SIDO = ("서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시", "대전광역시", "울산광역시",
        "세종특별자치시", "경기도", "강원특별자치도", "충청북도", "충청남도", "전북특별자치도", "전라남도",
        "경상북도", "경상남도", "제주특별자치도", "강원도", "전라북도")
# 지역 줄임말로 시작하지만 시·도가 아닌 시·군·구 이름
NOT_SIDO_HEAD = ("부산진구",)
SIDO_SHORT = {"서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
              "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
              "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
              "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도",
              "제주": "제주특별자치도"}

AI_PROMPT = """너는 한국 주소 표기 검수기다. 입력 주소의 띄어쓰기와 명백한 오타만 고친다.
규칙:
- 시·도, 시·군·구, 읍·면·동, 도로명(로·길), 건물번호, 상세주소(동·호·층)를 띄어 쓴다.
- 입력에 없는 동·호수·건물번호·지명을 새로 만들지 않는다. 모르면 입력을 그대로 둔다.
- 줄임말 시·도(서울, 경기 등)는 정식 이름으로 바꿔도 된다. 입력에 없는 시·도를 추측해 붙이지 않는다
  (세종대로·대구로처럼 지역 이름으로 시작하는 도로명은 도로명일 뿐이다).
- road_keyword에는 도로명주소 검색에 넣을 부분(상세주소 제외)을, detail에는 동·호·층 같은 상세주소를 넣는다.
- 주소가 아닌 글이면 is_address를 false로 한다."""
AI_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["is_address", "corrected", "road_keyword", "detail", "changed"],
    "properties": {
        "is_address": {"type": "boolean"},
        "corrected": {"type": "string"},
        "road_keyword": {"type": "string"},
        "detail": {"type": "string"},
        "changed": {"type": "boolean"},
    },
}


def tidy(address: str) -> str:
    """붙여 쓴 주소를 규칙대로 띄어 쓴다. 글자는 지우거나 만들지 않는다."""
    s = re.sub(r"[\s　]+", " ", str(address or "")).strip()[:MAX_LEN]
    if not s:
        return ""
    for name in sorted(SIDO, key=len, reverse=True):
        s = re.sub(rf"^{name}(?=\S)", name + " ", s)
    head = s.split(" ", 1)[0]
    # '서울시 강남구' → '서울특별시 강남구'. 제주시(시 이름)·광주시(경기도 광주시와 겹침)는 그대로 둔다.
    m = re.match(r"(서울|부산|대구|인천|대전|울산|세종)시(?=\s|[가-힣]{1,4}(?:구|군)(?:[가-힣\d\s,]|$))", s)
    if m:
        s = SIDO_SHORT[m.group(1)] + " " + s[m.end():].lstrip()
        head = s.split(" ", 1)[0]
    # '서울 강남구', '서울강남구' → '서울특별시 강남구'. 줄임말 뒤가 시·군·구 이름일 때만 바꾼다
    # ('세종대로', '대구로', '부산진구'처럼 지역 이름으로 시작하는 도로·구 이름은 그대로 둔다).
    if head in SIDO_SHORT:
        s = f"{SIDO_SHORT[head]} {s[len(head):].lstrip()}".strip()
    elif (head[:2] in SIDO_SHORT and not head.startswith(tuple(SIDO)) and not head.startswith(NOT_SIDO_HEAD)
          and re.match(r"[가-힣]{1,4}?(?:시|군|구)(?=[가-힣\d\s,]|$)", s[2:])):
        s = f"{SIDO_SHORT[head[:2]]} {s[2:]}"
    # 시·도 이름은 떼어 두고 나머지만 띄어 쓴다('대구광역시'의 '구'를 구 이름으로 보지 않게).
    sido = next((n for n in sorted(SIDO, key=len, reverse=True) if s.startswith(n + " ") or s == n), "")
    if sido:
        s = s[len(sido):].lstrip()
    # 시·군·구 / 읍·면·동 뒤에 바로 다른 지명이 붙은 경우: '강남구테헤란로' → '강남구 테헤란로'
    s = re.sub(r"(?<=[가-힣])(?<![시군구])(?<!\b(?:서울|부산|대구|인천|대전|울산|세종))(시|군|구)(?=[가-힣]{1,10}(?:시|군|구|읍|면|동|로|길|리)(?:\d|\s|$))", r"\1 ", s)
    s = re.sub(r"(?<=[가-힣])(읍|면)(?=[가-힣]{1,10}(?:로|길|리)(?:\d|\s|$))", r"\1 ", s)
    # 도로명 뒤 건물번호: '테헤란로123' → '테헤란로 123', '중앙로12번길3' → '중앙로12번길 3'
    s = re.sub(r"((?:로|길))(\d+(?:-\d+)?)(?=$|[\s,(]|번지|[가-힣])", r"\1 \2", s)
    s = re.sub(r"(로|길) (\d+)(번길|가길|길)", r"\1\2\3", s)
    # 건물번호 뒤 상세주소: '123,101동' / '123 101동202호' → '123, 101동 202호'
    s = re.sub(r"(\d)(?:\s*,\s*|\s+)(\d+동)", r"\1, \2", s)
    s = re.sub(r"(\d+동)(\d+호)", r"\1 \2", s)
    s = re.sub(r"(\d+층)(\d+호)", r"\1 \2", s)
    s = re.sub(r"\s*,\s*", ", ", s)
    if sido:
        s = f"{sido} {s}"
    return re.sub(r" {2,}", " ", s).strip()


def split_detail(address: str) -> tuple[str, str]:
    """도로명주소 검색어와 상세주소(동·호·층)를 나눈다."""
    s = tidy(address)
    m = re.search(r"((?:로|길) \d+(?:-\d+)?)(.*)$", s)
    if m:
        return s[:m.end(1)].strip(), m.group(2).strip(" ,")
    m = re.search(r"(\s\d+(?:-\d+)?(?:번지)?)(\s.*)$", s)
    if m and re.search(r"(동|호|층)", m.group(2)):
        return s[:m.end(1)].strip(), m.group(2).strip(" ,")
    return s, ""


def ai_review(address: str, *, chat=None) -> dict | None:
    """OpenAI로 띄어쓰기·오타만 검수한다. 키가 없거나 실패하면 None."""
    load_env()
    if not (os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")) and chat is None:
        return None
    if chat is None:
        import llm
        chat = llm._chat
    try:
        result = chat(AI_PROMPT, address[:MAX_LEN], AI_SCHEMA, "address_review")
    except Exception:
        return None
    if not isinstance(result, dict) or not result.get("is_address"):
        return None
    corrected = tidy(str(result.get("corrected") or ""))
    if not corrected or _invented_numbers(address, corrected) or _invented_sido(address, corrected):
        return None
    return {"corrected": corrected, "road_keyword": tidy(str(result.get("road_keyword") or "")) or split_detail(corrected)[0],
            "detail": str(result.get("detail") or "").strip()[:60]}


def _invented_sido(before: str, after: str) -> bool:
    """AI가 입력에 없던 시·도를 맨 앞에 붙였는지 확인한다(예: '세종대로 209' → '세종특별자치시 세종대로 209')."""
    head = after.split(" ", 1)[0]
    if head not in SIDO:
        return False
    short = next((k for k, v in SIDO_SHORT.items() if v == head), head[:2])
    return not re.match(rf"\s*(?:{re.escape(head)}|{re.escape(short)}(?!대로|로|길))", before)


def _invented_numbers(before: str, after: str) -> bool:
    """AI가 입력에 없던 숫자(동·호·번지)를 만들었는지 확인한다."""
    return not set(re.findall(r"\d+", after)) <= set(re.findall(r"\d+", before))


def search_juso(keyword: str, *, opener=None, limit: int = 5) -> list[dict] | None:
    """도로명주소 API 검색. 키가 없거나 실패하면 None, 결과가 없으면 빈 목록."""
    load_env()
    key = os.environ.get("JUSO_API_KEY", "").strip()
    keyword = re.sub(r"[%=><\[\]]", " ", keyword or "").strip()[:80]
    if not key or len(keyword) < 2:
        return None
    query = urllib.parse.urlencode({"confmKey": key, "currentPage": 1, "countPerPage": limit,
                                    "keyword": keyword, "resultType": "json"})
    try:
        with (opener or urllib.request.urlopen)(f"{JUSO_URL}?{query}", timeout=8) as r:
            data = json.load(r)
        common = data["results"]["common"]
        if common.get("errorCode") != "0":
            # 운영 로그에서 원인을 볼 수 있게 오류 코드만 남긴다(주소·키는 남기지 않는다).
            print(f"[juso] errorCode={common.get('errorCode')}", flush=True)
            return None
        rows = data["results"].get("juso") or []
    except Exception as exc:
        print(f"[juso] {type(exc).__name__}: {str(exc)[:80].replace(key, '***')}", flush=True)
        return None
    return [{"road": str(j.get("roadAddrPart1") or j.get("roadAddr") or "").strip(),
             "jibun": str(j.get("jibunAddr") or "").strip(),
             "zip": str(j.get("zipNo") or "").strip(),
             "rn": str(j.get("rn") or "").strip(),
             "number": "-".join(x for x in (str(j.get("buldMnnm") or "").strip(), str(j.get("buldSlno") or "").strip())
                                if x and x != "0")} for j in rows[:limit] if j.get("roadAddr")]


def exact_matches(keyword: str, rows: list[dict]) -> tuple[list[dict], bool]:
    """검색 결과 중 입력한 도로명·건물번호와 정확히 같은 주소만 고른다(도로명주소 검색은 비슷한 주소도 돌려준다).
    (주소들, 정확히 같은지). 같은 것이 없거나 번호 없이 지역만 적었으면 비슷한 주소를 그대로 돌려주고 False."""
    rows = list({r["road"]: r for r in reversed(rows)}.values())[::-1]  # 같은 주소가 두 번 오는 경우(건물 여러 동)
    m = re.search(r"([가-힣A-Za-z0-9]+(?:로|길))\s*(\d+(?:-\d+)?)\s*$", keyword or "")
    if not m:
        return rows, bool(re.search(r"\d", keyword or ""))  # 지번 주소(…리 123번지)는 검색 결과를 믿는다
    road, number = m.group(1), m.group(2)
    same = [r for r in rows if r.get("rn") == road and r.get("number") == number]
    return (same, True) if same else (rows, False)


def check(address: str, *, chat=None, opener=None) -> dict:
    """주소 확인 전체 흐름. 화면은 결과의 suggestion/candidates만 보여 준다."""
    original = re.sub(r"[\s　]+", " ", str(address or "")).strip()[:MAX_LEN]
    result = {"original": original, "suggestion": tidy(original), "candidates": [], "detail": "",
              "ai_used": False, "juso_used": False, "verified": False, "notes": []}
    if not original:
        return result
    keyword, detail = split_detail(original)
    review = ai_review(original, chat=chat)
    if review:
        result.update(ai_used=True, suggestion=review["corrected"])
        keyword, detail = review["road_keyword"], review["detail"] or detail
    else:
        result["notes"].append("AI 검수를 쓰지 못해 기본 띄어쓰기 정리만 했습니다.")
    result["detail"] = detail
    found = search_juso(keyword, opener=opener, limit=10)
    if review and found == []:
        # AI가 고친 검색어로 못 찾으면 입력을 규칙으로만 정리한 검색어로 한 번 더 찾는다.
        local_keyword, local_detail = split_detail(original)
        if local_keyword != keyword:
            found = search_juso(local_keyword, opener=opener, limit=10)
            if found:
                keyword, detail = local_keyword, local_detail
                result["detail"] = detail
    exact = True
    if found:
        found, exact = exact_matches(keyword, found)
        found = found[:5]
    if found is None:
        result["notes"].append("도로명주소 검색을 쓰지 못해 실제 주소인지 확인하지 못했습니다.")
        return result
    result["juso_used"] = True
    # 도로명주소 검색으로 확인했으면 AI 검수를 못 쓴 것은 사용자가 알 필요 없다.
    result["notes"] = [n for n in result["notes"] if not n.startswith("AI 검수를")]
    result["candidates"] = [{**c, "full": f"{c['road']} {detail}".strip()} for c in found]
    if found and not exact:
        result["notes"].append("입력한 건물번호와 똑같은 주소는 없어요. 아래 비슷한 주소 중 맞는 것이 있으면 고르고, "
                               "없으면 도로명과 건물번호를 다시 확인해 주세요." if re.search(r"\d", keyword)
                               else "주소가 덜 적혔어요. 도로명과 건물번호까지 적어 주세요(예: 테헤란로 123).")
    elif found:
        result["verified"] = True
        if len(found) == 1:
            result["suggestion"] = result["candidates"][0]["full"]
    else:
        result["notes"].append("도로명주소에서 찾지 못했습니다. 도로명과 건물번호를 확인해 주세요.")
    return result
