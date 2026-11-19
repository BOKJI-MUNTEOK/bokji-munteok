FROM eclipse-temurin:17-jdk-jammy AS hwplib-builder

WORKDIR /build
COPY java/HwpLibTool.java java/HwpLibTool.java

RUN mkdir -p /opt/hwplib/classes && \
    curl -fsSL https://repo1.maven.org/maven2/kr/dogfoot/hwplib/1.1.10/hwplib-1.1.10.jar -o /opt/hwplib/hwplib-1.1.10.jar && \
    echo "fce557a7b4aff0c626c544558e0939459601a008d9378457b6183f7f3ae9d1b9  /opt/hwplib/hwplib-1.1.10.jar" | sha256sum -c - && \
    javac -encoding UTF-8 -cp /opt/hwplib/hwplib-1.1.10.jar -d /opt/hwplib/classes java/HwpLibTool.java

# 운영 이미지의 Debian 버전(python:3.12-slim은 현재 trixie)에 openjdk-17 패키지가 없으므로
# apt 대신 빌드에 쓴 것과 같은 Temurin 17 JRE를 그대로 복사한다.
FROM eclipse-temurin:17-jre-jammy AS jre

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONUTF8=1 \
    HWPLIB_DIR=/opt/hwplib \
    JAVA_HOME=/opt/java/openjdk \
    PATH="/opt/java/openjdk/bin:${PATH}"

COPY --from=jre /opt/java/openjdk /opt/java/openjdk

WORKDIR /app

COPY --from=hwplib-builder /opt/hwplib /opt/hwplib
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 기존 앱과 데이터만 복사한다. 로컬 인증 정보와 환경 파일은 포함하지 않는다.
COPY app.py .
COPY .streamlit/config.toml .streamlit/config.toml
COPY src/ src/
COPY third_party/ third_party/
COPY data/sample/ data/sample/
COPY data/ai_cache/ data/ai_cache/
COPY eval/ eval/
COPY assets/ assets/
COPY prompts/ prompts/

# Vercel Container Runtime의 기본 HTTP 포트는 80이다.
EXPOSE 80
CMD ["python", "-m", "streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=80", "--server.headless=true"]
