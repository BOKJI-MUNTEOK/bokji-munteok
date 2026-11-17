FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 기존 앱과 데이터만 복사한다. 로컬 인증 정보와 환경 파일은 포함하지 않는다.
COPY app.py .
COPY .streamlit/config.toml .streamlit/config.toml
COPY src/ src/
COPY data/sample/ data/sample/
COPY data/ai_cache/ data/ai_cache/
COPY eval/ eval/
COPY assets/ assets/
COPY prompts/ prompts/

# Vercel Container Runtime의 기본 HTTP 포트는 80이다.
EXPOSE 80
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=80", "--server.headless=true"]
