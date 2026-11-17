# 기존 Streamlit 앱의 Vercel 배포

Vercel 이전 당시에는 커밋 `e58f8ed209cd3c116c95284e4d6289df70533552`의 Streamlit 앱을 그대로 옮겼습니다.
공식 API 연동은 `docs: update README.md (#23)`(772720f)을 기준으로 진행했습니다.
화면 구성·테마·점수 계산식을 유지하며, 데이터 로딩은 고정 샘플에서 공식 API 전체 목록·선택한 서비스 상세 조회로 변경했습니다.

운영 주소: [복지문턱](https://bokji-munteok.vercel.app/) · [내 복지 찾기](https://bokji-munteok.vercel.app/find)

`kdh0381/bokji-munteok` 프로젝트에 기존 앱을 배포했습니다.
GitHub Actions 워크플로로 `main` 변경을 자동 배포합니다.
배포용 `VERCEL_TOKEN`을 저장소 Actions Secret에 등록하고, 기존 Vercel 프로젝트 접근 권한을 확인했습니다.
`main`에 PR을 병합하면 기존 테스트를 통과한 뒤 운영 배포가 실행됩니다.

기존 Streamlit 주소: https://bokji-munteok.streamlit.app

## 자동 배포 설정

2026-11-17 인증 키 등록과 워크플로 정적 검사를 완료했습니다.
아래 1~2번은 새로 연결하거나 인증 키를 교체할 때 사용하는 절차입니다.

현재 Vercel Hobby 팀은 GitHub 조직 저장소를 직접 연결할 수 없어,
[Vercel이 지원하는 GitHub Actions 방식](https://vercel.com/kb/guide/how-can-i-use-github-actions-with-vercel)을 사용합니다.
기존 요금제와 Streamlit Container 배포 구성을 사용합니다.

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
- CI는 `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `VERCEL_TOKEN` 환경변수로 배포 대상을 지정합니다.
  프로젝트 범위 토큰에서 계정 조회 오류가 발생하는 `--scope` 옵션은 사용하지 않습니다.
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

## 복지 데이터 API 설정

- Vercel 프로젝트 `bokji-munteok`의 **Settings → Environment Variables**에 `DATA_GO_KR_KEY`를 Secret으로 설정합니다.
- 적용 대상은 **Production·Preview**입니다. 2026-11-17 등록을 확인했습니다.
- 키에는 공공데이터포털의 중앙부처복지서비스·지자체복지서비스 활용 승인이 모두 필요합니다.
- 로컬에서는 `.env.example`을 `.env`로 복사해 같은 변수에 입력합니다. Encoding/Decoding 키 모두 지원합니다.
- 환경변수 변경은 새 배포에 적용됩니다. 키를 Dockerfile, GitHub 소스, README, PR 본문에 넣지 않습니다.
- Actions 테스트는 API 응답을 모의 처리하므로 공공데이터 키와 실제 호출이 필요 없습니다. 배포 컨테이너가 Vercel 환경변수를 읽습니다.

목록은 `totalCount`에 맞춰 마지막 페이지까지 받습니다. 2026-11-17 실호출 확인 결과 중앙 461건·지자체 4,828건(총 5,289건)입니다. 이 숫자를 코드에 고정하지 않습니다.

상세는 사용자가 ‘신청 방법 보기’를 누를 때 조회합니다. 수천 건의 상세 API를 시작할 때 일괄 호출하지 않으며, 미조회 서비스의 점수는 비워 둡니다. 신청 난이도 필터·점수 정렬·분석 통계는 상세 조회가 완료된 자료 기준입니다. 기존 점수 규칙과 가중치는 같습니다.

서버 임시 저장소에 목록 6시간·상세 24시간 캐시를 둡니다. 만료 후 다음 요청 때 갱신하고, 지자체 목록의 수정일이 바뀌면 기존 상세 캐시도 갱신합니다. 갱신 실패 시 최대 7일 이내의 정상 응답을 경고·조회 시각과 함께 표시합니다. 정상 캐시가 없으면 오류를 알리며 샘플로 대체하지 않습니다. 인증·한도 오류는 5분간 반복 요청을 막습니다.

캐시는 인스턴스별이며 재시작·재배포·확장 시 공유되지 않습니다. 운영 호출량이 커지면 공공데이터포털의 승인 트래픽을 확인하고 운영계정 한도나 공용 저장소를 조정해야 합니다. API 키와 요청 URL은 캐시·오류 메시지에 넣지 않습니다.

새 서비스의 AI 설명은 자동 생성하지 않습니다. 기존 캐시의 원문이 현재 상세와 같을 때만 사용하고, 다르면 공식 안내를 표시합니다. 기존 정답표의 정확도 역시 같은 원문의 조회 자료만 비교합니다.

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

Vercel 이전 완료 시 다음을 확인했습니다.

- 원본 커밋과 앱 코드·테마·데이터·의존성의 Git diff 없음
- 기존 테스트 21개 통과
- Vercel 컨테이너 빌드 및 운영 배포 성공
- 공개 주소의 `/_stcore/health` 응답 `ok`, 5개 페이지 HTTP 200
- WebSocket으로 원본 Streamlit 스크립트 실행 완료

브라우저 화면 비교는 하지 않았으며, 사용자가 직접 확인합니다.

공식 API 연동 검증에서는 전체 목록 5,289건과 양쪽 상세 응답·기존 규칙 점수 계산을 확인했습니다.
단위 테스트는 전체 페이지·캐시·오류 처리·미조회 점수·기존 AI 원문 대조를 포함하며, 실제 키를 사용하지 않습니다.

Vercel의 WebSocket 연결은 Function 최대 실행 시간이 지나면 종료됩니다.
재연결이 다른 인스턴스로 갈 수 있으므로 장시간 사용 중 입력 세션이 초기화될 수 있습니다.
Streamlit의 세션과 생성 파일은 메모리에 있으므로 여러 인스턴스 환경의 파일 다운로드도 확인이 필요합니다.
이 제약을 해결하려고 원본 코드의 세션·저장 방식을 임의로 바꾸지 않습니다.

공식 근거: [Container Runtime](https://vercel.com/docs/functions/runtimes/container),
[WebSockets](https://vercel.com/docs/functions/websockets),
[Streamlit 서버 구조](https://docs.streamlit.io/develop/concepts/architecture/architecture).
