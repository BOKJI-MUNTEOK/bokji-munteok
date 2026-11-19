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
import packet  # noqa: E402


def _make_minimal_hwpx(section_xml: bytes, extra_entries: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", b"application/hwp+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("Contents/header.xml", b"<hh:head xmlns:hh='http://www.hancom.co.kr/hwpml/2011/head'/>")
        zf.writestr("Contents/section0.xml", section_xml)
        for k, v in (extra_entries or {}).items():
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
        out_bytes, report = fill_hwpx.fill_hwpx_bytes(data)
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["filled_count"], 2)
        with zipfile.ZipFile(io.BytesIO(out_bytes), "r") as zout:
            sec_out = zout.read("Contents/section0.xml").decode("utf-8")
        self.assertIn("2026 년 11 월 19 일", sec_out)
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
                    {"name": "지원신청서.hwpx", "url": "https://example.invalid/a.hwpx"},
                    {"name": "소득확인서식.xlsx", "url": "https://example.invalid/b.xlsx"},
                    {"name": "사업공고문.pdf", "url": "https://example.invalid/c.pdf"},
                ],
            },
            "ai": None,
            "ext": {"documents": ["신분증"]},
        }
        files = {
            "https://example.invalid/a.hwpx": hwpx_raw,
            "https://example.invalid/b.xlsx": xlsx_raw,
            "https://example.invalid/c.pdf": ref_raw,
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

        # 2. packet_box 내부에 type="password" 사용 및 st.exception 미사용 확인
        pkt_fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "packet_box")
        has_password_rrn = False
        for node in ast.walk(pkt_fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "exception":
                    self.fail("packet_box must not call st.exception (risk of leaking user input)")
                if node.func.attr == "text_input":
                    for kw in node.keywords:
                        if kw.arg == "type" and isinstance(kw.value, ast.Constant) and kw.value.value == "password":
                            has_password_rrn = True
        self.assertTrue(has_password_rrn, "packet_box must mask RRN input with type='password'")

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

    def test_app_packet_zip_flow_apptest(self) -> None:
        import base64
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        from parse import load_all

        st.cache_data.clear()
        st.cache_resource.clear()
        self.addCleanup(st.cache_data.clear)
        self.addCleanup(st.cache_resource.clear)

        root = Path(__file__).resolve().parent.parent
        sample = load_all(root / "data" / "sample")
        svc = next(s for s in sample if any(packet.is_form(f["name"]) for f in s.get("form_files", [])))
        sid = svc["servId"]

        sec_xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
            'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:p><hp:run><hp:t>신청인 :               (서명)</hp:t></hp:run></hp:p>'
            '</hs:sec>'
        ).encode("utf-8")
        hwpx_b64 = base64.b64encode(_make_minimal_hwpx(sec_xml)).decode("ascii")

        def run_packet_ui(repo_dir: str, serv_id: str, fake_hwpx_b64: str) -> None:
            import base64 as _b64
            import sys as _sys
            from pathlib import Path as _Path
            from unittest.mock import patch as _patch

            _root = _Path(repo_dir)
            for _p in (str(_root), str(_root / "src")):
                if _p not in _sys.path:
                    _sys.path.insert(0, _p)
            import app as _app
            import packet as _packet
            from parse import load_all as _load_all
            from pipeline import build as _build

            _sample = _load_all(_root / "data" / "sample")
            _svc = next(x for x in _sample if x["servId"] == serv_id)
            _row = _build(dict(_app.W), "규칙", [_svc])[0]
            _raw = _b64.b64decode(fake_hwpx_b64)

            def _fake_dl(url: str, timeout: int = 15) -> bytes:
                return _raw

            with _patch.object(_packet, "download", side_effect=_fake_dl):
                _app.packet_box(_row)

        with patch("welfare_api.WelfareAPI") as m_api:
            m_api.return_value.catalog.return_value = {
                "services": sample,
                "counts": {"central": 25, "local": 25},
                "updated_at": 1794873600,
                "warnings": [],
            }
            m_api.return_value.services.return_value = sample
            at = AppTest.from_function(run_packet_ui, args=(str(root), sid, hwpx_b64), default_timeout=60).run()
            self.assertFalse(at.exception, at.exception)

            at.text_input(key=f"pk_name_{sid}").input("홍길동")
            at.text_input(key=f"pk_phone_{sid}").input("010-0000-0000")
            at.text_input(key=f"pk_rrn_{sid}").input("500101-1234567")
            at.text_input(key=f"pk_income_{sid}").input("1,000,000")
            at.text_input(key=f"pk_addr_{sid}").input("서울특별시 구로구 가마산로 000")
            at.text_input(key=f"pk_bank_{sid}").input("농협")
            at.text_input(key=f"pk_acct_{sid}").input("000-0000-0000-00")
            at.text_input(key=f"pk_holder_{sid}").input("홍길동")
            at.text_input(key=f"pk_proxy_{sid}").input("김대리")
            at.text_input(key=f"pk_rel_{sid}").input("자녀")
            at.button(key=f"pk_make_{sid}").click().run()
            self.assertFalse(at.exception, at.exception)

            zip_data, failed = at.session_state[f"pk_{sid}"]
            self.assertEqual(failed, [])
            with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zout:
                names = zout.namelist()
                self.assertTrue(any(n.startswith("1_신청준비_") and n.endswith(".pdf") for n in names))
                self.assertTrue(any(n.startswith("2_기관양식(작성해서 제출)/작성됨_") for n in names))


if __name__ == "__main__":
    unittest.main()
