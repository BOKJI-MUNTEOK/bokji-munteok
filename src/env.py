"""저장소 루트의 .env를 읽어 환경변수로 넣는다(이미 있는 값은 덮지 않음). python-dotenv 없이 동작."""
import os
from pathlib import Path


SECRET_KEYS = ("MONGODB_URI", "MONGODB_DATABASE", "LLM_API_KEY", "OPENAI_API_KEY", "JUSO_API_KEY", "HWPLIB_AUTO_SETUP",
               "DATA_GO_KR_KEY", "DATA_GO_KR_KEY_2", "DATA_GO_KR_KEY_3")


def _load_streamlit_secrets() -> None:
    """Streamlit Community Cloud의 Secrets(최상위 문자열 값)를 환경변수로 넣는다. 없으면 조용히 넘어간다."""
    try:
        import streamlit as st
        secrets = st.secrets
        for k in SECRET_KEYS:
            if k in secrets and isinstance(secrets[k], (str, int)) and str(secrets[k]).strip():
                os.environ.setdefault(k, str(secrets[k]).strip())
    except Exception:
        pass


def load_env(path: Path = Path(__file__).resolve().parent.parent / ".env") -> None:
    _load_streamlit_secrets()
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
