"""PDF 서식 자동 기입 엔진 (pdfplumber + reportlab + pypdf, 허용형 라이선스).

특징:
1. 입력칸(AcroForm /Tx)이 있는 PDF는 pypdf 폼 기입을 우선 적용.
2. 일반 평면 PDF는 pdfplumber로 벡터 선분·단어 좌표를 추출해 표 셀·인라인 칸을 복원한 뒤,
   reportlab(NanumGothic TTF) 오버레이를 pypdf merge_page/merge_transformed_page로 합성.
3. MediaBox/CropBox 원점 및 페이지 /Rotate(0, 90, 180, 270) 좌표 변환 지원.
4. 조례 본문·스캔 이미지 페이지는 건너뛰고, 서명·(인)·날인·도장 칸만 공란 유지.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pdfplumber
from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas

import form_matcher as fm

FONT_PATH = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "NanumGothic-Regular.ttf"
_FONT_NAME = "NanumGothic"
_FONT_REGISTERED = False
_INK_COLOR = (0, 0, 0)  # 서식 글씨와 같은 검은색


def _ensure_font() -> str:
    global _FONT_REGISTERED
    if not _FONT_REGISTERED:
        pdfmetrics.registerFont(TTFont(_FONT_NAME, str(FONT_PATH)))
        _FONT_REGISTERED = True
    return _FONT_NAME


def _text_width(text: str, fontsize: float) -> float:
    return float(pdfmetrics.stringWidth(text, _ensure_font(), fontsize))


def _is_ordinance_page(text: str) -> bool:
    has_articles = len(re.findall(r"제\d+조\(", text)) >= 2
    has_form_marker = bool(re.search(r"\[?별지\s*제?\d+호|신청서|신고서|동의서", text))
    return has_articles and not has_form_marker


def _is_ledger_page(text: str) -> bool:
    top_lines = " ".join((text or "").splitlines()[:5])
    top_ns = re.sub(r"\s+", "", top_lines)
    return bool(fm.LEDGER_TITLE_RE.search(top_lines) or fm.LEDGER_TITLE_RE.search(top_ns))


def _add_rect_edges(
    x0: float,
    top: float,
    x1: float,
    bottom: float,
    h_lines: list[tuple[float, float, float]],
    v_lines: list[tuple[float, float, float]],
) -> None:
    w, h = x1 - x0, bottom - top
    if w >= 6 and h >= 6:
        h_lines.append((top, x0, x1))
        h_lines.append((bottom, x0, x1))
        v_lines.append((x0, top, bottom))
        v_lines.append((x1, top, bottom))
    elif w >= 6 and h < 6:
        h_lines.append(((top + bottom) / 2, x0, x1))
    elif h >= 6 and w < 6:
        v_lines.append(((x0 + x1) / 2, top, bottom))


def _extract_lines(page: Any) -> tuple[list[tuple[float, float, float]], list[tuple[float, float, float]]]:
    h_lines: list[tuple[float, float, float]] = []
    v_lines: list[tuple[float, float, float]] = []

    for ln in page.lines or []:
        x0, x1 = float(ln["x0"]), float(ln["x1"])
        top, bottom = float(ln["top"]), float(ln["bottom"])
        if abs(bottom - top) <= 1.2 and (x1 - x0) >= 6:
            h_lines.append(((top + bottom) / 2, min(x0, x1), max(x0, x1)))
        elif abs(x1 - x0) <= 1.2 and (bottom - top) >= 6:
            v_lines.append(((x0 + x1) / 2, min(top, bottom), max(top, bottom)))

    for rc in page.rects or []:
        _add_rect_edges(
            float(rc["x0"]), float(rc["top"]), float(rc["x1"]), float(rc["bottom"]),
            h_lines, v_lines,
        )

    for cv in page.curves or []:
        pts = cv.get("pts") or []
        if len(pts) == 2:
            (ax, ay), (bx, by) = pts
            # pdfplumber curve coords match top-down x0/top/x1/bottom bbox
            x0, x1 = min(ax, bx), max(ax, bx)
            if abs(float(cv["bottom"]) - float(cv["top"])) <= 1.2 and (x1 - x0) >= 6:
                h_lines.append(((float(cv["top"]) + float(cv["bottom"])) / 2, x0, x1))
            elif abs(x1 - x0) <= 1.2 and (float(cv["bottom"]) - float(cv["top"])) >= 6:
                v_lines.append(((x0 + x1) / 2, float(cv["top"]), float(cv["bottom"])))
        elif len(pts) in (4, 5) and cv.get("stroke", True):
            _add_rect_edges(
                float(cv["x0"]), float(cv["top"]), float(cv["x1"]), float(cv["bottom"]),
                h_lines, v_lines,
            )

    return h_lines, v_lines


def _has_vline_between(x_left: float, x_right: float, yc: float, v_lines: list[tuple[float, float, float]]) -> bool:
    for vx, vy0, vy1 in v_lines:
        if vy0 - 2 <= yc <= vy1 + 2 and (x_left + 1.0) <= vx <= (x_right - 1.0):
            return True
    return False


def _cell_bounds_for_rect(
    bbox: tuple[float, float, float, float],
    h_lines: list[tuple[float, float, float]],
    v_lines: list[tuple[float, float, float]],
    page_x0: float,
    page_x1: float,
) -> tuple[float, float, float, float] | None:
    """닫힌 셀 또는 수평선 기반 행 셀(열린 좌우 테두리 지원)을 찾는다."""
    x0, y0, x1, y1 = bbox
    xc, yc = (x0 + x1) / 2, (y0 + y1) / 2
    top_hs = [hy for hy, hx0, hx1 in h_lines if hx0 - 4 <= xc <= hx1 + 4 and hy <= y0 + 2]
    bot_hs = [hy for hy, hx0, hx1 in h_lines if hx0 - 4 <= xc <= hx1 + 4 and hy >= y1 - 2]
    if not (top_hs and bot_hs):
        return None
    cy0, cy1 = max(top_hs), min(bot_hs)
    left_vs = [vx for vx, vy0, vy1 in v_lines if vy0 - 2 <= yc <= vy1 + 2 and vx <= x0 + 2]
    right_vs = [vx for vx, vy0, vy1 in v_lines if vy0 - 2 <= yc <= vy1 + 2 and vx >= x1 - 2]

    if left_vs:
        cx0 = max(left_vs)
    else:
        row_hs_top = [hx0 for hy, hx0, hx1 in h_lines if abs(hy - cy0) <= 1.5 and hx0 <= x0]
        cx0 = max(row_hs_top) if row_hs_top else page_x0 + 50.0

    if right_vs:
        cx1 = min(right_vs)
    else:
        row_hs_top = [hx1 for hy, hx0, hx1 in h_lines if abs(hy - cy0) <= 1.5 and hx1 >= x1]
        cx1 = min(row_hs_top) if row_hs_top else page_x1 - 50.0

    if cx1 - cx0 < 15 or cy1 - cy0 < 8:
        return None
    return (cx0, cy0, cx1, cy1)


def _right_adjacent_cell(
    cell: tuple[float, float, float, float],
    v_lines: list[tuple[float, float, float]],
) -> tuple[float, float, float, float] | None:
    cx0, cy0, cx1, cy1 = cell
    yc = (cy0 + cy1) / 2
    next_vs = sorted({round(vx, 1) for vx, vy0, vy1 in v_lines if vy0 - 2 <= yc <= vy1 + 2 and vx > cx1 + 8})
    if not next_vs:
        return None
    return (cx1, cy0, next_vs[0], cy1)


def _below_adjacent_cell(
    cell: tuple[float, float, float, float],
    h_lines: list[tuple[float, float, float]],
) -> tuple[float, float, float, float] | None:
    cx0, cy0, cx1, cy1 = cell
    xc = (cx0 + cx1) / 2
    next_hs = sorted({round(hy, 1) for hy, hx0, hx1 in h_lines if hx0 - 2 <= xc <= hx1 + 2 and hy > cy1 + 8})
    if not next_hs:
        return None
    return (cx0, cy1, cx1, next_hs[0])


def _words_in_rect(words: list[dict[str, Any]], rect: tuple[float, float, float, float], pad: float = 2.0) -> list[dict[str, Any]]:
    rx0, ry0, rx1, ry1 = rect
    out = []
    for w in words:
        cx = (w["x0"] + w["x1"]) / 2
        cy = (w["top"] + w["bottom"]) / 2
        if rx0 + pad <= cx <= rx1 - pad and ry0 + pad <= cy <= ry1 - pad:
            out.append({"bbox": (w["x0"], w["top"], w["x1"], w["bottom"]), "text": w["text"]})
    return out


def _cluster_visual_rows(words: list[dict[str, Any]], y_tol: float = 4.0) -> list[list[dict[str, Any]]]:
    if not words:
        return []
    ordered = sorted(words, key=lambda w: ((w["top"] + w["bottom"]) / 2, w["x0"]))
    rows: list[list[dict[str, Any]]] = [[ordered[0]]]
    cur_y = (ordered[0]["top"] + ordered[0]["bottom"]) / 2
    for w in ordered[1:]:
        wy = (w["top"] + w["bottom"]) / 2
        if abs(wy - cur_y) <= y_tol:
            rows[-1].append(w)
        else:
            rows.append([w])
            cur_y = wy
    for r in rows:
        r.sort(key=lambda w: w["x0"])
    return rows


def _group_words_into_lines(words: list[dict[str, Any]], v_lines: list[tuple[float, float, float]]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for row in _cluster_visual_rows(words, y_tol=3.5):
        segs: list[list[dict[str, Any]]] = [[row[0]]]
        for w in row[1:]:
            prev = segs[-1][-1]
            yc = (prev["top"] + prev["bottom"] + w["top"] + w["bottom"]) / 4
            gap = w["x0"] - prev["x1"]
            if gap > 22.0 or _has_vline_between(prev["x1"], w["x0"], yc, v_lines):
                segs.append([w])
            else:
                segs[-1].append(w)
        for seg in segs:
            txt = " ".join(x["text"] for x in seg).strip()
            if not txt:
                continue
            bbox = (
                min(x["x0"] for x in seg),
                min(x["top"] for x in seg),
                max(x["x1"] for x in seg),
                max(x["bottom"] for x in seg),
            )
            lines.append({"text": txt, "bbox": bbox, "words": seg})
    lines.sort(key=lambda l: (round(l["bbox"][1], 1), round(l["bbox"][0], 1)))
    return lines


def _wrap_text(text: str, avail_w: float, fs: float) -> list[str]:
    lines_out: list[str] = []
    cur = ""
    tokens = re.findall(r"\S+\s*", text) or list(text)
    for tok in tokens:
        cand = cur + tok
        if _text_width(cand.rstrip(), fs) <= avail_w:
            cur = cand
        else:
            if cur.strip():
                lines_out.append(cur.rstrip())
                cur = tok.lstrip()
            else:
                cur = ""
            if _text_width(cur.rstrip(), fs) > avail_w:
                piece = ""
                for ch in tok.strip():
                    if _text_width(piece + ch, fs) <= avail_w:
                        piece += ch
                    else:
                        if piece:
                            lines_out.append(piece)
                        piece = ch
                cur = piece + (" " if tok.endswith(" ") else "")
    if cur.strip():
        lines_out.append(cur.rstrip())
    return lines_out


def _plan_insert(
    draw_queue: list[tuple[float, float, str, float, tuple[float, float, float]]],
    rect: tuple[float, float, float, float],
    text: str,
    align_vcenter: bool = True,
    max_fs: float = 9.5,
    min_fs: float = 5.5,
    allow_wrap: bool = True,
) -> tuple[bool, float, str]:
    rx0, ry0, rx1, ry1 = rect
    pad_x, pad_y = 3.5, 2.0
    avail_w = (rx1 - rx0) - 2 * pad_x
    avail_h = (ry1 - ry0) - 2 * pad_y
    if avail_w < 12 or avail_h < 8:
        return False, 0.0, "box_too_small"

    fs = max_fs
    while fs >= min_fs:
        if _text_width(text, fs) <= avail_w and fs * 1.25 <= avail_h + 1.0:
            y_base = (ry0 + ry1) / 2 + fs * 0.35 if align_vcenter else ry0 + pad_y + fs
            draw_queue.append((rx0 + pad_x, y_base, text, fs, _INK_COLOR))
            return True, fs, "single_line"
        fs -= 0.5

    if allow_wrap and avail_h >= 24:
        for try_fs in (8.5, 7.5, 7.0):
            wrapped = _wrap_text(text, avail_w, try_fs)
            line_h = try_fs * 1.25
            if wrapped and len(wrapped) * line_h <= avail_h:
                for li, line_str in enumerate(wrapped):
                    y_base = ry0 + pad_y + try_fs + li * line_h
                    draw_queue.append((rx0 + pad_x, y_base, line_str, try_fs, _INK_COLOR))
                return True, try_fs, "wrapped"

    return False, 0.0, "text_overflow"


def _fill_date_inline(
    draw_queue: list[tuple[float, float, str, float, tuple[float, float, float]]],
    row_seg: list[dict[str, Any]],
    today_parts: tuple[str, str, str],
) -> bool:
    yr, mo, dy = today_parts
    texts = [w["text"] for w in row_seg]
    joined = " ".join(texts).strip()
    if not re.fullmatch(r"(?:20\s*)?년\s*월\s*일", joined):
        return False
    wmap = {w["text"]: w for w in row_seg}
    if not all(k in wmap for k in ("년", "월", "일")):
        return False
    wy, wm, wd = wmap["년"], wmap["월"], wmap["일"]
    w20 = wmap.get("20")
    fs = min(9.5, max(7.5, (wy["bottom"] - wy["top"]) * 0.85))
    y_base = float(wy["bottom"]) - 1.5
    mo_w = _text_width(mo, fs)
    dy_w = _text_width(dy, fs)

    gap_ym = wm["x0"] - wy["x1"]
    gap_md = wd["x0"] - wm["x1"]
    if gap_ym < mo_w + 2 or gap_md < dy_w + 2:
        return False

    if w20 is not None and w20["x1"] < wy["x0"]:
        yr_val = yr[-2:]
        yr_w = _text_width(yr_val, fs)
        gap_20y = wy["x0"] - w20["x1"]
        if gap_20y < yr_w + 2:
            return False
        yr_x = w20["x1"] + (gap_20y - yr_w) / 2
    else:
        yr_val = yr
        yr_w = _text_width(yr_val, fs)
        yr_x = wy["x0"] - yr_w - 2.0
        if yr_x < 5:
            return False

    mo_x = wy["x1"] + (gap_ym - mo_w) / 2
    dy_x = wm["x1"] + (gap_md - dy_w) / 2
    for tx, val in ((yr_x, yr_val), (mo_x, mo), (dy_x, dy)):
        draw_queue.append((tx, y_base, val, fs, _INK_COLOR))
    return True


def _try_acroform_fill(
    reader: PdfReader,
    writer: PdfWriter,
    prof: dict[str, str],
    report: dict[str, Any],
    allowed_pages: set[int] | None = None,
) -> bool:
    """AcroForm 입력칸(/FT == /Tx)은 한글 깨짐 방지를 위해 필드 /V는 비우고 나눔고딕 오버레이로 그린다."""
    fields = reader.get_fields() or {}
    if not fields:
        return False

    drawn_pages: list[int] = []
    for p_idx, page in enumerate(writer.pages):
        if allowed_pages is not None and p_idx not in allowed_pages:
            continue
        annots = page.get("/Annots") or []
        mx0 = float(page.mediabox.left)
        my0 = float(page.mediabox.bottom)
        mx1 = float(page.mediabox.right)
        my1 = float(page.mediabox.top)
        mw, mh = mx1 - mx0, my1 - my0
        rot = int(page.get("/Rotate", 0) or 0) % 360

        ov_items: list[tuple[float, float, int, float, str]] = []
        for annot_ref in annots:
            annot = annot_ref.get_object()
            if annot.get("/Subtype") != "/Widget":
                continue
            parent = annot.get("/Parent").get_object() if annot.get("/Parent") else None
            ft = annot.get("/FT") or (parent.get("/FT") if parent else None)
            if ft != "/Tx":
                continue
            rect_obj = annot.get("/Rect")
            if not rect_obj or len(rect_obj) != 4:
                continue
            fname = str(annot.get("/T") or (parent.get("/T") if parent else "") or "")
            tu = str(annot.get("/TU") or (parent.get("/TU") if parent else "") or fname)
            if not fname and not tu:
                continue
            dec = fm.match_label(tu, section="applicant", profile=prof)
            if dec.action != "fill" or not dec.value:
                continue
            existing = annot.get("/V") or (parent.get("/V") if parent else "")
            if str(existing or "").strip():
                report["skipped_fields"].append({
                    "label": tu, "location": f"p{p_idx + 1} acroform={fname or tu}",
                    "reason": "existing_value", "field_key": dec.field_key,
                })
                continue

            wx0, wy0, wx1, wy1 = [float(v) for v in rect_obj]
            bw, bh = (abs(wy1 - wy0), abs(wx1 - wx0)) if rot in (90, 270) else (abs(wx1 - wx0), abs(wy1 - wy0))
            avail_w, avail_h = bw - 7.0, bh - 4.0
            if avail_w < 12 or avail_h < 8:
                continue
            fs = 9.5
            while fs >= 5.5:
                if _text_width(dec.value, fs) <= avail_w and fs * 1.25 <= avail_h + 1.0:
                    break
                fs -= 0.5
            if fs < 5.5:
                report["skipped_fields"].append({
                    "label": tu, "location": f"p{p_idx + 1} acroform={fname or tu}",
                    "reason": "text_overflow", "field_key": dec.field_key,
                })
                continue

            if rot == 0:
                ux, uy, ang = (wx0 - mx0) + 3.5, ((wy0 + wy1) / 2 - my0) - fs * 0.35, 0
            elif rot == 90:
                ux, uy, ang = ((wx0 + wx1) / 2 - mx0) + fs * 0.35, (wy0 - my0) + 3.5, 90
            elif rot == 180:
                ux, uy, ang = (wx1 - mx0) - 3.5, ((wy0 + wy1) / 2 - my0) + fs * 0.35, 180
            else:
                ux, uy, ang = ((wx0 + wx1) / 2 - mx0) - fs * 0.35, (wy1 - my0) - 3.5, 270

            ov_items.append((ux, uy, ang, fs, dec.value))
            report["filled_fields"].append({
                "label": tu,
                "location": f"p{p_idx + 1} acroform={fname or tu}",
                "field_key": dec.field_key,
                "value": dec.value,
                "section": dec.section,
                "mode": "acroform_overlay",
                "fontsize": fs,
            })

        if ov_items:
            _ensure_font()
            ov_buf = io.BytesIO()
            canvas = Canvas(ov_buf, pagesize=(mw, mh))
            for ux, uy, ang, fs, val in ov_items:
                canvas.saveState()
                canvas.translate(ux, uy)
                if ang:
                    canvas.rotate(ang)
                canvas.setFillColorRGB(*_INK_COLOR)
                canvas.setFont(_FONT_NAME, fs)
                canvas.drawString(0, 0, val)
                canvas.restoreState()
            canvas.save()
            ov_pg = PdfReader(io.BytesIO(ov_buf.getvalue())).pages[0]
            if abs(mx0) > 1e-6 or abs(my0) > 1e-6:
                page.merge_transformed_page(ov_pg, Transformation().translate(mx0, my0))
            else:
                page.merge_page(ov_pg)
            drawn_pages.append(p_idx + 1)

    if not drawn_pages:
        return False

    report["pages_processed"] = drawn_pages
    report["filled_count"] = len(report["filled_fields"])
    return True


def _vis_to_unrotated_pdf(
    x_vis: float,
    y_vis: float,
    page_bbox: tuple[float, float, float, float],
    mediabox: tuple[float, float, float, float],
    rotation: int,
) -> tuple[float, float, int]:
    """pdfplumber 시각 top-down 좌표를 unrotated PDF MediaBox 좌표(rx, ry)와 회전각으로 변환한다."""
    dx = x_vis - page_bbox[0]
    dy = y_vis - page_bbox[1]
    mx0, my0, mx1, my1 = mediabox
    mw, mh = mx1 - mx0, my1 - my0
    rot = rotation % 360
    if rot == 0:
        return dx, mh - dy, 0
    if rot == 90:
        return dy, dx, 90
    if rot == 180:
        return mw - dx, dy, 180
    return mw - dy, mh - dx, 270


def _merge_overlay_page(
    target_page: Any,
    draw_cmds: list[tuple[float, float, str, float, tuple[float, float, float]]],
    page_bbox: tuple[float, float, float, float],
) -> None:
    _ensure_font()
    mx0 = float(target_page.mediabox.left)
    my0 = float(target_page.mediabox.bottom)
    mx1 = float(target_page.mediabox.right)
    my1 = float(target_page.mediabox.top)
    mw, mh = mx1 - mx0, my1 - my0
    rot = int(target_page.get("/Rotate", 0) or 0) % 360

    ov_buf = io.BytesIO()
    canvas = Canvas(ov_buf, pagesize=(mw, mh))
    for x_vis, y_base_vis, text, fs, color in draw_cmds:
        ux, uy, angle = _vis_to_unrotated_pdf(x_vis, y_base_vis, page_bbox, (mx0, my0, mx1, my1), rot)
        canvas.saveState()
        canvas.translate(ux, uy)
        if angle:
            canvas.rotate(angle)
        canvas.setFillColorRGB(*color)
        canvas.setFont(_FONT_NAME, fs)
        canvas.drawString(0, 0, text)
        canvas.restoreState()
    canvas.save()

    overlay_page = PdfReader(io.BytesIO(ov_buf.getvalue())).pages[0]
    if abs(mx0) > 1e-6 or abs(my0) > 1e-6:
        target_page.merge_transformed_page(overlay_page, Transformation().translate(mx0, my0))
    else:
        target_page.merge_page(overlay_page)


def _segment_row_by_vlines(row: list[dict[str, Any]], v_lines: list[tuple[float, float, float]], max_gap: float = 65.0) -> list[list[dict[str, Any]]]:
    segs: list[list[dict[str, Any]]] = [[row[0]]]
    for w in row[1:]:
        prev = segs[-1][-1]
        yc = (prev["top"] + prev["bottom"] + w["top"] + w["bottom"]) / 4
        if (w["x0"] - prev["x1"]) > max_gap or _has_vline_between(prev["x1"], w["x0"], yc, v_lines):
            segs.append([w])
        else:
            segs[-1].append(w)
    return segs


def fill_pdf_bytes(
    data: bytes,
    profile: dict[str, str] | None = None,
    *,
    allowed_pages: set[int] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    prof = fm.default_profile() if profile is None else profile
    date_parts = re.findall(r"\d+", prof.get("apply_date") or "")
    y_s, m_s, d_s = date_parts[:3] if len(date_parts) >= 3 else ("", "", "")
    apply_date_dot = (prof.get("apply_date_dot") or "").strip()
    app_name = (prof.get("applicant.name") or "").strip()
    app_phone = (prof.get("applicant.phone") or "").strip()
    app_income = (prof.get("applicant.income_monthly") or "").strip()
    _ensure_font()

    report: dict[str, Any] = {
        "format": "pdf",
        "status": "ok",
        "pages_total": 0,
        "pages_processed": [],
        "found_labels": [],
        "filled_fields": [],
        "skipped_fields": [],
        "sensitive_blank_fields": [],
        "filled_count": 0,
    }

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                dec_res = reader.decrypt("")
            except Exception:
                dec_res = 0
            if not dec_res:
                report["status"] = "error"
                report["error"] = "pdf_encrypted"
                return data, report
        report["pages_total"] = len(reader.pages)
        writer = PdfWriter(clone_from=reader)
    except Exception as exc:
        report["status"] = "error"
        report["error"] = f"pdf_open_error:{type(exc).__name__}"
        return data, report

    try:
        if _try_acroform_fill(reader, writer, prof, report, allowed_pages):
            out_buf = io.BytesIO()
            writer.write(out_buf)
            return out_buf.getvalue(), report
        if any("acroform=" in item.get("location", "") for item in report["skipped_fields"]):
            # Do not retry a rejected widget as an ordinary blank table cell.
            report["status"] = "no_fillable_fields"
            return data, report
    except Exception as exc:
        report["status"] = "error"
        report["error"] = f"pdf_acroform_error:{type(exc).__name__}"
        report["filled_fields"] = []
        report["filled_count"] = 0
        return data, report

    page_overlays: list[tuple[int, tuple[float, float, float, float], list[tuple[float, float, str, float, tuple[float, float, float]]]]] = []

    try:
        pdf_ctx = pdfplumber.open(io.BytesIO(data))
    except Exception as exc:
        report["status"] = "error"
        report["error"] = f"pdf_open_error:{type(exc).__name__}"
        return data, report

    with pdf_ctx as pdf:
        total_chars = sum(len((pg.extract_text() or "").strip()) for pg in pdf.pages)
        total_imgs = sum(len(pg.images) for pg in pdf.pages)
        if total_chars < 30 and total_imgs > 0:
            report["status"] = "unsupported"
            report["error"] = "scanned_pdf"
            return data, report

        for p_idx, page in enumerate(pdf.pages):
            if allowed_pages is not None and p_idx not in allowed_pages:
                continue
            p_no = p_idx + 1
            page_text = page.extract_text() or ""
            if not page_text.strip() or _is_ordinance_page(page_text):
                continue
            if _is_ledger_page(page_text):
                report["skipped_fields"].append({
                    "label": (page_text.splitlines()[0] if page_text.splitlines() else "")[:40],
                    "location": f"p{p_no}",
                    "reason": "excluded_section",
                })
                continue

            h_lines, v_lines = _extract_lines(page)
            if len(h_lines) < 3 and not re.search(r"신청서|신고서|동의서|신청인", page_text):
                continue

            report["pages_processed"].append(p_no)
            words = page.extract_words(keep_blank_chars=False, use_text_flow=False) or []
            lines = _group_words_into_lines(words, v_lines)
            vis_x0, _, vis_x1, _ = [float(v) for v in page.bbox]
            vis_w = vis_x1 - vis_x0

            section_bands: list[tuple[float, float, str]] = []
            band_texts: list[str] = []
            for l in lines:
                lb = l["bbox"]
                if lb[0] - vis_x0 < vis_w * 0.30:
                    cb = _cell_bounds_for_rect(lb, h_lines, v_lines, vis_x0, vis_x1)
                    if cb:
                        cell_text = " ".join(
                            x["text"] for x in lines
                            if cb[0] - 2 <= (x["bbox"][0] + x["bbox"][2]) / 2 <= cb[2] + 2
                            and cb[1] - 2 <= (x["bbox"][1] + x["bbox"][3]) / 2 <= cb[3] + 2
                        )
                    else:
                        cell_text = l["text"]
                    # 서식 아래쪽 큰 테두리 상자처럼 글이 많은 칸은 상자 전체가 아니라 이 줄만 보고 판단한다
                    # (상자 안 '담당 공무원 확인' 문장 때문에 신청인 서명·날짜 줄까지 제외되던 문제).
                    big_cell = bool(cb) and len(cell_text) > 120
                    if big_cell:
                        cell_text = l["text"]
                    sec = fm.detect_section_context(cell_text, current="keep")
                    if sec != "keep":
                        sy0 = cb[1] if cb and not big_cell else lb[1] - 2
                        sy1 = cb[3] if cb and not big_cell else lb[3] + 40
                        section_bands.append((sy0, sy1, sec))
                        band_texts.append(re.sub(r"\s+", "", cell_text))
            # 같은 쪽에 '신청인' 구역과 '보호자' 구역이 따로 있으면 보호자는 다른 사람이다(청소년 신청서 등).
            has_applicant_band = any(re.match(r"신청인(?!\(?보호자)", t) for t in band_texts)
            for i, t in enumerate(band_texts):
                if has_applicant_band and re.match(r"보호자", t) and section_bands[i][2] == "applicant":
                    section_bands[i] = (section_bands[i][0], section_bands[i][1], "other_person")

            def section_at_y(y: float) -> str:
                hits = [sec for sy0, sy1, sec in section_bands if sy0 - 2 <= y <= sy1 + 2]
                if hits:
                    return hits[-1]
                prev = [sec for sy0, _sy1, sec in section_bands if sy0 <= y]
                return prev[-1] if prev else "applicant"

            draw_queue: list[tuple[float, float, str, float, tuple[float, float, float]]] = []
            placed_rects: list[tuple[float, float, float, float]] = []

            def overlaps_placed(r: tuple[float, float, float, float]) -> bool:
                return any(
                    min(r[2], pr[2]) - max(r[0], pr[0]) > 4 and min(r[3], pr[3]) - max(r[1], pr[1]) > 4
                    for pr in placed_rects
                )

            # A. 행 단위 인라인 날짜 ('년 월 일') 및 서명 줄('신청인 ... (서명')
            visual_rows = _cluster_visual_rows(words, y_tol=4.5)
            # 한 쪽에 '신청인 … (서명)' 줄이 여러 개면 여러 사람이 서명하는 명단이다. 신청인 이름을 넣지 않는다.
            sig_roster = sum(
                1 for rw in visual_rows
                if re.search(r"\(서명", " ".join(w["text"] for w in rw))
                and re.search(r"신\s*청\s*인|이\s*름\s*:", " ".join(w["text"] for w in rw))
            ) >= 3
            handled_sig_ys: list[float] = []
            handled_date_ys: list[float] = []

            for r_words in visual_rows:
                yc = sum((w["top"] + w["bottom"]) / 2 for w in r_words) / len(r_words)
                row_sec = section_at_y(yc)
                if row_sec in ("excluded", "other_person"):
                    row_s_txt = " ".join(w["text"] for w in r_words).strip()
                    if re.search(r"년\s*월\s*일|\(서명", row_s_txt):
                        report["skipped_fields"].append({
                            "label": row_s_txt[:30],
                            "location": f"p{p_no} y={yc:.1f}",
                            "reason": "other_person" if row_sec == "other_person" else "excluded_section",
                        })
                    continue

                for seg in _segment_row_by_vlines(r_words, v_lines, max_gap=70.0):
                    seg_txt = " ".join(w["text"] for w in seg).strip()
                    if re.fullmatch(r"(?:20\s*)?년\s*월\s*일", seg_txt):
                        if not (y_s and m_s and d_s and apply_date_dot):
                            continue
                        seg_x0 = seg[0]["x0"]
                        left_words = [w["text"] for w in r_words if w["x1"] < seg_x0]
                        row_ctx = " ".join(left_words)
                        if any(bad in row_ctx for bad in ("사망", "출산", "발생", "접수", "지급", "입사")):
                            report["skipped_fields"].append({
                                "label": seg_txt,
                                "location": f"p{p_no} y={yc:.1f}",
                                "reason": "non_application_date_context",
                            })
                        elif _fill_date_inline(draw_queue, seg, (y_s, m_s, d_s)):
                            handled_date_ys.append(yc)
                            report["filled_fields"].append({
                                "label": seg_txt,
                                "location": f"p{p_no} y={yc:.1f}",
                                "field_key": "apply_date",
                                "value": apply_date_dot,
                                "section": "applicant",
                            })

                for seg in _segment_row_by_vlines(r_words, v_lines, max_gap=150.0):
                    seg_txt = " ".join(w["text"] for w in seg)
                    if not re.search(r"\(서명", seg_txt):
                        continue
                    if sig_roster:
                        report["skipped_fields"].append({"label": seg_txt[:30], "location": f"p{p_no} y={yc:.1f}",
                                                         "reason": "signature_roster"})
                        continue
                    sig_w = next((w for w in seg if "(서명" in w["text"]), None)
                    if not sig_w:
                        continue
                    before_sig = [w for w in seg if w["x0"] < sig_w["x0"]]
                    clusters: list[list[dict[str, Any]]] = []
                    for w in before_sig:
                        if not clusters or (w["x0"] - clusters[-1][-1]["x1"]) > 25.0:
                            clusters.append([w])
                        else:
                            clusters[-1].append(w)
                    lbl_grp = next(
                        (
                            grp for grp in reversed(clusters)
                            if re.search(r"신\s*청\s*인|이\s*름\s*:|보\s*호\s*자\s*:|신\s*고\s*인|청\s*구\s*인", " ".join(x["text"] for x in grp))
                            and grp[0]["x0"] - vis_x0 > vis_w * 0.22
                        ),
                        None,
                    )
                    if not lbl_grp:
                        continue
                    app_w = lbl_grp[0]
                    left_end = lbl_grp[-1]["x1"]
                    gap = sig_w["x0"] - left_end
                    if app_name and gap >= 35:
                        top_y = min(app_w["top"], sig_w["top"])
                        bot_y = max(app_w["bottom"], sig_w["bottom"])
                        target_r = (left_end + 4, top_y - 2, min(sig_w["x0"] - 10, left_end + 55), bot_y + 2)
                        if not overlaps_placed(target_r):
                            ok, fs_used, _ = _plan_insert(draw_queue, target_r, app_name, max_fs=9.5, allow_wrap=False)
                            if ok:
                                placed_rects.append(target_r)
                                handled_sig_ys.append(yc)
                                report["filled_fields"].append({
                                    "label": "서명란 성명",
                                    "location": f"p{p_no} rect={[round(v, 1) for v in target_r]}",
                                    "field_key": "applicant.name",
                                    "value": app_name,
                                    "section": "applicant",
                                    "fontsize": fs_used,
                                })
                                report["sensitive_blank_fields"].append({
                                    "label": "(서명 또는 인)",
                                    "location": f"p{p_no} y={yc:.1f}",
                                    "reason": "signature_kept_blank",
                                })

            # B. 셀 기반 또는 인라인 콜론 라벨 처리
            for l in lines:
                txt = l["text"]
                lb = l["bbox"]
                yc = (lb[1] + lb[3]) / 2
                if any(abs(yc - dy) <= 5.0 for dy in handled_date_ys) and re.fullmatch(r"(?:20|년|월|일|\s)+", txt):
                    continue
                if any(abs(yc - sy) <= 5.0 for sy in handled_sig_ys) and re.search(r"신\s*청\s*인|이\s*름\s*:", txt) and lb[0] - vis_x0 > vis_w * 0.22:
                    continue

                cur_sec = section_at_y(yc)
                cell = _cell_bounds_for_rect(lb, h_lines, v_lines, vis_x0, vis_x1)
                if cell is None:
                    continue

                # 1) 인라인 콜론 / 복합 줄 (예: WLF00000044_1, WLF00001634_1 p6 전화)
                if ":" in txt or "휴대전화" in txt or "전화" in txt:
                    row_words = [w for w in words if abs((w["top"] + w["bottom"]) / 2 - yc) <= 4.5]

                    # '연락처: (휴대전화번호)     (이메일)'
                    if "휴대전화" in txt and cur_sec == "applicant":
                        hp_w = next((w for w in row_words if "휴대전화" in w["text"]), None)
                        em_w = next((w for w in row_words if "이메일" in w["text"] and (hp_w is None or w["x0"] > hp_w["x1"])), None)
                        if app_phone and hp_w and em_w and em_w["x0"] - hp_w["x1"] >= 55:
                            target_r = (hp_w["x1"] + 4, cell[1], em_w["x0"] - 4, cell[3])
                            if not _words_in_rect(words, target_r) and not overlaps_placed(target_r):
                                ok, fs_used, _ = _plan_insert(draw_queue, target_r, app_phone, max_fs=9.0, allow_wrap=False)
                                if ok:
                                    placed_rects.append(target_r)
                                    report["filled_fields"].append({
                                        "label": "휴대전화번호",
                                        "location": f"p{p_no} rect={[round(v, 1) for v in target_r]}",
                                        "field_key": "applicant.phone",
                                        "value": app_phone,
                                        "section": "applicant",
                                        "fontsize": fs_used,
                                    })
                            continue

                    # '월평균소득:          원' (신청인 근로 월평균소득 명시 칸)
                    if re.search(r"월평균\s*소득\s*:", txt):
                        won_w = next((w for w in row_words if w["text"] == "원" and w["x0"] > lb[0] + 40), None)
                        lbl_end = max((w["x1"] for w in row_words if "소득" in w["text"]), default=lb[0] + 60)
                        if app_income and won_w and won_w["x0"] - lbl_end >= 45:
                            target_r = (lbl_end + 8, cell[1], won_w["x0"] - 6, cell[3])
                            if not _words_in_rect(words, target_r) and not overlaps_placed(target_r):
                                ok, fs_used, _ = _plan_insert(draw_queue, target_r, app_income, max_fs=9.0, allow_wrap=False)
                                if ok:
                                    placed_rects.append(target_r)
                                    report["filled_fields"].append({
                                        "label": "월평균소득",
                                        "location": f"p{p_no} rect={[round(v, 1) for v in target_r]}",
                                        "field_key": "applicant.income_monthly",
                                        "value": app_income,
                                        "section": "applicant",
                                        "fontsize": fs_used,
                                    })
                            continue

                    # '(전화 :     )' 분리형 (예: WLF00001634_1 p6)
                    if txt.startswith("(전화") and cur_sec == "applicant":
                        rparen = next((w for w in row_words if w["text"].strip() == ")" and w["x0"] > lb[2]), None)
                        if app_phone and rparen and rparen["x0"] - lb[2] >= 45:
                            target_r = (lb[2] + 3, cell[1], rparen["x0"] - 3, cell[3])
                            if not _words_in_rect(words, target_r) and not overlaps_placed(target_r):
                                ok, fs_used, _ = _plan_insert(draw_queue, target_r, app_phone, max_fs=8.0, min_fs=5.5, allow_wrap=False)
                                if ok:
                                    placed_rects.append(target_r)
                                    report["filled_fields"].append({
                                        "label": "(전화 : )",
                                        "location": f"p{p_no} rect={[round(v, 1) for v in target_r]}",
                                        "field_key": "applicant.phone",
                                        "value": app_phone,
                                        "section": "applicant",
                                        "fontsize": fs_used,
                                    })
                            continue

                    # 단일 인라인 '성 명:', '주민등록번호:', '주소:'
                    if txt.endswith(":") and abs(lb[0] - cell[0]) < 30:
                        dec = fm.match_label(txt, section=cur_sec, profile=prof)
                        if dec.action == "fill":
                            lbl_end = lb[2]
                            next_same_cell_words = [
                                w for w in row_words
                                if cell[0] <= w["x0"] and w["x1"] <= cell[2] and w["x0"] > lbl_end + 8
                            ]
                            right_bound = min((w["x0"] - 4 for w in next_same_cell_words), default=cell[2] - 2)
                            target_r = (lbl_end + 6, cell[1], right_bound, cell[3])
                            # 한 칸에 '자택:'/'휴대전화:'처럼 여러 줄이 있으면 이 라벨의 줄 높이에 맞춰 쓴다.
                            other_rows = [w for w in words if cell[0] <= w["x0"] and w["x1"] <= cell[2]
                                          and cell[1] <= w["top"] and w["bottom"] <= cell[3]
                                          and abs((w["top"] + w["bottom"]) / 2 - yc) > 6]
                            if other_rows:
                                target_r = (lbl_end + 6, max(cell[1], lb[1] - 3), right_bound, min(cell[3], lb[3] + 3))
                            if not _words_in_rect(words, target_r) and not overlaps_placed(target_r):
                                ok, fs_used, _ = _plan_insert(draw_queue, target_r, dec.value or "", max_fs=9.5, allow_wrap=False)
                                if ok:
                                    placed_rects.append(target_r)
                                    report["filled_fields"].append({
                                        "label": txt,
                                        "location": f"p{p_no} rect={[round(v, 1) for v in target_r]}",
                                        "field_key": dec.field_key,
                                        "value": dec.value,
                                        "section": dec.section,
                                        "fontsize": fs_used,
                                    })
                            continue
                        elif dec.action == "skip":
                            report["skipped_fields"].append({
                                "label": txt,
                                "location": f"p{p_no} y={yc:.1f}",
                                "reason": dec.reason,
                            })
                            continue

                # 2) 일반 표 셀 라벨 -> 오른쪽 빈 셀 우선, 없으면 아래 빈 셀
                dec = fm.match_label(txt, section=cur_sec, profile=prof)
                if dec.action == "skip" and dec.reason in ("empty_or_too_long_label", "unmatched_label"):
                    continue

                report["found_labels"].append({
                    "label": txt,
                    "location": f"p{p_no} y={yc:.1f}",
                    "section": dec.section,
                    "decision": dec.action,
                })

                if dec.action == "sensitive_blank":
                    report["sensitive_blank_fields"].append({
                        "label": txt,
                        "location": f"p{p_no} y={yc:.1f}",
                        "reason": dec.reason,
                    })
                    continue

                if dec.action == "skip":
                    report["skipped_fields"].append({
                        "label": txt,
                        "location": f"p{p_no} y={yc:.1f}",
                        "reason": dec.reason,
                    })
                    continue

                # '(전화: )'처럼 괄호로 감싼 안내 글자는 칸 이름이 아니다. 다른 칸으로 옮겨 쓰지 않는다.
                if re.fullmatch(r"\(.*\)", txt.strip()):
                    report["skipped_fields"].append({"label": txt, "location": f"p{p_no} y={yc:.1f}",
                                                     "reason": "inline_template_label"})
                    continue
                right_adj = _right_adjacent_cell(cell, v_lines)
                below_adj = _below_adjacent_cell(cell, h_lines)
                target_adj = None
                if right_adj is not None:
                    spans_r = _words_in_rect(words, right_adj)
                    if not spans_r:
                        target_adj = right_adj
                    else:
                        min_x_in_adj = min(s["bbox"][0] for s in spans_r)
                        if min_x_in_adj - right_adj[0] >= 120:
                            target_adj = (right_adj[0], right_adj[1], min_x_in_adj - 6, right_adj[3])
                # 오른쪽 칸에 '자택:'·'휴대전화:'·'☎' 같은 작은 라벨이 있으면 값 자리는 그 칸이다(아래 칸으로 내려가지 않는다).
                right_has_sublabels = right_adj is not None and any(
                    re.search(r"[:：☎]|휴대|전화|자택|이메일|E-?mail", s.get("text", ""), re.I)
                    for s in _words_in_rect(words, right_adj))
                # 아래 칸 바로 왼쪽 칸이 같은 높이의 칸이고 그 안에 칸 이름(예: '주 소')이 있으면 그 이름의 자리다.
                # 여러 줄을 묶은 '신청인' 같은 머리 칸은 높이가 달라 해당하지 않는다.
                below_owned = False
                if below_adj is not None:
                    left_cell = _cell_bounds_for_rect((below_adj[0] - 4, below_adj[1] + 2, below_adj[0] - 2, below_adj[3] - 2),
                                                      h_lines, v_lines, vis_x0, vis_x1)
                    below_owned = bool(left_cell and abs((left_cell[3] - left_cell[1]) - (below_adj[3] - below_adj[1])) < 4
                                       and left_cell[2] <= below_adj[0] + 2 and _words_in_rect(words, left_cell))
                    # 아래 칸이 옆 칸까지 합쳐진 큰 칸이고 그 안에 이미 칸 이름(예: '주소')이 있으면 다른 칸이다.
                    cx, cy = (below_adj[0] + below_adj[2]) / 2, (below_adj[1] + below_adj[3]) / 2
                    real_below = _cell_bounds_for_rect((cx - 1, cy - 1, cx + 1, cy + 1), h_lines, v_lines, vis_x0, vis_x1)
                    if real_below and real_below[0] < below_adj[0] - 4 and _words_in_rect(words, real_below):
                        below_owned = True
                    # 제목 줄 아래 값 줄처럼 좌우 폭이 라벨 칸과 같을 때만 내려간다(폭이 다르면 다른 칸·다른 구역이다).
                    if real_below and (abs(real_below[0] - cell[0]) > 4 or abs(real_below[2] - cell[2]) > 4):
                        below_owned = True
                if (target_adj is None and below_adj is not None and not right_has_sublabels and not below_owned
                        and not _words_in_rect(words, below_adj)):
                    if right_adj is None or _words_in_rect(words, right_adj):
                        target_adj = below_adj

                if target_adj is None and dec.field_key in fm.IN_CELL_KEYS and cell[2] - lb[2] >= 100:
                    # 칸 왼쪽 위에 라벨만 있고 오른쪽이 비어 있으면(정부 서식) 같은 칸 라벨 뒤에 쓴다.
                    in_cell = (lb[2] + 8, max(cell[1], lb[1] - 3), cell[2] - 4, min(cell[3], lb[3] + 3))
                    if not _words_in_rect(words, (lb[2] + 4, cell[1], cell[2], cell[3])):
                        target_adj = in_cell
                if (target_adj is None and dec.field_key in fm.IN_CELL_KEYS and cell[3] - lb[3] >= 14
                        and cell[2] - cell[0] >= 60):
                    # 칸 위쪽에 라벨이 있고 그 아래가 비어 있으면 같은 칸 안 라벨 아래에 쓴다.
                    under = (cell[0] + 4, lb[3] + 2, cell[2] - 4, cell[3] - 2)
                    if not _words_in_rect(words, under):
                        target_adj = under
                if target_adj is None:
                    report["skipped_fields"].append({
                        "label": txt,
                        "location": f"p{p_no} y={yc:.1f}",
                        "reason": "no_empty_adjacent_cell",
                    })
                    continue
                if overlaps_placed(target_adj):
                    continue

                ok, fs_used, mode = _plan_insert(draw_queue, target_adj, dec.value or "", max_fs=9.5)
                if ok:
                    placed_rects.append(target_adj)
                    report["filled_fields"].append({
                        "label": txt,
                        "location": f"p{p_no} rect={[round(v, 1) for v in target_adj]}",
                        "field_key": dec.field_key,
                        "value": dec.value,
                        "section": dec.section,
                        "fontsize": fs_used,
                        "mode": mode,
                    })
                else:
                    report["skipped_fields"].append({
                        "label": txt,
                        "location": f"p{p_no} rect={[round(v, 1) for v in target_adj]}",
                        "reason": mode,
                    })

            if draw_queue:
                page_overlays.append((p_idx, tuple(float(v) for v in page.bbox), draw_queue))

    report["filled_count"] = len(report["filled_fields"])
    if report["filled_count"] == 0:
        report["status"] = "no_fillable_fields"
        return data, report

    for p_idx, page_bbox, draw_cmds in page_overlays:
        _merge_overlay_page(writer.pages[p_idx], draw_cmds, page_bbox)

    out_buf = io.BytesIO()
    writer.write(out_buf)
    return out_buf.getvalue(), report
