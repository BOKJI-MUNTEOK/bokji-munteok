"""Local, browser-only preview of the exact bytes offered for download.

The iframe bundles its parsers, fonts and workers. Document contents are never
sent to a conversion service or persisted in browser storage.
"""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import streamlit.components.v1 as components

MAX_PREVIEW_BYTES = 20 * 1024 * 1024
_ASSETS = Path(__file__).resolve().parent.parent / "assets" / "document_viewer"
_component = components.declare_component("bokji_document_viewer", path=str(_ASSETS))


def render_document(data: bytes, filename: str, format: str, key: str,
                    focus_texts: list[str] | None = None, pages: list[int] | None = None) -> dict:
    """Render actual filled PDF/HWP/HWPX pages and return preview status.

    ``ready`` means rendering completed, not that a template was certified or
    every field was correctly filled. The caller must keep those states apart.
    """
    if format not in {"pdf", "hwp", "hwpx"}:
        return {"status": "unsupported", "page_count": 0, "format": format}
    if not data or len(data) > MAX_PREVIEW_BYTES:
        return {"status": "error", "page_count": 0, "format": format,
                "reason": "preview_size_limit"}
    request_id = hashlib.sha256(data).hexdigest()
    pending = {"status": "loading", "page_count": 0, "format": format,
               "request_id": request_id}
    result = _component(
        data=base64.b64encode(data).decode("ascii"), filename=Path(filename).name,
        format=format, request_id=request_id, default=pending, key=key,
        focus_texts=[str(x)[:200] for x in (focus_texts or [])[:4] if x],
        pages=[int(p) for p in (pages or [])[:40] if int(p) > 0],
    )
    # Streamlit can return a component's previous value during rerendering.
    if not isinstance(result, dict) or result.get("request_id") != request_id:
        return pending
    return {k: result[k] for k in (
        "status", "page_count", "format", "request_id", "reason", "layout_warnings", "focused_page"
    ) if k in result}
