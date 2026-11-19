"""HWPX 서식 자동 기입 엔진 (zipfile + lxml.etree).

특징:
1. xml.etree.ElementTree 대신 lxml.etree를 사용하여 원본 네임스페이스(hp/hs/hc 등)와 header.xml을 보존.
2. 표 셀(<hp:tc>)의 cellAddr/cellSpan 기반 격자(grid)를 복원해 구역(신청인/대리인/제외구역)을 추적하고,
   오른쪽 빈 셀 또는 헤더행 바로 아래 빈 셀(계좌 헤더행 등)에 기입.
3. 표 내부 또는 본문의 인라인 신청일('년   월   일')과
   신청인 성명 줄('신청인(대리신청인) :        (서명 또는 인)')도 서명란을 비운 채 성명만 안전하게 기입.
   단, 출산예정일/사망일 등 신청일 외 문맥이나 '20   년'처럼 앞 두 자리 고정 연도 문맥은 보수적으로 판별.
4. 수정한 문단의 오래된 <hp:linesegarray>를 제거해 한컴오피스/뷰어에서 텍스트가 잘리지 않도록 함.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from typing import Any

from lxml import etree

import form_matcher as fm

NS_HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
MAX_HWPX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
MAX_HWPX_ENTRIES = 1000
SAFE_XML_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _tc_text(tc: etree._Element) -> str:
    return "\n".join(
        "".join(p.xpath(".//*[local-name()='t']/text()"))
        for p in tc.xpath("./*[local-name()='subList']/*[local-name()='p']")
    ).strip()


def _set_tc_text(tc: etree._Element, value: str) -> bool:
    """빈 셀 tc의 첫 번째 문단 첫 번째 run에 value를 넣는다. 원본 문단/글자 스타일은 유지."""
    sub = next((ch for ch in tc if _local(ch.tag) == "subList"), None)
    if sub is None:
        return False
    ps = [ch for ch in sub if _local(ch.tag) == "p"]
    if not ps:
        return False
    p0 = ps[0]
    for lsa in list(p0):
        if _local(lsa.tag) == "linesegarray":
            p0.remove(lsa)
    runs = [ch for ch in p0 if _local(ch.tag) == "run"]
    if runs:
        run0 = runs[0]
        t_el = next((ch for ch in run0 if _local(ch.tag) == "t"), None)
        if t_el is None:
            t_el = etree.SubElement(run0, f"{{{NS_HP}}}t")
        t_el.text = value
        for r in runs[1:]:
            if not any(_local(c.tag) in ("tbl", "ctrl", "rect", "pic", "linesegarray") for c in r):
                for c in r:
                    if _local(c.tag) == "t":
                        c.text = ""
    else:
        run0 = etree.SubElement(p0, f"{{{NS_HP}}}run", charPrIDRef="0")
        t_el = etree.SubElement(run0, f"{{{NS_HP}}}t")
        t_el.text = value
    return True


def _insert_into_inline_whitespace(p_el: etree._Element, insert_after_sub: str, value: str) -> bool:
    """원본 문단의 어떤 글자도 삭제하지 않고, 지정된 앵커 문자열 뒤의 공백 run/t 자리에 value를 끼워 넣는다."""
    runs = [ch for ch in p_el if _local(ch.tag) == "run"]
    if not runs:
        return False
    if any(_local(c.tag) in ("tbl", "rect", "pic") for r in runs for c in r):
        return False
    for lsa in list(p_el):
        if _local(lsa.tag) == "linesegarray":
            p_el.remove(lsa)

    # 전체 텍스트가 한 개의 run이나 여러 <t>에 나뉘어 있을 수 있으므로 첫 일치 위치에 정확히 삽입
    t_elems = []
    for r in runs:
        for c in r:
            if _local(c.tag) == "t" and c.text:
                t_elems.append(c)
    if not t_elems:
        return False
    full = "".join(t.text for t in t_elems)
    pos = full.find(insert_after_sub)
    if pos < 0:
        return False
    target_idx = pos + len(insert_after_sub)

    cur = 0
    for t in t_elems:
        nxt = cur + len(t.text)
        if target_idx <= nxt:
            offset = target_idx - cur
            before = t.text[:offset]
            after = t.text[offset:]
            # 공백만 일부 대체하되 비공백 문자는 절대 삭제하지 않음
            lead_spaces = len(after) - len(after.lstrip(" "))
            if lead_spaces >= len(value) + 2:
                after = " " + value + " " * max(1, lead_spaces - len(value) - 1) + after.lstrip(" ")
            else:
                after = " " + value + " " + after.lstrip(" ")
            t.text = before + after
            return True
        cur = nxt
    return False


def _fill_inline_paragraphs(
    paragraphs: list[etree._Element],
    profile: dict[str, str],
    location_prefix: str,
    report: dict[str, Any],
    context_hint: str = "",
) -> int:
    """서명 블록(년 월 일, 신청인 : ___ (서명 또는 인)) 문단을 원본 글자 100% 보존하며 채운다."""
    filled = 0
    for pi, p_el in enumerate(paragraphs):
        raw = "".join(p_el.xpath(".//*[local-name()='t']/text()"))
        stripped = re.sub(r"\s+", " ", raw).strip()
        if not stripped:
            continue

        # 1) 신청 날짜 인라인: '년   월   일' 또는 '20   년   월   일'
        if re.fullmatch(r"(?:20\s*)?년\s*월\s*일", stripped):
            prev_ctx = context_hint + " "
            if pi > 0:
                prev_ctx += "".join(paragraphs[pi - 1].xpath(".//*[local-name()='t']/text()"))
            # 출산예정일/사망일자/계약일 등이면 절대 기입 금지
            if any(bad in prev_ctx for bad in ("출산", "사망", "발생", "접수", "지급", "입사")):
                report["skipped_fields"].append({
                    "label": stripped,
                    "location": f"{location_prefix}, p{pi}",
                    "reason": "non_application_date_context",
                })
                continue
            # 선언/서명 문맥 확인 (단독 '년 월 일'인 경우 앞이나 뒤 문단에 신청/동의/확인/서명이 있어야 함)
            next_ctx = "".join(paragraphs[pi + 1].xpath(".//*[local-name()='t']/text()")) if pi + 1 < len(paragraphs) else ""
            if not any(ok in (prev_ctx + " " + next_ctx) for ok in ("신청", "동의", "확인", "신고", "서명")):
                report["skipped_fields"].append({
                    "label": stripped,
                    "location": f"{location_prefix}, p{pi}",
                    "reason": "unverified_date_context",
                })
                continue

            yr, mo, dy = profile["apply_date"].replace("년", " ").replace("월", " ").replace("일", " ").split()
            # '20   년   월   일'처럼 '20'이 고정돼 있으면 두 자리 연도('26')는 검증 허용목록 충돌로 건너뛰고 보고
            m_20 = re.match(r"^(\s*20)(\s+)(년)(\s+)(월)(\s+)(일\s*)$", raw)
            m_plain = re.match(r"^(\s*)(년)(\s+)(월)(\s+)(일\s*)$", raw)
            if m_20:
                yr2 = yr[-2:]
                runs = [ch for ch in p_el if _local(ch.tag) == "run"]
                t_elems = [c for r in runs for c in r if _local(c.tag) == "t" and c.text]
                for lsa in list(p_el):
                    if _local(lsa.tag) == "linesegarray":
                        p_el.remove(lsa)
                inserted_y2 = False
                for t in t_elems:
                    if t.text and "20" in t.text:
                        idx_20 = t.text.index("20") + 2
                        after_20 = t.text[idx_20:]
                        lead_sp = len(after_20) - len(after_20.lstrip(" "))
                        keep_sp = " " if lead_sp >= 3 else after_20[:lead_sp]
                        t.text = t.text[:idx_20] + yr2 + keep_sp + after_20.lstrip(" ")
                        inserted_y2 = True
                        break
                if inserted_y2:
                    ok2 = _insert_into_inline_whitespace(p_el, "년", mo)
                    ok3 = _insert_into_inline_whitespace(p_el, "월", dy)
                    for t in t_elems:
                        if t.text and "년" in t.text and "월" in t.text:
                            t.text = re.sub(rf"년\s*{re.escape(mo)}\s*월", f"년 {mo} 월", t.text)
                        if t.text and dy in t.text and "일" in t.text:
                            t.text = re.sub(rf"월\s*{re.escape(dy)}\s*일", f"월 {dy} 일", t.text)
                            t.text = re.sub(rf"^\s*{re.escape(dy)}\s*일", f" {dy} 일", t.text)
                    if ok2 and ok3:
                        report["filled_fields"].append({
                            "label": stripped,
                            "location": f"{location_prefix}, p{pi}",
                            "field_key": "apply_date",
                            "value": profile["apply_date"],
                            "section": "applicant",
                        })
                        filled += 1
                continue
            elif m_plain:
                # '년' 앞에 연도 4자리 삽입 (원본 '년', '월', '일' 모두 보존)
                runs = [ch for ch in p_el if _local(ch.tag) == "run"]
                t_elems = [c for r in runs for c in r if _local(c.tag) == "t" and c.text]
                for lsa in list(p_el):
                    if _local(lsa.tag) == "linesegarray":
                        p_el.remove(lsa)
                inserted_year = False
                for t in t_elems:
                    if "년" in t.text:
                        idx_y = t.text.index("년")
                        before_y = t.text[:idx_y]
                        # 오른쪽 정렬·좁은 줄폭에서 줄바꿈으로 글자가 겹치지 않도록 과도한 선행 공백 제거
                        trailing_sp = len(before_y) - len(before_y.rstrip(" "))
                        trim = max(0, trailing_sp - 1) if trailing_sp <= 4 else trailing_sp
                        t.text = (before_y[:-trim] if trim else before_y) + yr + t.text[idx_y:]
                        inserted_year = True
                        break
                if inserted_year:
                    ok2 = _insert_into_inline_whitespace(p_el, "년", mo)
                    ok3 = _insert_into_inline_whitespace(p_el, "월", dy)
                    for t in t_elems:
                        if t.text and "년" in t.text and "월" in t.text:
                            t.text = re.sub(rf"년\s*{re.escape(mo)}\s*월", f"년 {mo} 월", t.text)
                        if t.text and dy in t.text and "일" in t.text:
                            t.text = re.sub(rf"월\s*{re.escape(dy)}\s*일", f"월 {dy} 일", t.text)
                            t.text = re.sub(rf"^\s*{re.escape(dy)}\s*일", f" {dy} 일", t.text)
                    if ok2 and ok3:
                        report["filled_fields"].append({
                            "label": stripped,
                            "location": f"{location_prefix}, p{pi}",
                            "field_key": "apply_date",
                            "value": profile["apply_date"],
                            "section": "applicant",
                        })
                        filled += 1
            continue

        # 2) 인라인 신청인 성명 + 서명란 (성명 뒤에 충분한 빈칸이 있고 '(서명'으로 끝나는 경우만)
        # 예: '신청인(대리신청인) :                (서명)' / '신청인(대리 신청인2)) 성명 :        (서명 또는 인)'
        m_sig = re.match(
            r"^(\s*신청인(?:\([^)]*\)|[2)]\))*\s*(?:성\s*명)?\s*:)(\s{6,})(\(서명(?:\s*또는\s*(?:인|날인))?\)\s*)$",
            raw,
        )
        if m_sig:
            prefix, mid_ws, suffix = m_sig.group(1), m_sig.group(2), m_sig.group(3)
            # 단, '성명'이 이미 명시된 경우에만 이름을 넣거나, ':' 바로 뒤 앞부분에 이름 넣고 뒷부분은 서명 여백으로 유지
            if "성명" in prefix or "성 명" in prefix or len(mid_ws) >= 12:
                # 앞부분에 이름만 넣고 뒤 공백과 (서명...)은 그대로 유지
                if _insert_into_inline_whitespace(p_el, ":", profile["applicant.name"]):
                    report["filled_fields"].append({
                        "label": prefix.strip(),
                        "location": f"{location_prefix}, p{pi}",
                        "field_key": "applicant.name",
                        "value": profile["applicant.name"],
                        "section": "applicant",
                    })
                    report["sensitive_blank_fields"].append({
                        "label": suffix.strip(),
                        "location": f"{location_prefix}, p{pi}",
                        "reason": "signature_kept_blank",
                    })
                    filled += 1
            continue

        if "신청인과의 관계" in stripped and "대리" in stripped:
            report["skipped_fields"].append({
                "label": stripped[:40],
                "location": f"{location_prefix}, p{pi}",
                "reason": "applicant_self_submission_no_proxy_needed",
            })
            continue

        if "법정대리인" in stripped or "미성년자" in stripped:
            report["skipped_fields"].append({
                "label": stripped[:40],
                "location": f"{location_prefix}, p{pi}",
                "reason": "excluded_minor_or_legal_guardian",
            })

    return filled


@dataclass
class CellInfo:
    tc: etree._Element
    row: int
    col: int
    row_span: int
    col_span: int
    text: str


def _parse_table(tbl: etree._Element) -> tuple[list[CellInfo], dict[tuple[int, int], CellInfo], int, int]:
    cells: list[CellInfo] = []
    grid: dict[tuple[int, int], CellInfo] = {}
    max_r, max_c = 0, 0
    for tr in tbl.xpath("./*[local-name()='tr']"):
        for tc in tr.xpath("./*[local-name()='tc']"):
            addr = tc.xpath("./*[local-name()='cellAddr']")[0]
            span = tc.xpath("./*[local-name()='cellSpan']")[0]
            r = int(addr.get("rowAddr"))
            c = int(addr.get("colAddr"))
            rs = int(span.get("rowSpan", "1"))
            cs = int(span.get("colSpan", "1"))
            ci = CellInfo(tc=tc, row=r, col=c, row_span=rs, col_span=cs, text=_tc_text(tc))
            cells.append(ci)
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    grid[(rr, cc)] = ci
            max_r = max(max_r, r + rs)
            max_c = max(max_c, c + cs)
    return cells, grid, max_r, max_c


def _row_section_context(row_cells: list[CellInfo], grid: dict[tuple[int, int], CellInfo], r: int, current_section: str) -> str:
    left_texts = []
    for c in range(0, 3):
        ci = grid.get((r, c))
        if ci and ci.text:
            left_texts.append(ci.text)
    if left_texts:
        sec = fm.detect_section_context(" ".join(left_texts), current="keep")
        if sec != "keep":
            return sec
    if len(row_cells) <= 2:
        row_txt = " ".join(ci.text for ci in row_cells if ci.text)
        sec = fm.detect_section_context(row_txt, current="keep")
        if sec != "keep":
            return sec
    return current_section


def fill_hwpx_bytes(
    data: bytes,
    profile: dict[str, str] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    prof = profile or fm.default_profile()
    report: dict[str, Any] = {
        "format": "hwpx",
        "status": "ok",
        "found_labels": [],
        "filled_fields": [],
        "skipped_fields": [],
        "sensitive_blank_fields": [],
        "filled_count": 0,
    }

    try:
        zin_ctx = zipfile.ZipFile(io.BytesIO(data), "r")
    except zipfile.BadZipFile:
        report["status"] = "unsupported"
        report["error"] = "invalid_hwpx_zip"
        return data, report

    with zin_ctx as zin:
        infos = zin.infolist()
        total_uncompressed = sum(max(0, info.file_size) for info in infos)
        if len(infos) > MAX_HWPX_ENTRIES or total_uncompressed > MAX_HWPX_UNCOMPRESSED_BYTES:
            report["status"] = "unsupported"
            report["error"] = "hwpx_zip_limit_exceeded"
            return data, report

        names = [info.filename for info in infos]
        sec_names = sorted(n for n in names if n.startswith("Contents/section") and n.endswith(".xml"))
        if not sec_names:
            report["status"] = "failed"
            report["error"] = "no_section_xml"
            return data, report

        updated_sections: dict[str, bytes] = {}
        filled_tcs: set[int] = set()

        for sec_name in sec_names:
            root = etree.fromstring(zin.read(sec_name), parser=SAFE_XML_PARSER)
            tables = root.xpath(".//*[local-name()='tbl']")

            _fill_inline_paragraphs(root.xpath("./*[local-name()='p']"), prof, f"{sec_name} top", report)

            for ti, tbl in enumerate(tables):
                cells, grid, max_r, max_c = _parse_table(tbl)
                row_map: dict[int, list[CellInfo]] = {}
                for ci in cells:
                    row_map.setdefault(ci.row, []).append(ci)

                tbl_section = "applicant"
                for r in range(max_r):
                    row_cells = sorted(row_map.get(r, []), key=lambda x: x.col)
                    tbl_section = _row_section_context(row_cells, grid, r, tbl_section)

                    # 같은 행/인접 구역에 '휴대전화'가 별도로 존재하는지 확인 (일반 '전화번호' 중복 기입 방지)
                    has_mobile_in_band = any(
                        "휴대전화" in c2.text or "휴대폰" in c2.text
                        for rr2 in range(max(0, r - 2), min(max_r, r + 3))
                        for c2 in row_map.get(rr2, [])
                    )

                    for ci in row_cells:
                        txt = ci.text
                        if not txt:
                            continue

                        # 단일 셀 내 인라인 서명/날짜 블록 처리
                        if "년" in txt and "월" in txt and "일" in txt and len(txt) >= 10:
                            # 왼쪽 셀(같은 행)의 문맥 확인 (예: 출산(예정)일!)
                            left_ctx = " ".join(
                                c2.text for c2 in row_cells if c2.col < ci.col and c2.text
                            )
                            cell_ps = ci.tc.xpath("./*[local-name()='subList']/*[local-name()='p']")
                            _fill_inline_paragraphs(
                                cell_ps,
                                prof,
                                f"{sec_name} T{ti}({ci.row},{ci.col})",
                                report,
                                context_hint=left_ctx,
                            )

                        if len(txt) > 60:
                            continue

                        decision = fm.match_label(txt, section=tbl_section, profile=prof)
                        if decision.action == "skip" and decision.reason in ("empty_or_too_long_label", "unmatched_label"):
                            continue

                        # 휴대전화 칸이 따로 있을 때 '전화번호' 단독 라벨은 집전화일 수 있으므로 건너뜀
                        if decision.field_key in ("applicant.phone", "proxy.phone") and fm.normalize_label(txt) == "전화번호" and has_mobile_in_band:
                            report["skipped_fields"].append({
                                "label": txt.replace("\n", " "),
                                "location": f"{sec_name} T{ti}({ci.row},{ci.col})",
                                "reason": "separate_mobile_field_exists",
                            })
                            continue

                        report["found_labels"].append({
                            "label": txt.replace("\n", " "),
                            "location": f"{sec_name} T{ti}({ci.row},{ci.col})",
                            "section": decision.section,
                            "decision": decision.action,
                        })

                        if decision.action == "sensitive_blank":
                            report["sensitive_blank_fields"].append({
                                "label": txt.replace("\n", " "),
                                "location": f"{sec_name} T{ti}({ci.row},{ci.col})",
                                "reason": decision.reason,
                            })
                            continue

                        if decision.action == "skip":
                            report["skipped_fields"].append({
                                "label": txt.replace("\n", " "),
                                "location": f"{sec_name} T{ti}({ci.row},{ci.col})",
                                "reason": decision.reason,
                            })
                            continue

                        # 1) 오른쪽 인접 빈 셀 우선
                        right_ci = grid.get((ci.row, ci.col + ci.col_span))
                        target_ci = None
                        if right_ci is not None and fm.is_cell_empty(right_ci.text):
                            target_ci = right_ci
                        # 2) 오른쪽이 다른 헤더 라벨이면, 바로 아래 셀 확인 (표 헤더행 패턴)
                        elif ci.row + ci.row_span < max_r:
                            below_ci = grid.get((ci.row + ci.row_span, ci.col))
                            if (
                                below_ci is not None
                                and below_ci.col == ci.col
                                and below_ci.col_span == ci.col_span
                                and fm.is_cell_empty(below_ci.text)
                            ):
                                target_ci = below_ci

                        if target_ci is None:
                            reason = "non_empty_adjacent_cell" if right_ci is not None else "no_adjacent_target_cell"
                            report["skipped_fields"].append({
                                "label": txt.replace("\n", " "),
                                "location": f"{sec_name} T{ti}({ci.row},{ci.col})",
                                "reason": reason,
                            })
                            continue

                        if id(target_ci.tc) in filled_tcs:
                            continue

                        if _set_tc_text(target_ci.tc, decision.value or ""):
                            filled_tcs.add(id(target_ci.tc))
                            target_ci.text = decision.value or ""
                            report["filled_fields"].append({
                                "label": txt.replace("\n", " "),
                                "location": f"{sec_name} T{ti}({target_ci.row},{target_ci.col})",
                                "field_key": decision.field_key,
                                "value": decision.value,
                                "section": decision.section,
                            })

            updated_sections[sec_name] = etree.tostring(
                root, xml_declaration=True, encoding="UTF-8", standalone=True
            )

        report["filled_count"] = len(report["filled_fields"])
        if report["filled_count"] == 0:
            report["status"] = "no_fillable_fields"
            return data, report

        out_buf = io.BytesIO()
        with zipfile.ZipFile(out_buf, "w") as zout:
            for item in zin.infolist():
                payload = updated_sections.get(item.filename)
                if payload is None:
                    payload = zin.read(item.filename)
                compress = zipfile.ZIP_STORED if item.filename == "mimetype" else zipfile.ZIP_DEFLATED
                zout.writestr(item, payload, compress_type=compress)
        return out_buf.getvalue(), report
