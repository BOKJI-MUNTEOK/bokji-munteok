"""원문의 숫자(나이·소득 기준·기간·금액)가 쉬운 설명에 모두 남았는지 대조한다."""
import re

NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
PAT = re.compile(NUM + r"\s*(억\s*원|천만\s*원|만\s*원|천\s*원|원|세|%|퍼센트|개월|년|일|주|시간|회)")
MULT = {"억원": 10**8, "천만원": 10**7, "만원": 10**4, "천원": 10**3, "원": 1}
KIND = {"세": "나이", "%": "비율", "퍼센트": "비율", "개월": "기간", "년": "기간", "일": "기간",
        "주": "기간", "시간": "기간", "회": "횟수"}


def _norm(value: str, unit: str):
    v = float(value.replace(",", ""))
    unit = unit.replace(" ", "")
    if unit in MULT:
        return ("금액", round(v * MULT[unit]))
    if unit == "퍼센트":
        unit = "%"
    return (KIND[unit], v, unit)


RANGE = re.compile(NUM + r"\s*[~∼～\-]\s*" + NUM + r"\s*(세|%|개월|년|일|시간|만\s*원|원)")


def numbers(text: str) -> list[dict]:
    out, seen = [], set()
    # '19~44세' → '19세~44세'로 펼쳐 양쪽 숫자를 모두 잡는다
    text = RANGE.sub(lambda m: f"{m.group(1)}{m.group(3)}~{m.group(2)}{m.group(3)}", text or "")
    for m in PAT.finditer(text):
        val, unit = m.group(1), m.group(2)
        # 연도(2026년), 날짜('19.1.1), 기준연도 같은 것은 뺀다
        if unit == "년" and len(val.replace(",", "")) == 4:
            continue
        key = _norm(val, unit)
        if key in seen:
            continue
        seen.add(key)
        out.append({"text": m.group(0), "kind": key[0], "key": key})
    return out


def check(original: str, easy: str) -> dict:
    src = numbers(original)
    have = {n["key"] for n in numbers(easy)}
    missing = [n for n in src if n["key"] not in have]
    return {"total": len(src), "kept": len(src) - len(missing), "missing": [n["text"] for n in missing],
            "ok": not missing}
