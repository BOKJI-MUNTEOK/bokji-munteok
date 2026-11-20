"""보안(.hwpx ZIP 상한·XXE 차단), PDF 회전·CropBox·AcroForm, HWP/청약서 기입 규칙 단위 테스트."""
from __future__ import annotations

import ast
import io
import json
import re
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pdfplumber
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    ArrayObject,
    BooleanObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import fill_form  # noqa: E402
import fill_hwp  # noqa: E402
import fill_hwp_plan  # noqa: E402
import fill_hwpx  # noqa: E402
import fill_pdf  # noqa: E402
import form_matcher as fm  # noqa: E402
import packet  # noqa: E402


def _make_minimal_hwpx(section_xml: bytes, extra_entries: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    extras = dict(extra_entries or {})
    header_xml = extras.pop("Contents/header.xml", b"<hh:head xmlns:hh='http://www.hancom.co.kr/hwpml/2011/head'/>")
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", b"application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("Contents/header.xml", header_xml)
        zf.writestr("Contents/section0.xml", section_xml)
        for k, v in extras.items():
            zf.writestr(k, v)
    return buf.getvalue()


class TestAutofillSecurityAndEngines(unittest.TestCase):
    def test_hwpx_rejects_oversized_uncompressed_zip(self) -> None:
        big_bytes = b"\x00" * (fill_hwpx.MAX_HWPX_UNCOMPRESSED_BYTES + 1024)
        sec = b"<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section'/>"
        data = _make_minimal_hwpx(sec, {"BinData/bomb.bin": big_bytes})

        out_bytes, report = fill_hwpx.fill_hwpx_bytes(data)
        self.assertEqual(out_bytes, data)
        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(report["error"], "hwpx_zip_limit_exceeded")
        self.assertEqual(report["filled_count"], 0)

    def test_hwpx_rejects_too_many_entries(self) -> None:
        sec = b"<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section'/>"
        extra = {f"extra/f{i}.txt": b"x" for i in range(fill_hwpx.MAX_HWPX_ENTRIES + 5)}
        data = _make_minimal_hwpx(sec, extra)

        out_bytes, report = fill_hwpx.fill_hwpx_bytes(data)
        self.assertEqual(out_bytes, data)
        self.assertEqual(report["status"], "unsupported")
        self.assertEqual(report["error"], "hwpx_zip_limit_exceeded")

    def test_hwpx_blocks_xxe_external_entity(self) -> None:
        xxe_section = (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<!DOCTYPE sec [<!ENTITY xxe SYSTEM "file:///etc/hosts">]>\n'
            b'<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            b'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            b'<hp:p><hp:run><hp:t>&xxe;</hp:t></hp:run></hp:p>'
            b'<hp:p><hp:run><hp:t>\xec\x8b\xa0\xec\xb2\xad\xec\x9d\xb8 :               (\xec\x84\x9c\xeb\xaa\x85)</hp:t></hp:run></hp:p>'
            b'</hs:sec>'
        )
        data = _make_minimal_hwpx(xxe_section)
        out_bytes, report = fill_hwpx.fill_hwpx_bytes(data)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["filled_count"], 1)
        with zipfile.ZipFile(io.BytesIO(out_bytes), "r") as zout:
            sec_out = zout.read("Contents/section0.xml").decode("utf-8", errors="ignore")
        self.assertNotIn("127.0.0.1", sec_out)
        self.assertNotIn("localhost", sec_out)
        self.assertIn("홍길동", sec_out)

    def test_hwpx_date20_fills_two_digit_year(self) -> None:
        sec_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:p><hp:run><hp:t>위와 같이 동의하며 신청합니다.</hp:t></hp:run></hp:p>'
            '<hp:p><hp:run><hp:t>20   년     월     일</hp:t></hp:run></hp:p>'
            '<hp:p><hp:run><hp:t>신청인 :               (서명)</hp:t></hp:run></hp:p>'
            '</hs:sec>'
        ).encode("utf-8")
        data = _make_minimal_hwpx(sec_xml)
        out_bytes, report = fill_hwpx.fill_hwpx_bytes(data, profile=fm.default_profile(date(2026, 11, 19)))
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["filled_count"], 2)
        with zipfile.ZipFile(io.BytesIO(out_bytes), "r") as zout:
            sec_out = zout.read("Contents/section0.xml").decode("utf-8")
        # 공백 대체 후 여백 폭을 유지하므로 1칸 이상 공백을 정규화한 문자열로 값·서명 공란 확인
        norm_out = re.sub(r"\s+", " ", sec_out)
        self.assertIn("2026 년 11 월 19 일", norm_out)
        self.assertIn("신청인 : 홍길동", sec_out)
        self.assertIn("(서명)", sec_out)

    def test_pdf_rotation_and_cropbox_overlay(self) -> None:
        font_name = "TestNG"
        if font_name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(font_name, str(fill_pdf.FONT_PATH)))

        buf = io.BytesIO()
        c = Canvas(buf, pagesize=(500, 700))
        c.rect(100, 120, 40, 120)
        c.rect(100, 240, 40, 220)
        c.saveState()
        c.translate(124, 135)
        c.rotate(90)
        c.setFont(font_name, 10)
        c.drawString(0, 0, "성 명")
        c.restoreState()
        c.save()

        reader = PdfReader(io.BytesIO(buf.getvalue()))
        writer = PdfWriter(clone_from=reader)
        pg = writer.pages[0]
        pg.cropbox.lower_left = (20, 20)
        pg.cropbox.upper_right = (480, 680)
        pg.rotate(90)
        rotated_buf = io.BytesIO()
        writer.write(rotated_buf)

        out_bytes, report = fill_pdf.fill_pdf_bytes(rotated_buf.getvalue())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["filled_count"], 1)
        self.assertEqual(report["filled_fields"][0]["field_key"], "applicant.name")

        with pdfplumber.open(io.BytesIO(out_bytes)) as pdf:
            words = pdf.pages[0].extract_words()
            filled_word = next((w for w in words if "홍길동" in w["text"]), None)
            self.assertIsNotNone(filled_word)
            self.assertEqual(filled_word["direction"], "ltr")
            rects = pdf.pages[0].rects
            right_rect = max(rects, key=lambda r: r["x1"])
            self.assertLessEqual(right_rect["x0"], filled_word["x0"])
            self.assertLessEqual(filled_word["x1"], right_rect["x1"])
            self.assertLessEqual(right_rect["top"], filled_word["top"])
            self.assertLessEqual(filled_word["bottom"], right_rect["bottom"])

    def test_pdf_acroform_fill(self) -> None:
        buf = io.BytesIO()
        c = Canvas(buf, pagesize=(400, 300))
        c.drawString(50, 220, "Applicant Form")
        c.save()

        reader = PdfReader(io.BytesIO(buf.getvalue()))
        writer = PdfWriter(clone_from=reader)
        page = writer.pages[0]

        widget = DictionaryObject({
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Widget"),
            NameObject("/FT"): NameObject("/Tx"),
            NameObject("/T"): TextStringObject("applicant_name"),
            NameObject("/TU"): TextStringObject("성명"),
            NameObject("/V"): TextStringObject(""),
            NameObject("/Rect"): ArrayObject([NumberObject(100), NumberObject(150), NumberObject(250), NumberObject(175)]),
            NameObject("/P"): page.indirect_reference,
        })
        w_ref = writer._add_object(widget)
        page[NameObject("/Annots")] = ArrayObject([w_ref])
        writer._root_object[NameObject("/AcroForm")] = DictionaryObject({
            NameObject("/Fields"): ArrayObject([w_ref]),
            NameObject("/NeedAppearances"): BooleanObject(True),
        })

        in_buf = io.BytesIO()
        writer.write(in_buf)

        out_bytes, report = fill_pdf.fill_pdf_bytes(in_buf.getvalue())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["filled_count"], 1)
        self.assertEqual(report["filled_fields"][0]["mode"], "acroform_overlay")

        r_out = PdfReader(io.BytesIO(out_bytes))
        fields = r_out.get_fields()
        self.assertIsNotNone(fields)
        self.assertEqual(fields["applicant_name"].get("/V", ""), "")

        with pdfplumber.open(io.BytesIO(out_bytes)) as pdf:
            words = pdf.pages[0].extract_words()
            hit = next((w for w in words if "홍길동" in w["text"]), None)
            self.assertIsNotNone(hit)
            # 페이지 높이 300, /Rect [100, 150, 250, 175] -> top-down y=[125, 150]
            self.assertGreaterEqual(hit["x0"], 100.0)
            self.assertLessEqual(hit["x1"], 250.0)
            self.assertGreaterEqual(hit["top"], 125.0)
            self.assertLessEqual(hit["bottom"], 150.0)

    def test_is_form_includes_subscription_form(self) -> None:
        self.assertTrue(packet.is_form("어선보험가입청약서.hwp"))
        self.assertFalse(packet.is_form("사업안내문.pdf"))

    def test_fill_hwp_fallback_when_runtime_missing_or_invalid_file(self) -> None:
        raw = b"dummy-hwp-bytes"
        with tempfile.TemporaryDirectory() as tmp:
            out_bytes, rep = fill_hwp.fill_hwp_bytes(raw, hwplib_dir=Path(tmp))
            self.assertEqual(out_bytes, raw)
            self.assertEqual(rep["status"], "unsupported")
            self.assertEqual(rep["error"], "hwplib_runtime_unavailable")

        # 유효하지 않은 바이트 입력 시에도 크래시 없이 원본을 그대로 반환
        out2, rep2 = fill_hwp.fill_hwp_bytes(raw)
        self.assertEqual(out2, raw)
        self.assertEqual(rep2["status"], "unsupported")

    def test_hwp_plan_enhancements_rules(self) -> None:
        doc_dump = {
            "paragraphs": [
                {"section": 0, "para": 0, "text": "위와 같이 신청합니다."},
                {"section": 0, "para": 1, "text": ""},
                {"section": 0, "para": 2, "text": "     년     월     일"},
            ],
            "tables": [{
                "section": 0, "para": 3, "control": 0, "row_count": 4, "col_count": 3,
                "cells": [
                    {"cell_idx": 0, "row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "주민등록번호"},
                    {"cell_idx": 1, "row": 0, "col": 1, "row_span": 1, "col_span": 2, "text": "                   (만     세)"},
                    {"cell_idx": 2, "row": 1, "col": 0, "row_span": 1, "col_span": 1, "text": "주 소"},
                    {"cell_idx": 3, "row": 1, "col": 1, "row_span": 1, "col_span": 2, "text": "(전화번호 :                )"},
                    {"cell_idx": 4, "row": 2, "col": 0, "row_span": 1, "col_span": 1, "text": "1회보험료 입금확인"},
                    {"cell_idx": 5, "row": 2, "col": 1, "row_span": 1, "col_span": 1, "text": "    년   월   일\n입금됨 (인)"},
                    {"cell_idx": 6, "row": 2, "col": 2, "row_span": 2, "col_span": 1, "text": " 금융기관명"},
                    {"cell_idx": 7, "row": 3, "col": 0, "row_span": 1, "col_span": 2, "text": "기타"},
                ],
            }],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        self.assertEqual(len(plan["para_edits"]), 1)
        self.assertEqual(plan["para_edits"][0]["mode"], "date")
        modes_by_key = {(e.get("field_key"), e["mode"]) for e in plan["cell_edits"]}
        self.assertIn(("applicant.rrn", "prepend_trim_spaces"), modes_by_key)
        self.assertIn(("applicant.address", "prepend_trim_spaces"), modes_by_key)
        self.assertIn(("applicant.phone", "after_anchor"), modes_by_key)
        self.assertIn(("account.bank", "add_para"), modes_by_key)
        # 1회보험료 입금확인 셀 날짜는 건너뛰어야 함
        self.assertNotIn(("apply_date", "date"), modes_by_key)

    def test_hwp_plan_phone_label_prefers_empty_right_cell(self) -> None:
        doc_dump = {
            "paragraphs": [],
            "tables": [{
                "section": 0, "para": 0, "control": 0, "row_count": 1, "col_count": 2,
                "cells": [
                    {"cell_idx": 0, "row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "휴대전화"},
                    {"cell_idx": 1, "row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": ""},
                ],
            }],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        self.assertEqual(len(plan["cell_edits"]), 1)
        ed = plan["cell_edits"][0]
        self.assertEqual(ed["cell_idx"], 1)
        self.assertEqual(ed["mode"], "set")
        self.assertEqual(ed["field_key"], "applicant.phone")

    def test_fill_form_dispatch_and_unsupported(self) -> None:
        sec_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:p><hp:run><hp:t>신청인 :               (서명)</hp:t></hp:run></hp:p>'
            '</hs:sec>'
        ).encode("utf-8")
        hwpx_raw = _make_minimal_hwpx(sec_xml)
        out_hwpx, rep_hwpx = fill_form.fill_form(hwpx_raw, "신청서.hwpx")
        self.assertEqual(rep_hwpx["detected_format"], "hwpx")
        self.assertEqual(rep_hwpx["status"], "ok")
        self.assertGreater(rep_hwpx["filled_count"], 0)
        self.assertNotEqual(out_hwpx, hwpx_raw)

        # 지원하지 않는 형식 (zip / xlsx / xls / jpg) -> 원본 그대로 + unsupported
        xlsx_buf = io.BytesIO()
        with zipfile.ZipFile(xlsx_buf, "w") as zf:
            zf.writestr("xl/workbook.xml", b"<workbook/>")
        for fname, raw in (
            ("서식모음.zip", _make_minimal_hwpx(sec_xml).replace(b"Contents/section0.xml", b"readme.txt")),
            ("명단.xlsx", xlsx_buf.getvalue()),
            ("명단.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k\x00"),
            ("스캔.jpg", b"\xff\xd8\xff\xe0dummy"),
        ):
            out_u, rep_u = fill_form.fill_form(raw, fname)
            self.assertEqual(out_u, raw)
            self.assertEqual(rep_u["status"], "unsupported")
            self.assertEqual(rep_u["filled_count"], 0)

    def test_empty_profile_never_fills_dummy_defaults(self) -> None:
        self.assertEqual(fill_form.build_profile(None, {}), {})
        prof_rrn = fill_form.build_profile({"name": "홍길동"}, {"rrn": "5001011234567"})
        self.assertEqual(prof_rrn["applicant.rrn"], "500101-1234567")
        self.assertEqual(prof_rrn["applicant.birth_date"], "1950-01-01")

        sec_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:p><hp:run><hp:t>신청인 :               (서명)</hp:t></hp:run></hp:p>'
            '</hs:sec>'
        ).encode("utf-8")
        hwpx_raw = _make_minimal_hwpx(sec_xml)
        out_hwpx, rep_hwpx = fill_form.fill_form(hwpx_raw, "신청서.hwpx", profile={})
        self.assertEqual(out_hwpx, hwpx_raw)
        self.assertEqual(rep_hwpx["status"], "no_fillable_fields")
        self.assertEqual(rep_hwpx["filled_count"], 0)
        with zipfile.ZipFile(io.BytesIO(out_hwpx), "r") as zin:
            self.assertNotIn("홍길동", zin.read("Contents/section0.xml").decode("utf-8"))

        # 빈 프로필로 PDF 실행 시에도 가상값이 들어가지 않음
        buf = io.BytesIO()
        c = Canvas(buf, pagesize=(400, 300))
        c.rect(50, 180, 80, 30)
        c.rect(130, 180, 180, 30)
        c.save()
        out_pdf, rep_pdf = fill_form.fill_form(buf.getvalue(), "빈양식.pdf", profile={})
        self.assertEqual(out_pdf, buf.getvalue())
        self.assertEqual(rep_pdf["filled_count"], 0)

    def test_packet_build_zip_autofill_and_manual_notice(self) -> None:
        sec_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:p><hp:run><hp:t>신청인 :               (서명)</hp:t></hp:run></hp:p>'
            '</hs:sec>'
        ).encode("utf-8")
        hwpx_raw = _make_minimal_hwpx(sec_xml)
        xlsx_buf = io.BytesIO()
        with zipfile.ZipFile(xlsx_buf, "w") as zf:
            zf.writestr("xl/workbook.xml", b"<workbook/>")
        xlsx_raw = xlsx_buf.getvalue()
        ref_raw = b"%PDF-1.4\n%reference\n"

        row = {
            "svc": {
                "servId": "TEST_PK_01",
                "servNm": "테스트복지서비스",
                "org": "구로구",
                "region": "서울특별시 구로구",
                "contact": "129",
                "link": "",
                "form_files": [
                    {"name": "지원신청서.hwpx", "url": "https://www.bokjiro.go.kr/test/a.hwpx"},
                    {"name": "소득확인서식.xlsx", "url": "https://www.bokjiro.go.kr/test/b.xlsx"},
                    {"name": "사업공고문.pdf", "url": "https://bokjiro.go.kr/test/c.pdf"},
                ],
            },
            "ai": None,
            "ext": {"documents": ["신분증"]},
        }
        files = {
            "https://www.bokjiro.go.kr/test/a.hwpx": hwpx_raw,
            "https://www.bokjiro.go.kr/test/b.xlsx": xlsx_raw,
            "https://bokjiro.go.kr/test/c.pdf": ref_raw,
        }
        p = {"name": "홍길동", "sido": "서울특별시", "sgg": "구로구", "age": 76}
        extra = {
            "phone": "010-0000-0000",
            "rrn": "500101-1234567",
            "monthly_income": "1,000,000",
            "address": "가마산로 000",
            "bank": "농협",
            "account_number": "000-0000-0000-00",
            "account_holder": "홍길동",
            "proxy": "김대리",
            "relation": "자녀",
        }
        zip_bytes, failed = packet.build_zip(row, p, extra, fetch=files.__getitem__)
        self.assertEqual(failed, [])
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zout:
            names = zout.namelist()
            self.assertIn("2_기관양식(작성해서 제출)/작성됨_지원신청서.hwpx", names)
            self.assertIn("2_기관양식(작성해서 제출)/소득확인서식.xlsx", names)
            self.assertIn("3_참고자료(읽어 보기)/사업공고문.pdf", names)
            self.assertEqual(zout.read("2_기관양식(작성해서 제출)/소득확인서식.xlsx"), xlsx_raw)
            with zipfile.ZipFile(io.BytesIO(zout.read("2_기관양식(작성해서 제출)/작성됨_지원신청서.hwpx")), "r") as zh:
                sec_out = zh.read("Contents/section0.xml").decode("utf-8")
                self.assertIn("신청인 : 홍길동", sec_out)
                self.assertIn("(서명)", sec_out)

            pdf_name = next(n for n in names if n.startswith("1_신청준비_") and n.endswith(".pdf"))
            with pdfplumber.open(io.BytesIO(zout.read(pdf_name))) as pdf:
                full_text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
                self.assertIn("직접 작성 필요", full_text)
                self.assertIn("소득확인서식.xlsx", full_text)
                self.assertIn("신청서 작성 도우미", full_text)
                self.assertIn("위임장 도우미", full_text)
                self.assertIn("500101-*******", full_text)
                self.assertNotIn("500101-1234567", full_text)

    def test_privacy_rules_cache_and_error_redaction(self) -> None:
        app_path = Path(__file__).resolve().parent.parent / "app.py"
        tree = ast.parse(app_path.read_text(encoding="utf-8"))
        forbidden_arg_tokens = ("profile", "me", "extra", "rrn", "phone", "account", "income", "address", "pii")

        # 1. @st.cache_data / @st.cache_resource 함수 인자에 개인정보 없음
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            is_cached = any(
                (isinstance(d, ast.Attribute) and d.attr in ("cache_data", "cache_resource"))
                or (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in ("cache_data", "cache_resource")
                )
                for d in node.decorator_list
            )
            if is_cached:
                args = [a.arg.lower() for a in node.args.args + node.args.kwonlyargs]
                for arg in args:
                    for tok in forbidden_arg_tokens:
                        self.assertNotIn(tok, arg, f"cached function {node.name} accepts PII arg {arg}")

        # 2. 새 서류 작성 화면도 주민번호를 가리고 예외 상세를 노출하지 않는다.
        ui_tree = ast.parse((app_path.parent / "src" / "document_ui.py").read_text(encoding="utf-8"))
        pkt_fn = ui_tree
        has_password_rrn = False
        for node in ast.walk(pkt_fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "exception":
                    self.fail("document_ui must not call st.exception (risk of leaking user input)")
                if node.func.attr == "text_input":
                    for kw in node.keywords:
                        if kw.arg == "type" and isinstance(kw.value, ast.Constant) and kw.value.value == "password":
                            has_password_rrn = True
        self.assertTrue(has_password_rrn, "document_ui must mask RRN input with type='password'")

        # 3. 내부 엔진 예외에 주민번호/계좌가 들어 있어도 fill_form 보고/오류 문자열에 남지 않음
        sec_xml = b"<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section'><hs:p/></hs:sec>"
        hwpx_raw = _make_minimal_hwpx(sec_xml)
        with patch("fill_hwpx.fill_hwpx_bytes", side_effect=RuntimeError("leak 500101-1234567 000-0000-0000-00")):
            out_bytes, rep = fill_form.fill_form(hwpx_raw, "a.hwpx", profile={"applicant.rrn": "500101-1234567"})
        self.assertEqual(out_bytes, hwpx_raw)
        self.assertEqual(rep["status"], "error")
        dumped = json.dumps(rep, ensure_ascii=False)
        self.assertIsNone(re.search(r"\d{6}-\d{7}", dumped))
        self.assertNotIn("000-0000-0000-00", dumped)

    def test_app_single_document_flow_apptest(self) -> None:
        from streamlit.testing.v1 import AppTest
        import document_ui
        section = """<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">
        <hp:p><hp:run><hp:t>지원 신청서</hp:t></hp:run></hp:p>
        <hp:p><hp:run><hp:tbl><hp:tr>
          <hp:tc><hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>성명</hp:t></hp:run></hp:p></hp:subList></hp:tc>
          <hp:tc><hp:cellAddr colAddr="1" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc>
        </hp:tr><hp:tr>
          <hp:tc><hp:cellAddr colAddr="0" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>주소</hp:t></hp:run></hp:p></hp:subList></hp:tc>
          <hp:tc><hp:cellAddr colAddr="1" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc>
        </hp:tr></hp:tbl></hp:run></hp:p></hs:sec>""".encode()
        raw = _make_minimal_hwpx(section)
        row = {"svc": {"servId": "DOC_FLOW", "form_files": [{"name": "신청서.hwpx", "url": "https://www.bokjiro.go.kr/a.hwpx"}]}}
        code = "import document_ui\ndocument_ui.render_document_workspace(" + repr(row) + ")"
        with patch.object(packet, "download", return_value=raw), patch.object(document_ui.document_viewer, "render_document") as view:
            at = AppTest.from_string(code, default_timeout=30).run()
            at.button(key="dw_open_DOC_FLOW").click().run()
            self.assertFalse(at.exception, at.exception)
            at.text_input(key="dw_name_DOC_FLOW").input("홍길동")
            at.text_input(key="dw_address_DOC_FLOW").input("서울특별시 테스트로 1").run()
            at.button(key="dw_fill_DOC_FLOW").click().run()
            self.assertFalse(at.exception, at.exception)
            result = at.session_state["document_workspace_DOC_FLOW"]["prepared"]
            self.assertEqual(result["status"], "filled")
            self.assertEqual(view.call_args.args[0], result["data"])
            with zipfile.ZipFile(io.BytesIO(result["data"])) as z:
                text = z.read("Contents/section0.xml").decode()
                self.assertIn("홍길동", text)
                self.assertIn("서울특별시 테스트로 1", text)

    def test_packet_download_domain_and_redirect_allowlist(self) -> None:
        class _FakeResp:
            def __init__(self, body: bytes, final_url: str) -> None:
                self._body = body
                self._final_url = final_url

            def read(self, _n: int = -1) -> bytes:
                return self._body

            def geturl(self) -> str:
                return self._final_url

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        # 1. 허용 주소(www.bokjiro.go.kr / bokjiro.go.kr HTTPS) 통과
        for ok_url in (
            "https://www.bokjiro.go.kr/ssis-tbu/CmmFileUtil/siteQnaInfoDownload.do?atcflId=1",
            "https://bokjiro.go.kr/ssis-tbu/CmmFileUtil/getDownload.do?atcflId=2",
        ):
            self.assertEqual(packet.validate_download_url(ok_url), ok_url)
            with patch("urllib.request.urlopen", return_value=_FakeResp(b"%PDF-1.4\n", ok_url)) as mock_open:
                data = packet.download(ok_url)
                self.assertEqual(data, b"%PDF-1.4\n")
                mock_open.assert_called_once()

        # 2. 비허용 scheme/도메인/우회 주소 거부(오류 메시지에 URL 미포함, urlopen 미호출)
        bad_urls = (
            "http://www.bokjiro.go.kr/a.hwp",
            "http://bokjiro.go.kr/a.hwp",
            "https://evil.com/a.hwp",
            "https://bokjiro.go.kr.evil.com/a.hwp",
            "https://sub.bokjiro.go.kr/a.hwp",
            "https://x@evil.com/a.hwp",
            "https://www.bokjiro.go.kr@evil.com/a.hwp",
            "https://x@www.bokjiro.go.kr/a.hwp",
            "https://www.bokjiro.go.kr:8443/a.hwp",
        )
        for bad_url in bad_urls:
            with patch("urllib.request.urlopen") as mock_open:
                with self.assertRaises(ValueError) as cm:
                    packet.download(bad_url)
                self.assertEqual(str(cm.exception), "허용되지 않은 주소")
                self.assertNotIn("evil", str(cm.exception))
                mock_open.assert_not_called()

        # 3. 리다이렉트 뒤 최종 주소가 비허용 도메인/HTTP로 바뀌면 거부
        for redir_url in ("https://evil.com/redir.hwp", "http://www.bokjiro.go.kr/redir.hwp"):
            with patch(
                "urllib.request.urlopen",
                return_value=_FakeResp(b"data", redir_url),
            ):
                with self.assertRaises(ValueError) as cm:
                    packet.download("https://www.bokjiro.go.kr/start.hwp")
                self.assertEqual(str(cm.exception), "허용되지 않은 주소")

        # 4. build_zip은 거부된 서식을 원본 대신 '직접 작성 필요'로 안내
        sec_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:p><hp:run><hp:t>신청인 :               (서명)</hp:t></hp:run></hp:p>'
            '</hs:sec>'
        ).encode("utf-8")
        hwpx_raw = _make_minimal_hwpx(sec_xml)
        row = {
            "svc": {
                "servId": "TEST_DOMAIN_01",
                "servNm": "도메인검증서비스",
                "org": "구로구",
                "region": "서울특별시 구로구",
                "contact": "129",
                "link": "",
                "form_files": [
                    {"name": "허용신청서.hwpx", "url": "https://www.bokjiro.go.kr/ok.hwpx"},
                    {"name": "외부서식신청서.hwp", "url": "https://bokjiro.go.kr.evil.com/bad.hwp"},
                ],
            },
            "ai": None,
            "ext": {"documents": ["신분증"]},
        }
        seen_fetch_urls: list[str] = []

        def _fake_fetch(url: str) -> bytes:
            seen_fetch_urls.append(url)
            return hwpx_raw

        zip_bytes, failed = packet.build_zip(row, {"name": "홍길동"}, {}, fetch=_fake_fetch)
        self.assertEqual(seen_fetch_urls, ["https://www.bokjiro.go.kr/ok.hwpx"])
        self.assertEqual(failed, ["외부서식신청서.hwp"])
        with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as zout:
            names = zout.namelist()
            self.assertIn("2_기관양식(작성해서 제출)/작성됨_허용신청서.hwpx", names)
            self.assertNotIn("2_기관양식(작성해서 제출)/외부서식신청서.hwp", names)
            self.assertIn("내려받지_못한_파일.txt", names)
            self.assertNotIn("evil.com", zout.read("내려받지_못한_파일.txt").decode("utf-8"))
            pdf_name = next(n for n in names if n.startswith("1_신청준비_") and n.endswith(".pdf"))
            with pdfplumber.open(io.BytesIO(zout.read(pdf_name))) as pdf:
                full_text = "\n".join((pg.extract_text() or "") for pg in pdf.pages)
                self.assertIn("직접 작성 필요", full_text)
                self.assertIn("외부서식신청서.hwp", full_text)

    def test_fix_a_inline_whitespace_trim_and_no_room(self) -> None:
        # 1. HWPX 멀티-run 공백 치환: 전체 시각 폭이 늘어나지 않고 뒤 run의 공백만 줄어듦
        sec_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:p>'
            '<hp:run charPrIDRef="1"><hp:t>신청인(대리 신청인) 성명 :</hp:t></hp:run>'
            '<hp:run charPrIDRef="2"><hp:t>              </hp:t></hp:run>'
            '<hp:run charPrIDRef="3"><hp:t> (서명 또는 인)</hp:t></hp:run>'
            '</hp:p>'
            '<hp:p>'
            '<hp:run charPrIDRef="1"><hp:t>신청인(좁은 칸) 성명 :</hp:t></hp:run>'
            '<hp:run charPrIDRef="2"><hp:t>    </hp:t></hp:run>'
            '<hp:run charPrIDRef="3"><hp:t>(서명 또는 인)</hp:t></hp:run>'
            '</hp:p>'
            '</hs:sec>'
        ).encode("utf-8")
        out_bytes, rep = fill_hwpx.fill_hwpx_bytes(_make_minimal_hwpx(sec_xml))
        self.assertEqual(rep["filled_count"], 1)
        self.assertTrue(any(s["reason"] == "no_room" for s in rep["skipped_fields"]))
        with zipfile.ZipFile(io.BytesIO(out_bytes), "r") as zin:
            sec_out = zin.read("Contents/section0.xml").decode("utf-8")
        # 14칸 - 7폭(' 홍길동') = 7칸 남고, 뒤 run 1칸 포함 총 8칸 유지
        from lxml import etree as _etree
        sec_tree = _etree.fromstring(sec_out.encode("utf-8"))
        paras_txt = ["".join(p.xpath(".//*[local-name()='t']/text()")) for p in sec_tree.xpath(".//*[local-name()='p']")]
        self.assertEqual(paras_txt[0], "신청인(대리 신청인) 성명 : 홍길동        (서명 또는 인)")
        self.assertEqual(paras_txt[1], "신청인(좁은 칸) 성명 :    (서명 또는 인)")

        # 2. HWP 계획기 좁은 날짜/서명 줄은 skipped: no_room
        doc_dump = {
            "paragraphs": [
                {"section": 0, "para": 0, "text": "위와 같이 신청합니다."},
                {"section": 0, "para": 1, "text": "20 년 월 일"},
                {"section": 0, "para": 2, "text": "신청인 :    (서명)"},
            ],
            "tables": [],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        self.assertEqual(plan["para_edits"], [])
        reasons = [s["reason"] for s in plan["report"]["skipped_fields"]]
        self.assertGreaterEqual(reasons.count("no_room"), 2)

    def test_fix_b_tiny_font_fallback_and_skip(self) -> None:
        hdr_mixed = (
            '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
            '<hh:charPr id="1" height="900"/>'
            '<hh:charPr id="2" height="100"/>'
            '</hh:head>'
        ).encode("utf-8")
        hdr_tiny_only = (
            '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
            '<hh:charPr id="1" height="100"/>'
            '<hh:charPr id="2" height="100"/>'
            '</hh:head>'
        ).encode("utf-8")
        tbl_sec = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:p><hp:run><hp:tbl>'
            '<hp:tr>'
            '<hp:tc><hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
            '<hp:subList><hp:p><hp:run charPrIDRef="1"><hp:t>금융기관명</hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '</hp:tr>'
            '<hp:tr>'
            '<hp:tc><hp:cellAddr colAddr="0" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
            '<hp:subList><hp:p><hp:run charPrIDRef="2"><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '</hp:tr>'
            '</hp:tbl></hp:run></hp:p></hs:sec>'
        ).encode("utf-8")

        raw_ok = _make_minimal_hwpx(tbl_sec, {"Contents/header.xml": hdr_mixed})
        out_ok, rep_ok = fill_hwpx.fill_hwpx_bytes(raw_ok)
        self.assertEqual(rep_ok["filled_count"], 1)
        with zipfile.ZipFile(io.BytesIO(out_ok), "r") as zin:
            sec_xml = zin.read("Contents/section0.xml").decode("utf-8")
        self.assertIn('charPrIDRef="1"><hp:t>농협</hp:t>', sec_xml)

        raw_skip = _make_minimal_hwpx(tbl_sec, {"Contents/header.xml": hdr_tiny_only})
        out_skip, rep_skip = fill_hwpx.fill_hwpx_bytes(raw_skip)
        self.assertEqual(out_skip, raw_skip)
        self.assertEqual(rep_skip["filled_count"], 0)
        self.assertTrue(any(s["reason"] == "tiny_font" for s in rep_skip["skipped_fields"]))

        # HWP 계획기도 7pt(700) 미만 대상 셀에서 override_char_shape_id 또는 tiny_font 차단 동작
        dump_hwp = {
            "default_char_shape_id": 1,
            "default_font_pt100": 900,
            "paragraphs": [],
            "tables": [{
                "section": 0, "para": 0, "control": 0, "row_count": 2, "col_count": 1,
                "cells": [
                    {"cell_idx": 0, "row": 0, "col": 0, "row_span": 1, "col_span": 1, "char_shape_id": 1, "font_pt100": 900, "text": "금융기관명"},
                    {"cell_idx": 1, "row": 1, "col": 0, "row_span": 1, "col_span": 1, "char_shape_id": 2, "font_pt100": 100, "text": ""},
                ],
            }],
        }
        plan_hwp = fill_hwp_plan.plan_hwp_fills(dump_hwp)
        self.assertEqual(plan_hwp["cell_edits"][0].get("override_char_shape_id"), 1)
        dump_hwp_tiny = {
            "default_char_shape_id": -1,
            "default_font_pt100": 0,
            "paragraphs": [],
            "tables": [{
                "section": 0, "para": 0, "control": 0, "row_count": 2, "col_count": 1,
                "cells": [
                    {"cell_idx": 0, "row": 0, "col": 0, "row_span": 1, "col_span": 1, "char_shape_id": 2, "font_pt100": 100, "text": "금융기관명"},
                    {"cell_idx": 1, "row": 1, "col": 0, "row_span": 1, "col_span": 1, "char_shape_id": 2, "font_pt100": 100, "text": ""},
                ],
            }],
        }
        plan_skip = fill_hwp_plan.plan_hwp_fills(dump_hwp_tiny)
        self.assertEqual(plan_skip["cell_edits"], [])
        self.assertTrue(any(s["reason"] == "tiny_font" for s in plan_skip["report"]["skipped_fields"]))

    def test_fix_c_agency_and_official_section_excluded(self) -> None:
        sec_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:p><hp:run><hp:tbl>'
            '<hp:tr>'
            '<hp:tc><hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>처리기간</hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '<hp:tc><hp:cellAddr colAddr="1" rowAddr="0"/><hp:cellSpan colSpan="2" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>14일</hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '</hp:tr>'
            '<hp:tr>'
            '<hp:tc><hp:cellAddr colAddr="0" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>신청인</hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '<hp:tc><hp:cellAddr colAddr="1" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>성명</hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '<hp:tc><hp:cellAddr colAddr="2" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '</hp:tr>'
            '<hp:tr>'
            '<hp:tc><hp:cellAddr colAddr="0" rowAddr="2"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>요양기관</hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '<hp:tc><hp:cellAddr colAddr="1" rowAddr="2"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>전화번호</hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '<hp:tc><hp:cellAddr colAddr="2" rowAddr="2"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '</hp:tr>'
            '<hp:tr>'
            '<hp:tc><hp:cellAddr colAddr="0" rowAddr="3"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>읍ㆍ면장 확인</hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            '<hp:tc><hp:cellAddr colAddr="1" rowAddr="3"/><hp:cellSpan colSpan="2" rowSpan="1"/><hp:subList>'
            '<hp:p><hp:run><hp:t>확인자 : 성명         (서명)</hp:t></hp:run></hp:p>'
            '<hp:p><hp:run><hp:t>        년      월      일</hp:t></hp:run></hp:p>'
            '</hp:subList></hp:tc>'
            '</hp:tr>'
            '</hp:tbl></hp:run></hp:p>'
            '<hp:p><hp:run><hp:t>20    년      월      일</hp:t></hp:run></hp:p>'
            '<hp:p><hp:run><hp:t>1차 확인자 : 활동보조인 성명 :       (서명 또는 인)</hp:t></hp:run></hp:p>'
            '</hs:sec>'
        ).encode("utf-8")
        _, rep = fill_hwpx.fill_hwpx_bytes(_make_minimal_hwpx(sec_xml))
        # 처리기간 뒤 신청인 성명 1칸만 채워지고, 요양기관 전화번호·읍면장 확인 날짜·본문 1차 확인자 날짜는 모두 제외되어야 함
        self.assertEqual(rep["filled_count"], 1)
        self.assertEqual(rep["filled_fields"][0]["field_key"], "applicant.name")
        self.assertTrue(any(s["reason"] == "excluded_section" for s in rep["skipped_fields"]))

    def test_fix_d_coheir_gender_split_and_guarantor_excluded(self) -> None:
        doc_dump = {
            "paragraphs": [
                {"section": 0, "para": 0, "text": "효드림수당 지급대상자 명부"},
            ],
            "tables": [
                {
                    "section": 0, "para": 0, "control": 0, "row_count": 2, "col_count": 2,
                    "cells": [
                        {"cell_idx": 0, "row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "성명"},
                        {"cell_idx": 1, "row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "주 소"},
                        {"cell_idx": 2, "row": 1, "col": 0, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 3, "row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": ""},
                    ],
                },
                {
                    "section": 0, "para": 1, "control": 0, "row_count": 5, "col_count": 3,
                    "cells": [
                        {"cell_idx": 0, "row": 0, "col": 0, "row_span": 2, "col_span": 1, "text": "동순위수급권자"},
                        {"cell_idx": 1, "row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "성명"},
                        {"cell_idx": 2, "row": 0, "col": 2, "row_span": 1, "col_span": 1, "text": "대표자 선정"},
                        {"cell_idx": 3, "row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 4, "row": 1, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 5, "row": 2, "col": 0, "row_span": 1, "col_span": 1, "text": "성명(여성)"},
                        {"cell_idx": 6, "row": 2, "col": 1, "row_span": 1, "col_span": 1, "text": "생년월일"},
                        {"cell_idx": 7, "row": 2, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 8, "row": 3, "col": 0, "row_span": 1, "col_span": 1, "text": "관 계"},
                        {"cell_idx": 9, "row": 3, "col": 1, "row_span": 1, "col_span": 1, "text": "성 명"},
                        {"cell_idx": 10, "row": 4, "col": 0, "row_span": 1, "col_span": 1, "text": "여성"},
                        {"cell_idx": 11, "row": 4, "col": 1, "row_span": 1, "col_span": 1, "text": ""},
                    ],
                },
                {
                    "section": 0, "para": 2, "control": 0, "row_count": 2, "col_count": 1,
                    "cells": [
                        {"cell_idx": 0, "row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "인 우 보 증 서"},
                        {"cell_idx": 1, "row": 1, "col": 0, "row_span": 1, "col_span": 1, "text": "위의 사실을 확인하며 보증합니다.\n20   년      월      일"},
                    ],
                },
            ],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        self.assertEqual(plan["cell_edits"], [])
        self.assertEqual(plan["para_edits"], [])
        self.assertTrue(any(s["reason"] == "excluded_section" for s in plan["report"]["skipped_fields"]))
        # '신고인'이 '고인'으로 오인되어 제외되지 않는지 확인
        self.assertEqual(fm.detect_section_context("신고인(신청인)"), "applicant")
        self.assertEqual(fm.detect_section_context("사망한 고인 성명"), "other_person")

    def test_fix_e_other_person_relation_and_family_roster_excluded(self) -> None:
        doc_dump = {
            "default_char_shape_id": 0,
            "default_font_pt100": 1000,
            "paragraphs": [{"section": 0, "para": 0, "text": ""}],
            "tables": [
                {
                    "section": 0, "para": 0, "control": 0, "row_count": 10, "col_count": 4,
                    "cells": [
                        {"cell_idx": 0, "row": 0, "col": 0, "row_span": 2, "col_span": 1, "text": "신청인 (대리 신청인)"},
                        {"cell_idx": 1, "row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "성명"},
                        {"cell_idx": 2, "row": 0, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 3, "row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": "수급권자와의 관계"},
                        {"cell_idx": 4, "row": 1, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 5, "row": 2, "col": 0, "row_span": 1, "col_span": 1, "text": "출산자"},
                        {"cell_idx": 6, "row": 2, "col": 1, "row_span": 1, "col_span": 1, "text": "성명"},
                        {"cell_idx": 7, "row": 2, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 8, "row": 3, "col": 0, "row_span": 1, "col_span": 1, "text": "등록대상자"},
                        {"cell_idx": 9, "row": 3, "col": 1, "row_span": 1, "col_span": 1, "text": "생년월일"},
                        {"cell_idx": 10, "row": 3, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 11, "row": 4, "col": 0, "row_span": 3, "col_span": 1, "text": "②가족상황"},
                        {"cell_idx": 12, "row": 4, "col": 1, "row_span": 1, "col_span": 1, "text": "본인과의 관계"},
                        {"cell_idx": 13, "row": 4, "col": 2, "row_span": 1, "col_span": 1, "text": "생년월일"},
                        {"cell_idx": 14, "row": 5, "col": 1, "col_span": 1, "row_span": 1, "text": ""},
                        {"cell_idx": 15, "row": 5, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 16, "row": 6, "col": 1, "col_span": 1, "row_span": 1, "text": ""},
                        {"cell_idx": 17, "row": 6, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 18, "row": 7, "col": 0, "row_span": 1, "col_span": 1, "text": "③지급계좌"},
                        {"cell_idx": 19, "row": 7, "col": 1, "row_span": 1, "col_span": 1, "text": "예금주"},
                        {"cell_idx": 20, "row": 7, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        # 2560_1 패턴: (전화번호:  ) 아래에 관계 라벨이 있어도 전화번호가 내려오지 않아야 함
                        {"cell_idx": 21, "row": 8, "col": 1, "row_span": 1, "col_span": 1, "text": "주 소"},
                        {"cell_idx": 22, "row": 8, "col": 2, "row_span": 1, "col_span": 2, "text": "                    (전화번호:        )", "width": 32633, "height": 3249},
                        {"cell_idx": 23, "row": 9, "col": 1, "row_span": 1, "col_span": 1, "text": "수급권자와의 관계"},
                        {"cell_idx": 24, "row": 9, "col": 2, "row_span": 1, "col_span": 2, "text": ""},
                    ],
                },
            ],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        filled_locs = {(e["row"], e["col"]): (e["field_key"], e["value"]) for e in plan["cell_edits"]}
        self.assertEqual(filled_locs[(0, 2)], ("applicant.name", "홍길동"))
        self.assertNotIn((1, 2), filled_locs)
        self.assertNotIn((2, 2), filled_locs)
        self.assertNotIn((3, 2), filled_locs)
        self.assertNotIn((5, 2), filled_locs)
        self.assertEqual(filled_locs[(7, 2)], ("account.holder", "홍길동"))
        self.assertNotIn((9, 2), filled_locs)
        self.assertEqual(fm.match_label("관계", section="proxy").action, "fill")
        self.assertEqual(fm.match_label("수급권자와의 관계", section="applicant").reason, "ambiguous_relation_section")
        self.assertTrue(any(s["reason"] == "other_person" for s in plan["report"]["skipped_fields"]))

    def test_fix_f_ledger_and_approval_table_excluded(self) -> None:
        sec = (
            b"<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
            b"xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
            b"<hp:p><hp:run><hp:t>\xec\xb6\x9c\xec\x82\xb0\xec\x84\x9c\xeb\xb9\x84\xec\x8a\xa4 \xed\x86\xb5\xed\x95\xa9\xec\xb2\x98\xeb\xa6\xac \xec\x8b\xa0\xec\xb2\xad \xec\xa0\x91\xec\x88\x98\xeb\x8c\x80\xec\x9e\xa5</hp:t></hp:run></hp:p>"
            b"<hp:tbl>"
            b"<hp:tr>"
            b"<hp:tc><hp:cellAddr colAddr='0' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:subList><hp:p><hp:run><hp:t>\xec\x84\xb1\xeb\xaa\x85</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:cellAddr colAddr='1' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:subList><hp:p><hp:run><hp:t>\xea\xb2\xb0\xec\x9e\xac</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"</hp:tr>"
            b"<hp:tr>"
            b"<hp:tc><hp:cellAddr colAddr='0' rowAddr='1'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:cellAddr colAddr='1' rowAddr='1'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"</hp:tr>"
            b"</hp:tbl>"
            b"<hp:p><hp:run><hp:t>\xeb\xb3\xb5\xec\xa7\x80\xea\xb8\x89\xec\x97\xac \xec\x8b\xa0\xec\xb2\xad\xec\x84\x9c</hp:t></hp:run></hp:p>"
            b"<hp:tbl>"
            b"<hp:tr><hp:tc><hp:cellAddr colAddr='0' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:subList><hp:p><hp:run><hp:t>\xec\x8b\xa0\xec\xb2\xad\xec\x9d\xb8</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:cellAddr colAddr='1' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:subList><hp:p><hp:run><hp:t>\xec\x84\xb1\xeb\xaa\x85</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:cellAddr colAddr='2' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc></hp:tr>"
            b"</hp:tbl>"
            b"</hs:sec>"
        )
        out_bytes, rep = fill_hwpx.fill_hwpx_bytes(_make_minimal_hwpx(sec))
        self.assertEqual(rep["filled_count"], 1)
        self.assertEqual(rep["filled_fields"][0]["label"], "성명")
        self.assertTrue(fm.is_ledger_or_approval_table(["결재", "담당"]))

    def test_fix_g_cell_width_fit_bounds_and_no_room(self) -> None:
        header = (
            b"<hh:head xmlns:hh='http://www.hancom.co.kr/hwpml/2011/head'>"
            b"<hh:charProperties itemCnt='1'>"
            b"<hh:charPr id='0' height='1200'><hh:spacing hangul='0' latin='0' hanja='0' japanese='0' other='0' symbol='0' user='0'/></hh:charPr>"
            b"</hh:charProperties>"
            b"<hh:paraProperties itemCnt='1'>"
            b"<hh:paraPr id='0'><hh:align horizontal='LEFT' vertical='BASELINE'/><hh:lineSpacing type='PERCENT' value='130' unit='HWPUNIT'/></hh:paraPr>"
            b"</hh:paraProperties>"
            b"</hh:head>"
        )
        sec = (
            b"<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
            b"xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
            b"<hp:tbl>"
            b"<hp:tr>"
            b"<hp:tc><hp:cellAddr colAddr='0' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:cellSz width='5000' height='2833'/><hp:cellMargin left='142' right='142' top='142' bottom='142'/><hp:subList><hp:p paraPrIDRef='0'><hp:run charPrIDRef='0'><hp:t>\xec\xa3\xbc\xec\x86\x8c</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:cellAddr colAddr='1' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:cellSz width='11055' height='2833'/><hp:cellMargin left='142' right='142' top='142' bottom='142'/><hp:subList><hp:p paraPrIDRef='0'><hp:run charPrIDRef='0'><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:cellAddr colAddr='2' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:cellSz width='5000' height='3546'/><hp:cellMargin left='141' right='141' top='141' bottom='141'/><hp:subList><hp:p paraPrIDRef='0'><hp:run charPrIDRef='0'><hp:t>\xea\xb3\x84\xec\xa2\x8c\xeb\xb2\x88\xed\x98\xb8</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:cellAddr colAddr='3' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:cellSz width='5914' height='3546'/><hp:cellMargin left='141' right='141' top='141' bottom='141'/><hp:subList><hp:p paraPrIDRef='0'><hp:run charPrIDRef='0'><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"</hp:tr>"
            b"</hp:tbl>"
            b"</hs:sec>"
        )
        out_bytes, rep = fill_hwpx.fill_hwpx_bytes(_make_minimal_hwpx(sec, {"Contents/header.xml": header}))
        self.assertEqual([f["field_key"] for f in rep["filled_fields"]], ["applicant.address"])
        self.assertTrue(any(s["label"] == "계좌번호" and s["reason"] == "no_room" for s in rep["skipped_fields"]))
        with zipfile.ZipFile(io.BytesIO(out_bytes)) as zf:
            hdr_out = zf.read("Contents/header.xml").decode("utf-8")
            self.assertIn("itemCnt=\"2\"", hdr_out)
            self.assertIn("height=\"900\"", hdr_out)
        # 8pt·자간 -10% 미만으로는 줄이지 않음
        ok_narrow, fit_h, fit_sp, _ = fm.fit_text_to_cell("000-0000-0000-00", 5914, 3546, base_pt100=1200, allow_wrap=False)
        self.assertFalse(ok_narrow)
        self.assertGreaterEqual(fit_h, 800)
        self.assertGreaterEqual(fit_sp, -10)

    def test_fix_h_sublabels_norm_dates_and_inline_colon_labels(self) -> None:
        # 1) 정규화 + 자택/휴대폰 하위 라벨 + '년 윌 일' + 가운데 정렬 + 4167_2 인라인 콜론 라벨
        self.assertEqual(fm.match_label("금  융\n기 관 명").field_key, "account.bank")
        self.assertEqual(fm.match_label("계좌 번호").field_key, "account.number")
        self.assertEqual(fm.match_label("H․P").field_key, "applicant.phone")
        doc_dump = {
            "default_char_shape_id": 0,
            "default_font_pt100": 1000,
            "paragraphs": [{"section": 0, "para": 0, "para_align": "Left", "font_pt100": 1000, "text": ""}],
            "tables": [
                {
                    "section": 0, "para": 0, "control": 0, "row_count": 3, "col_count": 2,
                    "cells": [
                        {"cell_idx": 0, "row": 0, "col": 0, "row_span": 1, "col_span": 1, "text": "연 락 처", "width": 8000, "height": 2000},
                        {"cell_idx": 1, "row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "자택 :\n휴대폰 :", "width": 16000, "height": 3600, "para_pt100s": [1000, 1000]},
                        {"cell_idx": 2, "row": 1, "col": 0, "row_span": 1, "col_span": 2, "text": "위와 같이 신청합니다.\n년 월 일\n년    윌    일", "width": 45000, "height": 6000, "para_aligns": ["Left", "Center", "Center"], "para_pt100s": [1000, 1000, 1000]},
                        {"cell_idx": 3, "row": 2, "col": 0, "row_span": 1, "col_span": 2, "text": "신청인  주 소 :                                 \n성 명 :                  \U000f012b\n(지급대상자와의 관계 :        )", "width": 45000, "height": 4000, "para_aligns": ["Left", "Left", "Left"], "para_pt100s": [1000, 1000, 1000], "para_line_seg_counts": [1, 1, 1]},
                    ],
                },
            ],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        labels_filled = [(e["mode"], e.get("anchor", ""), e["field_key"]) for e in plan["cell_edits"]]
        self.assertIn(("append", "", "applicant.phone"), labels_filled)
        self.assertEqual(sum(1 for e in plan["cell_edits"] if e["field_key"] == "apply_date"), 2)
        self.assertIn(("after_anchor", "주 소 :", "applicant.address"), labels_filled)
        self.assertIn(("after_anchor", "성 명 :", "applicant.name"), labels_filled)
        self.assertNotIn("proxy.relation", [e["field_key"] for e in plan["cell_edits"]])
        # 2) HWPX '202    .     .' 점 날짜 기입
        sec_dot = (
            b"<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
            b"xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
            b"<hp:p><hp:run><hp:t>\xec\x8b\xa0\xec\xb2\xad\xed\x95\xa9\xeb\x8b\x88\xeb\x8b\xa4.</hp:t></hp:run></hp:p>"
            b"<hp:p><hp:run><hp:t>                         202    .     .</hp:t></hp:run></hp:p>"
            b"</hs:sec>"
        )
        out_dot, rep_dot = fill_hwpx.fill_hwpx_bytes(_make_minimal_hwpx(sec_dot), profile=fm.default_profile(fill_hwpx.fm.date(2026, 11, 19) if hasattr(fill_hwpx.fm, "date") else None))
        self.assertEqual(rep_dot["filled_count"], 1)
        with zipfile.ZipFile(io.BytesIO(out_dot)) as zf:
            sec_txt = zf.read("Contents/section0.xml").decode("utf-8")
            self.assertNotIn("202    .     .", sec_txt)
            self.assertIn(".", sec_txt)

    def test_fix_08_priority_deceased_applicant_word_boundary(self) -> None:
        self.assertEqual(fm.detect_section_context("신청인\n(사망인)"), "other_person")
        self.assertEqual(fm.detect_section_context("신청인\n(지원대상자)"), "applicant")
        self.assertEqual(fm.detect_section_context("신청인 (대출자)"), "applicant")
        self.assertEqual(fm.detect_section_context("수급자\n(신청인)"), "applicant")
        self.assertEqual(fm.detect_section_context("어르신\n(봉양대상자)"), "other_person")
        self.assertEqual(fm.detect_section_context("지원계좌", current="other_person"), "other_person")
        self.assertTrue(bool(fm.ACCOUNT_SECTION_RE.match(fm.normalize_label("지원계좌"))))

    def test_fix_08_student_excluded_guardian_applicant(self) -> None:
        doc_dump = {
            "default_char_shape_id": 0,
            "default_font_pt100": 1000,
            "paragraphs": [{"section": 0, "para": 0, "text": ""}],
            "tables": [
                {
                    "section": 0, "para": 0, "control": 0, "row_count": 4, "col_count": 3,
                    "cells": [
                        {"cell_idx": 0, "row": 0, "col": 0, "row_span": 1, "col_span": 3, "text": "교복(하복)구입비 지원신청서\n\n □ 학생 인적사항"},
                        {"cell_idx": 1, "row": 1, "col": 0, "row_span": 1, "col_span": 1, "text": "성 명"},
                        {"cell_idx": 2, "row": 1, "col": 1, "row_span": 1, "col_span": 2, "text": ""},
                        {"cell_idx": 3, "row": 2, "col": 0, "row_span": 1, "col_span": 3, "text": "□ 보호자 인적사항"},
                        {"cell_idx": 4, "row": 3, "col": 0, "row_span": 1, "col_span": 1, "text": "성 명"},
                        {"cell_idx": 5, "row": 3, "col": 1, "row_span": 1, "col_span": 2, "text": ""},
                    ],
                },
            ],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        self.assertEqual([(e["row"], e["col"], e["value"]) for e in plan["cell_edits"]], [(3, 1, "홍길동")])
        self.assertEqual(fm.detect_section_context("대상학생"), "other_person")
        self.assertEqual(fm.detect_section_context("피보호자"), "other_person")
        self.assertEqual(fm.detect_section_context("수혜아동"), "other_person")

    def test_fix_08_proxy_only_proxy_fields(self) -> None:
        doc_dump = {
            "default_char_shape_id": 0,
            "default_font_pt100": 1000,
            "paragraphs": [{"section": 0, "para": 0, "text": ""}],
            "tables": [
                {
                    "section": 0, "para": 0, "control": 0, "row_count": 4, "col_count": 5,
                    "cells": [
                        {"cell_idx": 0, "row": 0, "col": 0, "row_span": 4, "col_span": 1, "text": "대리\n수령인"},
                        {"cell_idx": 1, "row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "성명\n(예금주)"},
                        {"cell_idx": 2, "row": 0, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 3, "row": 0, "col": 3, "row_span": 1, "col_span": 1, "text": "주민등록번호"},
                        {"cell_idx": 4, "row": 0, "col": 4, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 5, "row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": "관계"},
                        {"cell_idx": 6, "row": 1, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 7, "row": 1, "col": 3, "row_span": 1, "col_span": 1, "text": "전화번호"},
                        {"cell_idx": 8, "row": 1, "col": 4, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 9, "row": 2, "col": 1, "row_span": 1, "col_span": 1, "text": "주소"},
                        {"cell_idx": 10, "row": 2, "col": 2, "row_span": 1, "col_span": 3, "text": ""},
                        {"cell_idx": 11, "row": 3, "col": 1, "row_span": 1, "col_span": 1, "text": "금융기관"},
                        {"cell_idx": 12, "row": 3, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 13, "row": 3, "col": 3, "row_span": 1, "col_span": 1, "text": "계좌번호"},
                        {"cell_idx": 14, "row": 3, "col": 4, "row_span": 1, "col_span": 1, "text": ""},
                    ],
                },
            ],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        filled_map = {(e["row"], e["col"]): (e["field_key"], e["value"]) for e in plan["cell_edits"]}
        self.assertEqual(
            filled_map,
            {(0, 2): ("proxy.name", "김대리"), (1, 2): ("proxy.relation", "자녀"), (1, 4): ("proxy.phone", "010-1111-1111")},
        )
        self.assertTrue(any(s["reason"] == "no_proxy_account_in_profile" for s in plan["report"]["skipped_fields"]))

    def test_fix_08_old_new_blocks_only_new_filled(self) -> None:
        doc_dump = {
            "default_char_shape_id": 0,
            "default_font_pt100": 1000,
            "paragraphs": [{"section": 0, "para": 0, "text": ""}],
            "tables": [
                {
                    "section": 0, "para": 0, "control": 0, "row_count": 2, "col_count": 4,
                    "cells": [
                        {"cell_idx": 0, "row": 0, "col": 0, "row_span": 2, "col_span": 1, "text": "지급계좌"},
                        {"cell_idx": 1, "row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "현행"},
                        {"cell_idx": 2, "row": 0, "col": 2, "row_span": 1, "col_span": 1, "text": "계좌번호"},
                        {"cell_idx": 3, "row": 0, "col": 3, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 4, "row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": "변 경"},
                        {"cell_idx": 5, "row": 1, "col": 2, "row_span": 1, "col_span": 1, "text": "계좌번호"},
                        {"cell_idx": 6, "row": 1, "col": 3, "row_span": 1, "col_span": 1, "text": ""},
                    ],
                },
            ],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        self.assertEqual([(e["row"], e["col"], e["value"]) for e in plan["cell_edits"]], [(1, 3, "000-0000-0000-00")])

    def test_fix_08_inline_colon_requires_slot_spaces(self) -> None:
        doc_dump = {
            "default_char_shape_id": 0,
            "default_font_pt100": 1000,
            "paragraphs": [
                {"section": 0, "para": 0, "para_align": "Left", "font_pt100": 1000, "text": "신청인  주 소 :"},
                {"section": 0, "para": 1, "para_align": "Left", "font_pt100": 1000, "text": "성 명 :                  \U000f012b"},
            ],
            "tables": [],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        self.assertEqual([(e["para"], e["field_key"]) for e in plan["para_edits"]], [(1, "applicant.name")])
        self.assertTrue(any(s["reason"] == "no_room" and "주 소" in s["label"] for s in plan["report"]["skipped_fields"]))

    def test_fix_09_deceased_samanghan_excluded(self) -> None:
        self.assertEqual(fm.detect_section_context("사망한\n참  전\n유공자"), "other_person")
        self.assertEqual(fm.detect_section_context("사망위로금 지급신청서", current="applicant"), "applicant")
        self.assertEqual(fm.detect_section_context("사망진단서", current="applicant"), "applicant")
        doc_dump = {
            "default_char_shape_id": 0,
            "default_font_pt100": 1000,
            "paragraphs": [{"section": 0, "para": 0, "text": ""}],
            "tables": [
                {
                    "section": 0, "para": 0, "control": 0, "row_count": 4, "col_count": 5,
                    "cells": [
                        {"cell_idx": 0, "row": 0, "col": 0, "row_span": 1, "col_span": 5, "text": "사망한 참전유공자의 배우자 복지수당 지급신청서\n(제11조제1항 관련)"},
                        {"cell_idx": 1, "row": 1, "col": 0, "row_span": 1, "col_span": 1, "text": "사망한\n참  전\n유공자"},
                        {"cell_idx": 2, "row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": "성    명"},
                        {"cell_idx": 3, "row": 1, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 4, "row": 1, "col": 3, "row_span": 1, "col_span": 1, "text": "생년월일"},
                        {"cell_idx": 5, "row": 1, "col": 4, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 6, "row": 2, "col": 0, "row_span": 2, "col_span": 1, "text": "신청인"},
                        {"cell_idx": 7, "row": 2, "col": 1, "row_span": 1, "col_span": 1, "text": "성    명"},
                        {"cell_idx": 8, "row": 2, "col": 2, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 9, "row": 2, "col": 3, "row_span": 1, "col_span": 1, "text": "생년월일"},
                        {"cell_idx": 10, "row": 2, "col": 4, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 11, "row": 3, "col": 1, "row_span": 1, "col_span": 1, "text": "주    소"},
                        {"cell_idx": 12, "row": 3, "col": 2, "row_span": 1, "col_span": 3, "text": ""},
                    ],
                },
            ],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        self.assertEqual(
            {(e["row"], e["col"]): e["value"] for e in plan["cell_edits"]},
            {(2, 2): "홍길동", (2, 4): "1950-01-01", (3, 2): "서울특별시 구로구 가마산로 000"},
        )
        self.assertTrue(
            any(s["location"] == "T0(1,1)" and s["reason"] == "other_person" for s in plan["report"]["skipped_fields"])
        )

    def test_fix_09_band_first_row_deceased_and_claimant_rep(self) -> None:
        self.assertEqual(fm.detect_section_context("청구인\u2028\u2028(대표자)"), "applicant")
        self.assertEqual(fm.detect_section_context("청구인(대표자 선정)"), "applicant")
        doc_dump = {
            "default_char_shape_id": 0,
            "default_font_pt100": 900,
            "paragraphs": [{"section": 0, "para": 0, "text": ""}],
            "tables": [
                {
                    "section": 0, "para": 0, "control": 0, "row_count": 4, "col_count": 6,
                    "cells": [
                        {"cell_idx": 0, "row": 0, "col": 0, "row_span": 2, "col_span": 1, "text": "청구인\u2028\u2028(대표자)"},
                        {"cell_idx": 1, "row": 0, "col": 1, "row_span": 1, "col_span": 1, "text": "성명"},
                        {"cell_idx": 2, "row": 0, "col": 2, "row_span": 1, "col_span": 2, "text": ""},
                        {"cell_idx": 3, "row": 0, "col": 4, "row_span": 1, "col_span": 1, "text": "주민등록번호"},
                        {"cell_idx": 4, "row": 0, "col": 5, "row_span": 1, "col_span": 1, "text": ""},
                        {"cell_idx": 5, "row": 1, "col": 1, "row_span": 1, "col_span": 1, "text": "주소"},
                        {"cell_idx": 6, "row": 1, "col": 2, "row_span": 1, "col_span": 4, "text": ""},
                        {"cell_idx": 7, "row": 2, "col": 0, "row_span": 2, "col_span": 1, "text": "미지급\u2028\u2028보훈명예수당\u2028\u2028내역"},
                        {"cell_idx": 8, "row": 2, "col": 1, "row_span": 1, "col_span": 3, "text": "사망자"},
                        {"cell_idx": 9, "row": 2, "col": 4, "row_span": 1, "col_span": 2, "text": "주민등록번호"},
                        {"cell_idx": 10, "row": 3, "col": 1, "row_span": 1, "col_span": 3, "text": "전화번호(자택)"},
                        {"cell_idx": 11, "row": 3, "col": 4, "row_span": 1, "col_span": 2, "text": "휴대전화"},
                    ],
                },
            ],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        self.assertEqual(
            {(e["row"], e["col"]): e["value"] for e in plan["cell_edits"]},
            {(0, 2): "홍길동", (0, 5): "500101-1234567", (1, 2): "서울특별시 구로구 가마산로 000"},
        )
        self.assertTrue(
            any(s["location"] == "T0(3,4)" and s["reason"] == "other_person" for s in plan["report"]["skipped_fields"])
        )
        # HWPX 흰색 글자색(#FFFFFF) 빈 셀은 검정(#000000) 파생 charPr을 적용한다
        header = (
            b"<hh:head xmlns:hh='http://www.hancom.co.kr/hwpml/2011/head'>"
            b"<hh:charProperties itemCnt='2'>"
            b"<hh:charPr id='0' height='1000' textColor='#000000'><hh:spacing hangul='0' latin='0' hanja='0' japanese='0' other='0' symbol='0' user='0'/></hh:charPr>"
            b"<hh:charPr id='1' height='1000' textColor='#FFFFFF'><hh:spacing hangul='0' latin='0' hanja='0' japanese='0' other='0' symbol='0' user='0'/></hh:charPr>"
            b"</hh:charProperties>"
            b"<hh:paraProperties itemCnt='1'>"
            b"<hh:paraPr id='0'><hh:align horizontal='LEFT' vertical='BASELINE'/><hh:lineSpacing type='PERCENT' value='160' unit='HWPUNIT'/></hh:paraPr>"
            b"</hh:paraProperties>"
            b"</hh:head>"
        )
        sec = (
            b"<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
            b"xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
            b"<hp:tbl><hp:tr>"
            b"<hp:tc><hp:cellAddr colAddr='0' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:cellSz width='8000' height='2000'/><hp:cellMargin left='141' right='141' top='141' bottom='141'/><hp:subList><hp:p paraPrIDRef='0'><hp:run charPrIDRef='0'><hp:t>\xec\x84\xb1\xeb\xaa\x85</hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"<hp:tc><hp:cellAddr colAddr='1' rowAddr='0'/><hp:cellSpan colSpan='1' rowSpan='1'/><hp:cellSz width='12000' height='2000'/><hp:cellMargin left='141' right='141' top='141' bottom='141'/><hp:subList><hp:p paraPrIDRef='0'><hp:run charPrIDRef='1'><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc>"
            b"</hp:tr></hp:tbl></hs:sec>"
        )
        out_bytes, rep = fill_hwpx.fill_hwpx_bytes(_make_minimal_hwpx(sec, {"Contents/header.xml": header}))
        self.assertEqual(rep["filled_count"], 1)
        with zipfile.ZipFile(io.BytesIO(out_bytes)) as zf:
            hdr_out = zf.read("Contents/header.xml").decode("utf-8")
            sec_out = zf.read("Contents/section0.xml").decode("utf-8")
            self.assertIn('textColor="#000000"', hdr_out)
            self.assertIn('itemCnt="3"', hdr_out)
            self.assertIn('charPrIDRef="2"', sec_out)

    def test_refund_account_header_row_fills_every_column(self) -> None:
        # 머리 행(성 명·금융기관명·계좌번호·예금주) 아래 빈 값 행: 방금 채운 왼쪽 값 칸을 라벨로 보지 않아 네 칸 모두 채운다(2026-11-19 운영 E2E 026_3).
        sec_xml = '<?xml version="1.0" encoding="UTF-8"?><hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run><hp:tbl><hp:tr><hp:tc><hp:cellAddr colAddr="0" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="2"/><hp:subList><hp:p><hp:run><hp:t>본인부담금 환급계좌</hp:t></hp:run></hp:p></hp:subList></hp:tc><hp:tc><hp:cellAddr colAddr="1" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>성 명</hp:t></hp:run></hp:p></hp:subList></hp:tc><hp:tc><hp:cellAddr colAddr="2" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>금융기관명</hp:t></hp:run></hp:p></hp:subList></hp:tc><hp:tc><hp:cellAddr colAddr="3" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>계좌번호</hp:t></hp:run></hp:p></hp:subList></hp:tc><hp:tc><hp:cellAddr colAddr="4" rowAddr="0"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>예금주</hp:t></hp:run></hp:p></hp:subList></hp:tc></hp:tr><hp:tr><hp:tc><hp:cellAddr colAddr="1" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc><hp:tc><hp:cellAddr colAddr="2" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc><hp:tc><hp:cellAddr colAddr="3" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc><hp:tc><hp:cellAddr colAddr="4" rowAddr="1"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t></hp:t></hp:run></hp:p></hp:subList></hp:tc></hp:tr></hp:tbl></hp:run></hp:p></hs:sec>'.encode("utf-8")
        _, rep = fill_hwpx.fill_hwpx_bytes(_make_minimal_hwpx(sec_xml))
        keys = sorted(f["field_key"] for f in rep["filled_fields"])
        self.assertIn("account.bank", keys)
        self.assertIn("account.number", keys)
        self.assertIn("account.holder", keys)
        self.assertEqual(rep["filled_count"], 4)

    def test_fix_09_multiline_body_skipped_except_date(self) -> None:
        doc_dump = {
            "default_char_shape_id": 0,
            "default_font_pt100": 1000,
            "paragraphs": [
                {"section": 0, "para": 0, "para_align": "Left", "font_pt100": 1000, "line_seg_count": 1, "text": "위와 같이 신청합니다."},
                {"section": 0, "para": 1, "para_align": "Left", "font_pt100": 1000, "line_seg_count": 2, "text": "                     성    명 :                  (서명 또는 날인)\n보조 본문 둘째 줄"},
                {"section": 0, "para": 2, "para_align": "Left", "font_pt100": 1000, "line_seg_count": 1, "text": "                     성    명 :                  (서명 또는 날인)"},
            ],
            "tables": [
                {
                    "section": 0, "para": 0, "control": 0, "row_count": 1, "col_count": 1,
                    "cells": [
                        {
                            "cell_idx": 0, "row": 0, "col": 0, "row_span": 1, "col_span": 1,
                            "text": "20  년  월  일\n신청인  주 소 :                                 \u2028성 명 :                  \U000f012b\u2028(지급대상자와의 관계 :        )",
                            "width": 45000, "height": 5000,
                            "para_aligns": ["Center", "Left"],
                            "para_pt100s": [1000, 1000],
                            "para_line_seg_counts": [1, 3],
                        },
                    ],
                },
            ],
        }
        plan = fill_hwp_plan.plan_hwp_fills(doc_dump)
        self.assertEqual([(e["para"], e["field_key"]) for e in plan["para_edits"]], [(2, "applicant.name")])
        self.assertEqual([(e["cell_para_idx"], e["field_key"]) for e in plan["cell_edits"]], [(0, "apply_date")])
        self.assertGreaterEqual(
            sum(1 for s in plan["report"]["skipped_fields"] if s["reason"] == "multiline_body"),
            2,
        )
        # HWPX lineseg 2개 이상 문단: 날짜 줄만 채우고 성명 줄은 skipped: multiline_body
        sec_ml = (
            b"<hs:sec xmlns:hs='http://www.hancom.co.kr/hwpml/2011/section' "
            b"xmlns:hp='http://www.hancom.co.kr/hwpml/2011/paragraph'>"
            b"<hp:p><hp:run><hp:t>\xec\x8b\xa0\xec\xb2\xad\xed\x95\xa9\xeb\x8b\x88\xeb\x8b\xa4.</hp:t></hp:run></hp:p>"
            b"<hp:p><hp:run><hp:t>20    \xeb\x85\x84     \xec\x9b\x94     \xec\x9d\xbc</hp:t></hp:run>"
            b"<hp:linesegarray><hp:lineseg/><hp:lineseg/></hp:linesegarray></hp:p>"
            b"<hp:p><hp:run><hp:t>" + "성 명 :                  ㉿".encode("utf-8") + b"</hp:t></hp:run>"
            b"<hp:linesegarray><hp:lineseg/><hp:lineseg/></hp:linesegarray></hp:p>"
            b"</hs:sec>"
        )
        _, rep_ml = fill_hwpx.fill_hwpx_bytes(_make_minimal_hwpx(sec_ml))
        self.assertEqual([f["field_key"] for f in rep_ml["filled_fields"]], ["apply_date"])
        self.assertTrue(any(s["reason"] == "multiline_body" for s in rep_ml["skipped_fields"]))


if __name__ == "__main__":
    unittest.main()
