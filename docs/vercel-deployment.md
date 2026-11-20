# 이전 Vercel 배포 기록

현재 운영은 기존 [Streamlit 주소](https://bokji-munteok.streamlit.app/)로 전환합니다. 앱 연결·Secrets·MongoDB·예약 수집·웹 증분 갱신은 [Streamlit 배포 안내](streamlit-deployment.md)를 따릅니다.

Vercel의 자동 배포는 중단했습니다. 이 문서는 이전 실행 환경과 필요한 경우의 수동 배포 절차를 보관합니다. 과거 확인 결과가 현재 Vercel 주소의 정상 동작을 뜻하지는 않습니다.

## 이전 구성

Vercel 이전 당시에는 커밋 `e58f8ed209cd3c116c95284e4d6289df70533552`의 Streamlit 앱을 그대로 옮겼습니다. 공식 API 연동은 `docs: update README.md (#23)`(772720f)을 기준으로 진행했습니다. 이후 원본 화면·점수식을 유지하며 MongoDB 영구 저장과 예약 수집을 연결했습니다.

- 이전 주소: https://bokji-munteok.vercel.app
- Vercel 프로젝트: `kdh0381/bokji-munteok`
- Framework Preset: `Container`
- 실행 지역: `vercel.json`의 `icn1`(서울)
- 실행 파일: 저장소 루트 `Dockerfile`
- 실행 명령: `python -m streamlit run app.py --server.address=0.0.0.0 --server.port=80 --server.headless=true`
- `.dockerignore`·`.vercelignore`로 로컬 인증 파일·환경 파일 제외

## 자동 배포 중단

이전에는 `main` 변경마다 GitHub Actions가 테스트 후 Vercel을 배포했습니다. 현재 설정은 다음과 같습니다.

- `.github/workflows/deploy-vercel.yml`의 `push` 실행을 제거했습니다. **Vercel Manual Deployment**는 `workflow_dispatch`만 제공합니다.
- `vercel.json`의 `git.deploymentEnabled`를 `false`로 설정해 Git 연동 자동 배포도 중단했습니다.
- PR·`main`의 테스트는 `.github/workflows/ci.yml`의 **Tests**로 분리했습니다.
- 기존 Vercel 토큰·프로젝트를 코드 변경만으로 삭제하거나 요금제를 바꾸지 않습니다.

`main`에 PR을 병합해도 Vercel 배포는 실행하지 않습니다. [공식 Git 자동 배포 설정](https://vercel.com/docs/project-configuration/git-configuration#turning-off-all-automatic-deployments)

## 필요한 경우의 수동 배포

Vercel을 다시 사용하기로 결정한 경우에만 실행합니다. 계정·프로젝트가 배포 가능한 상태이고 `VERCEL_TOKEN`이 유효해야 합니다.

GitHub Actions의 **Vercel Manual Deployment → Run workflow → main**을 선택하면 Python 3.12 테스트 후 Vercel CLI로 운영 배포합니다. 저장소의 `VERCEL_TOKEN` Secret과 워크플로의 `VERCEL_ORG_ID`·`VERCEL_PROJECT_ID`를 사용합니다. 실행에는 별도 배포 자원이 사용됩니다.

로컬 CLI로도 실행할 수 있습니다.

```bash
vercel login
vercel link --project bokji-munteok --scope kdh0381
vercel deploy --scope kdh0381          # Preview
vercel deploy --prod --scope kdh0381   # 운영
```

Vercel 웹에는 `MONGODB_URI`와 `MONGODB_DATABASE=bokji_munteok`을 비밀 환경변수로 설정합니다. 선택 기능에는 AI·주소 API 키가 필요할 수 있습니다. 공공데이터 예약 수집은 GitHub Actions에서 계속 담당하며 웹 방문으로 시작하지 않습니다. 토큰·연결 문자열·실제 키는 코드·문서·PR에 적지 않습니다.

## 과거 확인 기록과 제약

최초 이전 당시 원본 코드·테마·데이터·의존성을 유지했고 기존 테스트 21개, 컨테이너 빌드, 공개 주소의 health 및 5개 페이지 HTTP 응답, WebSocket의 Streamlit 스크립트 실행을 확인했습니다. 브라우저 화면 비교는 사용자가 직접 진행했습니다. 공식 API 연동 당시 전체 목록 5,289건과 양쪽 상세 응답·규칙 점수 계산을 확인했습니다. 수량은 그때의 기록이며 현재 목록에 고정하지 않습니다.

Vercel에서 Streamlit을 실행하면 CPU·메모리 사용량과 장시간 WebSocket 연결이 함수 사용량에 반영됩니다. 함수 실행 시간이나 다른 인스턴스로의 재연결은 입력 세션과 메모리의 다운로드 파일에 영향을 줄 수 있습니다. 기존 팀의 사용량을 소모한 상태에서 코드 최적화만 적용한다고 이미 사용한 한도가 초기화되는 것은 아닙니다.

공식 자료: [Container Runtime](https://vercel.com/docs/functions/runtimes/container), [Functions 사용량](https://vercel.com/docs/functions/usage-and-pricing), [WebSockets](https://vercel.com/docs/functions/websockets), [Streamlit 서버 구조](https://docs.streamlit.io/develop/concepts/architecture/architecture).
