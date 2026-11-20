"""복지 양식 자동 기입 단일 진입 모듈 (.hwp / .hwpx / .pdf 판별 및 라우팅)."""
from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import fill_hwp
import fill_hwpx
import fill_pdf


_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_HWP5_MARKERS = (
    b"HWP Document File",
    b"F\x00i\x00l\x00e\x00H\x00e\x00a\x00d\x00e\x00r\x00",
    b"B\x00o\x00d\x00y\x00T\x00e\x00x\x00t\x00",
)
_XLS_MARKERS = (
    b"W\x00o\x00r\x00k\x00b\x00o\x00o\x00k\x00",
    b"B\x00o\x00o\x00k\x00",
)
_DOC_MARKER = b"W\x00o\x00r\x00d\x00D\x00o\x00c\x00u\x00m\x00e\x00n\x00t\x00"


def detect_signature(data: bytes) -> str:
    """파일 바이트의 실제 매직 시그니처와 컨테이너 내부 구조를 판별한다."""
    if not data:
        return "empty"

    head = data[:1024]
    # 기관 보안(파수 DRM)으로 암호화된 파일: 일반 컴퓨터에서는 어떤 프로그램으로도 열리지 않는다.
    if b"FasooSecureContainer" in head or b"DRMONE" in head:
        return "drm"
    if b"%PDF-" in head:
        return "pdf"

    if data[:4] in (b"PK\x03\x04", b"PK\x05\x06"):
        try:
            with zipfile.ZipFile(io.BytesIO(data), "r") as zf:
                names = [info.filename for info in zf.infolist()[:2000]]
                if any(n.startswith("Contents/section") and n.endswith(".xml") for n in names):
                    return "zip:hwpx"
                if "mimetype" in names:
                    info = zf.getinfo("mimetype")
                    if info.file_size <= 512 and b"hwp+zip" in zf.read("mimetype"):
                        return "zip:hwpx"
                if "xl/workbook.xml" in names:
                    return "zip:xlsx"
                if "word/document.xml" in names:
                    return "zip:docx"
                if "ppt/presentation.xml" in names:
                    return "zip:pptx"
                return "zip:other"
        except (zipfile.BadZipFile, OSError, ValueError):
            return "zip:corrupt"

    if data[:8] == _OLE2_MAGIC:
        if any(m in data for m in _HWP5_MARKERS):
            return "ole2:hwp"
        if any(m in data for m in _XLS_MARKERS):
            return "ole2:xls"
        if _DOC_MARKER in data:
            return "ole2:doc"
        return "ole2:other"

    if data[:17] == b"HWP Document File":
        return "hwp3"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"

    stripped = data[:256].lstrip()
    if stripped.startswith(b"{"):
        return "json"
    if stripped[:15].lower().startswith((b"<!doctype", b"<html")):
        return "html"
    if b"<HWPML" in head:
        return "hwpml"
    return "unknown"


def detect_format(data: bytes, filename: str = "") -> tuple[str, str]:
    """(지원 형식 키, 실제 시그니처)를 반환한다. 형식 키는 hwp / hwpx / pdf / unsupported."""
    sig = detect_signature(data)
    ext = Path(filename).suffix.lower() if filename else ""

    if sig == "pdf":
        return "pdf", sig
    if sig == "zip:hwpx":
        return "hwpx", sig
    if sig == "ole2:hwp":
        return "hwp", sig
    if sig == "ole2:other" and ext == ".hwp":
        return "hwp", sig
    return "unsupported", sig


def _birth_from_rrn(rrn: str) -> str:
    m = re.fullmatch(r"(\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])-?([0-9])(?:\d{6})?", (rrn or "").strip())
    if not m:
        return ""
    yy, mm, dd, g = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4)
    if g in ("1", "2", "5", "6"):
        century = 1900
    elif g in ("3", "4", "7", "8"):
        century = 2000
    elif g in ("9", "0"):
        century = 1800
    else:
        return ""
    try:
        return date(century + yy, mm, dd).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def build_profile(
    p: dict[str, Any] | None,
    extra: dict[str, Any] | None,
    *,
    today: date | None = None,
) -> dict[str, str]:
    """상담 프로필(p)과 추가 입력(extra)을 서식 기입용 사전에 매핑한다.

    사용자가 입력하지 않은 항목에 가상 기본값을 넣지 않는다.
    """
    p = p or {}
    extra = extra or {}

    name = str(p.get("name") or "").strip()
    rrn_raw = str(extra.get("rrn") or "").strip()
    if re.fullmatch(r"\d{13}", rrn_raw):
        rrn = f"{rrn_raw[:6]}-{rrn_raw[6:]}"
    else:
        rrn = rrn_raw

    birth_date = str(extra.get("birth_date") or "").strip() or _birth_from_rrn(rrn)
    phone = str(extra.get("phone") or "").strip()

    addr_parts = [str(x).strip() for x in (p.get("sido", ""), p.get("sgg", ""), extra.get("address", "")) if str(x or "").strip()]
    address = " ".join(addr_parts)

    bank = str(extra.get("bank") or "").strip()
    account_number = str(extra.get("account_number") or "").strip()
    account_holder = str(extra.get("account_holder") or "").strip()
    account_full = " ".join(x for x in (bank, account_number) if x)

    monthly_income = str(extra.get("monthly_income") or "").strip()
    proxy_name = str(extra.get("proxy") or "").strip()
    proxy_relation = str(extra.get("relation") or "").strip()
    proxy_phone = str(extra.get("proxy_phone") or "").strip()

    user_fields = {
        "applicant.name": name,
        "applicant.birth_date": birth_date,
        "applicant.rrn": rrn,
        "applicant.phone": phone,
        "applicant.address": address,
        "account.bank": bank,
        "account.number": account_number,
        "account.holder": account_holder,
        "account.full": account_full,
        "applicant.income_monthly": monthly_income,
        "proxy.name": proxy_name,
        "proxy.relation": proxy_relation,
        "proxy.phone": proxy_phone,
    }
    if not any(user_fields.values()):
        return {}

    d = today or date.today()
    user_fields["apply_date"] = d.strftime("%Y년 %m월 %d일")
    user_fields["apply_date_dot"] = d.strftime("%Y. %m. %d.")
    return user_fields


def fill_form(
    data: bytes,
    filename: str = "",
    profile: dict[str, str] | None = None,
    *,
    timeout_sec: int = 20,
    hwplib_dir: Path | None = None,
    styles: dict[str, str] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """첨부 바이트의 실제 형식을 판별해 알맞은 기입 엔진으로 처리한다.

    지원하지 않는 형식(zip, xls, xlsx, jpg 등)이나 오류 시에는 원본 바이트를 그대로 반환한다.
    예외/보고 사유에는 사용자 입력값을 넣지 않는다.
    """
    fmt, sig = detect_format(data, filename)
    fname = Path(filename).name if filename else ""

    if fmt == "unsupported":
        return data, {
            "format": "unsupported",
            "detected_format": "unsupported",
            "signature": sig,
            "filename": fname,
            "status": "unsupported",
            "opened": False,
            "found_labels": [],
            "filled_fields": [],
            "skipped_fields": [],
            "sensitive_blank_fields": [],
            "filled_count": 0,
            "error": f"unsupported_format:{sig}",
        }

    try:
        if fmt == "hwp":
            out_bytes, rep = fill_hwp.fill_hwp_bytes(
                data,
                profile=profile,
                timeout_sec=timeout_sec,
                hwplib_dir=hwplib_dir,
                styles=styles,
            )
        elif fmt == "hwpx":
            out_bytes, rep = fill_hwpx.fill_hwpx_bytes(data, profile=profile)
        else:
            out_bytes, rep = fill_pdf.fill_pdf_bytes(data, profile=profile)
    except Exception as exc:
        return data, {
            "format": fmt,
            "detected_format": fmt,
            "signature": sig,
            "filename": fname,
            "status": "error",
            "opened": False,
            "found_labels": [],
            "filled_fields": [],
            "skipped_fields": [],
            "sensitive_blank_fields": [],
            "filled_count": 0,
            "error": f"engine_error:{type(exc).__name__}",
        }

    report = dict(rep)
    report["detected_format"] = fmt
    report["signature"] = sig
    report["filename"] = fname
    status = report.get("status", "ok")
    filled_count = int(report.get("filled_count", 0) or 0)
    if status == "ok" and filled_count == 0:
        status = "no_fillable_fields"
        report["status"] = status
    report["opened"] = status in ("ok", "no_fillable_fields")
    return out_bytes, report
