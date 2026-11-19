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


def _plan_inline_line(
    raw: str,
    prev_ctx: str,
    next_ctx: str,
    prof: dict[str, str],
    location: str,
    report: dict[str, Any],
    section: str = "applicant",
    direct_ctx: str = "",
) -> dict[str, Any] | None:
    stripped = re.sub(r"\s+", " ", raw).strip()
    if not stripped:
        return None

    # 1) 신청 날짜 인라인: '년   월   일'
    if re.fullmatch(r"(?:20\s*)?년\s*월\s*일", stripped):
        bad_target = direct_ctx or prev_ctx
        if _NON_APP_DATE_RE.search(bad_target):
            report["skipped_fields"].append({"label": stripped, "location": location, "reason": "non_application_date_context"})
            return None
        if not any(ok in (prev_ctx + " " + next_ctx) for ok in ("신청", "동의", "확인", "신고", "서명")):
            report["skipped_fields"].append({"label": stripped, "location": location, "reason": "unverified_date_context"})
            return None
        m_20 = re.match(r"^(\s*20)(\s+)(년)(\s+)(월)(\s+)(일\s*)$", raw)
        m_plain = re.match(r"^(\s*)(년)(\s+)(월)(\s+)(일\s*)$", raw)
        date_parts = re.findall(r"\d+", prof.get("apply_date") or "")
        if len(date_parts) < 3:
            return None
        yr, mo, dy = date_parts[:3]
        apply_date_val = (prof.get("apply_date") or f"{yr}년 {mo}월 {dy}일").strip()
        if m_20:
            yr2 = yr[-2:]
            lead20, sp_20y, s_y, sp_ym, s_m, sp_md, s_d = m_20.groups()
            sp_20y2 = " " * max(1, len(sp_20y) - len(yr2))
            sp_ym2 = (" " + mo + " " * max(1, len(sp_ym) - len(mo) - 1)) if len(sp_ym) >= len(mo) + 2 else (" " + mo + " ")
            sp_md2 = (" " + dy + " " * max(1, len(sp_md) - len(dy) - 1)) if len(sp_md) >= len(dy) + 2 else (" " + dy + " ")
            new_text = f"{lead20}{yr2}{sp_20y2}{s_y}{sp_ym2}{s_m}{sp_md2}{s_d}"
            report["filled_fields"].append({
                "label": stripped, "location": location, "field_key": "apply_date",
                "value": apply_date_val, "section": "applicant",
            })
            return {"mode": "date20", "orig_text": raw, "new_text": new_text, "field_key": "apply_date", "value": apply_date_val}
        if m_plain:
            lead, s_y, sp_ym, s_m, sp_md, s_d = m_plain.groups()
            trim = min(max(0, len(lead) - 2), len(yr) + len(mo) + len(dy) + 2)
            lead2 = lead[:-trim] if trim else lead
            sp_ym2 = (" " + mo + " " * max(1, len(sp_ym) - len(mo) - 1)) if len(sp_ym) >= len(mo) + 2 else (" " + mo + " ")
            sp_md2 = (" " + dy + " " * max(1, len(sp_md) - len(dy) - 1)) if len(sp_md) >= len(dy) + 2 else (" " + dy + " ")
            new_text = f"{lead2}{yr}{s_y}{sp_ym2}{s_m}{sp_md2}{s_d}"
            report["filled_fields"].append({
                "label": stripped, "location": location, "field_key": "apply_date",
                "value": apply_date_val, "section": "applicant",
            })
            return {"mode": "date", "orig_text": raw, "new_text": new_text, "field_key": "apply_date", "value": apply_date_val}
        return None

    # 2) 인라인 성명 + 서명 줄 ('신청인 :     (서명 또는 인)' / '성 명:    (서명 또는 인)' / '신청인      (서명 또는 인)')
    m_sig = re.match(
        r"^(\s*(?:신\s*청\s*인|보험가입신고\(신청\)인|성\s*명|이\s*름)(?:\([^)]*\))*\s*:?)(\s{6,})(\(서명(?:\s*또는\s*(?:인|날인))?\)\s*)$",
        raw,
    )
    if m_sig:
        pre, mid_ws, suf = m_sig.groups()
        name = (prof.get("applicant.name") or "").strip()
        if name and len(mid_ws) >= len(name) + 6:
            rem = len(mid_ws) - len(name) - 1
            new_text = f"{pre} {name}{' ' * rem}{suf}"
            report["filled_fields"].append({
                "label": pre.strip(), "location": location, "field_key": "applicant.name",
                "value": name, "section": "applicant",
            })
            report["sensitive_blank_fields"].append({
                "label": suf.strip(), "location": location, "reason": "signature_kept_blank",
            })
            return {"mode": "after_anchor", "anchor": pre, "orig_text": raw, "new_text": new_text, "field_key": "applicant.name", "value": name}
    return None


def _can_fill_address_cell_first_line(cell_text: str) -> int | None:
    """첫 문단이 비어 있고 아래 문단에 `(전화번호: ...)`가 있는 셀이면 0번 문단 인덱스를 반환."""
    lines = cell_text.split("\n")
    if len(lines) >= 2 and not lines[0].strip() and any("전화" in ln for ln in lines[1:]):
        return 0
    return None


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

    for idx, p in enumerate(paras):
        prev_chunks: list[str] = []
        for pj in range(max(0, idx - 3), idx):
            ptxt = paras[pj].get("text", "").strip()
            if ptxt:
                prev_chunks.append(ptxt)
            prev_chunks.extend(tables_by_para.get((paras[pj]["section"], paras[pj]["para"]), []))
        next_chunks = [paras[pj].get("text", "").strip() for pj in range(idx + 1, min(len(paras), idx + 4)) if paras[pj].get("text", "").strip()]
        ed = _plan_inline_line(p["text"], " ".join(prev_chunks), " ".join(next_chunks), prof, f"body s{p['section']} p{p['para']}", report)
        if ed:
            para_edits.append({"section": p["section"], "para": p["para"], **ed})

    filled_cell_ops: set[tuple[int, int, int, str]] = set()
    handled_date_cells: set[tuple[int, int]] = set()
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

        tbl_section = "applicant"
        for r in range(max_r):
            row_cells = sorted(row_map.get(r, []), key=lambda x: x["col"])
            # 왼쪽 병합 구역 헤더(col 0~2 중 span이 크거나 0열) 확인
            c0_cell = grid.get((r, 0))
            if c0_cell and c0_cell.get("text"):
                sec = fm.detect_section_context(c0_cell["text"], current="keep")
                if sec != "keep":
                    tbl_section = sec
            if len(row_cells) <= 2:
                sec = fm.detect_section_context(" ".join(c["text"] for c in row_cells if c["text"]), current="keep")
                if sec != "keep":
                    tbl_section = sec

            has_mobile_in_band = any(
                ("휴대전화" in c2["text"] or "휴대폰" in c2["text"] or "핸드폰" in c2["text"])
                for rr2 in range(max(0, r - 2), min(max_r, r + 3))
                for c2 in row_map.get(rr2, [])
            )

            for c in row_cells:
                # 1) 셀 내부 인라인 문단(날짜/서명) 및 특수 인라인 패턴('[핸드폰]', '휴대전화', '(전화번호: )', '금융기관명\n') 처리
                raw_text = c.get("text") or ""
                lines = raw_text.split("\n")

                # 구역이 셀 내부에서 바뀌는 경우도 반영 (예: WLF00000041_1 T0(3,6) '신고인(신청인)')
                local_sec = tbl_section
                if r >= 2 and c["col"] >= 4:
                    left_neighbor = grid.get((r, c["col"] - 1))
                    if left_neighbor and left_neighbor.get("text"):
                        s2 = fm.detect_section_context(left_neighbor["text"], current="keep")
                        if s2 != "keep":
                            local_sec = s2

                # (a) 셀 내 인라인 날짜/서명 문단 확인 (인접 비공백 줄 및 인접 행 문맥 포함)
                if any("년" in ln or "서명" in ln for ln in lines):
                    prev_row_txt = " ".join(
                        rc.get("text", "")
                        for pr in range(max(0, r - 2), r)
                        for rc in row_map.get(pr, [])
                        if rc.get("text")
                    )
                    next_row_txt = " ".join(
                        rc.get("text", "")
                        for nr in range(r + 1, min(max_r, r + 3))
                        for rc in row_map.get(nr, [])
                        if rc.get("text")
                    )
                    left_same_row = grid.get((r, c["col"] - 1)) if c["col"] > 0 else None
                    left_same_txt = left_same_row.get("text", "") if left_same_row else ""
                    for l_idx, ln in enumerate(lines):
                        prev_ln_ctx = " ".join(lines[max(0, l_idx - 3):l_idx]) + " " + prev_row_txt
                        next_ln_ctx = " ".join(lines[l_idx + 1:min(len(lines), l_idx + 4)]) + " " + next_row_txt
                        direct_parts = [
                            left_same_txt,
                            lines[l_idx - 1] if l_idx > 0 else "",
                            lines[l_idx + 1] if l_idx + 1 < len(lines) else "",
                        ]
                        ed = _plan_inline_line(
                            ln, prev_ln_ctx, next_ln_ctx, prof,
                            f"T{t_idx}({c['row']},{c['col']}) p{l_idx}",
                            report,
                            direct_ctx=" ".join(direct_parts),
                        )
                        if ed:
                            if ed.get("field_key") == "apply_date":
                                handled_date_cells.add((t_idx, c["cell_idx"]))
                            k = (t_idx, c["cell_idx"], l_idx, ed["mode"])
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

                # (b) 셀 안에 '[핸드폰]' / '휴대전화' 단독 문단이 있는 경우 같은 줄 끝에 번호 추가 (오른쪽 빈 값 셀이 있으면 건너뜀)
                rs_c, cs_c = max(1, c.get("row_span", 1)), max(1, c.get("col_span", 1))
                right_neighbor_cell = grid.get((c["row"], c["col"] + cs_c))
                if (
                    local_sec != "excluded"
                    and len(lines) == 1
                    and not (right_neighbor_cell is not None and fm.is_cell_empty(right_neighbor_cell.get("text", "")))
                ):
                    s_clean = re.sub(r"\s+", "", raw_text)
                    if s_clean in ("[핸드폰]", "휴대전화", "휴대폰"):
                        key_p = "proxy.phone" if local_sec == "proxy" else "applicant.phone"
                        phone_val = (prof.get(key_p) or "").strip()
                        if not phone_val:
                            continue
                        k = (t_idx, c["cell_idx"], 0, "append")
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
                # 단, 오른쪽에 인접 셀이 없는(또는 오른쪽이 비어있지 않은) 1칸 통합형 라벨+값 셀일 때만 적용!
                if (
                    local_sec != "excluded"
                    and len(lines) >= 2
                    and lines[0].strip()
                    and all(not ln.strip() for ln in lines[1:])
                    and right_neighbor_cell is None
                ):
                    dec_top = fm.match_label(lines[0].strip(), section=local_sec, profile=prof)
                    if dec_top.action == "fill":
                        k = (t_idx, c["cell_idx"], 1, "set")
                        if k not in filled_cell_ops:
                            filled_cell_ops.add(k)
                            cell_edits.append({
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
                            })
                            report["filled_fields"].append({
                                "label": lines[0].strip(),
                                "location": f"T{t_idx}({c['row']},{c['col']}) p1",
                                "field_key": dec_top.field_key,
                                "value": dec_top.value,
                                "section": dec_top.section,
                            })
                        continue

                # (d) 셀 내 '(전화번호 :       )' / '휴대전화번호:' 인라인 삽입
                if local_sec == "applicant":
                    app_phone = (prof.get("applicant.phone") or "").strip()
                    for l_idx, ln in enumerate(lines):
                        if not app_phone:
                            break
                        if "휴대전화번호:" in ln:
                            k = (t_idx, c["cell_idx"], l_idx, "after_anchor")
                            if k not in filled_cell_ops:
                                m_hp = re.search(r"(휴대전화번호\s*:\s*)(\s{4,})", ln)
                                if m_hp:
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
                                        "field_key": "applicant.phone",
                                        "value": app_phone,
                                    })
                                    report["filled_fields"].append({
                                        "label": "휴대전화번호:",
                                        "location": f"T{t_idx}({c['row']},{c['col']}) p{l_idx}",
                                        "field_key": "applicant.phone",
                                        "value": app_phone,
                                        "section": "applicant",
                                    })
                        elif re.search(r"\(전화(?:번호)?\s*:\s*\)", re.sub(r"\s{2,}", " ", ln)):
                            k = (t_idx, c["cell_idx"], l_idx, "after_anchor")
                            if k not in filled_cell_ops and "휴대전화" not in ln:
                                m_ph = re.search(r"(\(전화(?:번호)?\s*:)\s{6,}\)", ln)
                                if m_ph:
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
                                        "anchor": m_ph.group(1),
                                        "orig_text": ln,
                                        "new_text": ln[:m_ph.end(1)] + " " + app_phone + ln[m_ph.end(1):],
                                        "field_key": "applicant.phone",
                                        "value": app_phone,
                                    })
                                    report["filled_fields"].append({
                                        "label": "(전화번호 : )",
                                        "location": f"T{t_idx}({c['row']},{c['col']}) p{l_idx}",
                                        "field_key": "applicant.phone",
                                        "value": app_phone,
                                        "section": "applicant",
                                    })

                # 2) 일반 표 라벨 -> 오른쪽 인접 셀 또는 헤더행 아래 셀
                txt = raw_text.strip()
                if not txt or len(txt) > 60:
                    continue
                if (t_idx, c["cell_idx"]) in handled_date_cells and re.fullmatch(r"(?:20\s*)?년\s*월\s*일", re.sub(r"\s+", " ", txt)):
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
                if right_c is not None:
                    r_text = right_c.get("text", "")
                    if fm.is_cell_empty(r_text):
                        target_c = right_c
                    elif dec.field_key in ("applicant.rrn", "proxy.rrn") and re.fullmatch(r"\s*\(만\s*세\)\s*", r_text):
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
                            if len(r_lines) == 1 and re.search(r"^\s*\(전화", r_lines[0]):
                                target_c = right_c
                                target_para_idx = 0
                                edit_mode = "prepend_trim_spaces"
                if target_c is None and c["row"] + rs < max_r:
                    below_c = grid.get((c["row"] + rs, c["col"]))
                    if (
                        below_c is not None
                        and below_c["col"] == c["col"]
                        and below_c.get("col_span", 1) == cs
                        and fm.is_cell_empty(below_c.get("text", ""))
                    ):
                        target_c = below_c
                if (
                    target_c is None
                    and local_sec != "excluded"
                    and rs >= 2
                    and len(lines) == 1
                    and right_c is None
                    and dec.field_key in ("account.bank", "account.number", "account.holder")
                ):
                    target_c = c
                    target_para_idx = 1
                    edit_mode = "add_para"

                if target_c is None:
                    report["skipped_fields"].append({
                        "label": txt.replace("\n", " "), "location": f"T{t_idx}({c['row']},{c['col']})",
                        "reason": "non_empty_adjacent_cell" if right_c is not None else "no_adjacent_target_cell",
                    })
                    continue

                key = (t_idx, target_c["cell_idx"], target_para_idx, edit_mode)
                if key in filled_cell_ops:
                    continue
                filled_cell_ops.add(key)
                cell_edits.append({
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
                })
                report["filled_fields"].append({
                    "label": txt.replace("\n", " "),
                    "location": f"T{t_idx}({target_c['row']},{target_c['col']}) p{target_para_idx}",
                    "field_key": dec.field_key,
                    "value": dec.value,
                    "section": dec.section,
                })

    report["filled_count"] = len(report["filled_fields"])
    return {"cell_edits": cell_edits, "para_edits": para_edits, "report": report}
