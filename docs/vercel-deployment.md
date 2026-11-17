# 기존 Streamlit 앱의 Vercel 배포

Vercel 이전 당시에는 커밋 `e58f8ed209cd3c116c95284e4d6289df70533552`의 Streamlit 앱을 그대로 옮겼습니다.
공식 API 연동은 `docs: update README.md (#23)`(772720f)을 기준으로 진행했습니다.
화면 구성·테마·점수 계산식을 유지하며, 데이터 로딩은 고정 샘플에서 공식 API 전체 목록과 MongoDB에 저장한 상세 조회로 변경했습니다.

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
- 실행 지역: `vercel.json`의 `regions: ["icn1"]`로 서울 지정. MongoDB Atlas(AWS 서울)와 같은 지역을 사용합니다.
- Root Directory: 저장소 루트
- Dockerfile: 루트의 `Dockerfile`
- 실행: `python -m streamlit run app.py --server.address=0.0.0.0 --server.port=80 --server.headless=true`
- Python UTF-8 모드는 컨테이너 환경변수로 설정합니다.
- `.dockerignore`와 `.vercelignore`에서 로컬 인증·환경 파일을 제외합니다.

Vercel Container Runtime과 WebSocket은 현재 공식 지원됩니다.
Streamlit 자체가 제공하는 화면, 대화 세션, 추천, 분석, 서류 다운로드를 사용합니다.

## 복지 데이터 API·MongoDB 설정

- Atlas 프로젝트·무료 클러스터: **BOKJI-MUNTEOK** (AWS 서울), 데이터베이스: `bokji_munteok`.
- GitHub Actions Secrets: `DATA_GO_KR_KEY`, `DATA_GO_KR_KEY_2`, `DATA_GO_KR_KEY_3`, `MONGODB_URI`. 예약 수집에 사용합니다.
- Vercel 웹에는 `MONGODB_URI`가 필요합니다. 기존 API 키 환경변수는 웹 접속으로 API 수집을 시작하는 데 사용하지 않습니다.
- 키마다 중앙부처·지자체 API 활용 승인이 모두 필요합니다. Encoding/Decoding 키를 지원하고 중복 키는 합칩니다.
- `MONGODB_DATABASE` 기본값은 `bokji_munteok`입니다. 앱 DB 사용자에는 해당 DB의 `readWrite`만 부여합니다.
- 로컬에서는 `.env.example`을 `.env`로 복사해 설정합니다. 접속 문자열·키·수집 원문은 Git에 올리지 않습니다.
- Vercel 환경변수를 바꾸면 새 배포부터 적용되고 Actions Secrets는 다음 예약 실행부터 적용됩니다. 단위 테스트는 모의 MongoDB·API만 사용합니다.

`Welfare Data Sync`가 한국시간 **00시·06시·12시·18시**에 실행됩니다. UTC cron은 `0 3,9,15,21 * * *`이며 `main`의 코드로 하루 네 번 실행합니다. 개인 PC와 웹 접속 여부에 의존하지 않습니다. 수집은 한 번에 최대 5,400초(90분), Actions 작업 제한은 100분이며 완료·한도 소진 시 일찍 종료합니다. 중복 실행은 Actions 동시 실행 제한과 MongoDB 임대 잠금으로 막습니다.

각 6시간 구간마다 전체 목록을 비교하고 같은 구간 재실행에서는 완료한 목록을 재호출하지 않습니다. 네 번의 작업은 같은 일일 호출 예산을 공유하고, 예산은 한국시간 자정에만 초기화됩니다. 오류·시간 제한으로 미완료된 작업은 Actions에서 실패로 표시하며 저장 자료는 보존해 다음 예약 실행에서 이어받습니다. 실행 이력 확인이나 수동 재개는 [Welfare Data Sync](https://github.com/BOKJI-MUNTEOK/bokji-munteok/actions/workflows/sync-welfare.yml)의 `Run workflow`에서 `main`을 선택합니다. 데이터 갱신은 DB만 변경하며 웹을 재배포하거나 Git 커밋을 만들지 않습니다.

웹에서 갱신 시작·진행 상태 폴링을 제거했습니다. 저장 자료를 5분간 캐시하고 만료 후 새로고침·메뉴 이동 등 전체 화면 실행 시 DB 변경 번호를 한 번 확인합니다. 변경이 없으면 이미 읽은 자료를 재사용하고, 건수도 그 자료에서 계산해 별도의 전체 조인·호출량 조회를 하지 않습니다. 열어 둔 화면은 자동으로 반복 조회하지 않으며, 사이드바 건수는 화면에 읽어 온 자료 기준입니다.

질문·상담·전체 서비스 검색은 Streamlit fragment로 분리했습니다. 질문 이동과 가중치 변경은 콜백에서 처리해 같은 입력으로 전체 스크립트를 두 번 실행하지 않습니다. 점수 캐시는 목록 조회 시각·MongoDB revision·가중치를 키로 하며 최대 8개 조합을 유지합니다. 공개 복지 자료만 캐시하고 입력한 개인정보는 캐시에 넣지 않습니다. 분석 탭은 선택한 내용만 계산하고, 분포 차트는 같은 점수·구간의 건수를 집계해 전송량을 줄입니다. 이를 위해 Streamlit 1.58 이상을 사용합니다.

[GitHub 예약 실행의 제약](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule): 대기열이 혼잡하면 정각보다 늦게 시작하거나 예약 작업이 누락될 수 있습니다. 공개 저장소에 60일간 활동이 없으면 예약 워크플로가 자동 비활성화될 수 있으므로 Actions에서 활성 상태를 확인합니다.

목록은 두 API의 `totalCount`에 맞춰 끝까지 받습니다. 2026-11-17 확인 수량은 중앙 461건·지자체 4,828건이며 코드에 고정하지 않습니다. 완성된 목록을 별도 세대로 저장한 뒤 공개 세대 포인터를 원자적으로 교체하므로 페이지·DB 저장 실패가 기존 목록의 삭제로 이어지지 않습니다.

API 목록의 모든 필드(조회수 제외)를 해시로 비교합니다. 지자체 수정일 `lastModYmd`도 포함합니다. 새 항목·변경된 항목·아직 받지 않은 상세만 조회하고, 같은 목록의 상세·분석은 재사용합니다. 중앙 목록에는 상세 수정일이 없으므로 목록에 드러나지 않는 상세 본문 변경을 감지할 수 없다는 제한이 있습니다.

세 키의 호출 예산은 중앙 합계 하루 300회, 지자체 합계 3,000회입니다. 목록·상세·재시도를 모두 포함하며, 키별 중앙 100회·지자체 1,000회를 넘기기 전에 중단합니다. 다른 앱에서 이미 쓴 호출량은 알 수 없으므로 제공기관의 한도·인증 오류가 먼저 발생하면 그 키를 그날 중지합니다. 따라서 이 수치는 보장된 상세 저장 건수가 아닙니다.

MongoDB 컬렉션:

| 컬렉션 | 저장 내용 |
| --- | --- |
| `catalog` | 세대별 공식 목록. 완료된 세대만 화면에 공개 |
| `details` | 서비스·목록 해시별 상세 원문과 기존 규칙 분석, 조회 시각 |
| `meta` | 공개 세대·갱신 날짜와 6시간 구간·추가/변경/삭제 수·300초 임대 잠금·화면 갱신 번호 |
| `quota` | 한국 날짜·API 종류·키 해시별 호출 수와 당일 중지 여부. 키 원문은 저장하지 않음 |
| `retries` | 일시 실패한 상세의 재시도 대기 시각 |

DB의 원자적 호출 예약으로 여러 인스턴스의 합산 한도를 지킵니다. 완료한 같은 구간의 작업은 반복하지 않으며 다음 구간에 목록부터 비교합니다. 삭제는 최신 공식 목록에서 제외하는 방식이고 과거 상세 저장은 즉시 파괴하지 않습니다. 오래된 미공개 목록 세대는 현재·직전 세대와 최근 이틀 자료를 남기고 정리합니다.

상세가 저장되지 않았거나 목록 변경으로 다시 받아야 하는 항목은 점수를 비워 둡니다. 분석 통계에는 최신 목록과 해시가 일치하는 저장 자료만 포함합니다. 기존 화면·점수식·가중치는 유지합니다. 새 AI 설명은 자동 생성하지 않으며 기존 AI 캐시·정답표도 원문이 일치할 때만 사용합니다.

공식 자료: [중앙 API](https://www.data.go.kr/data/15090532/openapi.do), [지자체 API](https://www.data.go.kr/data/15108347/openapi.do), [Atlas 연결·Vercel 네트워크 안내](https://www.mongodb.com/docs/atlas/reference/partner-integrations/vercel/).

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
단위 테스트는 전체 페이지·증분 갱신·원자적 호출 한도·DB 영속성·오류 처리·미조회 점수를 포함하며, 실제 키를 사용하지 않습니다.

Vercel의 WebSocket 연결은 Function 최대 실행 시간이 지나면 종료됩니다.
재연결이 다른 인스턴스로 갈 수 있으므로 장시간 사용 중 입력 세션이 초기화될 수 있습니다.
Streamlit의 세션과 생성 파일은 메모리에 있으므로 여러 인스턴스 환경의 파일 다운로드도 확인이 필요합니다.
이 제약을 해결하려고 원본 코드의 세션·저장 방식을 임의로 바꾸지 않습니다.

공식 근거: [Container Runtime](https://vercel.com/docs/functions/runtimes/container),
[WebSockets](https://vercel.com/docs/functions/websockets),
[Streamlit 서버 구조](https://docs.streamlit.io/develop/concepts/architecture/architecture).
