"""보안(.hwpx ZIP 상한·XXE 차단), PDF 회전·CropBox·AcroForm, HWP/청약서 기입 규칙 단위 테스트."""
from __future__ import annotations

import io
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
