"""HWP 5.0 기입 계획기 (Java hwplib 전용).

지원하는 기입 위치:
1. 표 셀 오른쪽/아래 빈 셀 (`cell_para_idx=0, offset=0`)
2. 표 셀 내부 첫 문단이 빈 줄이고 아래 줄에 `(전화번호: )` 등이 있는 셀 (예: 보훈처/경산시/해남군 주소+전화 셀)
3. 같은 셀 안 위 줄에 라벨(`금융기관명`, `신청인 명의의 계좌번호`, `[핸드폰]`, `휴대전화`)이 있고 빈 줄이나 뒤 공백에 기입하는 셀
4. 본문 및 표 셀 내부의 신청일(`년   월   일`) 및 신청인 서명 줄(`신청인 : ___ (서명 또는 인)` — 서명은 비우고 이름만 기입)
"""
from __future__ import annotations

import re
from typing import Any

import form_matcher as fm


_NON_APP_DATE_RE = re.compile(
    r"(?:출산\s*(?:예정)?|사망|발생|접수|지급|입사|성립|입금)\s*일|입금\s*(?:확인|됨)|보험료\s*입금"
)


def _target_para_pt100(target_c: dict[str, Any], para_idx: int = 0) -> int | None:
    pts = target_c.get("para_pt100s")
    if isinstance(pts, list) and 0 <= para_idx < len(pts):
        return int(pts[para_idx] or 0)
    if "font_pt100" in target_c:
        return int(target_c.get("font_pt100") or 0)
    return None


def _resolve_font_override(
    target_c: dict[str, Any],
    label_c: dict[str, Any] | None,
    doc_dump: dict[str, Any],
    para_idx: int = 0,
) -> tuple[bool, int, int]:
    """기입할 셀 글자 크기가 7pt(700) 미만이면 라벨 셀/문서 기본 글자 모양으로 교체하거나 건너뛴다."""
    cur_pt = _target_para_pt100(target_c, para_idx)
    if cur_pt is None:
        return True, -1, 1000
    if cur_pt >= 700:
        return True, -1, cur_pt
    if label_c is not None and int(label_c.get("font_pt100") or 0) >= 700:
        lbl_sid = int(label_c.get("char_shape_id", -1))
        if lbl_sid >= 0:
            return True, lbl_sid, int(label_c.get("font_pt100") or 1000)
    if int(doc_dump.get("default_font_pt100") or 0) >= 700:
        def_sid = int(doc_dump.get("default_char_shape_id", -1))
        if def_sid >= 0:
            return True, def_sid, int(doc_dump.get("default_font_pt100") or 1000)
    return False, -1, cur_pt


def _fit_cell_style(
    target_c: dict[str, Any],
    text: str,
    field_key: str | None,
    override_sid: int,
    base_pt100: int,
) -> tuple[bool, int, int, bool]:
    return fm.fit_text_to_cell(
        text,
        int(target_c.get("width", 0) or 0),
        int(target_c.get("height", 0) or 0),
        margin_l=int(target_c.get("margin_left", 141) or 0),
        margin_r=int(target_c.get("margin_right", 141) or 0),
        margin_t=int(target_c.get("margin_top", 141) or 0),
        margin_b=int(target_c.get("margin_bottom", 141) or 0),
        base_pt100=base_pt100 if base_pt100 >= 700 else 1000,
        base_spacing=0 if override_sid >= 0 else int(target_c.get("char_space", 0) or 0),
        line_space_pct=int(target_c.get("line_space_pct", 160) or 160),
        allow_wrap=(field_key == "applicant.address"),
        base_ratio=int(target_c.get("char_ratio", 100) or 100),
    )


_INLINE_COLON_RE = re.compile(
    r"^(.*?)"
    r"((?:주\s*소|성\s*명|생\s*년\s*월\s*일|연\s*락\s*처|휴\s*대\s*(?:전\s*화|폰)(?:\s*번\s*호)?)\s*:)"
    r"([ \t\u00a0\u3000_]*)"
    r"((?:㉿|㊞|\U000f012b|\(서명(?:\s*또는\s*(?:인|날인))?\)|\(인\))?\s*)$"
)
_SUBLABEL_PHONE_RE = re.compile(
    r"^[\s·•▪\-\[\(]*(?:휴대폰|휴대전화|핸드폰|H\s*[.․·ㆍ]?\s*P)(?:\s*번호)?\s*(?::|：)?\s*$",
    re.IGNORECASE,
)


def _plan_inline_lines(
    raw: str,
    prev_ctx: str,
    next_ctx: str,
    prof: dict[str, str],
    location: str,
    report: dict[str, Any],
    section: str = "applicant",
    direct_ctx: str = "",
    is_body: bool = False,
    para_align: str = "Left",
    avail_w_hwpunit: int = 42000,
    font_pt100: int = 1000,
    char_space: int = 0,
    char_ratio: int = 100,
    line_seg_count: int = 1,
) -> list[dict[str, Any]]:
    edits: list[dict[str, Any]] = []
    if not raw or not raw.strip():
        return edits
    if section in ("excluded", "other_person"):
        for seg in raw.split("\u2028"):
            st = re.sub(r"\s+", " ", seg).strip()
            if re.fullmatch(r"(?:20\s*)?년\s*[월윌]\s*일|(?:202|20)\s*\.\s*\.\s*\.?", st):
                report["skipped_fields"].append({
                    "label": st, "location": location,
                    "reason": "other_person" if section == "other_person" else "excluded_section",
                })
        return edits
    is_multiline_para = (int(line_seg_count or 1) >= 2) or ("\u2028" in raw)

    for raw_seg in raw.split("\u2028"):
        stripped = re.sub(r"\s+", " ", raw_seg).strip()
        if not stripped:
            continue

        # 1) 신청 날짜 인라인: '년   월(윌)   일' 또는 '20   년   월(윌)   일'
        if re.fullmatch(r"(?:20\s*)?년\s*[월윌]\s*일", stripped):
            bad_target = direct_ctx or prev_ctx
            if _NON_APP_DATE_RE.search(bad_target):
                report["skipped_fields"].append({"label": stripped, "location": location, "reason": "non_application_date_context"})
                continue
            if fm.OFFICIAL_INLINE_CTX_RE.search(prev_ctx + " " + next_ctx + " " + direct_ctx):
                report["skipped_fields"].append({"label": stripped, "location": location, "reason": "excluded_section"})
                continue
            if not any(ok in (prev_ctx + " " + next_ctx) for ok in ("신청", "동의", "확인", "신고", "서명")):
                report["skipped_fields"].append({"label": stripped, "location": location, "reason": "unverified_date_context"})
                continue
            m_20 = re.match(r"^(\s*20)([ \t\u00a0\u3000_]+)(년)([ \t\u00a0\u3000_]+)([월윌])([ \t\u00a0\u3000_]+)(일\s*)$", raw_seg)
            m_plain = re.match(r"^([ \t\u00a0\u3000_]*)(년)([ \t\u00a0\u3000_]+)([월윌])([ \t\u00a0\u3000_]+)(일\s*)$", raw_seg)
            date_parts = re.findall(r"\d+", prof.get("apply_date") or "")
            if len(date_parts) < 3:
                continue
            yr, mo, dy = date_parts[:3]
            apply_date_val = (prof.get("apply_date") or f"{yr}년 {mo}월 {dy}일").strip()
            centered_or_right = para_align in ("Center", "Right")
            ratio_eff = max(90, int(char_ratio or 100)) / 100.0
            dt_w_ok = fm._est_cell_text_width(apply_date_val, max(font_pt100, 700), char_space, ratio_eff) <= 0.85 * max(1, avail_w_hwpunit)

            if m_20:
                yr2 = yr[-2:]
                lead20, sp_20y, s_y, sp_ym, s_m, sp_md, s_d = m_20.groups()
                has_slot = (
                    fm.slot_width_spaces(sp_20y) >= len(yr2) + 1
                    and fm.slot_width_spaces(sp_ym) >= len(mo) + 1
                    and fm.slot_width_spaces(sp_md) >= len(dy) + 1
                )
                if not has_slot and not (centered_or_right and dt_w_ok):
                    report["skipped_fields"].append({"label": stripped, "location": location, "reason": "no_room"})
                    continue
                sp_20y2 = " " * max(1, len(sp_20y) - len(yr2))
                sp_ym2 = (" " + mo + " " * max(1, len(sp_ym) - len(mo) - 1)) if len(sp_ym) >= len(mo) + 2 else (mo + " ")
                sp_md2 = (" " + dy + " " * max(1, len(sp_md) - len(dy) - 1)) if len(sp_md) >= len(dy) + 2 else (dy + " ")
                new_text = f"{lead20}{yr2}{sp_20y2}{s_y}{sp_ym2}{s_m}{sp_md2}{s_d}"
                report["filled_fields"].append({
                    "label": stripped, "location": location, "field_key": "apply_date",
                    "value": apply_date_val, "section": "applicant",
                })
                edits.append({"mode": "date20", "orig_text": raw_seg, "new_text": new_text, "field_key": "apply_date", "value": apply_date_val})
                continue
            if m_plain:
                lead, s_y, sp_ym, s_m, sp_md, s_d = m_plain.groups()
                has_slot = (
                    fm.slot_width_spaces(sp_ym) >= len(mo) + 1
                    and fm.slot_width_spaces(sp_md) >= len(dy) + 1
                    and not (is_body and 0 < fm.slot_width_spaces(lead) < len(yr))
                )
                if not has_slot and not (centered_or_right and dt_w_ok and (not is_body or fm.slot_width_spaces(lead) == 0 or fm.slot_width_spaces(lead) >= len(yr))):
                    report["skipped_fields"].append({"label": stripped, "location": location, "reason": "no_room"})
                    continue
                trim = min(len(lead), len(yr))
                lead2 = lead[:-trim] if trim else lead
                sp_ym2 = (" " + mo + " " * max(1, len(sp_ym) - len(mo) - 1)) if len(sp_ym) >= len(mo) + 2 else (mo + " ")
                sp_md2 = (" " + dy + " " * max(1, len(sp_md) - len(dy) - 1)) if len(sp_md) >= len(dy) + 2 else (dy + " ")
                new_text = f"{lead2}{yr}{s_y}{sp_ym2}{s_m}{sp_md2}{s_d}"
                report["filled_fields"].append({
                    "label": stripped, "location": location, "field_key": "apply_date",
                    "value": apply_date_val, "section": "applicant",
                })
                edits.append({"mode": "date", "orig_text": raw_seg, "new_text": new_text, "field_key": "apply_date", "value": apply_date_val})
            continue

        # 1b) 점 날짜 형식: '202 . .' 또는 '20 . .'
        m_dot = re.match(r"^\s*(202|20)([ \t\u00a0\u3000_]*\.[ \t\u00a0\u3000_]*\.[ \t\u00a0\u3000_]*\.?\s*)$", raw_seg)
        if m_dot:
            prefix = m_dot.group(1)
            bad_target = direct_ctx or prev_ctx
            if _NON_APP_DATE_RE.search(bad_target):
                report["skipped_fields"].append({"label": stripped, "location": location, "reason": "non_application_date_context"})
                continue
            if fm.OFFICIAL_INLINE_CTX_RE.search(prev_ctx + " " + next_ctx + " " + direct_ctx):
                report["skipped_fields"].append({"label": stripped, "location": location, "reason": "excluded_section"})
                continue
            if not any(ok in (prev_ctx + " " + next_ctx) for ok in ("신청", "동의", "확인", "신고", "서명")):
                report["skipped_fields"].append({"label": stripped, "location": location, "reason": "unverified_date_context"})
                continue
            date_parts = re.findall(r"\d+", prof.get("apply_date") or "")
            if len(date_parts) < 3 or not date_parts[0].startswith(prefix):
                continue
            yr, mo, dy = date_parts[:3]
            apply_date_val = (prof.get("apply_date") or f"{yr}년 {mo}월 {dy}일").strip()
            report["filled_fields"].append({
                "label": stripped, "location": location, "field_key": "apply_date",
                "value": apply_date_val, "section": "applicant",
            })
            edits.append({
                "mode": "date_dot", "anchor": prefix, "orig_text": raw_seg,
                "new_text": apply_date_val, "field_key": "apply_date", "value": apply_date_val,
            })
            continue

        # 2) 인라인 성명 + 서명 줄 ('신청인 :     (서명 또는 인)' / '성 명:    (서명 또는 인)' / '신청인      (서명 또는 인)')
        m_sig = re.match(
            r"^(\s*(?:신\s*청\s*인|보험가입신고\(신청\)인|성\s*명|이\s*름)(?:\([^)]*\))*\s*:?)([ \t\u00a0\u3000_]{2,})(\(서명(?:\s*또는\s*(?:인|날인))?\)\s*)$",
            raw_seg,
        )
        if m_sig:
            pre, mid_ws, suf = m_sig.groups()
            if fm.OFFICIAL_INLINE_CTX_RE.search(prev_ctx + " " + direct_ctx + " " + pre):
                report["skipped_fields"].append({"label": pre.strip(), "location": location, "reason": "excluded_section"})
                continue
            if is_multiline_para:
                report["skipped_fields"].append({"label": pre.strip(), "location": location, "reason": "multiline_body"})
                continue
            name = (prof.get("applicant.name") or "").strip()
            if not name:
                continue
            need_w = 1 + fm.display_width_spaces(name) + 1
            if fm.slot_width_spaces(mid_ws) < need_w:
                report["skipped_fields"].append({"label": pre.strip(), "location": location, "reason": "no_room"})
                continue
            rem = max(1, fm.slot_width_spaces(mid_ws) - fm.display_width_spaces(name) - 1)
            new_text = f"{pre} {name}{' ' * rem}{suf}"
            report["filled_fields"].append({
                "label": pre.strip(), "location": location, "field_key": "applicant.name",
                "value": name, "section": "applicant",
            })
            report["sensitive_blank_fields"].append({
                "label": suf.strip(), "location": location, "reason": "signature_kept_blank",
            })
            edits.append({"mode": "after_anchor", "anchor": pre, "orig_text": raw_seg, "new_text": new_text, "field_key": "applicant.name", "value": name})
            continue

        # 3) 하단/본문 인라인 콜론 라벨 ('신청인 주 소 :', '성 명 :    ㉿', '생년월일 :') — 콜론 뒤 빈칸이 값 폭 이상일 때만 기입
        m_col = _INLINE_COLON_RE.match(raw_seg)
        if m_col and "관계" not in raw_seg:
            head_pre, lbl_anchor, mid_ws, sig_suf = m_col.groups()
            if fm.OFFICIAL_INLINE_CTX_RE.search(prev_ctx + " " + direct_ctx + " " + head_pre):
                report["skipped_fields"].append({"label": lbl_anchor.strip(), "location": location, "reason": "excluded_section"})
                continue
            eff_sec = section
            if fm.PROXY_SECTION_RE.search(head_pre) and not fm.APPLICANT_MIXED_RE.search(head_pre):
                eff_sec = "proxy"
            elif re.search(r"신\s*청\s*인|위임한\s*사람", head_pre):
                eff_sec = "applicant"
            lbl_clean = re.sub(r"[\s:]+$", "", lbl_anchor)
            dec = fm.match_label(lbl_clean, section=eff_sec, profile=prof)
            if dec.action == "fill" and dec.value:
                if is_multiline_para:
                    report["skipped_fields"].append({"label": lbl_anchor.strip(), "location": location, "reason": "multiline_body"})
                    continue
                need_w = 1 + fm.display_width_spaces(dec.value) + (1 if sig_suf.strip() else 0)
                if fm.slot_width_spaces(mid_ws) < need_w:
                    report["skipped_fields"].append({"label": lbl_anchor.strip(), "location": location, "reason": "no_room"})
                    continue
                if sig_suf.strip():
                    report["sensitive_blank_fields"].append({
                        "label": sig_suf.strip(), "location": location, "reason": "signature_kept_blank",
                    })
                else:
                    ratio_eff = max(90, int(char_ratio or 100)) / 100.0
                    full_est = fm._est_cell_text_width(raw_seg.rstrip() + " " + dec.value, max(font_pt100, 700), char_space, ratio_eff)
                    if avail_w_hwpunit > 0 and full_est > avail_w_hwpunit:
                        report["skipped_fields"].append({"label": lbl_anchor.strip(), "location": location, "reason": "no_room"})
                        continue
                report["filled_fields"].append({
                    "label": lbl_anchor.strip(), "location": location, "field_key": dec.field_key,
                    "value": dec.value, "section": eff_sec,
                })
                edits.append({
                    "mode": "after_anchor", "anchor": lbl_anchor, "orig_text": raw_seg,
                    "new_text": dec.value, "field_key": dec.field_key, "value": dec.value,
                })
    return edits


def _can_fill_address_cell_first_line(cell_text: str) -> int | None:
    """첫 문단이 비어 있고 아래 문단에 `(전화번호: ...)`가 있는 셀이면 0번 문단 인덱스를 반환."""
    lines = cell_text.split("\n")
    if len(lines) >= 2 and not lines[0].strip() and any("전화" in ln for ln in lines[1:]):
        return 0
    return None


APPLICANT_ROW_HEAD_RE = re.compile(r"^(?:주소|현주소|전화번호|연락처|휴대전화|휴대폰|핸드폰)$")


def plan_hwp_fills(doc_dump: dict[str, Any], profile: dict[str, str] | None = None) -> dict[str, Any]:
    prof = fm.default_profile() if profile is None else profile
    report: dict[str, Any] = {
        "found_labels": [],
        "filled_fields": [],
        "skipped_fields": [],
        "sensitive_blank_fields": [],
        "filled_count": 0,
    }
    cell_edits: list[dict[str, Any]] = []
    para_edits: list[dict[str, Any]] = []

    paras = doc_dump.get("paragraphs", [])
    tables_by_para: dict[tuple[int, int], list[str]] = {}
    for tbl in doc_dump.get("tables", []):
        tail_cells = tbl.get("cells", [])[-6:]
        tail_txt = " ".join(c.get("text", "") for c in tail_cells if c.get("text"))
        if tail_txt:
            tables_by_para.setdefault((tbl.get("section", 0), tbl.get("para", 0)), []).append(tail_txt)

    para_pos_map = {(p.get("section", 0), p.get("para", 0)): i for i, p in enumerate(paras)}
    para_section_state: list[str] = []
    cur_body_sec = "applicant"
    cur_page_excluded = False
    for idx, p in enumerate(paras):
        pt_raw = p.get("text", "")
        pt_clean = re.sub(r"\s+", " ", pt_raw).strip()
        if pt_clean:
            pt_ns = re.sub(r"\s+", "", pt_clean)
            if fm.LEDGER_TITLE_RE.search(pt_clean) or fm.LEDGER_TITLE_RE.search(pt_ns):
                cur_page_excluded = True
            elif len(pt_ns) <= 80 and (fm.FORM_TITLE_RE.search(pt_clean) or re.search(r"별지\s*제?\d+호|위\s*임\s*장", pt_clean)):
                cur_page_excluded = False
                cur_body_sec = "applicant"
            if not cur_page_excluded and len(pt_ns) <= 60:
                bsec = fm.detect_section_context(pt_clean, current="keep")
                if bsec in ("applicant", "proxy", "other_person", "excluded"):
                    cur_body_sec = bsec
        para_section_state.append("excluded" if cur_page_excluded else cur_body_sec)
        if cur_page_excluded or cur_body_sec in ("excluded", "other_person"):
            continue
        prev_chunks: list[str] = []
        for pj in range(max(0, idx - 3), idx):
            ptxt = paras[pj].get("text", "").strip()
            if ptxt:
                prev_chunks.append(ptxt)
            prev_chunks.extend(tables_by_para.get((paras[pj]["section"], paras[pj]["para"]), []))
        next_chunks = [paras[pj].get("text", "").strip() for pj in range(idx + 1, min(len(paras), idx + 4)) if paras[pj].get("text", "").strip()]
        for ed in _plan_inline_lines(
            p["text"], " ".join(prev_chunks), " ".join(next_chunks), prof,
            f"body s{p['section']} p{p['para']}", report,
            section=cur_body_sec,
            is_body=True,
            para_align=str(p.get("para_align") or "Left"),
            font_pt100=int(p.get("font_pt100") or 1000),
            char_space=int(p.get("char_space") or 0),
            char_ratio=int(p.get("char_ratio") or 100),
            line_seg_count=int(p.get("line_seg_count") or 1),
        ):
            para_edits.append({"section": p["section"], "para": p["para"], **ed})

    filled_cell_ops: set[tuple[int, int, int, str, str]] = set()
    handled_date_cells: set[tuple[int, int]] = set()
    handled_inline_cells: set[tuple[int, int]] = set()
    for t_idx, tbl in enumerate(doc_dump.get("tables", [])):
        cells = tbl.get("cells", [])
        grid: dict[tuple[int, int], dict] = {}
        row_map: dict[int, list[dict]] = {}
        max_r = tbl.get("row_count", 0)
        for c in cells:
            r0, c0 = c["row"], c["col"]
            rs, cs = max(1, c.get("row_span", 1)), max(1, c.get("col_span", 1))
            row_map.setdefault(r0, []).append(c)
            for rr in range(r0, r0 + rs):
                for cc in range(c0, c0 + cs):
                    grid[(rr, cc)] = c
            max_r = max(max_r, r0 + rs)

        tbl_sec_num, tbl_para_num = tbl.get("section", 0), tbl.get("para", 0)
        p_pos = para_pos_map.get((tbl_sec_num, tbl_para_num), -1)
        prev_body_title = ""
        if p_pos >= 0:
            prev_titles = [
                paras[j].get("text", "").strip()
                for j in range(max(0, p_pos - 2), p_pos + 1)
                if paras[j].get("section", 0) == tbl_sec_num and paras[j].get("text", "").strip()
            ]
            prev_body_title = " ".join(prev_titles[-2:])

        # F. 쪽 전체 제외 또는 기관 장부/상단 결재 표 전체 건너뛰기
        top_texts = [c.get("text", "") for c in cells if c.get("row", 0) <= 3 and c.get("text")]
        if (p_pos >= 0 and para_section_state[p_pos] == "excluded") or fm.is_ledger_or_approval_table(top_texts, prev_body_title):
            report["skipped_fields"].append({
                "label": (prev_body_title or (top_texts[0] if top_texts else ""))[:40],
                "location": f"T{t_idx}",
                "reason": "excluded_section",
            })
            continue

        tbl_section = fm.detect_section_context(prev_body_title, current="applicant")
        if tbl_section not in ("excluded", "other_person", "proxy", "applicant"):
            tbl_section = "applicant"
        max_c = tbl.get("col_count", 0) or (max((c["col"] + max(1, c.get("col_span", 1)) for c in cells), default=0))
        norm_cell_texts = [fm.normalize_label(c.get("text", "")) for c in cells if c.get("text")]
        tbl_has_old_new = (
            any(fm.OLD_BLOCK_HEAD_RE.match(t) for t in norm_cell_texts)
            and any(fm.NEW_BLOCK_HEAD_RE.match(t) for t in norm_cell_texts)
        )
        head_restore: tuple[int, str] | None = None  # (구역 머리 칸이 끝나는 줄, 되돌릴 구역)
        for r in range(max_r):
            row_cells = sorted(row_map.get(r, []), key=lambda x: x["col"])
            # 왼쪽 병합 구역 헤더(col 0) 및 배너 제목 행 확인
            c0_cell = grid.get((r, 0))
            if (head_restore and r >= head_restore[0] and c0_cell and c0_cell["row"] == r
                    and APPLICANT_ROW_HEAD_RE.match(fm.normalize_label(c0_cell.get("text", "")))):
                # '입양아동' 같은 구역 머리 칸이 끝나고 '주소'·'전화번호' 줄이 오면 원래 구역으로 돌아간다.
                tbl_section = head_restore[1]
                head_restore = None
            if c0_cell and c0_cell.get("text"):
                c0_norm_head = fm.normalize_label(c0_cell["text"])
                if (
                    fm.ACCOUNT_SECTION_RE.match(c0_norm_head)
                    and tbl_section in ("other_person", "proxy")
                    and not fm.PROXY_SECTION_RE.search(c0_cell["text"])
                    and not fm.OTHER_PERSON_SECTION_RE.search(c0_cell["text"])
                ):
                    tbl_section = "applicant"
                else:
                    sec = fm.detect_section_context(c0_cell["text"], current="keep")
                    if sec != "keep":
                        if sec == "other_person" and tbl_section == "applicant" and c0_cell["row"] == r:
                            head_restore = (r + max(1, c0_cell.get("row_span", 1)), tbl_section)
                        tbl_section = sec
            if c0_cell and max(1, c0_cell.get("row_span", 1)) >= 2:
                band_r0 = c0_cell["row"]
                first_row_texts = [
                    rc.get("text", "")
                    for rc in row_map.get(band_r0, [])
                    if rc["col"] > 0 and rc.get("text")
                ]
                if fm.is_deceased_band_first_row(first_row_texts):
                    tbl_section = "other_person"
            is_banner_row = (
                len(row_cells) <= 2
                or any(max(1, rc.get("col_span", 1)) >= max(3, int(max_c * 0.65)) for rc in row_cells)
            )
            if is_banner_row:
                row_joined = " ".join(rc.get("text", "") for rc in row_cells if rc.get("text"))
                if row_joined and len(row_joined) <= 120:
                    rj_norm = fm.normalize_label(row_joined)
                    if (
                        fm.ACCOUNT_SECTION_RE.match(rj_norm)
                        and tbl_section in ("other_person", "proxy")
                        and not fm.PROXY_SECTION_RE.search(row_joined)
                        and not fm.OTHER_PERSON_SECTION_RE.search(row_joined)
                    ):
                        tbl_section = "applicant"
                    else:
                        wsec = fm.detect_section_context(row_joined, current="keep")
                        if wsec != "keep":
                            tbl_section = wsec

            c0_norm = fm.normalize_label(c0_cell.get("text", "")) if c0_cell else ""
            row_only_excluded = bool(
                fm.EXCLUDED_ROW_HEAD_RE.match(c0_norm)
                or re.fullmatch(r"성명(?:여성|남성)", c0_norm)
            )

            has_mobile_in_band = any(
                ("휴대전화" in c2["text"] or "휴대폰" in c2["text"] or "핸드폰" in c2["text"] or "H․P" in c2["text"] or "HP" in c2["text"])
                for rr2 in range(max(0, r - 2), min(max_r, r + 3))
                for c2 in row_map.get(rr2, [])
            )

            for c in row_cells:
                # 1) 셀 내부 인라인 문단(날짜/서명/하위라벨/콜론줄)
                raw_text = c.get("text") or ""
                lines = raw_text.split("\n")

                local_sec = "other_person" if row_only_excluded else tbl_section
                # col 1..2 하위 구역 헤더(예: '신청인' 안의 '참전유공자'/'유족') 반영
                if not row_only_excluded:
                    for hcol in (0, 1, 2):
                        if hcol >= c["col"]:
                            break
                        hc = grid.get((r, hcol))
                        if hc and hc.get("text") and (hc["col"] <= 1 or hc.get("row_span", 1) >= 2):
                            s_sub = fm.detect_section_context(hc["text"], current="keep")
                            if s_sub != "keep":
                                local_sec = s_sub
                if not row_only_excluded and local_sec not in ("excluded", "other_person") and r >= 2 and c["col"] >= 4:
                    left_neighbor = grid.get((r, c["col"] - 1))
                    if left_neighbor and left_neighbor.get("text"):
                        s2 = fm.detect_section_context(left_neighbor["text"], current="keep")
                        if s2 != "keep":
                            local_sec = s2
                # 현행/변경(또는 기존/신규) 두 블록이면 현행/기존 쪽은 제외
                if tbl_has_old_new and local_sec not in ("excluded", "other_person"):
                    old_new_state = ""
                    for hcol in range(0, min(4, c["col"])):
                        hc = grid.get((r, hcol))
                        if hc and hc.get("text"):
                            hn = fm.normalize_label(hc["text"])
                            if fm.OLD_BLOCK_HEAD_RE.match(hn):
                                old_new_state = "old"
                            elif fm.NEW_BLOCK_HEAD_RE.match(hn):
                                old_new_state = "new"
                    if not old_new_state:
                        for ar in range(max(0, r - 2), r):
                            ac = grid.get((ar, c["col"]))
                            if ac and ac.get("text"):
                                an = fm.normalize_label(ac["text"])
                                if fm.OLD_BLOCK_HEAD_RE.match(an):
                                    old_new_state = "old"
                                elif fm.NEW_BLOCK_HEAD_RE.match(an):
                                    old_new_state = "new"
                    if old_new_state == "old":
                        local_sec = "excluded"

                # (a) 셀 내 인라인 날짜/서명/콜론 문단 확인
                has_inline_cand = any(
                    ("년" in ln or "서명" in ln or "202" in ln or ":" in ln or "㉿" in ln or "㊞" in ln or "\U000f012b" in ln)
                    for ln in lines
                )
                if has_inline_cand:
                    prev_row_txt = " ".join(
                        rc.get("text", "")
                        for pr in range(max(0, r - 2), r)
                        for rc in row_map.get(pr, [])
                        if rc.get("text")
                    )
                    left_same_row = grid.get((r, c["col"] - 1)) if c["col"] > 0 else None
                    left_same_txt = left_same_row.get("text", "") if left_same_row else ""
                    if local_sec in ("excluded", "other_person"):
                        if any(re.fullmatch(r"(?:20\s*)?년\s*[월윌]\s*일|(?:202|20)\s*\.\s*\.\s*\.?", re.sub(r"\s+", " ", ln).strip()) for ln in lines):
                            report["skipped_fields"].append({
                                "label": "년 월 일", "location": f"T{t_idx}({c['row']},{c['col']})",
                                "reason": "other_person" if local_sec == "other_person" else "excluded_section",
                            })
                    else:
                        para_aligns = c.get("para_aligns") or []
                        para_pts = c.get("para_pt100s") or []
                        para_ls_counts = c.get("para_line_seg_counts") or []
                        cell_inner_w = max(1, int(c.get("width", 42000) or 42000) - int(c.get("margin_left", 141) or 0) - int(c.get("margin_right", 141) or 0))
                        for l_idx, ln in enumerate(lines):
                            prev_ln_ctx = " ".join(lines[max(0, l_idx - 3):l_idx]) + " " + prev_row_txt
                            next_ln_ctx = " ".join(lines[l_idx + 1:min(len(lines), l_idx + 4)])
                            direct_parts = [
                                left_same_txt,
                                lines[l_idx - 1] if l_idx > 0 else "",
                                lines[l_idx + 1] if l_idx + 1 < len(lines) else "",
                            ]
                            p_align = str(para_aligns[l_idx]) if l_idx < len(para_aligns) else "Left"
                            p_pt = int(para_pts[l_idx]) if l_idx < len(para_pts) else int(c.get("font_pt100") or 1000)
                            p_ls_cnt = int(para_ls_counts[l_idx]) if l_idx < len(para_ls_counts) else 1
                            for ed in _plan_inline_lines(
                                ln, prev_ln_ctx, next_ln_ctx, prof,
                                f"T{t_idx}({c['row']},{c['col']}) p{l_idx}",
                                report,
                                section=local_sec,
                                direct_ctx=" ".join(direct_parts),
                                para_align=p_align,
                                avail_w_hwpunit=cell_inner_w,
                                font_pt100=p_pt,
                                char_space=int(c.get("char_space") or 0),
                                char_ratio=int(c.get("char_ratio") or 100),
                                line_seg_count=p_ls_cnt,
                            ):
                                if ed.get("field_key") == "apply_date":
                                    handled_date_cells.add((t_idx, c["cell_idx"]))
                                handled_inline_cells.add((t_idx, c["cell_idx"]))
                                k = (t_idx, c["cell_idx"], l_idx, ed["mode"], str(ed.get("anchor", "")))
                                if k not in filled_cell_ops:
                                    filled_cell_ops.add(k)
                                    cell_edits.append({
                                        "table_index": t_idx,
                                        "section": tbl.get("section", 0),
                                        "para": tbl.get("para", 0),
                                        "control": tbl.get("control", 0),
                                        "cell_idx": c["cell_idx"],
                                        "cell_para_idx": l_idx,
                                        "row": c["row"],
                                        "col": c["col"],
                                        **ed,
                                    })

                # (a2) 한 셀 안의 하위 라벨 줄('자택 :\n휴대폰 :' 등) 처리
                if local_sec in ("applicant", "proxy") and len(lines) >= 2:
                    key_p = "proxy.phone" if local_sec == "proxy" else "applicant.phone"
                    phone_val = (prof.get(key_p) or "").strip()
                    if phone_val and any(_SUBLABEL_PHONE_RE.match(ln) for ln in lines):
                        handled_inline_cells.add((t_idx, c["cell_idx"]))
                        for l_idx, ln in enumerate(lines):
                            if not _SUBLABEL_PHONE_RE.match(ln):
                                continue
                            ok_font, ov_sid, base_pt = _resolve_font_override(c, None, doc_dump, para_idx=l_idx)
                            if not ok_font:
                                report["skipped_fields"].append({
                                    "label": ln.strip(), "location": f"T{t_idx}({c['row']},{c['col']}) p{l_idx}", "reason": "tiny_font",
                                })
                                continue
                            combined = ln.rstrip() + " " + phone_val
                            fits, fit_pt, fit_sp, changed = _fit_cell_style(c, combined, key_p, ov_sid, base_pt)
                            if not fits:
                                report["skipped_fields"].append({
                                    "label": ln.strip(), "location": f"T{t_idx}({c['row']},{c['col']}) p{l_idx}", "reason": "no_room",
                                })
                                continue
                            k = (t_idx, c["cell_idx"], l_idx, "append", "")
                            if k not in filled_cell_ops:
                                filled_cell_ops.add(k)
                                payload_sub: dict[str, Any] = {
                                    "table_index": t_idx,
                                    "section": tbl.get("section", 0),
                                    "para": tbl.get("para", 0),
                                    "control": tbl.get("control", 0),
                                    "cell_idx": c["cell_idx"],
                                    "cell_para_idx": l_idx,
                                    "row": c["row"],
                                    "col": c["col"],
                                    "mode": "append",
                                    "orig_text": ln,
                                    "new_text": combined,
                                    "field_key": key_p,
                                    "value": phone_val,
                                }
                                if ov_sid >= 0:
                                    payload_sub["override_char_shape_id"] = ov_sid
                                if changed:
                                    payload_sub["fit_font_pt100"] = fit_pt
                                    payload_sub["fit_char_space"] = fit_sp
                                cell_edits.append(payload_sub)
                                report["filled_fields"].append({
                                    "label": ln.strip(),
                                    "location": f"T{t_idx}({c['row']},{c['col']}) p{l_idx}",
                                    "field_key": key_p,
                                    "value": phone_val,
                                    "section": local_sec,
                                })

                # (b) 셀 안에 '[핸드폰]' / '휴대전화' 단독 문단이 있는 경우 같은 줄 끝에 번호 추가 (오른쪽 빈 값 셀이 있으면 건너뜀)
                rs_c, cs_c = max(1, c.get("row_span", 1)), max(1, c.get("col_span", 1))
                right_neighbor_cell = grid.get((c["row"], c["col"] + cs_c))
                if (
                    local_sec not in ("excluded", "other_person")
                    and len(lines) == 1
                    and not (right_neighbor_cell is not None and fm.is_cell_empty(right_neighbor_cell.get("text", "")))
                ):
                    s_clean = re.sub(r"\s+", "", raw_text)
                    if s_clean in ("[핸드폰]", "휴대전화", "휴대폰"):
                        key_p = "proxy.phone" if local_sec == "proxy" else "applicant.phone"
                        phone_val = (prof.get(key_p) or "").strip()
                        if not phone_val:
                            continue
                        k = (t_idx, c["cell_idx"], 0, "append", "")
                        if k not in filled_cell_ops:
                            filled_cell_ops.add(k)
                            cell_edits.append({
                                "table_index": t_idx,
                                "section": tbl.get("section", 0),
                                "para": tbl.get("para", 0),
                                "control": tbl.get("control", 0),
                                "cell_idx": c["cell_idx"],
                                "cell_para_idx": 0,
                                "row": c["row"],
                                "col": c["col"],
                                "mode": "append",
                                "orig_text": raw_text,
                                "new_text": raw_text.rstrip() + " " + phone_val,
                                "field_key": key_p,
                                "value": phone_val,
                            })
                            report["filled_fields"].append({
                                "label": raw_text.strip(),
                                "location": f"T{t_idx}({c['row']},{c['col']})",
                                "field_key": key_p,
                                "value": phone_val,
                                "section": local_sec,
                            })
                        continue

                # (c) 셀 위쪽 문단에 라벨('금융기관명', '신청인 명의의 계좌번호')이 있고 아래 문단(p1+)이 비어 있는 경우
                if (
                    local_sec not in ("excluded", "other_person")
                    and len(lines) >= 2
                    and lines[0].strip()
                    and all(not ln.strip() for ln in lines[1:])
                    and right_neighbor_cell is None
                ):
                    dec_top = fm.match_label(lines[0].strip(), section=local_sec, profile=prof)
                    if dec_top.action == "fill":
                        ok_font, ov_sid, base_pt = _resolve_font_override(c, c, doc_dump, para_idx=1)
                        if not ok_font:
                            report["skipped_fields"].append({
                                "label": lines[0].strip(), "location": f"T{t_idx}({c['row']},{c['col']}) p1", "reason": "tiny_font",
                            })
                            continue
                        fits, fit_pt, fit_sp, changed = _fit_cell_style(c, dec_top.value or "", dec_top.field_key, ov_sid, base_pt)
                        if not fits:
                            report["skipped_fields"].append({
                                "label": lines[0].strip(), "location": f"T{t_idx}({c['row']},{c['col']}) p1", "reason": "no_room",
                            })
                            continue
                        k = (t_idx, c["cell_idx"], 1, "set", "")
                        if k not in filled_cell_ops:
                            filled_cell_ops.add(k)
                            ed_top: dict[str, Any] = {
                                "table_index": t_idx,
                                "section": tbl.get("section", 0),
                                "para": tbl.get("para", 0),
                                "control": tbl.get("control", 0),
                                "cell_idx": c["cell_idx"],
                                "cell_para_idx": 1,
                                "row": c["row"],
                                "col": c["col"],
                                "mode": "set",
                                "orig_text": lines[1],
                                "new_text": dec_top.value or "",
                                "field_key": dec_top.field_key,
                                "value": dec_top.value,
                            }
                            if ov_sid >= 0:
                                ed_top["override_char_shape_id"] = ov_sid
                            if changed:
                                ed_top["fit_font_pt100"] = fit_pt
                                ed_top["fit_char_space"] = fit_sp
                            cell_edits.append(ed_top)
                            report["filled_fields"].append({
                                "label": lines[0].strip(),
                                "location": f"T{t_idx}({c['row']},{c['col']}) p1",
                                "field_key": dec_top.field_key,
                                "value": dec_top.value,
                                "section": dec_top.section,
                            })
                        continue

                # (d) 셀 내 '(전화번호 :       )' / '휴대전화번호:' 인라인 삽입 (1줄 합체 주소+전화 셀은 아래 (2)에서 함께 처리)
                is_single_line_paren_phone = bool(
                    len(lines) == 1
                    and re.fullmatch(r"[ \t\u00a0\u3000]*\(전화(?:번호)?\s*:[^)]*\)\s*", re.sub(r"[ \t]{2,}", " ", raw_text))
                )
                if local_sec in ("applicant", "proxy") and not is_single_line_paren_phone:
                    key_ph = "proxy.phone" if local_sec == "proxy" else "applicant.phone"
                    app_phone = (prof.get(key_ph) or "").strip()
                    for l_idx, ln in enumerate(lines):
                        if not app_phone:
                            break
                        if "휴대전화번호:" in ln:
                            k = (t_idx, c["cell_idx"], l_idx, "after_anchor", "휴대전화번호:")
                            if k not in filled_cell_ops:
                                m_hp = re.search(r"(휴대전화번호\s*:)([ \t\u00a0\u3000_]+)", ln)
                                if m_hp:
                                    need_ph_w = 1 + fm.display_width_spaces(app_phone) + 1
                                    if fm.slot_width_spaces(m_hp.group(2)) < need_ph_w:
                                        report["skipped_fields"].append({
                                            "label": "휴대전화번호:",
                                            "location": f"T{t_idx}({c['row']},{c['col']}) p{l_idx}",
                                            "reason": "no_room",
                                        })
                                        continue
                                    filled_cell_ops.add(k)
                                    cell_edits.append({
                                        "table_index": t_idx,
                                        "section": tbl.get("section", 0),
                                        "para": tbl.get("para", 0),
                                        "control": tbl.get("control", 0),
                                        "cell_idx": c["cell_idx"],
                                        "cell_para_idx": l_idx,
                                        "row": c["row"],
                                        "col": c["col"],
                                        "mode": "after_anchor",
                                        "anchor": "휴대전화번호:",
                                        "orig_text": ln,
                                        "new_text": ln[:m_hp.end(1)] + " " + app_phone + ln[m_hp.end(1):],
                                        "field_key": key_ph,
                                        "value": app_phone,
                                    })
                                    report["filled_fields"].append({
                                        "label": "휴대전화번호:",
                                        "location": f"T{t_idx}({c['row']},{c['col']}) p{l_idx}",
                                        "field_key": key_ph,
                                        "value": app_phone,
                                        "section": local_sec,
                                    })
                        elif local_sec == "applicant" and re.search(r"\(전화(?:번호)?\s*:\s*\)", re.sub(r"\s{2,}", " ", ln)) and "휴대전화" not in ln:
                            m_ph = re.search(r"(\(전화(?:번호)?\s*:)([ \t\u00a0\u3000_]+)\)", ln)
                            if m_ph:
                                k = (t_idx, c["cell_idx"], l_idx, "after_anchor", m_ph.group(1))
                                if k in filled_cell_ops:
                                    continue
                                has_geom = int(c.get("width", 0) or 0) > 0
                                need_ph_w = 6 if has_geom else (1 + fm.display_width_spaces(app_phone) + 1)
                                if fm.slot_width_spaces(m_ph.group(2)) < need_ph_w:
                                    report["skipped_fields"].append({
                                        "label": "(전화번호 : )",
                                        "location": f"T{t_idx}({c['row']},{c['col']}) p{l_idx}",
                                        "reason": "no_room",
                                    })
                                    continue
                                sim_ln = ln[:m_ph.end(1)] + " " + app_phone + " )"
                                ok_font, ov_sid, base_pt = _resolve_font_override(c, None, doc_dump, para_idx=l_idx)
                                fits, fit_pt, fit_sp, changed = _fit_cell_style(c, sim_ln, key_ph, ov_sid, base_pt) if ok_font else (False, base_pt, 0, False)
                                if not ok_font or not fits:
                                    report["skipped_fields"].append({
                                        "label": "(전화번호 : )",
                                        "location": f"T{t_idx}({c['row']},{c['col']}) p{l_idx}",
                                        "reason": "tiny_font" if not ok_font else "no_room",
                                    })
                                    continue
                                filled_cell_ops.add(k)
                                ed_p_in: dict[str, Any] = {
                                    "table_index": t_idx,
                                    "section": tbl.get("section", 0),
                                    "para": tbl.get("para", 0),
                                    "control": tbl.get("control", 0),
                                    "cell_idx": c["cell_idx"],
                                    "cell_para_idx": l_idx,
                                    "row": c["row"],
                                    "col": c["col"],
                                    "mode": "after_anchor",
                                    "anchor": m_ph.group(1),
                                    "orig_text": ln,
                                    "new_text": sim_ln,
                                    "field_key": key_ph,
                                    "value": app_phone,
                                }
                                if ov_sid >= 0:
                                    ed_p_in["override_char_shape_id"] = ov_sid
                                if changed:
                                    ed_p_in["fit_font_pt100"] = fit_pt
                                    ed_p_in["fit_char_space"] = fit_sp
                                cell_edits.append(ed_p_in)
                                report["filled_fields"].append({
                                    "label": "(전화번호 : )",
                                    "location": f"T{t_idx}({c['row']},{c['col']}) p{l_idx}",
                                    "field_key": key_ph,
                                    "value": app_phone,
                                    "section": local_sec,
                                })

                # 2) 일반 표 라벨 -> 오른쪽 인접 셀 또는 헤더행 아래 셀
                if (t_idx, c["cell_idx"]) in handled_inline_cells:
                    continue
                if re.fullmatch(r"\s*\(전화(?:번호)?\s*:[^)]*\)\s*", re.sub(r"\s{2,}", " ", raw_text)):
                    handled_inline_cells.add((t_idx, c["cell_idx"]))
                    continue

                txt = raw_text.strip()
                if not txt or len(txt) > 60:
                    continue
                if (t_idx, c["cell_idx"]) in handled_date_cells and re.fullmatch(r"(?:20\s*)?년\s*[월윌]\s*일", re.sub(r"\s+", " ", txt)):
                    continue

                dec = fm.match_label(txt, section=local_sec, profile=prof)
                if dec.action == "skip" and dec.reason in ("empty_or_too_long_label", "unmatched_label"):
                    continue
                if dec.field_key in ("applicant.phone", "proxy.phone") and fm.normalize_label(txt) in ("전화번호", "집") and has_mobile_in_band:
                    report["skipped_fields"].append({
                        "label": txt.replace("\n", " "), "location": f"T{t_idx}({c['row']},{c['col']})",
                        "reason": "separate_mobile_field_exists",
                    })
                    continue

                report["found_labels"].append({
                    "label": txt.replace("\n", " "), "location": f"T{t_idx}({c['row']},{c['col']})",
                    "section": dec.section, "decision": dec.action,
                })
                if dec.action == "sensitive_blank":
                    report["sensitive_blank_fields"].append({
                        "label": txt.replace("\n", " "), "location": f"T{t_idx}({c['row']},{c['col']})",
                        "reason": dec.reason,
                    })
                    continue
                if dec.action == "skip":
                    report["skipped_fields"].append({
                        "label": txt.replace("\n", " "), "location": f"T{t_idx}({c['row']},{c['col']})",
                        "reason": dec.reason,
                    })
                    continue

                rs, cs = max(1, c.get("row_span", 1)), max(1, c.get("col_span", 1))
                right_c = grid.get((c["row"], c["col"] + cs))
                target_c = None
                target_para_idx = 0
                edit_mode = "set"
                template_text = ""
                extra_phone_anchor: tuple[str, str] | None = None
                if right_c is not None:
                    r_text = right_c.get("text", "")
                    if fm.is_cell_empty(r_text):
                        target_c = right_c
                    elif dec.field_key in ("applicant.rrn", "proxy.rrn") and re.fullmatch(r"([ \t\u00a0\u3000_]*)\(만\s*세\)\s*", r_text):
                        lead_rrn = re.match(r"^([ \t\u00a0\u3000_]*)", r_text).group(1)
                        if fm.slot_width_spaces(lead_rrn) < fm.display_width_spaces(dec.value or "") + 1:
                            report["skipped_fields"].append({
                                "label": txt.replace("\n", " "), "location": f"T{t_idx}({c['row']},{c['col']})",
                                "reason": "no_room",
                            })
                            continue
                        target_c = right_c
                        target_para_idx = 0
                        edit_mode = "prepend_trim_spaces"
                    elif dec.field_key == "applicant.address":
                        # 첫 문단이 비어 있고 아래 문단에 (전화번호: ...)가 있는 주소+전화 통합 셀
                        p_idx0 = _can_fill_address_cell_first_line(r_text)
                        if p_idx0 is not None:
                            target_c = right_c
                            target_para_idx = p_idx0
                        else:
                            r_lines = r_text.split("\n")
                            m_par = re.search(r"^\s*(\(전화(?:번호)?\s*:)([ \t\u00a0\u3000_]*)\)\s*$", r_lines[0]) if len(r_lines) == 1 else None
                            if m_par:
                                lead_addr = re.match(r"^([ \t\u00a0\u3000_]*)", r_lines[0]).group(1)
                                lead_w = fm.slot_width_spaces(lead_addr)
                                app_phone = (prof.get("applicant.phone") or "").strip()
                                ph_slot_w = fm.slot_width_spaces(m_par.group(2))
                                can_ph = bool(app_phone and ph_slot_w >= 6)
                                sim_line = (dec.value or "") + " " + (f"{m_par.group(1)} {app_phone} )" if can_ph else r_lines[0].strip())
                                # 좁은 인라인 공백뿐이거나 셀 폭 자체가 없으면 거부; 셀 폭이 주어지면 fit_text_to_cell로 판정
                                has_geom = int(right_c.get("width", 0) or 0) > 0
                                if lead_w > 0 and not has_geom and lead_w < fm.display_width_spaces(dec.value or "") + 1:
                                    report["skipped_fields"].append({
                                        "label": txt.replace("\n", " "), "location": f"T{t_idx}({c['row']},{c['col']})",
                                        "reason": "no_room",
                                    })
                                    continue
                                target_c = right_c
                                target_para_idx = 0
                                edit_mode = "prepend_trim_spaces"
                                if can_ph:
                                    extra_phone_anchor = (m_par.group(1), app_phone)
                                handled_inline_cells.add((t_idx, right_c["cell_idx"]))
                if target_c is None and right_c is not None:
                    # 값 칸에 '(남, 여)'·'(현 거주지)'·'☎' 같은 안내만 있으면 안내를 남기고 값을 넣는다.
                    tmode = fm.template_fill_mode(dec.field_key, right_c.get("text", ""))
                    if tmode:
                        target_c = right_c
                        edit_mode = "prepend_trim_spaces" if tmode == "prepend" else "append"
                        template_text = right_c.get("text", "").strip()
                if target_c is None and c["row"] + rs < max_r:
                    below_c = grid.get((c["row"] + rs, c["col"]))
                    if (
                        below_c is not None
                        and below_c["col"] == c["col"]
                        and below_c.get("col_span", 1) == cs
                        and fm.is_cell_empty(below_c.get("text", ""))
                    ):
                        # 바로 아래 행 왼쪽에 자체 라벨 셀이 있거나, 위/행 머리가 타인 구역이거나, 동일 열에 빈 행이 반복(명부형 표)되면 건너뜀
                        above_h = grid.get((c["row"] - 1, c["col"])) if c["row"] > 0 else None
                        above_sec = fm.detect_section_context(above_h.get("text", ""), current="keep") if above_h and above_h.get("text") else "keep"
                        left_b = grid.get((below_c["row"], below_c["col"] - 1)) if below_c["col"] > 0 else None
                        left_b_busy = bool(
                            left_b is not None
                            and max(1, left_b.get("row_span", 1)) == 1
                            and not fm.is_cell_empty(left_b.get("text", ""))
                        )
                        rh_below = grid.get((below_c["row"], 0))
                        rh_norm = fm.normalize_label(rh_below.get("text", "")) if rh_below else ""
                        rh_sec = fm.detect_section_context(rh_below.get("text", ""), current="keep") if rh_below and rh_below.get("text") else "keep"
                        next_rep_row = below_c["row"] + max(1, below_c.get("row_span", 1))
                        next_rep = grid.get((next_rep_row, below_c["col"])) if next_rep_row < max_r else None
                        is_multi_row_roster = bool(
                            next_rep is not None
                            and next_rep["col"] == below_c["col"]
                            and next_rep.get("col_span", 1) == cs
                            and fm.is_cell_empty(next_rep.get("text", ""))
                        )
                        if left_b_busy:
                            below_c = None
                        elif above_sec in ("excluded", "other_person") or rh_sec in ("excluded", "other_person") or fm.EXCLUDED_ROW_HEAD_RE.match(rh_norm):
                            report["skipped_fields"].append({
                                "label": txt.replace("\n", " "), "location": f"T{t_idx}({below_c['row']},{below_c['col']})",
                                "reason": "other_person" if (above_sec == "other_person" or rh_sec == "other_person" or fm.EXCLUDED_ROW_HEAD_RE.match(rh_norm)) else "excluded_section",
                            })
                            continue
                        elif is_multi_row_roster:
                            report["skipped_fields"].append({
                                "label": txt.replace("\n", " "), "location": f"T{t_idx}({below_c['row']},{below_c['col']})",
                                "reason": "other_person" if rh_sec == "other_person" else "excluded_section",
                            })
                            continue
                        if below_c is not None:
                            target_c = below_c
                if (
                    target_c is None
                    and local_sec not in ("excluded", "other_person")
                    and len(lines) == 1
                    and fm.can_write_after_label(dec.field_key, txt, right_c.get("text", "") if right_c is not None else None)
                    and not fm.in_cell_blocked(txt, c["row"], c["col"], [
                        (x["row"], x["col"], x.get("row_span", 1), x.get("text", "")) for x in cells])
                ):
                    target_c = c
                    target_para_idx = 0
                    edit_mode = "append"
                    template_text = txt.strip() + " "
                if (
                    target_c is None
                    and local_sec not in ("excluded", "other_person")
                    and rs >= 2
                    and len(lines) == 1
                    and right_c is None
                    and dec.field_key in ("account.bank", "account.number", "account.holder", "account.full")
                ):
                    target_c = c
                    target_para_idx = 1
                    edit_mode = "add_para"

                if target_c is not None and fm.parent_rows(c["row"], c["col"], [
                        (x["row"], x["col"], x.get("row_span", 1), x.get("text", "")) for x in cells]):
                    report["skipped_fields"].append({
                        "label": txt.replace("\n", " "), "location": f"T{t_idx}({c['row']},{c['col']})",
                        "reason": "parent_rows",
                    })
                    continue
                if target_c is None:
                    report["skipped_fields"].append({
                        "label": txt.replace("\n", " "), "location": f"T{t_idx}({c['row']},{c['col']})",
                        "reason": "non_empty_adjacent_cell" if right_c is not None else "no_adjacent_target_cell",
                        "adjacent": (right_c.get("text", "") if right_c is not None else "")[:40],
                    })
                    continue

                ok_font, override_sid, base_pt = _resolve_font_override(target_c, c, doc_dump, para_idx=target_para_idx)
                if not ok_font:
                    report["skipped_fields"].append({
                        "label": txt.replace("\n", " "),
                        "location": f"T{t_idx}({target_c['row']},{target_c['col']})",
                        "reason": "tiny_font",
                    })
                    continue

                fit_sample_text = dec.value or ""
                if template_text:
                    fit_sample_text = (f"{fit_sample_text}   {template_text}" if edit_mode == "prepend_trim_spaces"
                                       else f"{template_text} {fit_sample_text}")
                if extra_phone_anchor is not None:
                    fit_sample_text = f"{dec.value or ''} {extra_phone_anchor[0]} {extra_phone_anchor[1]} )"
                fits, fit_pt, fit_sp, changed = _fit_cell_style(
                    target_c, fit_sample_text, dec.field_key, override_sid, base_pt
                )
                if not fits:
                    report["skipped_fields"].append({
                        "label": txt.replace("\n", " "),
                        "location": f"T{t_idx}({target_c['row']},{target_c['col']})",
                        "reason": "no_room",
                    })
                    continue

                key = (t_idx, target_c["cell_idx"], target_para_idx, edit_mode, "")
                if key in filled_cell_ops:
                    continue
                filled_cell_ops.add(key)
                ed_payload: dict[str, Any] = {
                    "table_index": t_idx,
                    "section": tbl.get("section", 0),
                    "para": tbl.get("para", 0),
                    "control": tbl.get("control", 0),
                    "cell_idx": target_c["cell_idx"],
                    "cell_para_idx": target_para_idx,
                    "row": target_c["row"],
                    "col": target_c["col"],
                    "mode": edit_mode,
                    "orig_text": "",
                    "new_text": dec.value or "",
                    "field_key": dec.field_key,
                    "value": dec.value,
                    "label": txt.replace("\n", " "),
                }
                if override_sid >= 0:
                    ed_payload["override_char_shape_id"] = override_sid
                if changed:
                    ed_payload["fit_font_pt100"] = fit_pt
                    ed_payload["fit_char_space"] = fit_sp
                cell_edits.append(ed_payload)
                report["filled_fields"].append({
                    "label": txt.replace("\n", " "),
                    "location": f"T{t_idx}({target_c['row']},{target_c['col']}) p{target_para_idx}",
                    "field_key": dec.field_key,
                    "value": dec.value,
                    "section": dec.section,
                })
                if extra_phone_anchor is not None:
                    p_anchor, p_val = extra_phone_anchor
                    k_ph = (t_idx, target_c["cell_idx"], target_para_idx, "after_anchor", p_anchor)
                    if k_ph not in filled_cell_ops:
                        filled_cell_ops.add(k_ph)
                        ed_ph: dict[str, Any] = {
                            "table_index": t_idx,
                            "section": tbl.get("section", 0),
                            "para": tbl.get("para", 0),
                            "control": tbl.get("control", 0),
                            "cell_idx": target_c["cell_idx"],
                            "cell_para_idx": target_para_idx,
                            "row": target_c["row"],
                            "col": target_c["col"],
                            "mode": "after_anchor",
                            "anchor": p_anchor,
                            "orig_text": target_c.get("text", ""),
                            "new_text": p_val,
                            "field_key": "applicant.phone",
                            "value": p_val,
                        }
                        if override_sid >= 0:
                            ed_ph["override_char_shape_id"] = override_sid
                        if changed:
                            ed_ph["fit_font_pt100"] = fit_pt
                            ed_ph["fit_char_space"] = fit_sp
                        cell_edits.append(ed_ph)
                        report["filled_fields"].append({
                            "label": p_anchor,
                            "location": f"T{t_idx}({target_c['row']},{target_c['col']}) p{target_para_idx}",
                            "field_key": "applicant.phone",
                            "value": p_val,
                            "section": "applicant",
                        })

    # 부·모처럼 나뉜 줄의 칸은 어느 경로로 계획됐든 빼고 직접 작성으로 남긴다.
    tables = doc_dump.get("tables", [])

    def _in_parent_rows(t_idx: int, row: int, col: int) -> bool:
        if not 0 <= t_idx < len(tables):
            return False
        return fm.parent_rows(row, col, [(x["row"], x["col"], x.get("row_span", 1), x.get("text", ""))
                                         for x in tables[t_idx].get("cells", [])])

    def _band_head(t_idx: int, row: int, col: int) -> dict | None:
        cells = tables[t_idx].get("cells", []) if 0 <= t_idx < len(tables) else []
        return next((x for x in cells if x["col"] == 0 and x["col"] < col
                     and x["row"] <= row < x["row"] + max(1, x.get("row_span", 1))), None)

    def _roster_row(t_idx: int, row: int, col: int) -> bool:
        cells = tables[t_idx].get("cells", []) if 0 <= t_idx < len(tables) else []
        return any(x["col"] < col and x["row"] <= row < x["row"] + max(1, x.get("row_span", 1))
                   and re.fullmatch(r"연번|순번|번호|no\.?", fm.normalize_label(x.get("text", "")), re.I) for x in cells)

    dropped: dict[tuple[int, int, int], str] = {}
    for e in cell_edits:
        pos = (e["table_index"], e["row"], e["col"])
        if _in_parent_rows(*pos):
            dropped[pos] = "parent_rows"
        elif _roster_row(*pos):
            dropped[pos] = "roster_row"
    # 같은 구역 머리(예: '신청인(농업인)') 안에서 같은 항목이 또 나오면 여러 사람 칸이다. 첫 칸만 남긴다.
    seen_band: set[tuple[int, str, int]] = set()
    for e in cell_edits:
        head = _band_head(e["table_index"], e["row"], e["col"])
        # 연락처(집·휴대폰)와 주소(주민등록지·실거주지)는 한 사람에게 칸이 둘인 경우가 많아 제외한다.
        if (head is None or not str(e.get("field_key", "")).startswith("applicant.")
                or e.get("field_key") in ("applicant.phone", "applicant.address") or e.get("mode") == "after_anchor"):
            continue
        key = (e["table_index"], e["field_key"], head["cell_idx"])
        if key in seen_band:
            dropped.setdefault((e["table_index"], e["row"], e["col"]), "repeated_in_band")
        seen_band.add(key)
    if dropped:
        cell_edits[:] = [e for e in cell_edits if (e["table_index"], e["row"], e["col"]) not in dropped]
        kept = []
        for f in report["filled_fields"]:
            m = re.match(r"T(\d+)\((\d+),(\d+)\)", f.get("location", ""))
            if m and tuple(map(int, m.groups())) in dropped:
                report["skipped_fields"].append({"label": f.get("label", ""), "location": f["location"],
                                                 "reason": dropped[tuple(map(int, m.groups()))]})
            else:
                kept.append(f)
        report["filled_fields"] = kept
    report["filled_count"] = len(report["filled_fields"])
    return {"cell_edits": cell_edits, "para_edits": para_edits, "report": report}
