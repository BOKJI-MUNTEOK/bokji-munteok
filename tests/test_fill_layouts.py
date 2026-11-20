"""실제 복지로 서식 770개 분석에서 나온 칸 모양: 안내 글자가 인쇄된 값 칸, 라벨만 있는 표준서식 칸, 명부·부모 칸."""
import io
import sys
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import document_workspace as dw
import form_matcher as fm
from lxml import etree

PROFILE = {"applicant.name": "가상시험", "applicant.birth_date": "1950. 1. 1.", "applicant.phone": "010-1234-5678",
           "applicant.address": "서울특별시 강남구 테헤란로 123"}


def hwpx(rows, width=30000):
    """rows: [[(text, rowspan, colspan), ...], ...] — 열 위치는 왼쪽부터 차례로 계산한다."""
    trs, occupied = [], set()
    for y, row in enumerate(rows):
        x, tcs = 0, []
        for text, rs, cs in row:
            while (y, x) in occupied:
                x += 1
            for yy in range(y, y + rs):
                for xx in range(x, x + cs):
                    occupied.add((yy, xx))
            tcs.append(f'<hp:tc><hp:cellAddr colAddr="{x}" rowAddr="{y}"/><hp:cellSpan colSpan="{cs}" rowSpan="{rs}"/>'
                       f'<hp:cellSz width="{width * cs}" height="2400"/>'
                       f'<hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList></hp:tc>')
            x += cs
        trs.append("<hp:tr>" + "".join(tcs) + "</hp:tr>")
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("mimetype", "application/hwp+zip")
        z.writestr("Contents/header.xml", "<head/>")
        z.writestr("Contents/section0.xml", '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" '
                   'xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph"><hp:p><hp:run><hp:tbl>'
                   + "".join(trs) + "</hp:tbl></hp:run></hp:p></hs:sec>")
    return b.getvalue()


def cells(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        root = etree.fromstring(z.read("Contents/section0.xml"))
    return ["".join(tc.itertext()) for tc in root.iter("{*}tc")]


def one(text):
    return (text, 1, 1)


class Helpers(unittest.TestCase):
    def test_template_fill_mode(self):
        self.assertEqual(fm.template_fill_mode("applicant.birth_date", "(남, 여)"), "prepend")
        self.assertEqual(fm.template_fill_mode("applicant.name", "(남,여)"), "prepend")  # 원아복 구입비 신청서
        self.assertEqual(fm.detect_section_context("공인중개사 정보", "keep"), "other_person")
        self.assertEqual(fm.template_fill_mode("applicant.birth_date", "( 만    세 )"), "prepend")
        self.assertEqual(fm.template_fill_mode("applicant.address", "(전화번호 : )"), "prepend")
        self.assertEqual(fm.template_fill_mode("applicant.phone", "☎"), "append")
        for key, text in (("applicant.address", "(서명 또는 인)"), ("applicant.birth_date", "-"),
                          ("applicant.address", "경기도 화성시 ○○○"),
                          ("applicant.birth_date", "1950년 (남, 여)"), ("applicant.address", "(전화번호 : )\n(휴대폰)")):
            self.assertIsNone(fm.template_fill_mode(key, text), text)

    def test_can_write_after_label(self):
        self.assertTrue(fm.can_write_after_label("applicant.name", "성명", "생년월일(성별)"))
        self.assertTrue(fm.can_write_after_label("applicant.address", "주소", None))
        self.assertFalse(fm.can_write_after_label("applicant.name", "성명", "□□□ - □□□"))
        self.assertFalse(fm.can_write_after_label("applicant.name", "성명", "( )"))
        self.assertFalse(fm.can_write_after_label("account.bank", "은행명", "계좌번호"))
        self.assertFalse(fm.can_write_after_label("applicant.name", "성명\n(한글)", "생년월일"))
        self.assertFalse(fm.can_write_after_label("applicant.name", "서명", "생년월일"))

    def test_roster_and_parent_rows(self):
        roster = [(0, 0, 3, "가족관계"), (0, 1, 1, "성명"), (1, 1, 1, "성명"), (2, 1, 1, "성명")]
        self.assertTrue(fm.in_cell_blocked("성명", 0, 1, roster))
        family_head = [(0, 0, 2, "가구원 현황"), (0, 1, 1, "성명")]
        self.assertTrue(fm.in_cell_blocked("성명", 0, 1, family_head))
        applicant = [(0, 0, 2, "신청인 (가정위탁보호자)"), (0, 1, 1, "성명")]
        self.assertFalse(fm.in_cell_blocked("성명", 0, 1, applicant))
        parents = [(0, 0, 2, "신청인"), (0, 1, 2, "부"), (0, 2, 1, "성명"), (2, 1, 2, "모"), (2, 2, 1, "성명")]
        self.assertTrue(fm.parent_rows(0, 2, parents))
        self.assertTrue(fm.parent_rows(2, 2, parents))
        self.assertFalse(fm.parent_rows(0, 1, parents))

    def test_compound_child_sections_are_other_person(self):
        for text in ("아동수당 신청아동", "대상아동", "위탁아동", "입양아동", "출생아기"):
            self.assertEqual(fm.detect_section_context(text, "applicant"), "other_person", text)
        for text in ("1. 아동의 보호자", "신청아동의 보호자", "신청인"):
            self.assertEqual(fm.detect_section_context(text, "applicant"), "applicant", text)

    def test_beneficiary_is_applicant_only_without_applicant_section(self):
        self.assertTrue(fm.beneficiary_is_applicant("지급대상자 성명 생년월일 주소 위임받은 자"))
        self.assertFalse(fm.beneficiary_is_applicant("신청인 성명 / 지원대상자 성명"))
        self.assertFalse(fm.beneficiary_is_applicant("성명 주소"))
        self.assertEqual(fm.detect_section_context("지급대상자", "applicant"), "other_person")
        token = fm.BENEFICIARY_IS_APPLICANT.set(True)
        try:
            self.assertEqual(fm.detect_section_context("지급대상자", "applicant"), "applicant")
            self.assertEqual(fm.detect_section_context("대상자 (장애인)", "applicant"), "applicant")
            self.assertEqual(fm.detect_section_context("② 가족상황", "applicant"), "other_person")  # 가족은 여전히 다른 사람
        finally:
            fm.BENEFICIARY_IS_APPLICANT.reset(token)
        self.assertIsNone(fm.BENEFICIARY_IS_APPLICANT.get())

    def test_drm_file_is_detected(self):
        import fill_form
        self.assertEqual(fill_form.detect_signature(b"<!-- FasooSecureContainer - Ver. 3.0.01 -->\xff\xff"), "drm")
        self.assertEqual(fill_form.detect_signature(b"\x9b DRMONE  This Document is encrypted"), "drm")
        r = dw.prepare_document(b"\x9b DRMONE  This Document is encrypted", "신청서.hwp", PROFILE)
        self.assertEqual(r["status"], "drm")
        self.assertIn("DRM", r["warnings"][0])

    def test_attachment_kind(self):
        for name in ("아동수당서식모음.pdf", "2026년 학습지원사업 안내문 및 신청서(동의서포함).hwp",
                     "의성군 국가보훈대상자 예우 및 지원에 관한 조례 및 별지서식.pdf", "[별지 제1호서식] 수도요금 감면(조례 시행규칙).hwp",
                     "S36BW-421042717170.pdf", "위임장.hwp", "발달재활서비스 의뢰서.hwp", "보조금 사업계획서.hwp",
                     "[별지 제1호서식] 보조공학기기 지원 업무처리규칙.hwp", "보훈대상자 수당 지급.hwp", "방법 안내 및 신청서.hwp",
                     "신청서 및 작성 예시.hwp", "신청서(작성예시 포함).hwp"):
            self.assertEqual(dw.attachment_kind(name), "form", name)
        for name in ("2025년 아동수당 사업안내.pdf", "고양시 출산가구 전월세자금 대출이자 지원 조례.pdf", "운영지침_지자체 배포.pdf",
                     "(연장공고)2026년 충청남도 청년 주택임차보증금 이자 지원사업 공고문.hwpx", "업무 매뉴얼.pdf", "2020 세부 추진계획.pdf",
                     "아동복지법(법률)(제17784호).pdf", "행여자 귀향여비 지급 기준.hwp", "(2022년 1월)보훈예우수당 지급명세.xlsx",
                     "임산부 검사지원(홈페이지).PNG", "첨부파일없음.hwp", "[별표]인구증가시책별 세부지원 기준.hwp",
                     "장애인복지법(16733).hwp", "자원봉사활동 기본법.hwp", "북한이탈주민 교육지원 예규(제71호).pdf", "없음.hwp",
                     "아이돌봄서비스 본인부담금 추가 지원사업 개요.hwp", "치매검사비 지원 방침결정.pdf",
                     "대통령과학장학금_홈페이지 신청서 작성 매뉴얼_21년 버전.hwp", "신청서 작성방법.pdf", "온라인 신청 가이드.pdf"):
            self.assertEqual(dw.attachment_kind(name), "reference", name)

    def test_real_form_section_phrases(self):
        """아동수당·문화이용권·긴급지원비·무연고사망자 서식 검토에서 나온 문구."""
        cases = {"본인(대리신청인 포함)은 뒷면의 유의사항 등에 대하여": "applicant",
                 "신 청 인 (보호자 또는 대리인)": "applicant", "아동의 인적사항": "other_person",
                 "변경 전 보호자": "other_person", "변경 후 보호자 (신청인)": "applicant", "수급아동": "other_person",
                 "그 외 연고자": "other_person", "단체 정보": "excluded", "대표자 정보": "applicant",
                 "1. 아동의 보호자": "applicant"}
        for text, want in cases.items():
            self.assertEqual(fm.detect_section_context(text, "keep" if want != "applicant" else "applicant"), want, text)
        long_statement = "사실상 시신이나 유골을 관리하는 자(연고자) 또는 장례주관자 지정을 위와 같이 신청합니다."
        self.assertEqual(fm.detect_section_context(long_statement, "keep"), "applicant")
        self.assertEqual(fm.detect_section_context("것을 요청합니다.", "keep"), "applicant")
        self.assertEqual(fm.match_label("성명(국문)", profile={"applicant.name": "가"}).action, "fill")

    def test_decide_kind_from_content(self):
        """운영 DB 첨부 5,358개 내용 검증으로 정한 기준."""
        self.assertEqual(dw.decide_kind("대통령과학장학금_홈페이지 신청서 작성 매뉴얼.hwp", "no_form"), "reference")
        self.assertEqual(dw.decide_kind("2026년 한부모가족지원사업 안내 지침.pdf", "form", pages=355), "reference")
        self.assertEqual(dw.decide_kind("휠체어 수리지원 사업 추진 계획.pdf", "form", pages=4), "form")
        self.assertEqual(dw.decide_kind("증평군 입양가정 지원 조례.hwp", "form", size_kb=60), "form")
        self.assertEqual(dw.decide_kind("2026년 가족사업안내(1권).hwp", "form", size_kb=4000), "reference")
        self.assertEqual(dw.decide_kind("입학준비금 신청서류.hwp", "form", size_kb=900), "form")  # 서식 묶음은 크기 무관
        self.assertEqual(dw.decide_kind("개인정보 수집·이용 동의서.hwp", "no_form"), "form")
        self.assertEqual(dw.decide_kind("(서식) 시신처리 위임서.hwpx", "no_form"), "form")
        self.assertEqual(dw.decide_kind("2023 수어교실 실행계획서.pdf", "no_form"), "reference")
        self.assertEqual(dw.decide_kind("육아휴직 급여 결정 통지서 서식.hwp", "no_form"), "reference")

    def test_attachment_kind_uses_verified_table_first(self):
        from unittest.mock import patch
        url = "https://www.bokjiro.go.kr/x"
        with patch.object(dw, "_attachment_kinds", return_value={dw.attachment_key(url): "reference"}):
            self.assertEqual(dw.attachment_kind("신청서.hwp", url), "reference")
            self.assertEqual(dw.attachment_kind("신청서.hwp", "https://www.bokjiro.go.kr/new"), "form")  # 표에 없으면 이름 규칙

    def test_checkbox_option_line_is_not_a_section_header(self):
        line = "[ ] 참전유공자 명예수당 지급 [ ] 참전유공자 배우자 복지수당 지급"
        self.assertEqual(fm.detect_section_context(line, "applicant"), "applicant")


class EngineLayouts(unittest.TestCase):
    def fill(self, rows, name="지원신청서.hwpx"):
        r = dw.prepare_document(hwpx(rows), name, PROFILE)
        return r, cells(r["data"])

    def test_prefix_template_keeps_printed_hint(self):
        r, out = self.fill([[(" 지원신청서", 1, 2)], [one("성명"), one("")], [one("생년월일"), one("(남, 여)")],
                            [one("주소"), one("(전화번호 : )")]])
        self.assertIn("1950. 1. 1.   (남, 여)", out)
        self.assertIn("서울특별시 강남구 테헤란로 123   (전화번호 : )", out)

    def test_standard_form_writes_after_label_in_same_cell(self):
        r, out = self.fill([[("지원신청서", 1, 3)],
                            [("신청인", 3, 1), one("성명"), one("생년월일(성별)")],
                            [one("전화번호"), one("전자우편주소")],
                            [("주소", 1, 2)]])
        self.assertIn("성명  가상시험", out)
        self.assertIn("전화번호  010-1234-5678", out)
        self.assertIn("주소  서울특별시 강남구 테헤란로 123", out)
        self.assertIn("전자우편주소", out)  # 다른 라벨은 그대로

    def test_roster_rows_stay_blank(self):
        r, out = self.fill([[("지원신청서", 1, 3)],
                            [one("신청인"), one("성명"), one("")],
                            [("가족관계", 2, 1), one("성명"), one("생년월일")],
                            [one("성명"), one("생년월일")]])
        self.assertEqual(sum("가상시험" in c for c in out), 1)

    def test_parent_rows_stay_blank(self):
        r, out = self.fill([[("지원신청서", 1, 4)],
                            [("신청인", 2, 1), one("부"), one("성명"), one("")],
                            [one("모"), one("성명"), one("")]])
        self.assertFalse(any("가상시험" in c for c in out))


if __name__ == "__main__":
    unittest.main()
