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

import copy
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


def _parse_header_styles(
    zin: zipfile.ZipFile,
) -> tuple[etree._Element | None, dict[str, dict[str, int]], str | None, dict[str, dict[str, Any]]]:
    """Contents/header.xml에서 charPr 스타일, 문서 기본 id, paraPr 정렬·줄간격 정보를 추출한다."""
    char_info: dict[str, dict[str, int]] = {}
    para_info: dict[str, dict[str, Any]] = {}
    if "Contents/header.xml" not in zin.namelist():
        return None, char_info, None, para_info
    try:
        info = zin.getinfo("Contents/header.xml")
        if info.file_size > MAX_HWPX_UNCOMPRESSED_BYTES:
            return None, char_info, None, para_info
        hdr = etree.fromstring(zin.read("Contents/header.xml"), parser=SAFE_XML_PARSER)
    except (etree.XMLSyntaxError, KeyError, zipfile.BadZipFile, ValueError):
        return None, char_info, None, para_info

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
        sp_el = next((c for c in cp if _local(c.tag) == "spacing"), None)
        rt_el = next((c for c in cp if _local(c.tag) == "ratio"), None)
        try:
            sp_val = int(sp_el.get("hangul", "0")) if sp_el is not None else 0
        except ValueError:
            sp_val = 0
        try:
            rt_val = int(rt_el.get("hangul", "100")) if rt_el is not None else 100
        except ValueError:
            rt_val = 100
        tc_hex = (cp.get("textColor") or "").strip().lstrip("#")
        is_white_col = 0
        if len(tc_hex) == 6:
            try:
                r_c, g_c, b_c = int(tc_hex[0:2], 16), int(tc_hex[2:4], 16), int(tc_hex[4:6], 16)
                if r_c >= 220 and g_c >= 220 and b_c >= 220:
                    is_white_col = 1
            except ValueError:
                pass
        char_info[cid] = {"height": h_val, "spacing": sp_val, "ratio": rt_val, "is_white": is_white_col}
        if default_cid is None and 800 <= h_val <= 1100:
            default_cid = cid
        if fallback_cid is None and h_val >= 700:
            fallback_cid = cid

    for pp in hdr.xpath(".//*[local-name()='paraPr']"):
        pid = pp.get("id")
        if not pid:
            continue
        al_el = next((c for c in pp if _local(c.tag) == "align"), None)
        horiz = (al_el.get("horizontal", "LEFT") if al_el is not None else "LEFT").upper()
        align_name = "Center" if horiz == "CENTER" else ("Right" if horiz == "RIGHT" else "Left")
        ls_pct = 160
        for ls_el in pp.xpath(".//*[local-name()='lineSpacing']"):
            if (ls_el.get("type") or "").upper() == "PERCENT":
                try:
                    ls_pct = max(100, min(300, int(ls_el.get("value", "160"))))
                except ValueError:
                    pass
                break
        para_info[pid] = {"align": align_name, "line_space_pct": ls_pct}

    return hdr, char_info, (default_cid or fallback_cid), para_info


def _ensure_char_pr_variant(
    hdr_root: etree._Element | None,
    orig_cid: str | None,
    new_h: int,
    new_sp: int,
    char_info: dict[str, dict[str, int]],
    cache: dict[tuple[str, int, int, bool], str],
    doc_default_cid: str | None,
    force_black: bool = False,
) -> tuple[str | None, bool]:
    """orig_cid 기반 파생 charPr(높이·자간·검정 글자색 변경)을 header.xml에 복제·등록한다."""
    if hdr_root is None:
        return None, False
    base_cid = orig_cid if (orig_cid and orig_cid in char_info) else doc_default_cid
    if not base_cid or base_cid not in char_info:
        return orig_cid, False
    base_st = char_info[base_cid]
    target_h = max(800, int(new_h))
    target_sp = max(-10, int(new_sp))
    if not force_black and target_h == base_st["height"] and target_sp == base_st["spacing"]:
        return (orig_cid if orig_cid == base_cid else base_cid), False

    ck = (base_cid, target_h, target_sp, force_black)
    if ck in cache:
        return cache[ck], False

    cp_container = next((el for el in hdr_root.iter() if _local(el.tag) == "charProperties"), None)
    if cp_container is None:
        return orig_cid, False
    orig_el = next((el for el in cp_container if _local(el.tag) == "charPr" and el.get("id") == base_cid), None)
    if orig_el is None:
        return orig_cid, False

    max_id = max((int(k) for k in char_info if k.isdigit()), default=0)
    new_cid = str(max_id + 1)
    cloned = copy.deepcopy(orig_el)
    cloned.set("id", new_cid)
    cloned.set("height", str(target_h))
    if force_black:
        cloned.set("textColor", "#000000")
    for ch in cloned:
        if _local(ch.tag) == "spacing":
            for attr in ("hangul", "latin", "hanja", "japanese", "other", "symbol", "user"):
                if attr in ch.attrib or attr in ("hangul", "latin"):
                    ch.set(attr, str(target_sp))
    cp_container.append(cloned)
    cnt_now = sum(1 for el in cp_container if _local(el.tag) == "charPr")
    cp_container.set("itemCnt", str(cnt_now))
    char_info[new_cid] = {
        "height": target_h,
        "spacing": target_sp,
        "ratio": base_st["ratio"],
        "is_white": 0 if force_black else int(base_st.get("is_white", 0)),
    }
    cache[ck] = new_cid
    return new_cid, True


def _parse_char_pr_heights(zin: zipfile.ZipFile) -> tuple[dict[str, int], str | None]:
    _, char_info, def_cid, _ = _parse_header_styles(zin)
    return {cid: st["height"] for cid, st in char_info.items()}, def_cid


def _tc_para_pr_id(tc: etree._Element) -> str | None:
    sub = next((ch for ch in tc if _local(ch.tag) == "subList"), None)
    if sub is None:
        return None
    p0 = next((ch for ch in sub if _local(ch.tag) == "p"), None)
    return p0.get("paraPrIDRef") if p0 is not None else None


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


_INLINE_COLON_RE = re.compile(
    r"^(.*?)"
    r"((?:주\s*소|성\s*명|생\s*년\s*월\s*일|연\s*락\s*처|휴\s*대\s*(?:전\s*화|폰)(?:\s*번\s*호)?)\s*:)"
    r"([ \u00a0\u3000_]*)"
    r"((?:㉿|㊞|\U000f012b|\(서명(?:\s*또는\s*(?:인|날인))?\)|\(인\))?\s*)$"
)
_SUBLABEL_PHONE_RE = re.compile(
    r"^[\s·•▪\-\[\(]*(?:휴대폰|휴대전화|핸드폰|H\s*[.․·ㆍ]?\s*P)(?:\s*번호)?\s*(?::|：)?\s*$",
    re.IGNORECASE,
)


def _replace_slot_at(
    p_el: etree._Element,
    target_idx: int,
    value: str,
    *,
    leading_space: bool = True,
    min_keep: int = 1,
    allow_no_trim: bool = False,
) -> tuple[bool, str]:
    """여러 run에 걸친 target_idx 위치에서 뒤따르는 공백 폭을 줄여 value를 삽입한다."""
    t_elems = _collect_t_elems(p_el)
    if not t_elems:
        return False, "no_text"

    avail_w, slots = _slot_width_after(t_elems, target_idx)
    ins = (" " + value) if leading_space else value
    ins_w = fm.display_width_spaces(ins)
    if not slots:
        if not allow_no_trim:
            return False, "no_room"
        for lsa in list(p_el):
            if _local(lsa.tag) == "linesegarray":
                p_el.remove(lsa)
        cur = 0
        for t in t_elems:
            txt = t.text or ""
            end = cur + len(txt)
            if cur <= target_idx <= end:
                rel = target_idx - cur
                t.text = txt[:rel] + ins + txt[rel:]
                return True, "ok"
            cur = end
        return False, "no_room"

    if avail_w < ins_w + min_keep and not allow_no_trim:
        return False, "no_room"

    for lsa in list(p_el):
        if _local(lsa.tag) == "linesegarray":
            p_el.remove(lsa)

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


def _replace_slot_after(
    p_el: etree._Element,
    anchor: str,
    value: str,
    *,
    leading_space: bool = True,
    min_keep: int = 1,
    allow_no_trim: bool = False,
) -> tuple[bool, str]:
    """anchor 뒤 공백 폭을 줄여 value를 삽입한다."""
    t_elems = _collect_t_elems(p_el)
    if not t_elems:
        return False, "no_text"
    full = "".join(t.text or "" for t in t_elems)
    pos = full.find(anchor)
    if pos < 0:
        return False, "no_anchor"
    return _replace_slot_at(
        p_el, pos + len(anchor), value,
        leading_space=leading_space, min_keep=min_keep, allow_no_trim=allow_no_trim,
    )


def _fill_hwpx_date_line(
    p_el: etree._Element,
    yr: str,
    mo: str,
    dy: str,
    *,
    is_20: bool,
    allow_tight: bool = False,
) -> tuple[bool, str]:
    t_elems = _collect_t_elems(p_el)
    if not t_elems:
        return False, "no_text"
    full = "".join(t.text or "" for t in t_elems)
    y_pos = full.find("년")
    m_char = "월"
    m_pos = full.find("월", max(0, y_pos + 1)) if y_pos >= 0 else -1
    if m_pos < 0 and y_pos >= 0:
        m_char = "윌"
        m_pos = full.find("윌", y_pos + 1)
    if y_pos < 0 or m_pos < 0:
        return False, "no_anchor"

    yr2 = yr[-2:]
    avail_ym, _ = _slot_width_after(t_elems, y_pos + 1)
    avail_md, _ = _slot_width_after(t_elems, m_pos + 1)
    if not allow_tight and (avail_ym < len(mo) + 1 or avail_md < len(dy) + 1):
        return False, "no_room"

    p20 = full.find("20") if is_20 else -1
    if is_20:
        if p20 < 0 or p20 > y_pos:
            return False, "no_anchor"
        avail_20, _ = _slot_width_after(t_elems, p20 + 2)
        if not allow_tight and avail_20 < len(yr2) + 1:
            return False, "no_room"

    ok_md, reason_md = _replace_slot_after(
        p_el, m_char, dy, leading_space=(avail_md >= len(dy) + 2), min_keep=1, allow_no_trim=allow_tight
    )
    if not ok_md:
        return False, reason_md
    ok_ym, reason_ym = _replace_slot_after(
        p_el, "년", mo, leading_space=(avail_ym >= len(mo) + 2), min_keep=1, allow_no_trim=allow_tight
    )
    if not ok_ym:
        return False, reason_ym

    if is_20:
        return _replace_slot_after(p_el, "20", yr2, leading_space=False, min_keep=1, allow_no_trim=allow_tight)

    t_elems2 = _collect_t_elems(p_el)
    full2 = "".join(t.text or "" for t in t_elems2)
    y_pos2 = full2.find("년")
    if y_pos2 < 0:
        return False, "no_anchor"
    cur = 0
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


def _fill_hwpx_date_dot_line(
    p_el: etree._Element,
    prefix: str,
    yr: str,
    mo: str,
    dy: str,
) -> tuple[bool, str]:
    t_elems = _collect_t_elems(p_el)
    if not t_elems or not yr.startswith(prefix):
        return False, "no_text"
    full = "".join(t.text or "" for t in t_elems)
    p_idx = full.find(prefix)
    if p_idx < 0:
        return False, "no_anchor"
    dot1 = full.find(".", p_idx + len(prefix))
    dot2 = full.find(".", dot1 + 1) if dot1 >= 0 else -1
    if dot1 < 0 or dot2 < 0:
        return False, "no_anchor"
    dot3 = full.find(".", dot2 + 1)

    avail_md, _ = _slot_width_after(t_elems, dot2 + 1)
    ok_md, reason_md = _replace_slot_at(
        p_el, dot2 + 1, dy,
        leading_space=(dot3 < 0 or avail_md >= len(dy) + 2),
        min_keep=(1 if dot3 >= 0 else 0),
        allow_no_trim=(dot3 < 0),
    )
    if not ok_md:
        return False, reason_md

    avail_ym, _ = _slot_width_after(t_elems, dot1 + 1)
    ok_ym, reason_ym = _replace_slot_at(
        p_el, dot1 + 1, mo,
        leading_space=(avail_ym >= len(mo) + 2),
        min_keep=1,
        allow_no_trim=True,
    )
    if not ok_ym:
        return False, reason_ym

    yr_suf = yr[len(prefix):]
    if not yr_suf:
        return True, "ok"
    return _replace_slot_at(
        p_el, p_idx + len(prefix), yr_suf,
        leading_space=False, min_keep=1, allow_no_trim=True,
    )


def _fill_inline_paragraphs(
    paragraphs: list[etree._Element],
    profile: dict[str, str],
    location_prefix: str,
    report: dict[str, Any],
    context_hint: str = "",
    para_pr_info: dict[str, dict[str, Any]] | None = None,
    char_pr_info: dict[str, dict[str, int]] | None = None,
    avail_w_hwpunit: int = 42000,
    track_page_state: bool = False,
) -> int:
    """서명 블록(년 월 일, 202 . ., 신청인 : ___, 라벨 :) 문단을 원본 글자 보존하며 채운다."""
    filled = 0
    page_excluded = False
    cur_sec = "applicant"
    p_info = para_pr_info or {}
    c_info = char_pr_info or {}

    for pi, p_el in enumerate(paragraphs):
        raw = "".join(p_el.xpath("./*[local-name()='run']/*[local-name()='t']/text()"))
        stripped = re.sub(r"\s+", " ", raw).strip()
        if not stripped:
            continue

        if track_page_state:
            s_ns = re.sub(r"\s+", "", stripped)
            if fm.LEDGER_TITLE_RE.search(stripped) or fm.LEDGER_TITLE_RE.search(s_ns):
                page_excluded = True
                continue
            if len(s_ns) <= 80 and (fm.FORM_TITLE_RE.search(stripped) or re.search(r"별지\s*제?\d+호|위\s*임\s*장", stripped)):
                page_excluded = False
                cur_sec = "applicant"
            if len(s_ns) <= 60:
                bsec = fm.detect_section_context(stripped, current="keep")
                if bsec in ("applicant", "proxy", "other_person", "excluded"):
                    cur_sec = bsec
        if page_excluded or cur_sec in ("excluded", "other_person"):
            if re.fullmatch(r"(?:20\s*)?년\s*[월윌]\s*일|(?:202|20)\s*\.\s*\.\s*\.?", stripped):
                report["skipped_fields"].append({
                    "label": stripped, "location": f"{location_prefix}, p{pi}",
                    "reason": "other_person" if cur_sec == "other_person" else "excluded_section",
                })
            continue

        pid = p_el.get("paraPrIDRef") or ""
        p_align = str((p_info.get(pid) or {}).get("align", "Left"))
        run0 = next((ch for ch in p_el if _local(ch.tag) == "run"), None)
        cid = run0.get("charPrIDRef") if run0 is not None else None
        cst = c_info.get(cid or "") or {"height": 1000, "spacing": 0, "ratio": 100}
        lineseg_cnt = len(p_el.xpath("./*[local-name()='linesegarray']/*[local-name()='lineseg']"))
        has_soft_break = bool(p_el.xpath(".//*[local-name()='lineBreak']")) or ("\n" in raw) or ("\u2028" in raw)
        is_multiline_para = (lineseg_cnt >= 2) or has_soft_break

        # 1) 신청 날짜 인라인: '년   월(윌)   일' 또는 '20   년   월(윌)   일'
        if re.fullmatch(r"(?:20\s*)?년\s*[월윌]\s*일", stripped):
            prev_ctx = context_hint + " " + " ".join(
                "".join(paragraphs[pj].xpath(".//*[local-name()='t']/text()")).strip()
                for pj in range(max(0, pi - 3), pi)
            )
            if _NON_APP_DATE_RE.search(prev_ctx):
                report["skipped_fields"].append({
                    "label": stripped, "location": f"{location_prefix}, p{pi}", "reason": "non_application_date_context",
                })
                continue
            next_ctx = " ".join(
                "".join(paragraphs[pj].xpath(".//*[local-name()='t']/text()")).strip()
                for pj in range(pi + 1, min(len(paragraphs), pi + 4))
            )
            if fm.OFFICIAL_INLINE_CTX_RE.search(prev_ctx + " " + next_ctx):
                report["skipped_fields"].append({
                    "label": stripped, "location": f"{location_prefix}, p{pi}", "reason": "excluded_section",
                })
                continue
            if not any(ok in (prev_ctx + " " + next_ctx) for ok in ("신청", "동의", "확인", "신고", "서명")):
                report["skipped_fields"].append({
                    "label": stripped, "location": f"{location_prefix}, p{pi}", "reason": "unverified_date_context",
                })
                continue

            date_parts = re.findall(r"\d+", profile.get("apply_date") or "")
            if len(date_parts) < 3:
                continue
            yr, mo, dy = date_parts[:3]
            apply_date_val = (profile.get("apply_date") or f"{yr}년 {mo}월 {dy}일").strip()
            dt_w_ok = fm._est_cell_text_width(
                apply_date_val, max(cst["height"], 700), cst["spacing"], max(90, cst["ratio"]) / 100.0
            ) <= 0.85 * max(1, avail_w_hwpunit)
            m_20 = re.match(r"^(\s*20)([ \t\u00a0\u3000_]+)(년)([ \t\u00a0\u3000_]+)([월윌])([ \t\u00a0\u3000_]+)(일\s*)$", raw)
            m_plain = re.match(r"^([ \t\u00a0\u3000_]*)(년)([ \t\u00a0\u3000_]+)([월윌])([ \t\u00a0\u3000_]+)(일\s*)$", raw)
            if m_20 or m_plain:
                ok_dt, reason_dt = _fill_hwpx_date_line(
                    p_el, yr, mo, dy, is_20=bool(m_20),
                    allow_tight=(p_align in ("Center", "Right") and dt_w_ok),
                )
                if ok_dt:
                    report["filled_fields"].append({
                        "label": stripped, "location": f"{location_prefix}, p{pi}",
                        "field_key": "apply_date", "value": apply_date_val, "section": "applicant",
                    })
                    filled += 1
                else:
                    report["skipped_fields"].append({
                        "label": stripped, "location": f"{location_prefix}, p{pi}", "reason": reason_dt,
                    })
            continue

        # 1b) 점 날짜 형식: '202    .     .' 또는 '20    .     .'
        m_dot = re.match(r"^\s*(202|20)([ \t\u00a0\u3000_]*\.[ \t\u00a0\u3000_]*\.[ \t\u00a0\u3000_]*\.?\s*)$", raw)
        if m_dot:
            prefix = m_dot.group(1)
            prev_ctx = context_hint + " " + " ".join(
                "".join(paragraphs[pj].xpath(".//*[local-name()='t']/text()")).strip()
                for pj in range(max(0, pi - 3), pi)
            )
            if _NON_APP_DATE_RE.search(prev_ctx):
                report["skipped_fields"].append({
                    "label": stripped, "location": f"{location_prefix}, p{pi}", "reason": "non_application_date_context",
                })
                continue
            next_ctx = " ".join(
                "".join(paragraphs[pj].xpath(".//*[local-name()='t']/text()")).strip()
                for pj in range(pi + 1, min(len(paragraphs), pi + 4))
            )
            if fm.OFFICIAL_INLINE_CTX_RE.search(prev_ctx + " " + next_ctx):
                report["skipped_fields"].append({
                    "label": stripped, "location": f"{location_prefix}, p{pi}", "reason": "excluded_section",
                })
                continue
            if not any(ok in (prev_ctx + " " + next_ctx) for ok in ("신청", "동의", "확인", "신고", "서명")):
                report["skipped_fields"].append({
                    "label": stripped, "location": f"{location_prefix}, p{pi}", "reason": "unverified_date_context",
                })
                continue
            date_parts = re.findall(r"\d+", profile.get("apply_date") or "")
            if len(date_parts) < 3 or not date_parts[0].startswith(prefix):
                continue
            yr, mo, dy = date_parts[:3]
            apply_date_val = (profile.get("apply_date") or f"{yr}년 {mo}월 {dy}일").strip()
            ok_dot, reason_dot = _fill_hwpx_date_dot_line(p_el, prefix, yr, mo, dy)
            if ok_dot:
                report["filled_fields"].append({
                    "label": stripped, "location": f"{location_prefix}, p{pi}",
                    "field_key": "apply_date", "value": apply_date_val, "section": "applicant",
                })
                filled += 1
            else:
                report["skipped_fields"].append({
                    "label": stripped, "location": f"{location_prefix}, p{pi}", "reason": reason_dot,
                })
            continue

        # 2) 인라인 신청인 성명 + 서명란
        m_sig = re.match(
            r"^(\s*신청인(?:\([^)]*\)|[2)]\))*\s*(?:성\s*명)?\s*:)([ \u00a0\u3000_]{2,})(\(서명(?:\s*또는\s*(?:인|날인))?\)\s*)$",
            raw,
        )
        if m_sig:
            prefix, mid_ws, suffix = m_sig.group(1), m_sig.group(2), m_sig.group(3)
            prev_ctx = context_hint + " " + ("".join(paragraphs[pi - 1].xpath(".//*[local-name()='t']/text()")) if pi > 0 else "")
            if fm.OFFICIAL_INLINE_CTX_RE.search(prev_ctx + " " + prefix):
                report["skipped_fields"].append({
                    "label": prefix.strip(), "location": f"{location_prefix}, p{pi}", "reason": "excluded_section",
                })
                continue
            if is_multiline_para:
                report["skipped_fields"].append({
                    "label": prefix.strip(), "location": f"{location_prefix}, p{pi}", "reason": "multiline_body",
                })
                continue
            app_name = (profile.get("applicant.name") or "").strip()
            if not app_name:
                continue
            if fm.slot_width_spaces(mid_ws) < 1 + fm.display_width_spaces(app_name) + 1:
                report["skipped_fields"].append({
                    "label": prefix.strip(), "location": f"{location_prefix}, p{pi}", "reason": "no_room",
                })
                continue
            if "성명" in prefix or "성 명" in prefix or fm.slot_width_spaces(mid_ws) >= 12:
                ok_nm, reason_nm = _replace_slot_after(p_el, ":", app_name, leading_space=True, min_keep=1)
                if ok_nm:
                    report["filled_fields"].append({
                        "label": prefix.strip(), "location": f"{location_prefix}, p{pi}",
                        "field_key": "applicant.name", "value": app_name, "section": "applicant",
                    })
                    report["sensitive_blank_fields"].append({
                        "label": suffix.strip(), "location": f"{location_prefix}, p{pi}", "reason": "signature_kept_blank",
                    })
                    filled += 1
                else:
                    report["skipped_fields"].append({
                        "label": prefix.strip(), "location": f"{location_prefix}, p{pi}", "reason": reason_nm,
                    })
            continue

        # 3) 하단/본문 인라인 콜론 라벨 ('신청인 주소 :', '성 명 :   ㉿')
        m_col = _INLINE_COLON_RE.match(raw)
        if m_col and "관계" not in raw:
            head_pre, lbl_anchor, mid_ws, sig_suf = m_col.groups()
            prev_ctx = context_hint + " " + ("".join(paragraphs[pi - 1].xpath(".//*[local-name()='t']/text()")) if pi > 0 else "")
            if fm.OFFICIAL_INLINE_CTX_RE.search(prev_ctx + " " + head_pre):
                report["skipped_fields"].append({
                    "label": lbl_anchor.strip(), "location": f"{location_prefix}, p{pi}", "reason": "excluded_section",
                })
                continue
            eff_sec = cur_sec
            if fm.PROXY_SECTION_RE.search(head_pre) and not fm.APPLICANT_MIXED_RE.search(head_pre):
                eff_sec = "proxy"
            elif re.search(r"신\s*청\s*인|위임한\s*사람", head_pre):
                eff_sec = "applicant"
            dec = fm.match_label(re.sub(r"[\s:]+$", "", lbl_anchor), section=eff_sec, profile=profile)
            if dec.action == "fill" and dec.value:
                if is_multiline_para:
                    report["skipped_fields"].append({
                        "label": lbl_anchor.strip(), "location": f"{location_prefix}, p{pi}", "reason": "multiline_body",
                    })
                    continue
                need_w = 1 + fm.display_width_spaces(dec.value) + (1 if sig_suf.strip() else 0)
                if fm.slot_width_spaces(mid_ws) < need_w:
                    report["skipped_fields"].append({
                        "label": lbl_anchor.strip(), "location": f"{location_prefix}, p{pi}", "reason": "no_room",
                    })
                    continue
                if sig_suf.strip():
                    report["sensitive_blank_fields"].append({
                        "label": sig_suf.strip(), "location": f"{location_prefix}, p{pi}", "reason": "signature_kept_blank",
                    })
                else:
                    full_est = fm._est_cell_text_width(
                        raw.rstrip() + " " + dec.value, max(cst["height"], 700), cst["spacing"], max(90, cst["ratio"]) / 100.0
                    )
                    if avail_w_hwpunit > 0 and full_est > avail_w_hwpunit:
                        report["skipped_fields"].append({
                            "label": lbl_anchor.strip(), "location": f"{location_prefix}, p{pi}", "reason": "no_room",
                        })
                        continue
                ok_col, reason_col = _replace_slot_after(
                    p_el, lbl_anchor, dec.value, leading_space=True, min_keep=1, allow_no_trim=False
                )
                if ok_col:
                    report["filled_fields"].append({
                        "label": lbl_anchor.strip(), "location": f"{location_prefix}, p{pi}",
                        "field_key": dec.field_key, "value": dec.value, "section": eff_sec,
                    })
                    filled += 1
                else:
                    report["skipped_fields"].append({
                        "label": lbl_anchor.strip(), "location": f"{location_prefix}, p{pi}", "reason": reason_col,
                    })
            continue

        if "신청인과의 관계" in stripped and "대리" in stripped:
            report["skipped_fields"].append({
                "label": stripped[:40], "location": f"{location_prefix}, p{pi}",
                "reason": "applicant_self_submission_no_proxy_needed",
            })
            continue

        if "법정대리인" in stripped or "미성년자" in stripped:
            report["skipped_fields"].append({
                "label": stripped[:40], "location": f"{location_prefix}, p{pi}",
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
    width: int = 0
    height: int = 0
    margin_l: int = 141
    margin_r: int = 141
    margin_t: int = 141
    margin_b: int = 141


def _parse_table(tbl: etree._Element) -> tuple[list[CellInfo], dict[tuple[int, int], CellInfo], int, int]:
    cells: list[CellInfo] = []
    grid: dict[tuple[int, int], CellInfo] = {}
    max_r, max_c = 0, 0
    for tr in tbl.xpath("./*[local-name()='tr']"):
        for tc in tr.xpath("./*[local-name()='tc']"):
            addr = tc.xpath("./*[local-name()='cellAddr']")[0]
            span = tc.xpath("./*[local-name()='cellSpan']")[0]
            sz_list = tc.xpath("./*[local-name()='cellSz']")
            mg_list = tc.xpath("./*[local-name()='cellMargin']")
            sz = sz_list[0].attrib if sz_list else {}
            mg = mg_list[0].attrib if mg_list else {}
            r = int(addr.get("rowAddr"))
            c = int(addr.get("colAddr"))
            rs = int(span.get("rowSpan", "1"))
            cs = int(span.get("colSpan", "1"))
            ci = CellInfo(
                tc=tc, row=r, col=c, row_span=rs, col_span=cs, text=_tc_text(tc),
                width=int(sz.get("width", "0") or 0),
                height=int(sz.get("height", "0") or 0),
                margin_l=int(mg.get("left", "141") or 141),
                margin_r=int(mg.get("right", "141") or 141),
                margin_t=int(mg.get("top", "141") or 141),
                margin_b=int(mg.get("bottom", "141") or 141),
            )
            cells.append(ci)
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    grid[(rr, cc)] = ci
            max_r = max(max_r, r + rs)
            max_c = max(max_c, c + cs)
    return cells, grid, max_r, max_c


def _row_section_context(row_cells: list[CellInfo], grid: dict[tuple[int, int], CellInfo], r: int, current_section: str) -> str:
    c0 = grid.get((r, 0))
    if c0 and c0.text:
        if (
            fm.ACCOUNT_SECTION_RE.match(fm.normalize_label(c0.text))
            and current_section in ("other_person", "proxy")
            and not fm.PROXY_SECTION_RE.search(c0.text)
            and not fm.OTHER_PERSON_SECTION_RE.search(c0.text)
        ):
            return "applicant"
        sec = fm.detect_section_context(c0.text, current="keep")
        if sec != "keep":
            return sec
    if len(row_cells) <= 2:
        row_txt = " ".join(ci.text for ci in row_cells if ci.text)
        if (
            fm.ACCOUNT_SECTION_RE.match(fm.normalize_label(row_txt))
            and current_section in ("other_person", "proxy")
            and not fm.PROXY_SECTION_RE.search(row_txt)
            and not fm.OTHER_PERSON_SECTION_RE.search(row_txt)
        ):
            return "applicant"
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

        hdr_root, char_pr_info, doc_default_cid, para_pr_info = _parse_header_styles(zin)
        char_pr_heights = {cid: st["height"] for cid, st in char_pr_info.items()}
        char_variant_cache: dict[tuple[str, int, int, bool], str] = {}
        header_modified = False
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

            _fill_inline_paragraphs(
                root.xpath("./*[local-name()='p']"),
                prof,
                f"{sec_name} top",
                report,
                para_pr_info=para_pr_info,
                char_pr_info=char_pr_info,
                track_page_state=True,
            )

            # 각 표 직전 본문 문단 제목·쪽 제외 여부 추적
            body_ps = root.xpath("./*[local-name()='p']")
            table_prev_text: dict[int, str] = {}
            table_page_excluded: dict[int, bool] = {}
            recent_texts: list[str] = []
            bp_excluded = False
            for bp in body_ps:
                bt = "".join(t for t in bp.xpath("./*[local-name()='run']/*[local-name()='t']/text()")).strip()
                if bt:
                    bt_ns = re.sub(r"\s+", "", bt)
                    if fm.LEDGER_TITLE_RE.search(bt) or fm.LEDGER_TITLE_RE.search(bt_ns):
                        bp_excluded = True
                    elif len(bt_ns) <= 80 and (fm.FORM_TITLE_RE.search(bt) or re.search(r"별지\s*제?\d+호|위\s*임\s*장", bt)):
                        bp_excluded = False
                    recent_texts.append(bt)
                for sub_tbl in bp.xpath(".//*[local-name()='tbl']"):
                    table_prev_text[id(sub_tbl)] = " ".join(recent_texts[-2:])
                    table_page_excluded[id(sub_tbl)] = bp_excluded

            for ti, tbl in enumerate(tables):
                cells, grid, max_r, max_c = _parse_table(tbl)
                row_map: dict[int, list[CellInfo]] = {}
                for ci in cells:
                    row_map.setdefault(ci.row, []).append(ci)

                prev_title = table_prev_text.get(id(tbl), "")
                top_texts = [ci.text for ci in cells if ci.row <= 3 and ci.text]
                if table_page_excluded.get(id(tbl), False) or fm.is_ledger_or_approval_table(top_texts, prev_title):
                    report["skipped_fields"].append({
                        "label": (prev_title or (top_texts[0] if top_texts else ""))[:40],
                        "location": f"{sec_name} T{ti}",
                        "reason": "excluded_section",
                    })
                    continue

                tbl_section = fm.detect_section_context(prev_title, current="applicant")
                if tbl_section not in ("excluded", "other_person", "proxy", "applicant"):
                    tbl_section = "applicant"
                norm_cell_texts = [fm.normalize_label(ci.text) for ci in cells if ci.text]
                tbl_has_old_new = (
                    any(fm.OLD_BLOCK_HEAD_RE.match(t) for t in norm_cell_texts)
                    and any(fm.NEW_BLOCK_HEAD_RE.match(t) for t in norm_cell_texts)
                )
                for r in range(max_r):
                    row_cells = sorted(row_map.get(r, []), key=lambda x: x.col)
                    tbl_section = _row_section_context(row_cells, grid, r, tbl_section)
                    if any(rc.col_span >= max(3, int(max_c * 0.65)) for rc in row_cells):
                        row_joined = " ".join(rc.text for rc in row_cells if rc.text)
                        if row_joined and len(row_joined) <= 120:
                            wsec = fm.detect_section_context(row_joined, current="keep")
                            if wsec != "keep":
                                tbl_section = wsec
                    c0_band = grid.get((r, 0))
                    if c0_band and c0_band.row_span >= 2:
                        first_row_texts = [
                            rc.text for rc in row_map.get(c0_band.row, [])
                            if rc.col > 0 and rc.text
                        ]
                        if fm.is_deceased_band_first_row(first_row_texts):
                            tbl_section = "other_person"

                    row_head_c0 = grid.get((r, 0))
                    row_head_norm = fm.normalize_label(row_head_c0.text) if row_head_c0 and row_head_c0.text else ""
                    row_only_excluded = bool(
                        fm.EXCLUDED_ROW_HEAD_RE.match(row_head_norm)
                        or re.fullmatch(r"성명(?:여성|남성)", row_head_norm)
                    )

                    has_mobile_in_band = any(
                        "휴대전화" in c2.text or "휴대폰" in c2.text or "H․P" in c2.text or "HP" in c2.text
                        for rr2 in range(max(0, r - 2), min(max_r, r + 3))
                        for c2 in row_map.get(rr2, [])
                    )

                    for ci in row_cells:
                        txt = ci.text
                        if not txt:
                            continue
                        cur_sec = "other_person" if row_only_excluded else tbl_section
                        if not row_only_excluded:
                            for hcol in (0, 1, 2):
                                if hcol >= ci.col:
                                    break
                                hc = grid.get((r, hcol))
                                if hc and hc.text and (hc.col <= 1 or hc.row_span >= 2):
                                    s_sub = fm.detect_section_context(hc.text, current="keep")
                                    if s_sub != "keep":
                                        cur_sec = s_sub
                        if tbl_has_old_new and cur_sec not in ("excluded", "other_person"):
                            old_new_state = ""
                            for hcol in range(0, min(4, ci.col)):
                                hc = grid.get((r, hcol))
                                if hc and hc.text:
                                    hn = fm.normalize_label(hc.text)
                                    if fm.OLD_BLOCK_HEAD_RE.match(hn):
                                        old_new_state = "old"
                                    elif fm.NEW_BLOCK_HEAD_RE.match(hn):
                                        old_new_state = "new"
                            if not old_new_state:
                                for ar in range(max(0, r - 2), r):
                                    ac = grid.get((ar, ci.col))
                                    if ac and ac.text:
                                        an = fm.normalize_label(ac.text)
                                        if fm.OLD_BLOCK_HEAD_RE.match(an):
                                            old_new_state = "old"
                                        elif fm.NEW_BLOCK_HEAD_RE.match(an):
                                            old_new_state = "new"
                            if old_new_state == "old":
                                cur_sec = "excluded"

                        # 단일 셀 내 인라인 서명/날짜/점날짜/하위라벨/콜론 블록 처리
                        has_inline_markers = (
                            ("년" in txt and ("월" in txt or "윌" in txt) and "일" in txt)
                            or "202" in txt
                            or ":" in txt
                            or "㉿" in txt
                            or "\U000f012b" in txt
                        )
                        if has_inline_markers and len(txt) >= 5:
                            left_ctx = " ".join(
                                c2.text for c2 in row_cells if c2.col < ci.col and c2.text
                            )
                            if cur_sec in ("excluded", "other_person") or fm.OFFICIAL_INLINE_CTX_RE.search(left_ctx + " " + txt):
                                if ("년" in txt and ("월" in txt or "윌" in txt) and "일" in txt) or "202" in txt:
                                    report["skipped_fields"].append({
                                        "label": txt.replace("\n", " ")[:40],
                                        "location": f"{sec_name} T{ti}({ci.row},{ci.col})",
                                        "reason": "other_person" if cur_sec == "other_person" else "excluded_section",
                                    })
                            else:
                                cell_ps = ci.tc.xpath("./*[local-name()='subList']/*[local-name()='p']")
                                _fill_inline_paragraphs(
                                    cell_ps,
                                    prof,
                                    f"{sec_name} T{ti}({ci.row},{ci.col})",
                                    report,
                                    context_hint=left_ctx,
                                    para_pr_info=para_pr_info,
                                    char_pr_info=char_pr_info,
                                    avail_w_hwpunit=max(1, ci.width - ci.margin_l - ci.margin_r) if ci.width > 0 else 42000,
                                )

                        # 한 셀 안의 하위 라벨('자택 :\n휴대폰 :' 등) 처리
                        if cur_sec in ("applicant", "proxy") and "\n" in txt:
                            cell_ps_sub = ci.tc.xpath("./*[local-name()='subList']/*[local-name()='p']")
                            key_p = "proxy.phone" if cur_sec == "proxy" else "applicant.phone"
                            ph_val = (prof.get(key_p) or "").strip()
                            if ph_val:
                                for pi_sub, p_sub in enumerate(cell_ps_sub):
                                    p_raw = "".join(p_sub.xpath(".//*[local-name()='t']/text()"))
                                    if not _SUBLABEL_PHONE_RE.match(p_raw):
                                        continue
                                    ok_s, _ = _replace_slot_after(
                                        p_sub, p_raw.rstrip(), ph_val, leading_space=True, min_keep=0, allow_no_trim=True
                                    )
                                    if ok_s:
                                        report["filled_fields"].append({
                                            "label": p_raw.strip(),
                                            "location": f"{sec_name} T{ti}({ci.row},{ci.col}), p{pi_sub}",
                                            "field_key": key_p,
                                            "value": ph_val,
                                            "section": cur_sec,
                                        })

                        if len(txt) > 60:
                            continue
                        if re.fullmatch(r"\s*\(전화(?:번호)?\s*:[^)]*\)\s*", re.sub(r"\s{2,}", " ", txt)):
                            continue

                        decision = fm.match_label(txt, section=cur_sec, profile=prof)
                        if decision.action == "skip" and decision.reason in ("empty_or_too_long_label", "unmatched_label"):
                            continue

                        # 휴대전화 칸이 따로 있을 때 '전화번호' 단독 라벨은 집전화일 수 있으므로 건너뜀
                        if decision.field_key in ("applicant.phone", "proxy.phone") and fm.normalize_label(txt) in ("전화번호", "집") and has_mobile_in_band:
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
                        # 2) 오른쪽이 다른 헤더 라벨이면, 바로 아래 셀 확인 (단, 좌측 라벨 충돌·타인 위헤더·다중행 명부 차단)
                        elif ci.row + ci.row_span < max_r:
                            below_ci = grid.get((ci.row + ci.row_span, ci.col))
                            if (
                                below_ci is not None
                                and below_ci.col == ci.col
                                and below_ci.col_span == ci.col_span
                                and fm.is_cell_empty(below_ci.text)
                            ):
                                above_h = grid.get((ci.row - 1, ci.col)) if ci.row > 0 else None
                                above_sec = fm.detect_section_context(above_h.text, current="keep") if above_h and above_h.text else "keep"
                                left_b = grid.get((below_ci.row, below_ci.col - 1)) if below_ci.col > 0 else None
                                left_b_busy = bool(left_b is not None and left_b.row_span == 1 and not fm.is_cell_empty(left_b.text))
                                rh_below = grid.get((below_ci.row, 0))
                                rh_norm = fm.normalize_label(rh_below.text) if rh_below and rh_below.text else ""
                                rh_sec = fm.detect_section_context(rh_below.text, current="keep") if rh_below and rh_below.text else "keep"
                                next_rep_row = below_ci.row + below_ci.row_span
                                next_rep = grid.get((next_rep_row, below_ci.col)) if next_rep_row < max_r else None
                                is_multi_row_roster = bool(
                                    next_rep is not None
                                    and next_rep.col == below_ci.col
                                    and next_rep.col_span == ci.col_span
                                    and fm.is_cell_empty(next_rep.text)
                                )
                                if left_b_busy:
                                    below_ci = None
                                elif above_sec in ("excluded", "other_person") or rh_sec in ("excluded", "other_person") or fm.EXCLUDED_ROW_HEAD_RE.match(rh_norm):
                                    report["skipped_fields"].append({
                                        "label": txt.replace("\n", " "),
                                        "location": f"{sec_name} T{ti}({below_ci.row},{below_ci.col})",
                                        "reason": "other_person" if (above_sec == "other_person" or rh_sec == "other_person" or fm.EXCLUDED_ROW_HEAD_RE.match(rh_norm)) else "excluded_section",
                                    })
                                    continue
                                elif is_multi_row_roster:
                                    report["skipped_fields"].append({
                                        "label": txt.replace("\n", " "),
                                        "location": f"{sec_name} T{ti}({below_ci.row},{below_ci.col})",
                                        "reason": "other_person" if rh_sec == "other_person" else "excluded_section",
                                    })
                                    continue
                                if below_ci is not None:
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
                        target_st = char_pr_info.get(target_cid or "")
                        target_h = target_st["height"] if target_st else None
                        base_cid_for_fit = target_cid
                        if target_h is not None and target_h < 700:
                            lbl_cid = _tc_char_pr_id(ci.tc)
                            if lbl_cid is not None and char_pr_heights.get(lbl_cid, 0) >= 700:
                                override_cid = lbl_cid
                                base_cid_for_fit = lbl_cid
                            elif doc_default_cid is not None:
                                override_cid = doc_default_cid
                                base_cid_for_fit = doc_default_cid
                            else:
                                report["skipped_fields"].append({
                                    "label": txt.replace("\n", " "),
                                    "location": f"{sec_name} T{ti}({target_ci.row},{target_ci.col})",
                                    "reason": "tiny_font",
                                })
                                continue

                        # G. 셀 안쪽 폭 맞춤 (8pt·자간 -10% 한도; 넘치고 줄바꿈 불가 시 skipped: no_room)
                        base_st = char_pr_info.get(base_cid_for_fit or "") or {"height": 1000, "spacing": 0, "ratio": 100}
                        target_pid = _tc_para_pr_id(target_ci.tc)
                        target_ls = int((para_pr_info.get(target_pid or "") or {}).get("line_space_pct", 160))
                        fits, fit_h, fit_sp, fit_changed = fm.fit_text_to_cell(
                            decision.value or "",
                            target_ci.width,
                            target_ci.height,
                            margin_l=target_ci.margin_l,
                            margin_r=target_ci.margin_r,
                            margin_t=target_ci.margin_t,
                            margin_b=target_ci.margin_b,
                            base_pt100=base_st["height"],
                            base_spacing=base_st["spacing"],
                            line_space_pct=target_ls,
                            allow_wrap=(decision.field_key == "applicant.address"),
                            base_ratio=base_st["ratio"],
                        )
                        if not fits:
                            report["skipped_fields"].append({
                                "label": txt.replace("\n", " "),
                                "location": f"{sec_name} T{ti}({target_ci.row},{target_ci.col})",
                                "reason": "no_room",
                            })
                            continue
                        need_black = bool(base_st.get("is_white"))
                        if fit_changed or need_black:
                            new_cid, created = _ensure_char_pr_variant(
                                hdr_root, base_cid_for_fit, fit_h, fit_sp,
                                char_pr_info, char_variant_cache, doc_default_cid,
                                force_black=need_black,
                            )
                            if new_cid is not None:
                                override_cid = new_cid
                                if created:
                                    header_modified = True

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

        if header_modified and hdr_root is not None:
            updated_sections["Contents/header.xml"] = etree.tostring(
                hdr_root, xml_declaration=True, encoding="UTF-8", standalone=True
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
