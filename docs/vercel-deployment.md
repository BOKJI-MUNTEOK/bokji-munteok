# 기존 Streamlit 앱의 Vercel 배포

기준 코드는 Vercel 이전 직전 커밋 `e58f8ed209cd3c116c95284e4d6289df70533552`입니다.
`app.py`, `src/`, `.streamlit/config.toml`, 데이터, 의존성을 변경하지 않고 그대로 실행합니다.
별도 FastAPI 앱이나 HTML/JavaScript 화면으로 재작성하지 않습니다.

운영 주소: [복지문턱](https://bokji-munteok.vercel.app/) · [내 복지 찾기](https://bokji-munteok.vercel.app/find)

`kdh0381/bokji-munteok` 프로젝트에 기존 앱을 배포했습니다.
이제 GitHub Actions 워크플로로 `main` 변경을 자동 배포하도록 구성합니다.
배포용 `VERCEL_TOKEN`을 저장소 Actions Secret에 등록하고, 기존 Vercel 프로젝트 접근 권한을 확인했습니다.
워크플로가 포함된 PR을 `main`에 병합하면 첫 자동 배포가 실행됩니다.
이번 설정 작업에서는 운영 배포를 실행하지 않습니다.

기존 Streamlit 주소: https://bokji-munteok.streamlit.app

## 자동 배포 설정

2026-11-17 인증 키 등록과 워크플로 정적 검사를 완료했습니다.
사용자 요청에 따라 `main` 병합과 실제 자동 배포 실행은 보류한 상태입니다.
아래 1~2번은 새로 연결하거나 인증 키를 교체할 때 사용하는 절차입니다.

현재 Vercel Hobby 팀은 GitHub 조직 저장소를 직접 연결할 수 없어,
[Vercel이 지원하는 GitHub Actions 방식](https://vercel.com/kb/guide/how-can-i-use-github-actions-with-vercel)을 사용합니다.
요금제나 원본 앱의 코드·UI·기능은 변경하지 않습니다.

1. [Vercel Tokens](https://vercel.com/account/tokens)에서 배포용 토큰을 만듭니다.
   이름은 `bokji-munteok-github-actions`로 지정하고, 접근 범위는 `kdh0381` 팀의 이 프로젝트로 제한합니다.
2. [GitHub Actions Secrets](https://github.com/BOKJI-MUNTEOK/bokji-munteok/settings/secrets/actions)에
   이름이 `VERCEL_TOKEN`인 Repository secret으로 등록합니다. 토큰 값은 코드·문서·PR에 적지 않습니다.
3. 배포할 준비가 되면 이 워크플로가 포함된 PR을 `main`으로 Squash and merge합니다.
   **이 병합부터 운영 배포가 실행됩니다.**
4. [GitHub Actions](https://github.com/BOKJI-MUNTEOK/bokji-munteok/actions)에서
   `Vercel Production Deployment`의 결과를 확인합니다.

워크플로는 다음과 같이 동작합니다.

- `main` push·PR 병합 시 운영 배포합니다. PR 브랜치 push와 PR 생성은 배포하지 않습니다.
- Python 3.12에서 기존 단위 테스트를 실행하고, 실패하면 배포하지 않습니다.
- Vercel CLI 65.16.0으로 원본 소스를 업로드하고, Vercel이 기존 Dockerfile로 빌드합니다.
- 현재 프로젝트 `bokji-munteok`과 운영 주소를 계속 사용합니다.
- 배포를 순서대로 처리해 이전 커밋이 최신 배포를 뒤늦게 덮어쓰지 않게 합니다.
- 수동 재실행은 Actions의 `Run workflow`에서 `main`을 선택합니다.
- 인증 키가 없거나 만료되면 실패합니다. 만료 전에 새 토큰을 만들어 같은 Secret을 갱신합니다.

조직 저장소 직접 연결 제한: [Vercel Limits](https://vercel.com/docs/limits#connecting-a-project-to-a-git-repository).

## 배포 구성

- Vercel Framework Preset: **Container**
- Root Directory: 저장소 루트
- Dockerfile: 루트의 `Dockerfile`
- 실행: `python -m streamlit run app.py --server.address=0.0.0.0 --server.port=80 --server.headless=true`
- Python UTF-8 모드는 컨테이너 환경변수로 설정합니다.
- `.dockerignore`와 `.vercelignore`에서 로컬 인증·환경 파일을 제외합니다.

Vercel Container Runtime과 WebSocket은 현재 공식 지원됩니다.
Streamlit 자체가 제공하는 화면, 대화 세션, 추천, 분석, 서류 다운로드를 사용합니다.

## 수동 배포 명령

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

2026-11-17 운영 배포에서 다음을 확인했습니다.

- 원본 커밋과 앱 코드·테마·데이터·의존성의 Git diff 없음
- 기존 테스트 21개 통과
- Vercel 컨테이너 빌드 및 운영 배포 성공
- 공개 주소의 `/_stcore/health` 응답 `ok`, 5개 페이지 HTTP 200
- WebSocket으로 원본 Streamlit 스크립트 실행 완료

브라우저 화면 비교는 하지 않았으며, 사용자가 직접 확인합니다.

Vercel의 WebSocket 연결은 Function 최대 실행 시간이 지나면 종료됩니다.
재연결이 다른 인스턴스로 갈 수 있으므로 장시간 사용 중 입력 세션이 초기화될 수 있습니다.
Streamlit의 세션과 생성 파일은 메모리에 있으므로 여러 인스턴스 환경의 파일 다운로드도 확인이 필요합니다.
이 제약을 해결하려고 원본 코드의 세션·저장 방식을 임의로 바꾸지 않습니다.

공식 근거: [Container Runtime](https://vercel.com/docs/functions/runtimes/container),
[WebSockets](https://vercel.com/docs/functions/websockets),
[Streamlit 서버 구조](https://docs.streamlit.io/develop/concepts/architecture/architecture).
