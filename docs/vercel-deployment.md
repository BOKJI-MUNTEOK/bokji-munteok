# 기존 Streamlit 앱의 Vercel 배포

기준 코드는 Vercel 이전 직전 커밋 `e58f8ed209cd3c116c95284e4d6289df70533552`입니다.
`app.py`, `src/`, `.streamlit/config.toml`, 데이터, 의존성을 변경하지 않고 그대로 실행합니다.
별도 FastAPI 앱이나 HTML/JavaScript 화면으로 재작성하지 않습니다.

## 배포 구성

- Vercel Framework Preset: **Container**
- Root Directory: 저장소 루트
- Dockerfile: 루트의 `Dockerfile`
- 실행: `python -m streamlit run app.py --server.address=0.0.0.0 --server.port=80 --server.headless=true`
- Python UTF-8 모드는 컨테이너 환경변수로 설정합니다.
- `.dockerignore`와 `.vercelignore`에서 로컬 인증·환경 파일을 제외합니다.

Vercel Container Runtime과 WebSocket은 현재 공식 지원됩니다.
Streamlit 자체가 제공하는 화면, 대화 세션, 추천, 분석, 서류 다운로드를 사용합니다.

## 배포 명령

```bash
vercel login
vercel link --project bokji-munteok --scope kdh0381
vercel deploy --scope kdh0381
```

Preview를 확인한 뒤 main 병합과 운영 배포를 진행합니다.

```bash
vercel deploy --prod --scope kdh0381
```

## 확인 범위와 운영 제약

이 PR은 사용자가 직접 화면을 확인하도록 요청한 작업입니다. 브라우저 화면 비교는 하지 않습니다.
기존 코드와의 Git 비교, 기존 테스트, 컨테이너 빌드 결과를 기록합니다.

Vercel의 WebSocket 연결은 Function 최대 실행 시간이 지나면 종료됩니다.
재연결이 다른 인스턴스로 갈 수 있으므로 장시간 사용 중 입력 세션이 초기화될 수 있습니다.
Streamlit의 세션과 생성 파일은 메모리에 있으므로 여러 인스턴스 환경의 파일 다운로드도 확인이 필요합니다.
이 제약을 해결하려고 원본 코드의 세션·저장 방식을 임의로 바꾸지 않습니다.

공식 근거: [Container Runtime](https://vercel.com/docs/functions/runtimes/container),
[WebSockets](https://vercel.com/docs/functions/websockets),
[Streamlit 서버 구조](https://docs.streamlit.io/develop/concepts/architecture/architecture).
