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


def _parse_char_pr_heights(zin: zipfile.ZipFile) -> tuple[dict[str, int], str | None]:
    """Contents/header.xml에서 charPr id별 글자 크기(1/100 pt)와 문서 기본 글자 모양 id를 뽑는다."""
    heights: dict[str, int] = {}
    if "Contents/header.xml" not in zin.namelist():
        return heights, None
    try:
        info = zin.getinfo("Contents/header.xml")
        if info.file_size > MAX_HWPX_UNCOMPRESSED_BYTES:
            return heights, None
        hdr = etree.fromstring(zin.read("Contents/header.xml"), parser=SAFE_XML_PARSER)
    except (etree.XMLSyntaxError, KeyError, zipfile.BadZipFile, ValueError):
        return heights, None

    default_cid: str | None = None
    fallback_cid: str | None = None
    for cp in hdr.xpath(".//*[local-name()='charPr']"):
        cid = cp.get("id")
        h_str = cp.get("height")
        if not cid or not h_str:
            continue
        try:
            h_val = int(h_str)
        except ValueError:
            continue
        heights[cid] = h_val
        if default_cid is None and 800 <= h_val <= 1100:
            default_cid = cid
        if fallback_cid is None and h_val >= 700:
            fallback_cid = cid
    return heights, default_cid or fallback_cid


def _tc_char_pr_id(tc: etree._Element) -> str | None:
    sub = next((ch for ch in tc if _local(ch.tag) == "subList"), None)
    if sub is None:
        return None
    p0 = next((ch for ch in sub if _local(ch.tag) == "p"), None)
    if p0 is None:
        return None
    run0 = next((ch for ch in p0 if _local(ch.tag) == "run"), None)
    return run0.get("charPrIDRef") if run0 is not None else None


def _set_tc_text(tc: etree._Element, value: str, *, override_char_pr_id: str | None = None) -> bool:
    """빈 셀 tc의 첫 번째 문단 첫 번째 run에 value를 넣는다. 필요 시 글자 모양 id를 교체한다."""
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
        if override_char_pr_id is not None:
            run0.set("charPrIDRef", override_char_pr_id)
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
        run0 = etree.SubElement(p0, f"{{{NS_HP}}}run", charPrIDRef=override_char_pr_id or "0")
        t_el = etree.SubElement(run0, f"{{{NS_HP}}}t")
        t_el.text = value
    return True


_SLOT_CHARS = frozenset((" ", "_", "\u00a0", "\u3000"))
_NON_APP_DATE_RE = re.compile(
    r"(?:출산\s*(?:예정)?|사망|발생|접수|지급|입사)\s*일|입금\s*(?:확인|됨)|보험료\s*입금"
)


def _collect_t_elems(p_el: etree._Element) -> list[etree._Element]:
    runs = [ch for ch in p_el if _local(ch.tag) == "run"]
    if not runs or any(_local(c.tag) in ("tbl", "rect", "pic") for r in runs for c in r):
        return []
    return [c for r in runs for c in r if _local(c.tag) == "t" and c.text]


def _slot_width_after(t_elems: list[etree._Element], full_pos: int) -> tuple[int, list[tuple[int, int, int]]]:
    """full_pos 뒤부터 이어지는 공백/밑줄 자리표시자 목록과 총 표시 폭을 여러 <hp:t>에 걸쳐 계산한다."""
    slots: list[tuple[int, int, int]] = []
    cur = 0
    started = False
    for ti, t in enumerate(t_elems):
        txt = t.text or ""
        for ci, ch in enumerate(txt):
            gpos = cur + ci
            if gpos < full_pos:
                continue
            started = True
            if ch in _SLOT_CHARS:
                w = 2 if ch == "\u3000" else 1
                slots.append((ti, ci, w))
            elif started:
                return sum(s[2] for s in slots), slots
        cur += len(txt)
    return sum(s[2] for s in slots), slots


def _replace_slot_after(
    p_el: etree._Element,
    anchor: str,
    value: str,
    *,
    leading_space: bool = True,
    min_keep: int = 1,
) -> tuple[bool, str]:
    """원본 글자를 지우지 않고 여러 run에 걸친 뒤따르는 공백 폭을 줄여 value를 삽입한다."""
    t_elems = _collect_t_elems(p_el)
    if not t_elems:
        return False, "no_text"
    full = "".join(t.text or "" for t in t_elems)
    pos = full.find(anchor)
    if pos < 0:
        return False, "no_anchor"
    target_idx = pos + len(anchor)

    avail_w, slots = _slot_width_after(t_elems, target_idx)
    ins = (" " + value) if leading_space else value
    ins_w = fm.display_width_spaces(ins)
    if avail_w < ins_w + min_keep or not slots:
        return False, "no_room"

    for lsa in list(p_el):
        if _local(lsa.tag) == "linesegarray":
            p_el.remove(lsa)

    # ins_w만큼 뒤따르는 공백을 줄이되 최소 min_keep 폭은 남긴다
    to_drop: list[tuple[int, int]] = []
    dropped_w = 0
    for ti, ci, w in slots:
        if dropped_w >= ins_w or (avail_w - (dropped_w + w)) < min_keep:
            break
        to_drop.append((ti, ci))
        dropped_w += w

    first_ti, first_ci = slots[0][0], slots[0][1]
    drop_by_t: dict[int, set[int]] = {}
    for ti, ci in to_drop:
        drop_by_t.setdefault(ti, set()).add(ci)

    for ti, t in enumerate(t_elems):
        txt = t.text or ""
        drops = drop_by_t.get(ti, set())
        if ti == first_ti:
            parts: list[str] = []
            inserted = False
            for ci, ch in enumerate(txt):
                if ci == first_ci and not inserted:
                    parts.append(ins)
                    inserted = True
                if ci not in drops:
                    parts.append(ch)
            t.text = "".join(parts)
        elif drops:
            t.text = "".join(ch for ci, ch in enumerate(txt) if ci not in drops)
    return True, "ok"


def _fill_hwpx_date_line(p_el: etree._Element, yr: str, mo: str, dy: str, *, is_20: bool) -> tuple[bool, str]:
    t_elems = _collect_t_elems(p_el)
    if not t_elems:
        return False, "no_text"
    full = "".join(t.text or "" for t in t_elems)
    y_pos = full.find("년")
    m_pos = full.find("월", max(0, y_pos + 1))
    if y_pos < 0 or m_pos < 0:
        return False, "no_anchor"

    yr2 = yr[-2:]
    avail_ym, _ = _slot_width_after(t_elems, y_pos + 1)
    avail_md, _ = _slot_width_after(t_elems, m_pos + 1)
    if avail_ym < len(mo) + 1 or avail_md < len(dy) + 1:
        return False, "no_room"

    p20 = full.find("20") if is_20 else -1
    if is_20:
        if p20 < 0 or p20 > y_pos:
            return False, "no_anchor"
        avail_20, _ = _slot_width_after(t_elems, p20 + 2)
        if avail_20 < len(yr2) + 1:
            return False, "no_room"

    # 오른쪽에서 왼쪽으로 채워 앞쪽 인덱스를 흔들지 않는다
    ok_md, reason_md = _replace_slot_after(p_el, "월", dy, leading_space=(avail_md >= len(dy) + 2), min_keep=1)
    if not ok_md:
        return False, reason_md
    ok_ym, reason_ym = _replace_slot_after(p_el, "년", mo, leading_space=(avail_ym >= len(mo) + 2), min_keep=1)
    if not ok_ym:
        return False, reason_ym

    if is_20:
        return _replace_slot_after(p_el, "20", yr2, leading_space=False, min_keep=1)

    # '년' 앞의 공백에서 연도 자릿수(4)만큼 덜어내고 연도 삽입
    t_elems2 = _collect_t_elems(p_el)
    full2 = "".join(t.text or "" for t in t_elems2)
    y_pos2 = full2.find("년")
    if y_pos2 < 0:
        return False, "no_anchor"
    cur = 0
    # y_pos2 직전까지 역방향으로 공백 폭 <= len(yr) 만큼 제거
    lead_slots: list[tuple[int, int, int]] = []
    for ti, t in enumerate(t_elems2):
        txt = t.text or ""
        for ci, ch in enumerate(txt):
            if cur + ci >= y_pos2:
                break
            lead_slots.append((ti, ci, 2 if ch == "\u3000" else (1 if ch in _SLOT_CHARS else 0)))
        cur += len(txt)

    to_drop_y: list[tuple[int, int]] = []
    trimmed_w = 0
    for ti, ci, w in reversed(lead_slots):
        if w == 0 or trimmed_w >= len(yr):
            break
        to_drop_y.append((ti, ci))
        trimmed_w += w

    drop_map: dict[int, set[int]] = {}
    for ti, ci in to_drop_y:
        drop_map.setdefault(ti, set()).add(ci)

    cur = 0
    for ti, t in enumerate(t_elems2):
        txt = t.text or ""
        drops = drop_map.get(ti, set())
        parts: list[str] = []
        for ci, ch in enumerate(txt):
            if cur + ci == y_pos2:
                parts.append(yr)
            if ci not in drops:
                parts.append(ch)
        t.text = "".join(parts)
        cur += len(txt)
    return True, "ok"


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
            prev_ctx = context_hint + " " + " ".join(
                "".join(paragraphs[pj].xpath(".//*[local-name()='t']/text()")).strip()
                for pj in range(max(0, pi - 3), pi)
            )
            # 출산예정일/사망일자/계약일 등이면 절대 기입 금지 (단, '출산서비스'·'지급계좌' 같은 서비스명/안내어는 제외)
            if _NON_APP_DATE_RE.search(prev_ctx):
                report["skipped_fields"].append({
                    "label": stripped,
                    "location": f"{location_prefix}, p{pi}",
                    "reason": "non_application_date_context",
                })
                continue
            # 선언/서명 문맥 확인 (인접 3문단 범위 내 확인자/담당자 차단)
            next_ctx = " ".join(
                "".join(paragraphs[pj].xpath(".//*[local-name()='t']/text()")).strip()
                for pj in range(pi + 1, min(len(paragraphs), pi + 4))
            )
            if fm.OFFICIAL_INLINE_CTX_RE.search(prev_ctx + " " + next_ctx):
                report["skipped_fields"].append({
                    "label": stripped,
                    "location": f"{location_prefix}, p{pi}",
                    "reason": "excluded_section",
                })
                continue
            if not any(ok in (prev_ctx + " " + next_ctx) for ok in ("신청", "동의", "확인", "신고", "서명")):
                report["skipped_fields"].append({
                    "label": stripped,
                    "location": f"{location_prefix}, p{pi}",
                    "reason": "unverified_date_context",
                })
                continue

            date_parts = re.findall(r"\d+", profile.get("apply_date") or "")
            if len(date_parts) < 3:
                continue
            yr, mo, dy = date_parts[:3]
            apply_date_val = (profile.get("apply_date") or f"{yr}년 {mo}월 {dy}일").strip()
            m_20 = re.match(r"^(\s*20)([ \t\u00a0\u3000_]+)(년)([ \t\u00a0\u3000_]+)(월)([ \t\u00a0\u3000_]+)(일\s*)$", raw)
            m_plain = re.match(r"^([ \t\u00a0\u3000_]*)(년)([ \t\u00a0\u3000_]+)(월)([ \t\u00a0\u3000_]+)(일\s*)$", raw)
            if m_20 or m_plain:
                ok_dt, reason_dt = _fill_hwpx_date_line(p_el, yr, mo, dy, is_20=bool(m_20))
                if ok_dt:
                    report["filled_fields"].append({
                        "label": stripped,
                        "location": f"{location_prefix}, p{pi}",
                        "field_key": "apply_date",
                        "value": apply_date_val,
                        "section": "applicant",
                    })
                    filled += 1
                else:
                    report["skipped_fields"].append({
                        "label": stripped,
                        "location": f"{location_prefix}, p{pi}",
                        "reason": reason_dt,
                    })
            continue

        # 2) 인라인 신청인 성명 + 서명란 (성명 뒤에 충분한 빈칸이 있고 '(서명'으로 끝나는 경우만)
        m_sig = re.match(
            r"^(\s*신청인(?:\([^)]*\)|[2)]\))*\s*(?:성\s*명)?\s*:)([ \u00a0\u3000_]{2,})(\(서명(?:\s*또는\s*(?:인|날인))?\)\s*)$",
            raw,
        )
        if m_sig:
            prefix, mid_ws, suffix = m_sig.group(1), m_sig.group(2), m_sig.group(3)
            prev_ctx = context_hint + " " + ("".join(paragraphs[pi - 1].xpath(".//*[local-name()='t']/text()")) if pi > 0 else "")
            if fm.OFFICIAL_INLINE_CTX_RE.search(prev_ctx + " " + prefix):
                report["skipped_fields"].append({
                    "label": prefix.strip(),
                    "location": f"{location_prefix}, p{pi}",
                    "reason": "excluded_section",
                })
                continue
            app_name = (profile.get("applicant.name") or "").strip()
            if not app_name:
                continue
            if fm.slot_width_spaces(mid_ws) < 1 + fm.display_width_spaces(app_name) + 1:
                report["skipped_fields"].append({
                    "label": prefix.strip(),
                    "location": f"{location_prefix}, p{pi}",
                    "reason": "no_room",
                })
                continue
            if "성명" in prefix or "성 명" in prefix or fm.slot_width_spaces(mid_ws) >= 12:
                ok_nm, reason_nm = _replace_slot_after(p_el, ":", app_name, leading_space=True, min_keep=1)
                if ok_nm:
                    report["filled_fields"].append({
                        "label": prefix.strip(),
                        "location": f"{location_prefix}, p{pi}",
                        "field_key": "applicant.name",
                        "value": app_name,
                        "section": "applicant",
                    })
                    report["sensitive_blank_fields"].append({
                        "label": suffix.strip(),
                        "location": f"{location_prefix}, p{pi}",
                        "reason": "signature_kept_blank",
                    })
                    filled += 1
                else:
                    report["skipped_fields"].append({
                        "label": prefix.strip(),
                        "location": f"{location_prefix}, p{pi}",
                        "reason": reason_nm,
                    })
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
    prof = fm.default_profile() if profile is None else profile
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
        report["status"] = "error"
        report["error"] = "hwpx_bad_zip"
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
            report["status"] = "error"
            report["error"] = "hwpx_no_sections"
            return data, report

        char_pr_heights, doc_default_cid = _parse_char_pr_heights(zin)
        updated_sections: dict[str, bytes] = {}
        filled_tcs: set[int] = set()

        for sec_name in sec_names:
            try:
                root = etree.fromstring(zin.read(sec_name), parser=SAFE_XML_PARSER)
            except (etree.XMLSyntaxError, ValueError, KeyError, zipfile.BadZipFile) as exc:
                report["status"] = "error"
                report["error"] = f"hwpx_xml_error:{type(exc).__name__}"
                report["filled_fields"] = []
                report["filled_count"] = 0
                return data, report
            tables = root.xpath(".//*[local-name()='tbl']")

            _fill_inline_paragraphs(root.xpath("./*[local-name()='p']"), prof, f"{sec_name} top", report)

            # 각 표 직전 본문 문단 제목 추적 (명부 등 제외 구역 표 판별)
            body_ps = root.xpath("./*[local-name()='p']")
            table_prev_text: dict[int, str] = {}
            recent_texts: list[str] = []
            for bp in body_ps:
                for sub_tbl in bp.xpath(".//*[local-name()='tbl']"):
                    table_prev_text[id(sub_tbl)] = " ".join(recent_texts[-2:])
                bt = "".join(t for t in bp.xpath("./*[local-name()='run']/*[local-name()='t']/text()")).strip()
                if bt:
                    recent_texts.append(bt)

            for ti, tbl in enumerate(tables):
                cells, grid, max_r, max_c = _parse_table(tbl)
                row_map: dict[int, list[CellInfo]] = {}
                for ci in cells:
                    row_map.setdefault(ci.row, []).append(ci)

                prev_title = table_prev_text.get(id(tbl), "")
                tbl_section = "excluded" if fm.detect_section_context(prev_title, current="keep") == "excluded" else "applicant"
                for r in range(max_r):
                    row_cells = sorted(row_map.get(r, []), key=lambda x: x.col)
                    tbl_section = _row_section_context(row_cells, grid, r, tbl_section)
                    # 넓은 제목 행 판별 (같은 행 모든 셀을 합쳐 판단)
                    if any(rc.col_span >= max(3, max_c // 2) for rc in row_cells):
                        row_joined = " ".join(rc.text for rc in row_cells if rc.text)
                        if row_joined and len(row_joined) <= 120:
                            wsec = fm.detect_section_context(row_joined, current="keep")
                            if wsec != "keep":
                                tbl_section = wsec

                    row_head_c0 = grid.get((r, 0))
                    row_head_norm = fm.normalize_label(row_head_c0.text) if row_head_c0 and row_head_c0.text else ""
                    row_only_excluded = bool(
                        fm.EXCLUDED_ROW_HEAD_RE.match(row_head_norm)
                        or re.fullmatch(r"성명(?:여성|남성)", row_head_norm)
                    )

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
                        cur_sec = "excluded" if row_only_excluded else tbl_section

                        # 단일 셀 내 인라인 서명/날짜 블록 처리
                        if "년" in txt and "월" in txt and "일" in txt and len(txt) >= 10:
                            left_ctx = " ".join(
                                c2.text for c2 in row_cells if c2.col < ci.col and c2.text
                            )
                            if cur_sec == "excluded" or fm.OFFICIAL_INLINE_CTX_RE.search(left_ctx + " " + txt):
                                report["skipped_fields"].append({
                                    "label": txt.replace("\n", " ")[:40],
                                    "location": f"{sec_name} T{ti}({ci.row},{ci.col})",
                                    "reason": "excluded_section",
                                })
                            else:
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

                        decision = fm.match_label(txt, section=cur_sec, profile=prof)
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
                                rh_below = grid.get((below_ci.row, 0))
                                rh_norm = fm.normalize_label(rh_below.text) if rh_below and rh_below.text else ""
                                if fm.EXCLUDED_ROW_HEAD_RE.match(rh_norm) or (
                                    rh_below and fm.detect_section_context(rh_below.text, current="keep") == "excluded"
                                ):
                                    report["skipped_fields"].append({
                                        "label": txt.replace("\n", " "),
                                        "location": f"{sec_name} T{ti}({below_ci.row},{below_ci.col})",
                                        "reason": "excluded_section",
                                    })
                                    continue
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

                        override_cid: str | None = None
                        target_cid = _tc_char_pr_id(target_ci.tc)
                        target_h = char_pr_heights.get(target_cid) if target_cid is not None else None
                        if target_h is not None and target_h < 700:
                            lbl_cid = _tc_char_pr_id(ci.tc)
                            if lbl_cid is not None and char_pr_heights.get(lbl_cid, 0) >= 700:
                                override_cid = lbl_cid
                            elif doc_default_cid is not None:
                                override_cid = doc_default_cid
                            else:
                                report["skipped_fields"].append({
                                    "label": txt.replace("\n", " "),
                                    "location": f"{sec_name} T{ti}({target_ci.row},{target_ci.col})",
                                    "reason": "tiny_font",
                                })
                                continue

                        if _set_tc_text(target_ci.tc, decision.value or "", override_char_pr_id=override_cid):
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
