"""신청 준비 꾸러미: 입력 정보로 기관 양식을 미리 채우고 작성 도우미 PDF·참고자료를 ZIP으로 묶는다.

원칙:
- 기관 양식(.hwp·.hwpx·.pdf)은 단일 진입 함수 `fill_form`으로 채우고 서명·날인 칸은 비워 둔다.
- 기입 성공 양식은 `작성됨_<원래 이름>`, 실패·미지원 양식은 원본 그대로 넣고 PDF에 '직접 작성 필요' 목록을 적는다.
- 사용자 입력값과 개인정보는 저장·로그·외부 전송하지 않는다.
"""
import io
import re
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

from fpdf import FPDF, FontFace

import fill_form
import paperwork

FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
SELF = "(직접 작성)"
FORM_WORDS = r"신청.{0,6}서|서식|동의서|위임장|확인서|신고서|양식|청약서"
MAX_BYTES = 20 * 1024 * 1024  # 이보다 큰 안내 자료는 ZIP에 넣지 않고 주소만 남긴다
ALLOWED_DOWNLOAD_HOSTS = frozenset({"www.bokjiro.go.kr", "bokjiro.go.kr"})


def is_form(name: str) -> bool:
    """첨부 파일 중 직접 써서 내는 양식인지(아니면 조례·공고문 같은 참고 자료)."""
    return bool(re.search(FORM_WORDS, name))


def validate_download_url(url: str) -> str:
    """서식 다운로드 주소를 검증한다. 복지로 HTTPS 도메인만 허용하며 실패 시 주소를 노출하지 않는다."""
    if not isinstance(url, str) or any(ord(ch) < 32 for ch in url):
        raise ValueError("허용되지 않은 주소")
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("허용되지 않은 주소")
    parsed = urllib.parse.urlsplit(cleaned)
    if parsed.scheme.lower() != "https":
        raise ValueError("허용되지 않은 주소")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError("허용되지 않은 주소")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("허용되지 않은 주소") from None
    if port not in (None, 443):
        raise ValueError("허용되지 않은 주소")
    host = (parsed.hostname or "").lower()
    if host not in ALLOWED_DOWNLOAD_HOSTS or parsed.netloc.lower() not in (host, f"{host}:443"):
        raise ValueError("허용되지 않은 주소")
    return cleaned


def download(url: str, timeout: int = 15) -> bytes:
    safe_url = validate_download_url(url)
    req = urllib.request.Request(safe_url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        validate_download_url(r.geturl())
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("파일이 너무 큼")
    if data[:1] == b"{" and b'"ERROR"' in data[:200]:  # 복지로는 없는 파일에 200 + JSON 오류를 준다
        raise FileNotFoundError(data[:200].decode(errors="replace"))
    return data


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)[:80]


def _safe_with_ext(name: str, prefix: str = "") -> str:
    cleaned = re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "form"
    p = Path(cleaned)
    stem, suffix = p.stem, p.suffix
    max_stem = max(8, 80 - len(prefix) - len(suffix))
    return f"{prefix}{stem[:max_stem]}{suffix}"


def _manual_reason_ko(rep: dict) -> str:
    status = rep.get("status", "")
    err = rep.get("error", "")
    if status == "unsupported":
        if err == "hwplib_runtime_unavailable":
            return "HWP 자동 기입 런타임 미설치 · 원본 첨부"
        return "미지원 파일 형식 · 원본 첨부"
    if status == "no_fillable_fields":
        return "입력 정보 없음 또는 자동 기입 칸 미감지 · 원본 첨부"
    return "서식 열기·기입 오류 · 원본 첨부"


def _mask_rrn(rrn: str) -> str:
    digits = re.sub(r"\D", "", rrn or "")
    if len(digits) >= 6:
        return f"{digits[:6]}-*******"
    return "*******"


class _Doc(FPDF):
    def __init__(self):
        super().__init__(format="A4")
        self.add_font("NG", "", str(FONTS / "NanumGothic-Regular.ttf"))
        self.add_font("NG", "B", str(FONTS / "NanumGothic-Bold.ttf"))
        self.set_auto_page_break(True, 18)
        self.set_margins(16, 16, 16)

    def footer(self):
        self.set_y(-13)
        self.set_font("NG", "", 8)
        self.set_text_color(120)
        self.cell(0, 5, "복지문턱 작성 도우미 · 공식 서식이 아닙니다 · 기관 신청서에 옮겨 쓰거나 상담 때 보여 주세요", align="C")

    def top(self, text, sub=""):
        self.set_font("NG", "B", 18)
        self.set_text_color(6, 58, 116)
        self.cell(0, 11, text, new_x="LMARGIN", new_y="NEXT")
        if sub:
            self.set_font("NG", "", 10)
            self.set_text_color(80)
            self.multi_cell(0, 6, sub, new_x="LMARGIN", new_y="NEXT")
        self.ln(3)

    def section(self, text):
        self.ln(2)
        self.set_font("NG", "B", 12)
        self.set_text_color(37, 110, 244)
        self.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.set_text_color(30)

    def para(self, text):
        self.set_font("NG", "", 10.5)
        self.multi_cell(0, 6.5, text, new_x="LMARGIN", new_y="NEXT")

    def rows(self, items, w=42):
        """(이름, 값) 표. 값에 SELF가 있으면 노란 칸(본인이 직접 쓰는 칸)."""
        self.set_font("NG", "", 10.5)
        with self.table(col_widths=(w, self.epw - w), first_row_as_headings=False, line_height=6.5, padding=2) as t:
            for k, v in items:
                r = t.row()
                r.cell(k, style=FontFace(emphasis="BOLD", fill_color=(244, 245, 246)))
                mine = SELF in v
                r.cell(v, style=FontFace(color=(140, 110, 0) if mine else None, fill_color=(255, 248, 214) if mine else None))


def _reason(p: dict) -> str:
    want = p.get("themes", []) + p.get("notes", [])
    return (f"{', '.join(want)} 문제로 도움이 필요하여 신청합니다." if want else SELF)


def build_pdf(
    row: dict,
    p: dict | None,
    extra: dict,
    manual_forms: list[tuple[str, str]] | None = None,
    filled_forms: list[str] | None = None,
) -> bytes:
    """row: build()의 한 줄, p: 상담 프로필(없으면 빈칸), extra: 연락처·상세 주소·주민번호·계좌·소득·대리인."""
    s, p = row["svc"], p or {}
    today = date.today().strftime("%Y년 %m월 %d일")
    region = " ".join(x for x in (p.get("sido", ""), p.get("sgg", "")) if x)
    addr = " ".join(x for x in (region, extra.get("address", "")) if x)
    rrn_val = str(extra.get("rrn") or "").strip()
    rrn_disp = f"{_mask_rrn(rrn_val)} · 기관 양식에 자동 기입" if rrn_val else SELF
    bank = str(extra.get("bank") or "").strip()
    acct_num = str(extra.get("account_number") or "").strip()
    acct_holder = str(extra.get("account_holder") or "").strip()
    acct_parts = [x for x in (bank, acct_num) if x]
    if acct_parts:
        acct_disp = " ".join(acct_parts) + (f" (예금주: {acct_holder})" if acct_holder else "")
    elif acct_holder:
        acct_disp = f"{SELF} 은행·계좌번호 (예금주: {acct_holder})"
    else:
        acct_disp = f"{SELF} 은행·계좌번호·예금주(본인 명의)"
    income_val = str(extra.get("monthly_income") or "").strip()
    income_disp = f"월 {income_val} (기관 조사·서류 요청 시 확인해요)" if income_val else f"{SELF} 기관 조사로 확인해요. 요청하면 서류를 내요."

    docs, listed = paperwork.documents(row)
    steps = (row.get("ai") or {}).get("easy", {}).get("steps") or ["담당 기관에 전화해 신청 방법과 서류를 확인해요.",
                                                                    "서류를 챙겨 신청해요."]
    d = _Doc()

    # 1. 신청 준비표
    d.add_page()
    d.top("신청 준비표", f"{s['servNm']}\n{s['org']} · {s['region']} · 문의 {s.get('contact') or '복지로 129'}")
    d.section("할 일 순서")
    d.para("\n".join(f"{i}. {x}" for i, x in enumerate(steps, 1)))
    d.section("챙길 서류" + ("" if listed else " (안내문에 목록이 없어 기본 서류만 적었어요)"))
    d.rows([(f"□ {doc}", (w[0] + (" · 온라인 발급 가능" if w[1] else "")) if (w := paperwork.where(doc)) else "담당 기관에 문의")
            for doc in docs], w=62)
    d.section("본인이 직접 하셔야 할 일")
    d.para("· '작성됨_' 기관 양식은 채워진 내용을 확인한 뒤 직접 서명·날인하기\n"
           "· 아래 '직접 작성 필요' 양식은 빈칸과 서명을 채워 내기\n"
           "· 소득·재산 서류 떼기(필요한 경우)\n"
           "· 주민센터 방문 또는 온라인으로 제출하기 (몸이 불편하면 전화로 방문 상담·직권 신청 요청)")
    if filled_forms:
        d.section("미리 채운 양식 (서명·날인만 직접 작성)")
        d.para("\n".join(f"· 작성됨_{_safe_with_ext(name)} (원본: {name})" for name in filled_forms))
    if manual_forms:
        d.section("직접 작성 필요")
        d.para("\n".join(f"· {name} ({reason})" for name, reason in manual_forms))
    if s.get("link"):
        d.section("복지로 안내")
        d.set_font("NG", "", 9)
        d.multi_cell(0, 5, s["link"], link=s["link"], new_x="LMARGIN", new_y="NEXT")

    # 2. 신청서 작성 도우미
    d.add_page()
    d.top("신청서 작성 도우미", "아래 내용을 기관 신청서에 옮겨 적으세요. 노란 칸은 본인이 직접 쓰는 칸이에요.")
    d.rows([
        ("신청 복지", s["servNm"]), ("담당 기관", s["org"]),
        ("성명", p.get("name") or SELF), ("주민등록번호", rrn_disp),
        ("나이", f"{p['age']}세" if p.get("age") is not None else SELF),
        ("주소", addr + ("" if extra.get("address") else f" · 상세 주소 {SELF}") if addr else SELF),
        ("연락처", extra.get("phone") or SELF), ("직업", p.get("job") or SELF),
        ("가구 구성", p.get("family") or SELF), ("해당 사항", ", ".join(p.get("groups", [])) or "없음"),
        ("신청 사유", _reason(p)), ("받을 계좌", acct_disp),
        ("소득·재산", income_disp),
        ("신청일", today), ("서명", f"{SELF} 신청인 서명 또는 도장"),
    ])

    # 3. 위임장 도우미(가족이 대신 낼 때)
    d.add_page()
    d.top("위임장 도우미", "가족이나 이웃이 대신 신청할 때 써요. 기관 위임장 양식이 있으면 그 양식에 옮겨 적으세요.")
    d.section("위임하는 사람 (신청인)")
    d.rows([("성명", p.get("name") or SELF), ("주소", addr or SELF), ("연락처", extra.get("phone") or SELF),
            ("서명", SELF)])
    d.section("위임받는 사람 (대신 내는 사람)")
    d.rows([("성명", extra.get("proxy") or SELF), ("신청인과 관계", extra.get("relation") or SELF),
            ("연락처", extra.get("proxy_phone") or SELF), ("신분증", "방문할 때 위임받는 사람 신분증을 챙겨요")])
    d.section("위임 내용")
    d.para(f"위 사람에게 '{s['servNm']}' 신청과 서류 제출을 위임합니다.\n\n{today}")
    return bytes(d.output())


def _unique_arcname(arcname: str, used: set[str]) -> str:
    if arcname not in used:
        used.add(arcname)
        return arcname
    p = Path(arcname)
    idx = 2
    while True:
        cand = f"{p.parent.as_posix()}/{p.stem}_{idx}{p.suffix}" if str(p.parent) != "." else f"{p.stem}_{idx}{p.suffix}"
        if cand not in used:
            used.add(cand)
            return cand
        idx += 1


def build_zip(row: dict, p: dict | None, extra: dict, fetch=None) -> tuple[bytes, list[str]]:
    """(ZIP, 못 받은 파일 이름). 기관 양식은 fill_form으로 채우고, fetch는 테스트에서 바꿔 끼운다."""
    if fetch is None:
        fetch = download
    s = row["svc"]
    profile = fill_form.build_profile(p, extra)
    buf = io.BytesIO()
    failed: list[str] = []
    failed_entries: list[tuple[str, str, bool]] = []
    attachments: list[tuple[str, bytes]] = []
    filled_forms: list[str] = []
    manual_forms: list[tuple[str, str]] = []
    used_arcnames: set[str] = set()

    seen = set()
    for f in s.get("form_files", []):
        if f["url"] in seen:
            continue
        seen.add(f["url"])
        try:
            safe_url = validate_download_url(f["url"])
            raw = fetch(safe_url)
        except ValueError as exc:
            is_disallowed = "허용되지 않은 주소" in str(exc)
            failed.append(f["name"])
            failed_entries.append((f["name"], f["url"], is_disallowed))
            if is_form(f["name"]):
                reason = "허용되지 않은 주소 · 복지로에서 직접 받아 작성" if is_disallowed else "내려받지 못함 · 복지로에서 직접 받아 작성"
                manual_forms.append((f["name"], reason))
            continue
        except Exception:  # 복지로 응답이 없으면 주소만 남긴다
            failed.append(f["name"])
            failed_entries.append((f["name"], f["url"], False))
            if is_form(f["name"]):
                manual_forms.append((f["name"], "내려받지 못함 · 복지로에서 직접 받아 작성"))
            continue

        if is_form(f["name"]):
            out_bytes, rep = fill_form.fill_form(raw, f["name"], profile)
            if rep.get("status") == "ok" and int(rep.get("filled_count", 0) or 0) > 0:
                arcname = f"2_기관양식(작성해서 제출)/{_safe_with_ext(f['name'], prefix='작성됨_')}"
                attachments.append((_unique_arcname(arcname, used_arcnames), out_bytes))
                filled_forms.append(f["name"])
            else:
                arcname = f"2_기관양식(작성해서 제출)/{_safe_with_ext(f['name'])}"
                attachments.append((_unique_arcname(arcname, used_arcnames), raw))
                manual_forms.append((f["name"], _manual_reason_ko(rep)))
        else:
            arcname = f"3_참고자료(읽어 보기)/{_safe_with_ext(f['name'])}"
            attachments.append((_unique_arcname(arcname, used_arcnames), raw))

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            f"1_신청준비_{_safe(s['servNm'])}.pdf",
            build_pdf(row, p, extra, manual_forms=manual_forms, filled_forms=filled_forms),
        )
        for arcname, payload in attachments:
            z.writestr(arcname, payload)
        if failed_entries:
            lines = [
                f"{fname}\n(허용되지 않은 주소라 내려받지 않았습니다)\n" if rejected else f"{fname}\n{furl}\n"
                for fname, furl, rejected in failed_entries
            ]
            z.writestr(
                "내려받지_못한_파일.txt",
                "용량이 크거나 복지로에서 받지 못한 파일이에요. 아래 주소로 직접 받아 주세요.\n\n" + "\n".join(lines),
            )
    return buf.getvalue(), failed
