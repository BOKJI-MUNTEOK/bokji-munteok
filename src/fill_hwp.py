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
import threading
from pathlib import Path
from typing import Any

import field_format
import fill_hwp_plan as hwp_plan
import form_matcher as fm

ROOT_DIR = Path(__file__).resolve().parent.parent
JAR_NAME = "hwplib-1.1.10.jar"
JAR_URL = "https://repo1.maven.org/maven2/kr/dogfoot/hwplib/1.1.10/hwplib-1.1.10.jar"
JAR_SHA256 = "fce557a7b4aff0c626c544558e0939459601a008d9378457b6183f7f3ae9d1b9"  # Dockerfile과 같은 값
_SETUP_LOCK = threading.Lock()
_setup_tried = False


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
    if hwplib_dir is None and os.environ.get("HWPLIB_AUTO_SETUP") == "1" and auto_setup(ROOT_DIR / ".cache" / "hwplib"):
        return _resolve_hwplib_runtime(ROOT_DIR / ".cache" / "hwplib")
    return None


def auto_setup(base: Path) -> bool:
    """Docker를 쓰지 않는 배포(Streamlit Community Cloud)에서 hwplib을 한 번 준비한다.

    HWPLIB_AUTO_SETUP=1일 때만 쓴다. Maven에서 받은 jar의 SHA-256을 Dockerfile과 같은 값으로 확인하고
    java/HwpLibTool.java를 javac로 컴파일한다. 실패하면 False(HWP는 원본 그대로 두는 기존 동작).
    """
    global _setup_tried
    with _SETUP_LOCK:
        if (base / JAR_NAME).is_file() and (base / "classes" / "HwpLibTool.class").is_file():
            return True
        javac = shutil.which("javac")
        if _setup_tried or not javac:
            return False
        _setup_tried = True
        import hashlib
        import urllib.request
        try:
            with urllib.request.urlopen(JAR_URL, timeout=60) as r:
                data = r.read()
            if hashlib.sha256(data).hexdigest() != JAR_SHA256:
                return False
            (base / "classes").mkdir(parents=True, exist_ok=True)
            jar = base / JAR_NAME
            tmp = jar.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(jar)
            done = subprocess.run([javac, "-encoding", "UTF-8", "-cp", str(jar), "-d", str(base / "classes"),
                                   str(ROOT_DIR / "java" / "HwpLibTool.java")], capture_output=True, timeout=180)
            return done.returncode == 0 and (base / "classes" / "HwpLibTool.class").is_file()
        except Exception:
            return False


def _dump_text(node: Any) -> str:
    """inspect 결과에 든 글자를 모은다(날짜·전화 표기 감지용)."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return "\n".join(_dump_text(v) for v in node.values())
    if isinstance(node, list):
        return "\n".join(_dump_text(v) for v in node)
    return ""


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
    styles: dict[str, str] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """styles를 주면 서류에서 표기를 읽는 대신 그 표기를 쓴다(좁은 칸 재시도용)."""
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

        # 이 서류에 쓰인 날짜·전화 표기를 따른다.
        dump_text = _dump_text(doc_dump)
        styles = styles or field_format.detect_document_styles(dump_text)
        if fm.BENEFICIARY_IS_APPLICANT.get() is None:
            token = fm.BENEFICIARY_IS_APPLICANT.set(fm.beneficiary_is_applicant(dump_text))
        else:
            token = None
        base_report["formats"] = styles
        try:
            plan = hwp_plan.plan_hwp_fills(doc_dump, field_format.adapt_profile(prof, styles))
        finally:
            if token is not None:
                fm.BENEFICIARY_IS_APPLICANT.reset(token)
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
