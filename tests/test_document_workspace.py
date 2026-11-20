"""Document flow: scoped filling, review evidence, input changes and PDF overflow."""
import io
import json
import sys
import unittest
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
import document_workspace as dw
import fill_form
import fill_pdf
import form_matcher
from lxml import etree
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DictionaryObject, NameObject, TextStringObject, ArrayObject, NumberObject
from reportlab.pdfgen.canvas import Canvas


def table(title):
    rows=[]
    for y, label in enumerate([title, '성명', '주소']):
        cells=[]
        for x,text in enumerate([label, '']):
            cells.append(f'<hp:tc><hp:cellAddr colAddr="{x}" rowAddr="{y}"/><hp:cellSpan colSpan="1" rowSpan="1"/><hp:subList><hp:p><hp:run><hp:t>{text}</hp:t></hp:run></hp:p></hp:subList></hp:tc>')
        rows.append('<hp:tr>'+''.join(cells)+'</hp:tr>')
    return '<hp:p><hp:run><hp:tbl>'+''.join(rows)+'</hp:tbl></hp:run></hp:p>'


def hwpx(*blocks):
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('mimetype','application/hwp+zip')
        z.writestr('Contents/header.xml','<head/>')
        z.writestr('Contents/section0.xml','<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section" xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'+''.join(blocks)+'</hs:sec>')
    return b.getvalue()


def acroform():
    b=io.BytesIO();c=Canvas(b,pagesize=(600,300));c.drawString(50,230,'Application');c.rect(100,150,120,25);c.save()
    w=PdfWriter(clone_from=PdfReader(io.BytesIO(b.getvalue())));p=w.pages[0]
    a=DictionaryObject({NameObject('/Type'):NameObject('/Annot'),NameObject('/Subtype'):NameObject('/Widget'),NameObject('/FT'):NameObject('/Tx'),NameObject('/T'):TextStringObject('address'),NameObject('/TU'):TextStringObject('주소'),NameObject('/V'):TextStringObject(''),NameObject('/Rect'):ArrayObject([NumberObject(v) for v in [100,150,220,175]]),NameObject('/P'):p.indirect_reference})
    ref=w._add_object(a);p[NameObject('/Annots')]=ArrayObject([ref]);w._root_object[NameObject('/AcroForm')]=DictionaryObject({NameObject('/Fields'):ArrayObject([ref])})
    b=io.BytesIO();w.write(b);return b.getvalue()


class DocumentWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.raw=hwpx(table('지원신청서'))
        self.profile={'applicant.name':'가상시험','applicant.address':'서울 가상로 1'}

    def test_refs_are_stable_and_deduplicated_without_network(self):
        a={'name':'신청서.hwpx','url':'https://www.bokjiro.go.kr/a'}
        with patch('urllib.request.urlopen',side_effect=AssertionError('must not fetch')):
            refs=dw.list_documents({'form_files':[a,a]})
        self.assertEqual(len(refs),1)
        self.assertEqual(refs[0]['id'],dw.list_documents({'form_files':[dict(a,name='새이름') ]})[0]['id'])

    def test_blank_profile_shows_original_without_default_person(self):
        r=dw.prepare_document(self.raw,'신청서.hwpx',{})
        self.assertEqual(r['data'],self.raw)
        self.assertEqual(r['filled_count'],0)
        self.assertTrue(r['analysis']['fillable_candidate'])
        self.assertEqual(r['template_status'],'new')

    def test_form_in_guide_only_fills_confirmed_block(self):
        raw=hwpx(table('담당자 안내'),table('지원신청서'))
        r=dw.prepare_document(raw,'사업 운영지침.hwpx',self.profile)
        self.assertEqual(r['status'],'filled')
        with zipfile.ZipFile(io.BytesIO(r['data'])) as z:
            root=etree.fromstring(z.read('Contents/section0.xml'))
        ps=list(root)
        self.assertNotIn('가상시험',''.join(ps[0].itertext()))
        self.assertIn('가상시험',''.join(ps[1].itertext()))
        self.assertIn('서울 가상로 1',''.join(ps[1].itertext()))

    def test_reference_and_bank_owned_blocks_remain_original(self):
        for title in ('담당자 안내','지원신청서 은행에서 작성','지원신청서 작성예시','대출 원리금 납입 증명서'):
            raw=hwpx(table(title))
            r=dw.prepare_document(raw,'사업 공고문.hwpx',self.profile)
            self.assertEqual(r['data'],raw,title)
            self.assertEqual(r['status'],'reference',title)

    def test_review_registry_requires_current_engine_and_visual_evidence(self):
        import hashlib
        digest=hashlib.sha256(self.raw).hexdigest()
        entry={'source_sha256':digest,'filenames':['신청서.hwpx'],'reviewed_at':'2026-11-20',
               'engine_version':dw.ENGINE_VERSION,'checks':{'visual_review':True}}
        reg={'entries':[entry]}
        self.assertEqual(dw.prepare_document(self.raw,'신청서.hwpx',{},registry=reg)['template_status'],'verified')
        self.assertEqual(len(dw.list_verified_templates(reg)),1)
        self.assertEqual(dw.prepare_document(hwpx(table('다른신청서')),'신청서.hwpx',{},registry=reg)['template_status'],'changed')
        entry['engine_version']='old-version'
        self.assertEqual(dw.prepare_document(self.raw,'신청서.hwpx',{},registry=reg)['template_status'],'changed')
        self.assertFalse(dw.list_verified_templates(reg))
        entry['engine_version']=dw.ENGINE_VERSION;entry['checks']['visual_review']=False
        self.assertNotEqual(dw.prepare_document(self.raw,'신청서.hwpx',{},registry=reg)['template_status'],'verified')

    def test_shipped_registry_entries_are_valid_reviews(self):
        reg=dw.load_registry()
        entries=reg.get('entries',[])
        self.assertTrue(entries)
        for e in entries:
            self.assertRegex(e['source_sha256'],r'^[0-9a-f]{64}$')
            self.assertTrue(e.get('filenames'))
            self.assertTrue(e.get('review_scope'))
        self.assertEqual(len(dw.list_verified_templates(reg)),len(entries))

    def test_reports_do_not_include_profile_values(self):
        r=dw.prepare_document(self.raw,'신청서.hwpx',self.profile)
        safe={k:v for k,v in r.items() if k!='data'}
        text=json.dumps(safe,ensure_ascii=False)
        self.assertNotIn('가상시험',text)
        self.assertNotIn('서울 가상로 1',text)
        self.assertIn('이름',r['filled_labels'])

    def test_fingerprint_changes_with_edits_and_clear(self):
        original={'document':'A','profile':self.profile}
        fp=dw.fingerprint_inputs(original)
        self.assertEqual(fp,dw.fingerprint_inputs({'profile':dict(reversed(list(self.profile.items()))),'document':'A'}))
        self.assertNotEqual(fp,dw.fingerprint_inputs({'document':'A','profile':{}}))
        self.assertNotEqual(fp,dw.fingerprint_inputs({'document':'B','profile':self.profile}))
        self.assertNotEqual(fp,dw.fingerprint_inputs({'document':'A','profile':dict(self.profile,**{'applicant.name':'수정'})}))

    def test_bad_or_unsupported_file_returns_original(self):
        for raw,name in [(b'not a zip','test.zip'),(b'%PDF-broken','신청서.pdf')]:
            r=dw.prepare_document(raw,name,self.profile)
            self.assertEqual(r['data'],raw)
            self.assertFalse(r['preview_allowed'])
            self.assertFalse(r['filled_count'])

    def test_download_names_follow_actual_format_and_drop_paths(self):
        for name in ('../../지원신청서.hwp', r'C:\\Users\\someone\\지원신청서.hwp'):
            opened=dw.prepare_document(acroform(),name,{})
            written=dw.prepare_document(acroform(),name,{'applicant.address':'서울'})
            self.assertEqual(opened['format'],'pdf')
            self.assertEqual(opened['filename'],'지원신청서.pdf')
            self.assertEqual(written['filename'],'작성됨_지원신청서.pdf')
        name=dw.safe_filename('가'*300+'.hwp','pdf',prefix='작성됨_')
        self.assertLessEqual(len(name),160)
        self.assertTrue(name.endswith('.pdf'))

    def test_long_acroform_address_is_not_drawn_outside_field(self):
        raw=acroform()
        value='서울특별시 구로구 가상로 123 가상아파트 123동 1234호 상세주소가아주긴경우추가설명'
        filled,report=fill_pdf.fill_pdf_bytes(raw,{'applicant.address':value})
        self.assertEqual(report['filled_count'],0)
        self.assertTrue(any(f['reason']=='text_overflow' for f in report['skipped_fields']))
        self.assertNotIn(value,PdfReader(io.BytesIO(filled)).pages[0].extract_text())
        r=dw.prepare_document(raw,'지원신청서.pdf',{'applicant.address':value})
        self.assertEqual(r['status'],'manual')
        self.assertTrue(any('좁아' in w for w in r['warnings']))

    def test_short_acroform_address_has_print_only_notice(self):
        r=dw.prepare_document(acroform(),'지원신청서.pdf',{'applicant.address':'서울 가상로 1'})
        self.assertEqual(r['status'],'filled')
        self.assertTrue(any('인쇄용' in w for w in r['warnings']))

    def test_pdf_allowed_pages_and_existing_values_are_preserved(self):
        raw=acroform()
        _, report=fill_pdf.fill_pdf_bytes(raw,{'applicant.address':'서울'},allowed_pages=set())
        self.assertEqual(report['filled_count'],0)
        w=PdfWriter(clone_from=PdfReader(io.BytesIO(raw)))
        w.pages[0]['/Annots'][0].get_object()[NameObject('/V')]=TextStringObject('기존 주소')
        b=io.BytesIO();w.write(b)
        filled,report=fill_pdf.fill_pdf_bytes(b.getvalue(),{'applicant.address':'새 주소'})
        self.assertEqual(report['filled_count'],0)
        self.assertEqual(PdfReader(io.BytesIO(filled)).get_fields()['address']['/V'],'기존 주소')

    def test_date_only_profile_does_not_mark_form_written(self):
        r=dw.prepare_document(self.raw,'신청서.hwpx',{'apply_date':'2026년 11월 20일'})
        self.assertEqual(r['data'],self.raw)
        self.assertEqual(r['filled_count'],0)

    def test_engine_output_that_cannot_reopen_is_rejected(self):
        with patch.object(fill_hwpx := __import__('fill_hwpx'),'fill_hwpx_bytes',return_value=(b'broken',{'status':'ok','filled_count':1})):
            r=dw.prepare_document(self.raw,'신청서.hwpx',self.profile)
        self.assertEqual(r['status'],'error')
        self.assertEqual(r['data'],self.raw)

    def test_mixed_binary_hwp_requires_scope_review(self):
        raw=b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'+b'HWP Document File'
        r=dw.prepare_document(raw,'사업 안내문 및 신청서.hwp',self.profile)
        self.assertTrue(r['analysis']['fillable_candidate'])
        self.assertFalse(r['analysis']['automatic_allowed'])
        self.assertEqual(r['status'],'manual')
        self.assertEqual(r['data'],raw)


if __name__=='__main__':unittest.main()
