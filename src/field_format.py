"""서류마다 다른 날짜·전화번호 표기를 맞춘다.

입력은 사람이 편한 대로 받고(19500101, 1950.1.1, 01012345678 등), 서류에 적을 때는
그 서류의 신청서 부분에 쓰인 표기(년 월 일 / 20 . . . / ( ) - 등)나 칸 이름 옆 안내
('(숫자만)', 'YYYYMMDD')를 따른다. 읽을 수 없는 값은 바꾸지 않고 그대로 둔다.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date

DATE_STYLES = ("dot", "dot_pad", "dash", "korean", "digits", "yymmdd", "dot_tight")
PHONE_STYLES = ("hyphen", "paren", "digits")
DEFAULT_DATE_STYLE = "dot"  # 공문서 표기(행정업무규정): 1950. 1. 1.
DEFAULT_PHONE_STYLE = "hyphen"
# 칸이 좁아 서류 표기로 들어가지 않을 때 다시 시도하는 짧은 표기
COMPACT_STYLES = {"date": "dot_tight", "phone": "digits"}

# 칸 이름 뒤 괄호 속 형식 안내: 생년월일(YYYYMMDD), 연락처(숫자만), 생년월일(예: 1950.01.01)
_HINT_RE = re.compile(
    r"[(（\[]\s*(?:[^)）\]]*?(?:YYYY|yyyy|YY|yy|MM|DD|숫자|자리|하이픈|'-'|-\s*없이|예\s*[:)]|예시|\d{2,4}\s*[.\-/년]\s*\d)[^)）\]]*)\s*[)）\]]"
)


def parse_date(raw: str, *, today: date | None = None) -> date | None:
    """여러 입력 표기를 날짜로 읽는다. 확실하지 않으면 None."""
    s = re.sub(r"\s+", "", str(raw or ""))
    if not s:
        return None
    m = (re.fullmatch(r"((?:19|20)\d{2})[.\-/년]?(\d{1,2})[.\-/월]?(\d{1,2})[.일]?", s)
         if not re.fullmatch(r"\d+", s) else None)
    if m is None and re.fullmatch(r"(?:19|20)\d{6}", s):
        m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m is None and re.fullmatch(r"\d{6}", s):
        yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:])
        now = today or date.today()
        century = 2000 if 2000 + yy <= now.year else 1900
        try:
            return date(century + yy, mm, dd)
        except ValueError:
            return None
    if m is None:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def format_date(d: date, style: str = DEFAULT_DATE_STYLE) -> str:
    if style == "dot_pad":
        return f"{d.year}. {d.month:02d}. {d.day:02d}."
    if style == "dash":
        return f"{d.year}-{d.month:02d}-{d.day:02d}"
    if style == "korean":
        return f"{d.year}년 {d.month}월 {d.day}일"
    if style == "digits":
        return f"{d.year}{d.month:02d}{d.day:02d}"
    if style == "yymmdd":
        return f"{d.year % 100:02d}{d.month:02d}{d.day:02d}"
    if style == "dot_tight":
        return f"{d.year}.{d.month}.{d.day}."
    return f"{d.year}. {d.month}. {d.day}."


def phone_digits(raw: str) -> str:
    s = str(raw or "").strip()
    digits = re.sub(r"\D", "", s)
    if s.startswith("+82") or (digits.startswith("82") and len(digits) in (11, 12)):
        digits = "0" + digits[2:]
    return digits


def phone_parts(raw: str) -> list[str] | None:
    """국내 전화번호를 국번 규칙대로 나눈다. 규칙에 맞지 않으면 None."""
    d = phone_digits(raw)
    n = len(d)
    if d.startswith("02") and n in (9, 10):
        return [d[:2], d[2:n - 4], d[n - 4:]]
    if re.fullmatch(r"1[5-9]\d{6}", d):  # 대표번호 1588-0000
        return [d[:4], d[4:]]
    if re.fullmatch(r"050\d{8,9}", d):  # 안심번호 0505-000-0000
        return [d[:4], d[4:n - 4], d[n - 4:]]
    if re.fullmatch(r"0(?:1[016789]|70|[3-6][1-5])\d{7,8}", d):
        return [d[:3], d[3:n - 4], d[n - 4:]]
    return None


def format_phone(raw: str, style: str = DEFAULT_PHONE_STYLE) -> str:
    parts = phone_parts(raw)
    if parts is None:
        return str(raw or "").strip()
    if style == "digits":
        return "".join(parts)
    if style == "paren" and len(parts) == 3:
        return f"({parts[0]}) {parts[1]}-{parts[2]}"
    return "-".join(parts)


def split_label_hint(raw_label: str) -> tuple[str, dict[str, str]]:
    """칸 이름에서 형식 안내를 떼어 내고, 안내가 뜻하는 표기를 돌려준다."""
    label = raw_label or ""
    styles: dict[str, str] = {}
    for m in _HINT_RE.finditer(label):
        hint = m.group(0)
        date_style = _date_style_from_hint(hint)
        if date_style:
            styles["date"] = date_style
        compact = re.sub(r"\s+", "", hint)
        if re.search(r"숫자만|-없이|'-'없이|하이픈없이|하이픈제외", compact):
            styles.setdefault("date", "digits")
            styles["phone"] = "digits"
        elif re.search(r"하이픈포함|'-'포함|-포함|\d{2,3}-\d{3,4}-\d{4}", compact):
            styles["phone"] = "hyphen"
    if not styles:
        return label, styles
    return _HINT_RE.sub("", label).strip(), styles


def _date_style_from_hint(hint: str) -> str | None:
    h = re.sub(r"\s+", "", hint)
    if re.search(r"YYYYMMDD|yyyymmdd|8자리", h):
        return "digits"
    if re.search(r"YYMMDD|yymmdd|6자리", h):
        return "yymmdd"
    if re.search(r"(?:YYYY|yyyy)-(?:MM|mm)-(?:DD|dd)|\d{4}-\d{1,2}-\d{1,2}", h):
        return "dash"
    if re.search(r"(?:YYYY|yyyy)\.(?:MM|mm)\.(?:DD|dd)|\d{4}\.\d{2}\.\d{2}", h):
        return "dot_pad"
    if re.search(r"\d{4}\.\d{1,2}\.\d{1,2}", h):
        return "dot"
    if re.search(r"년.*월.*일", h):
        return "korean"
    return None


def detect_document_styles(form_text: str) -> dict[str, str]:
    """신청서 부분의 글자에서 이 서류가 쓰는 날짜·전화 표기를 고른다.

    빈 날짜 자리('년  월  일', '20  .  .  .')와 이미 적힌 예시 날짜를 함께 센다.
    근거가 없으면 기본값(공문서 표기, 하이픈 전화번호)을 쓴다.
    """
    text = form_text or ""
    votes: Counter[str] = Counter()
    votes["korean"] += len(re.findall(r"년[ \t　]{1,}월[ \t　]{1,}일", text))
    votes["korean"] += len(re.findall(r"(?:19|20)\d{2}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일", text))
    votes["dot"] += len(re.findall(r"(?:20|19)?[ \t　]*\.[ \t　]{2,}\.[ \t　]{2,}\.", text))
    votes["dot_pad"] += len(re.findall(r"(?:19|20)\d{2}\.\s?(?:0\d|1[0-2])\.\s?(?:0\d|[12]\d|3[01])\.?", text))
    votes["dot"] += len(re.findall(r"(?:19|20)\d{2}\.\s?[1-9]\.\s?\d{1,2}\.?", text))
    votes["dash"] += len(re.findall(r"(?:19|20)\d{2}-\d{2}-\d{2}", text))
    date_style = DEFAULT_DATE_STYLE
    if votes:
        best, count = votes.most_common(1)[0]
        if count:
            # '년 월 일' 자리가 하나라도 있으면 서식 자체가 한글 날짜를 쓴다.
            date_style = best
    phone_style = DEFAULT_PHONE_STYLE
    compact = re.sub(r"[ \t　]+", " ", text)
    if re.search(r"(?:연락처|전화|휴대)[^\n]{0,12}\(\s*\)\s*-", compact) or re.search(r"\(\s{2,}\)\s{2,}-\s{2,}", text):
        phone_style = "paren"
    elif re.search(r"(?:연락처|전화|휴대)[^\n]{0,20}숫자\s*만", compact):
        phone_style = "digits"
    return {"date": date_style, "phone": phone_style}


def adapt_value(field_key: str, value: str, styles: dict[str, str]) -> str:
    """한 칸에 들어갈 값을 표기에 맞춘다. 읽을 수 없는 값은 원래대로 둔다."""
    v = str(value or "").strip()
    if not v:
        return v
    if field_key == "applicant.birth_date" and "date" in styles:
        d = parse_date(v)
        return format_date(d, styles["date"]) if d else v
    if field_key in ("applicant.phone", "proxy.phone") and "phone" in styles:
        return format_phone(v, styles["phone"])
    return v


def adapt_profile(profile: dict[str, str], styles: dict[str, str]) -> dict[str, str]:
    out = dict(profile or {})
    for key in ("applicant.birth_date", "applicant.phone", "proxy.phone"):
        if out.get(key):
            out[key] = adapt_value(key, out[key], styles)
    return out


def describe(styles: dict[str, str]) -> list[str]:
    """화면 안내용: 이 서류에 어떤 표기로 넣었는지."""
    sample_date = format_date(date(1950, 1, 1), styles.get("date", DEFAULT_DATE_STYLE))
    sample_phone = format_phone("01012345678", styles.get("phone", DEFAULT_PHONE_STYLE))
    return [f"날짜 {sample_date}", f"전화번호 {sample_phone}"]
