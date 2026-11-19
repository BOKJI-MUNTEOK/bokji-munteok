"""HWP 5.0 서식 자동 기입 엔진 (Java hwplib subprocess 래퍼).

보안·안정성 원칙:
- 요청마다 임시 디렉터리(tempfile.TemporaryDirectory) 안에서만 in.hwp, out.hwp를 처리하고 즉시 삭제하며,
  개인정보가 들어갈 수 있는 기입 계획 JSON은 디스크 파일 대신 표준 입력(stdin)으로만 전달한다.
- 표준 출력·로그에 사용자 개인정보를 출력하지 않는다.
- java 또는 hwplib 런타임이 없거나 실행 실패/타임아웃(기본 20초) 시 손상된 파일 대신 원본 바이트를 그대로 반환한다.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import fill_hwp_plan as hwp_plan
import form_matcher as fm

ROOT_DIR = Path(__file__).resolve().parent.parent
JAR_NAME = "hwplib-1.1.10.jar"


def _resolve_hwplib_runtime(hwplib_dir: Path | None = None) -> tuple[str, Path, Path] | None:
    java_bin = shutil.which("java")
    if not java_bin:
        return None
    if hwplib_dir is not None:
        candidates = [hwplib_dir]
    else:
        candidates = []
        if os.environ.get("HWPLIB_DIR"):
            candidates.append(Path(os.environ["HWPLIB_DIR"]))
        candidates.append(ROOT_DIR / ".cache" / "hwplib")
        candidates.append(Path("/opt/hwplib"))

    for base in candidates:
        jar = base / JAR_NAME
        classes = base / "classes"
        if jar.is_file() and (classes / "HwpLibTool.class").is_file():
            return java_bin, jar, classes
    return None


def _safe_inspect_tag(stderr: str) -> str:
    """inspect(개인정보 미포함) 실패 요약 태그를 짧게 뽑는다."""
    for ln in (stderr or "").splitlines():
        cleaned = " ".join(ln.strip().split())
        if cleaned:
            return cleaned[:120]
    return ""


def fill_hwp_bytes(
    data: bytes,
    profile: dict[str, str] | None = None,
    *,
    timeout_sec: int = 20,
    hwplib_dir: Path | None = None,
) -> tuple[bytes, dict[str, Any]]:
    prof = fm.default_profile() if profile is None else profile
    base_report: dict[str, Any] = {
        "format": "hwp",
        "engine": "hwplib:1.1.10",
        "status": "ok",
        "found_labels": [],
        "filled_fields": [],
        "skipped_fields": [],
        "sensitive_blank_fields": [],
        "filled_count": 0,
    }

    runtime = _resolve_hwplib_runtime(hwplib_dir)
    if runtime is None:
        base_report["status"] = "unsupported"
        base_report["error"] = "hwplib_runtime_unavailable"
        return data, base_report

    if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        base_report["status"] = "unsupported"
        base_report["error"] = "not_hwp_ole2"
        return data, base_report

    java_bin, jar_path, classes_dir = runtime
    classpath = f"{jar_path}{os.pathsep}{classes_dir}"

    with tempfile.TemporaryDirectory(prefix="bokji_hwp_") as tmpdir:
        tmp = Path(tmpdir)
        in_path = tmp / "in.hwp"
        out_path = tmp / "out.hwp"
        in_path.write_bytes(data)

        try:
            insp = subprocess.run(
                [java_bin, "-cp", classpath, "HwpLibTool", "inspect", str(in_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_sec,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            base_report["status"] = "error"
            base_report["error"] = f"hwplib_inspect_error:{type(exc).__name__}"
            return data, base_report

        if insp.returncode != 0 or not insp.stdout.strip():
            tag = _safe_inspect_tag(insp.stderr)
            base_report["status"] = "error"
            base_report["error"] = f"hwplib_inspect_failed:{tag}" if tag else "hwplib_inspect_failed"
            return data, base_report

        try:
            doc_dump = json.loads(insp.stdout)
        except json.JSONDecodeError:
            base_report["status"] = "error"
            base_report["error"] = "hwplib_inspect_invalid_json"
            return data, base_report

        plan = hwp_plan.plan_hwp_fills(doc_dump, prof)
        rep = plan["report"]
        base_report.update({
            "found_labels": rep["found_labels"],
            "filled_fields": rep["filled_fields"],
            "skipped_fields": rep["skipped_fields"],
            "sensitive_blank_fields": rep["sensitive_blank_fields"],
            "filled_count": rep["filled_count"],
        })

        if not plan["cell_edits"] and not plan["para_edits"]:
            base_report["status"] = "no_fillable_fields"
            return data, base_report

        plan_payload = json.dumps(
            {"cell_edits": plan["cell_edits"], "para_edits": plan["para_edits"]},
            ensure_ascii=False,
        )

        try:
            res = subprocess.run(
                [java_bin, "-cp", classpath, "HwpLibTool", "fill", str(in_path), "-", str(out_path)],
                input=plan_payload,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_sec,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            base_report["status"] = "error"
            base_report["error"] = f"hwplib_fill_error:{type(exc).__name__}"
            base_report["filled_fields"] = []
            base_report["filled_count"] = 0
            return data, base_report

        if res.returncode != 0 or not out_path.is_file():
            base_report["status"] = "error"
            base_report["error"] = f"hwplib_fill_failed:rc={res.returncode}"
            base_report["filled_fields"] = []
            base_report["filled_count"] = 0
            return data, base_report

        out_bytes = out_path.read_bytes()
        if not out_bytes:
            base_report["status"] = "error"
            base_report["error"] = "hwplib_empty_output"
            base_report["filled_fields"] = []
            base_report["filled_count"] = 0
            return data, base_report

        return out_bytes, base_report
