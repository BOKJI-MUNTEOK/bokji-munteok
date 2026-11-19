"""기본 테마 전환은 Streamlit에 맡기고 화면 스타일 메뉴의 모양만 맞춘다."""
from base64 import b64encode
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).resolve().parent.parent / "assets"
_css = (ASSETS / "appearance.css").read_text(encoding="utf-8")
_js = (ASSETS / "appearance.js").read_text(encoding="utf-8")
_dark_logo = b64encode((ASSETS / "logo" / "mark-dark.svg").read_bytes()).decode("ascii")


def render():
    """서버 콜백 없이 기본 테마 메뉴와 앱 전용 색상을 연결한다."""
    st.html(f'<style>:root{{--bm-dark-logo:url("data:image/svg+xml;base64,{_dark_logo}")}}{_css}</style>')
    # 실행 중인 런타임에 등록한다. 동일 정의의 재등록은 멱등이며,
    # AppTest처럼 새 런타임이 생겨도 이전 등록 객체를 재사용하지 않는다.
    bridge = st.components.v2.component("bokji_appearance", js=_js, isolate_styles=False)
    bridge(key="bokji_appearance", height=0)
