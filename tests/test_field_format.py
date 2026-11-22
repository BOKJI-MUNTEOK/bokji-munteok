"""서류마다 다른 날짜·전화번호 표기: 입력 해석, 서류 표기 감지, 실제 양식 기입."""
import io
import sys
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import document_workspace as dw
import field_format as ff
import form_matcher
from lxml import etree
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DictionaryObject, NameObject, NumberObject, TextStringObject
from reportlab.pdfgen.canvas import Canvas


def hwpx_form(rows, after=""):
    cells = []
    for y, (label, value) in enumerate(rows):
        cells.append("<hp:tr>" + "".join(
            f'<hp:tc><hp:cellAddr colAddr="{x}" rowAddr="{y}"/><hp:cellSpan colSpan="1" rowSpan="1"/>'
            f'<hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList></hp:tc>'
            for x, text in enumerate([label, value])) + "</hp:tr>")
    body = "<hp:p><hp:run><hp:tbl>" + "".join(cells) + "</hp:tbl></hp:run></hp:p>" + after
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/header.xml", "<head/>")
        z.writestr("Contents/section0.xml", '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
                   'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">' + body + "</hs:sec>")
    return b.getvalue()


def hwpx_text(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        return "".join(etree.fromstring(z.read("Contents/section0.xml")).itertext())


def acroform(label):
    b = io.BytesIO()
    c = Canvas(b, pagesize=(600, 300))
    c.drawString(50, 230, "Application")
    c.rect(100, 150, 200, 25)
    c.save()
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(b.getvalue())))
    p = w.pages[0]
    a = DictionaryObject({NameObject("/Type"): NameObject("/Annot"), NameObject("/Subtype"): NameObject("/Widget"),
                          NameObject("/FT"): NameObject("/Tx"), NameObject("/T"): TextStringObject("birth"),
                          NameObject("/TU"): TextStringObject(label), NameObject("/V"): TextStringObject(""),
                          NameObject("/Rect"): ArrayObject([NumberObject(v) for v in [100, 150, 300, 175]]),
                          NameObject("/P"): p.indirect_reference})
    ref = w._add_object(a)
    p[NameObject("/Annots")] = ArrayObject([ref])
    w._root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): ArrayObject([ref])})
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


class ParseAndFormat(unittest.TestCase):
    def test_dates_people_type(self):
        for raw in ("19500101", "1950.1.1", "1950-01-01", "1950/1/1", "1950년 1월 1일", "1950. 01. 01.", "500101"):
            self.assertEqual(ff.parse_date(raw, today=date(2026, 11, 20)), date(1950, 1, 1), raw)

    def test_six_digit_century_and_invalid_dates(self):
        self.assertEqual(ff.parse_date("051231", today=date(2026, 11, 20)), date(2005, 12, 31))
        self.assertEqual(ff.parse_date("301231", today=date(2026, 11, 20)), date(1930, 12, 31))
        for raw in ("19501301", "19500230", "abc", "", "1950", "12-34", "2026년"):
            self.assertIsNone(ff.parse_date(raw), raw)

    def test_date_styles(self):
        d = date(1950, 1, 5)
        self.assertEqual([ff.format_date(d, s) for s in ff.DATE_STYLES],
                         ["1950. 1. 5.", "1950. 01. 05.", "1950-01-05", "1950년 1월 5일", "19500105", "500105", "1950.1.5."])

    def test_phone_hyphen_rules(self):
        cases = {"01012345678": "010-1234-5678", "0101234567": "010-123-4567", "0212345678": "02-1234-5678",
                 "021234567": "02-123-4567", "0311234567": "031-123-4567", "03112345678": "031-1234-5678",
                 "0641234567": "064-123-4567", "07012345678": "070-1234-5678", "15881234": "1588-1234",
                 "050512345678": "0505-1234-5678", "+82 10-1234-5678": "010-1234-5678",
                 "010 1234 5678": "010-1234-5678", "(010)1234-5678": "010-1234-5678"}
        for raw, want in cases.items():
            self.assertEqual(ff.format_phone(raw), want, raw)

    def test_unknown_phone_is_left_as_typed(self):
        for raw in ("12345", "010-12", "0999999999999", "내선 123"):
            self.assertEqual(ff.format_phone(raw), raw.strip())
            self.assertIsNone(ff.phone_parts(raw))

    def test_phone_styles(self):
        self.assertEqual(ff.format_phone("01012345678", "paren"), "(010) 1234-5678")
        self.assertEqual(ff.format_phone("010-1234-5678", "digits"), "01012345678")
        self.assertEqual(ff.format_phone("15881234", "paren"), "1588-1234")


class LabelHints(unittest.TestCase):
    def test_hint_is_removed_and_read(self):
        cases = {"생년월일(YYYYMMDD)": ("생년월일", {"date": "digits"}),
                 "생년월일 (예: 1950.01.01)": ("생년월일", {"date": "dot_pad"}),
                 "생년월일(1950-01-01)": ("생년월일", {"date": "dash"}),
                 "생년월일(6자리)": ("생년월일", {"date": "yymmdd"}),
                 "휴대전화(010-0000-0000)": ("휴대전화", {"phone": "hyphen"}),
                 "연락처('-' 없이)": ("연락처", {"date": "digits", "phone": "digits"})}
        for label, want in cases.items():
            self.assertEqual(ff.split_label_hint(label), want, label)

    def test_ordinary_parentheses_are_kept(self):
        for label in ("주민등록번호(외국인등록번호)", "성명(한글)", "주소(주민등록상)", "생년월일"):
            self.assertEqual(ff.split_label_hint(label), (label, {}))

    def test_match_label_uses_hint_for_value(self):
        prof = {"applicant.birth_date": "1950. 1. 1.", "applicant.phone": "010-1234-5678",
                "applicant.rrn": "500101-1234567"}
        self.assertEqual(form_matcher.match_label("생년월일(YYYYMMDD)", profile=prof).value, "19500101")
        self.assertEqual(form_matcher.match_label("생년월일 (예: 1950-01-01)", profile=prof).value, "1950-01-01")
        self.assertEqual(form_matcher.match_label("연락처(숫자만)", profile=prof).value, "01012345678")
        rrn = form_matcher.match_label("주민등록번호(외국인등록번호)", profile=prof)
        self.assertEqual((rrn.action, rrn.value), ("fill", "500101-1234567"))

    def test_birth_label_with_alternate_number_note(self):
        # 국민연금 노후긴급자금 대부 신청서의 신청자 칸 '생년월일(nps번호)'
        prof = {"applicant.birth_date": "1957년 12월 31일"}
        for label in ("생년월일(nps번호)", "생년월일(NPS번호)", "생년월일 (가입자번호)"):
            d = form_matcher.match_label(label, profile=prof)
            self.assertEqual((d.action, d.field_key, d.value), ("fill", "applicant.birth_date", "1957년 12월 31일"), label)
        self.assertEqual(form_matcher.match_label("생년월일(nps번호)", section="proxy", profile=prof).action, "skip")

    def test_hint_never_invents_missing_value(self):
        d = form_matcher.match_label("생년월일(YYYYMMDD)", profile={"applicant.name": "가"})
        self.assertEqual(d.action, "skip")
        self.assertIsNone(d.value)

    def test_signature_label_with_hint_stays_blank(self):
        d = form_matcher.match_label("서명(예: 홍길동)", profile={"applicant.name": "가"})
        self.assertEqual(d.action, "sensitive_blank")


class DocumentStyles(unittest.TestCase):
    def test_detects_form_date_style(self):
        self.assertEqual(ff.detect_document_styles("신청일자    20   .    .    .")["date"], "dot")
        self.assertEqual(ff.detect_document_styles("위와 같이 신청합니다.\n     년     월     일")["date"], "korean")
        self.assertEqual(ff.detect_document_styles("작성 예: 2026-01-05")["date"], "dash")
        self.assertEqual(ff.detect_document_styles("개정 2018. 01. 22.")["date"], "dot_pad")
        self.assertEqual(ff.detect_document_styles("")["date"], ff.DEFAULT_DATE_STYLE)

    def test_detects_phone_style(self):
        self.assertEqual(ff.detect_document_styles("연락처 (     )     -")["phone"], "paren")
        self.assertEqual(ff.detect_document_styles("연락처(숫자만 기재)")["phone"], "digits")
        self.assertEqual(ff.detect_document_styles("연락처")["phone"], "hyphen")

    def test_adapt_profile_only_touches_readable_values(self):
        prof = {"applicant.birth_date": "19500101", "applicant.phone": "01012345678", "proxy.phone": "내선 12",
                "applicant.name": "1950-01-01", "apply_date": "2026년 11월 20일"}
        out = ff.adapt_profile(prof, {"date": "korean", "phone": "hyphen"})
        self.assertEqual(out["applicant.birth_date"], "1950년 1월 1일")
        self.assertEqual(out["applicant.phone"], "010-1234-5678")
        self.assertEqual(out["proxy.phone"], "내선 12")
        self.assertEqual(out["applicant.name"], "1950-01-01")
        self.assertEqual(out["apply_date"], "2026년 11월 20일")
        self.assertEqual(ff.adapt_profile({}, {"date": "dot"}), {})
        self.assertNotIn("applicant.phone", ff.adapt_profile({"applicant.name": "가"}, {"phone": "hyphen"}))

    def test_bad_birth_date_is_kept_as_typed(self):
        out = ff.adapt_profile({"applicant.birth_date": "1950년쯤"}, {"date": "dot"})
        self.assertEqual(out["applicant.birth_date"], "1950년쯤")


class RealFormFilling(unittest.TestCase):
    """실제 기입 엔진까지 거쳐 서류마다 다른 표기로 들어가는지 확인한다."""
    profile = {"applicant.name": "가상시험", "applicant.birth_date": "19500101", "applicant.phone": "01012345678"}

    def test_hwpx_korean_date_form(self):
        raw = hwpx_form([("지원신청서", ""), ("성명", ""), ("생년월일", ""), ("연락처", ""),
                         ("위와 같이 신청합니다.", "      년     월     일")])
        r = dw.prepare_document(raw, "지원신청서.hwpx", self.profile)
        self.assertEqual(r["formats"]["date"], "korean")
        text = hwpx_text(r["data"])
        self.assertIn("1950년 1월 1일", text)
        self.assertIn("010-1234-5678", text)
        self.assertNotIn("19500101", text)

    def test_hwpx_dot_date_form(self):
        raw = hwpx_form([("지원신청서", ""), ("성명", ""), ("생년월일", ""), ("신청일자", "20   .    .    .")])
        r = dw.prepare_document(raw, "지원신청서.hwpx", self.profile)
        self.assertEqual(r["formats"]["date"], "dot")
        self.assertIn("1950. 1. 1.", hwpx_text(r["data"]))

    def test_hwpx_paren_phone_form(self):
        raw = hwpx_form([("지원신청서", ""), ("성명", ""), ("연락처", ""), ("전화 (     )     -", "")])
        r = dw.prepare_document(raw, "지원신청서.hwpx", self.profile)
        self.assertEqual(r["formats"]["phone"], "paren")
        self.assertIn("(010) 1234-5678", hwpx_text(r["data"]))

    def test_hwpx_cell_hint_beats_document_style(self):
        raw = hwpx_form([("지원신청서", ""), ("성명", ""), ("생년월일(YYYYMMDD)", ""),
                         ("위와 같이 신청합니다.", "      년     월     일")])
        r = dw.prepare_document(raw, "지원신청서.hwpx", self.profile)
        text = hwpx_text(r["data"])
        self.assertIn("19500101", text)
        self.assertNotIn("1950년 1월 1일", text)

    def test_guide_text_outside_form_does_not_decide_style(self):
        guide = "<hp:p><hp:run><hp:t>개정 2018-01-22 시행 2019-03-04 2020-05-06</hp:t></hp:run></hp:p>"
        raw = hwpx_form([("지원신청서", ""), ("성명", ""), ("생년월일", "")], after=guide)
        r = dw.prepare_document(raw, "지원신청서.hwpx", self.profile)
        self.assertEqual(r["formats"]["date"], ff.DEFAULT_DATE_STYLE)

    def test_pdf_acroform_label_hint(self):
        r = dw.prepare_document(acroform("생년월일(YYYYMMDD)"), "신청서.pdf", self.profile)
        self.assertEqual(r["status"], "filled")
        text = "".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(r["data"])).pages)
        fields = PdfReader(io.BytesIO(r["data"])).get_fields() or {}
        values = text + "".join(str(f.get("/V") or "") for f in fields.values())
        self.assertIn("19500101", values)

    def test_narrow_cell_retries_with_compact_format(self):
        """실제 서식에서 발견: 하이픈 넣은 번호가 좁은 칸에 안 들어가 빈칸이 되던 문제."""
        real_fill = dw.fill_hwpx.fill_hwpx_bytes

        def narrow(raw, profile=None, allowed_blocks=None):
            data, rep = real_fill(raw, profile=profile, allowed_blocks=allowed_blocks)
            if len(profile.get("applicant.phone", "")) > 11:  # 이 칸에는 11자까지만 들어간다
                rep = dict(rep, filled_count=rep["filled_count"] - 1,
                           filled_fields=[f for f in rep["filled_fields"] if f["field_key"] != "applicant.phone"],
                           skipped_fields=rep["skipped_fields"] + [{"label": "휴대전화", "reason": "no_room"}])
            return data, rep
        raw = hwpx_form([("지원신청서", ""), ("성명", ""), ("휴대전화", "")])
        with patch.object(dw.fill_hwpx, "fill_hwpx_bytes", side_effect=narrow) as fill:
            r = dw.prepare_document(raw, "지원신청서.hwpx", self.profile)
        self.assertEqual(fill.call_count, 2)
        self.assertEqual(r["formats"], ff.COMPACT_STYLES)
        self.assertEqual(r["filled_count"], 2)
        self.assertIn("01012345678", hwpx_text(r["data"]))

    def test_no_retry_when_everything_fits(self):
        raw = hwpx_form([("지원신청서", ""), ("성명", ""), ("휴대전화", "")])
        with patch.object(dw.fill_hwpx, "fill_hwpx_bytes", wraps=dw.fill_hwpx.fill_hwpx_bytes) as fill:
            r = dw.prepare_document(raw, "지원신청서.hwpx", self.profile)
        self.assertEqual(fill.call_count, 1)
        self.assertIn("010-1234-5678", hwpx_text(r["data"]))

    def test_formats_report_has_no_personal_values(self):
        raw = hwpx_form([("지원신청서", ""), ("성명", ""), ("생년월일", "")])
        r = dw.prepare_document(raw, "지원신청서.hwpx", self.profile)
        report = repr({k: v for k, v in r.items() if k != "data"})
        for value in ("가상시험", "1950. 1. 1.", "19500101", "010-1234-5678", "01012345678"):
            self.assertNotIn(value, report)


if __name__ == "__main__":
    unittest.main()
