"""신청 준비 꾸러미: 아는 정보를 미리 채운 작성 도우미 PDF + 복지로 원본 양식 내려받기를 ZIP 하나로.

원본 hwp 양식은 기관마다 칸 배치가 달라 자동으로 채우지 않는다. 대신 같은 내용을 PDF로 채워 두고,
주민등록번호·서명·계좌·소득처럼 본인만 쓸 수 있는 칸은 '직접 작성'으로 비워 둔다. 입력은 저장하지 않는다.
"""
import io
import re
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

from fpdf import FPDF, FontFace

import paperwork

FONTS = Path(__file__).resolve().parent.parent / "assets" / "fonts"
SELF = "(직접 작성)"
FORM_WORDS = r"신청.{0,6}서|서식|동의서|위임장|확인서|신고서|양식"
MAX_BYTES = 20 * 1024 * 1024  # 이보다 큰 안내 자료는 ZIP에 넣지 않고 주소만 남긴다


def is_form(name: str) -> bool:
    """첨부 파일 중 직접 써서 내는 양식인지(아니면 조례·공고문 같은 참고 자료)."""
    return bool(re.search(FORM_WORDS, name))


def download(url: str, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("파일이 너무 큼")
    if data[:1] == b"{" and b'"ERROR"' in data[:200]:  # 복지로는 없는 파일에 200 + JSON 오류를 준다
        raise FileNotFoundError(data[:200].decode(errors="replace"))
    return data


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)[:80]


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
        self.cell(0, 5, "복지온길 작성 도우미 · 공식 서식이 아닙니다 · 기관 신청서에 옮겨 쓰거나 상담 때 보여 주세요", align="C")

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


def build_pdf(row: dict, p: dict | None, extra: dict) -> bytes:
    """row: build()의 한 줄, p: 상담 프로필(없으면 빈칸), extra: 연락처·상세 주소·대리인."""
    s, p = row["svc"], p or {}
    today = date.today().strftime("%Y년 %m월 %d일")
    region = " ".join(x for x in (p.get("sido", ""), p.get("sgg", "")) if x)
    addr = " ".join(x for x in (region, extra.get("address", "")) if x)
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
    d.para("· 기관 신청서(원본 양식)에 서명하기\n· 주민등록번호, 받을 계좌번호 적기\n· 소득·재산 서류 떼기(필요한 경우)\n"
           "· 주민센터 방문 또는 온라인으로 제출하기 (몸이 불편하면 전화로 방문 상담·직권 신청 요청)")
    if s.get("link"):
        d.section("복지로 안내")
        d.set_font("NG", "", 9)
        d.multi_cell(0, 5, s["link"], link=s["link"], new_x="LMARGIN", new_y="NEXT")

    # 2. 신청서 작성 도우미
    d.add_page()
    d.top("신청서 작성 도우미", "아래 내용을 기관 신청서에 옮겨 적으세요. 노란 칸은 본인이 직접 쓰는 칸이에요.")
    d.rows([
        ("신청 복지", s["servNm"]), ("담당 기관", s["org"]),
        ("성명", p.get("name") or SELF), ("주민등록번호", SELF),
        ("나이", f"{p['age']}세" if p.get("age") is not None else SELF),
        ("주소", addr + ("" if extra.get("address") else f" · 상세 주소 {SELF}") if addr else SELF),
        ("연락처", extra.get("phone") or SELF), ("직업", p.get("job") or SELF),
        ("가구 구성", p.get("family") or SELF), ("해당 사항", ", ".join(p.get("groups", [])) or "없음"),
        ("신청 사유", _reason(p)), ("받을 계좌", f"{SELF} 은행·계좌번호·예금주(본인 명의)"),
        ("소득·재산", f"{SELF} 기관 조사로 확인해요. 요청하면 서류를 내요."),
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
            ("연락처", SELF), ("신분증", "방문할 때 위임받는 사람 신분증을 챙겨요")])
    d.section("위임 내용")
    d.para(f"위 사람에게 '{s['servNm']}' 신청과 서류 제출을 위임합니다.\n\n{today}")
    return bytes(d.output())


def build_zip(row: dict, p: dict | None, extra: dict, fetch=download) -> tuple[bytes, list[str]]:
    """(ZIP, 못 받은 파일 이름). fetch는 테스트에서 바꿔 끼운다."""
    s = row["svc"]
    buf, failed = io.BytesIO(), []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"1_신청준비_{_safe(s['servNm'])}.pdf", build_pdf(row, p, extra))
        seen = set()
        for f in s.get("form_files", []):
            if f["url"] in seen:
                continue
            seen.add(f["url"])
            folder = "2_기관양식(작성해서 제출)" if is_form(f["name"]) else "3_참고자료(읽어 보기)"
            try:
                z.writestr(f"{folder}/{_safe(f['name'])}", fetch(f["url"]))
            except Exception:  # 복지로 응답이 없으면 주소만 남긴다
                failed.append(f["name"])
        if failed:
            z.writestr("내려받지_못한_파일.txt", "용량이 크거나 복지로에서 받지 못한 파일이에요. 아래 주소로 직접 받아 주세요.\n\n"
                       + "\n".join(f"{f['name']}\n{f['url']}\n" for f in s["form_files"] if f["name"] in failed))
    return buf.getvalue(), failed
