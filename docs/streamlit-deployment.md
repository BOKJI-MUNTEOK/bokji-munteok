# Streamlit Community Cloud 배포

운영 주소는 [복지문턱](https://bokji-munteok.streamlit.app/)이며, [내 복지 찾기](https://bokji-munteok.streamlit.app/find)도 같은 앱에서 제공합니다. 기존 화면·문턱 점수식·가중치·신청서 기능을 유지하고 실행 환경을 Streamlit Community Cloud로 전환합니다. MongoDB Atlas와 GitHub Actions 예약 수집은 그대로 사용합니다.

## 앱 연결과 비밀값

기존 앱의 설정을 다음과 같이 맞춥니다. 새 앱을 중복 생성하기보다 기존 주소의 앱 연결을 확인합니다.

| 설정 | 값 |
| --- | --- |
| 저장소 | `BOKJI-MUNTEOK/bokji-munteok` |
| 브랜치 | `main` |
| 실행 파일 | `app.py` |
| Python | `3.12` |
| 앱 주소 | `bokji-munteok.streamlit.app` |

앱 설정의 **Secrets**에 아래 키를 등록합니다. 예시의 `...`는 실제 값으로 바꾸되 Git에 올리지 않습니다.

```toml
MONGODB_URI = "..."
MONGODB_DATABASE = "bokji_munteok"
HWPLIB_AUTO_SETUP = "1"
LLM_API_KEY = "..."   # 선택: AI 상담·쉬운 설명·서류 안내
JUSO_API_KEY = "..."  # 선택: 도로명주소 검색
```

`src/env.py`가 최상위 Secrets 값을 읽습니다. 웹이 공식 복지 API를 수집하지 않으므로 공공데이터 키 세 개는 웹에 넣을 필요가 없습니다. `LLM_API_KEY`가 없으면 규칙 상담과 공식 안내를 사용하고, `JUSO_API_KEY`가 없으면 주소 API 검증 없이 서류를 작성합니다.

`requirements.txt`는 Python 의존성, `packages.txt`는 Java JDK를 설치합니다. HWP 자동 기입은 첫 사용 때 `hwplib` JAR의 SHA-256을 확인하고 Java 도구를 컴파일하며, 이후 같은 실행 환경에서는 준비된 파일을 재사용합니다. 실패하면 원본 서식을 보존하는 기존 처리를 따릅니다. Streamlit Cloud는 Dockerfile을 실행하지 않습니다.

공식 안내: [배포 설정](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy), [Secrets](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management), [의존성](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/app-dependencies).

## 코드 자동 반영과 테스트

Community Cloud는 연결된 저장소·브랜치의 변경을 자동 반영합니다. 이 앱을 `main`에 연결한 상태에서는 PR 병합으로 코드가 반영됩니다. PR 브랜치만 변경하면 운영 앱의 코드는 바뀌지 않습니다. [공식 자동 반영 안내](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app/edit-your-app)

`.github/workflows/ci.yml`의 `Tests`가 PR과 `main` 변경에서 Python 3.12로 전체 테스트를 실행합니다. **PR 테스트 성공을 확인하고 병합합니다.** Streamlit 자동 반영과 GitHub 테스트는 별도이므로, `main` 테스트가 배포를 막아 주는 구조로 설명하지 않습니다. 테스트에는 실제 DB·공공데이터·유료 AI 키를 제공하지 않습니다.

Vercel 자동 배포는 다음 두 곳에서 중단합니다.

- `.github/workflows/deploy-vercel.yml`: `push` 실행을 제거하고 `workflow_dispatch`만 유지
- `vercel.json`: `git.deploymentEnabled: false`로 모든 브랜치의 Git 연동 자동 배포 중단

수동 Vercel 배포는 별도 실행이 필요합니다. 기존 주소와 설정은 [이전 Vercel 배포 기록](vercel-deployment.md)에 보관합니다. [Vercel Git 자동 배포 설정](https://vercel.com/docs/project-configuration/git-configuration#turning-off-all-automatic-deployments)

## 복지 데이터 API·MongoDB 설정

- Atlas 프로젝트·무료 클러스터: `BOKJI-MUNTEOK` (AWS 서울)
- 데이터베이스: `bokji_munteok`
- 앱 계정: 이 DB에 한정한 `readWrite` 권한
- Streamlit Secrets: `MONGODB_URI`, `MONGODB_DATABASE`
- GitHub Actions Secrets: `MONGODB_URI`, `DATA_GO_KR_KEY`, `DATA_GO_KR_KEY_2`, `DATA_GO_KR_KEY_3`

예약 수집용 키마다 중앙부처·지자체 API 활용 승인이 모두 필요합니다. Encoding/Decoding 키를 지원하며 중복 키는 합칩니다. 비밀번호·연결 문자열·키 원문은 코드와 로그에 기록하지 않습니다. 이용자가 입력한 이름·연락처·주소도 이 DB에 저장하지 않습니다.

| 컬렉션 | 저장 내용 |
| --- | --- |
| `catalog` | 세대별 공식 목록. 완성된 목록만 웹에 공개 |
| `details` | 서비스·목록 해시별 상세 원문, 규칙 분석 결과, 조회 시각 |
| `meta` | 공개 세대, 확인 날짜·6시간 구간, 추가·변경·삭제 건수, 갱신 잠금, 데이터 버전 |
| `quota` | 한국 날짜·API 종류·키 해시별 사용량과 당일 중지 여부 |
| `retries` | 실패한 상세의 재시도 대기 시각 |

## 하루 네 번 수집과 변경 반영

`Welfare Data Sync`는 **한국시간 00시·06시·12시·18시**에 GitHub Actions에서 실행됩니다. UTC cron은 `0 3,9,15,21 * * *`입니다. 개인 PC나 웹 접속 여부와 관계없이 동작하고, 웹을 재배포하거나 데이터 커밋을 만들지 않습니다. 실행 이력과 수동 재개는 [예약 수집 워크플로](https://github.com/BOKJI-MUNTEOK/bokji-munteok/actions/workflows/sync-welfare.yml)에서 확인합니다.

1. **목록 비교**: 두 API의 전체 페이지를 정상 수신한 뒤 서비스 ID와 목록 해시를 비교합니다. 조회수는 제외하고 지자체 수정일 `lastModYmd` 등 나머지 목록 필드를 비교합니다.
2. **추가·변경**: 새 항목·목록이 바뀐 항목·아직 받지 못한 상세만 API에서 조회하고 분석해 저장합니다.
3. **삭제**: 완성된 새 목록에서 빠진 서비스는 웹의 현재 표시 대상에서 제외합니다. 이전 상세 기록은 즉시 물리 삭제하지 않습니다.
4. **변경 없음**: 기존 상세와 분석을 유지합니다. 목록 확인 날짜·구간은 갱신하되, 같은 목록을 새 세대로 저장하거나 데이터 버전을 올리지 않습니다.

전체 목록 확인 호출은 변경 여부를 알아내기 위해 계속 필요합니다. 목록이나 수정일에 나타나지 않는 상세 본문만의 변경은 감지하지 못합니다. 페이지 수신·DB 저장이 실패하면 마지막 정상 목록을 유지하고, 미완료 상세는 다음 실행에서 이어받습니다.

세 승인 키의 일일 예산은 중앙 합계 최대 300회·지자체 합계 최대 3,000회이며 목록·상세·재시도를 모두 포함합니다. 네 번의 실행이 같은 예산을 공유하고 한국시간 자정에 초기화합니다. 제공기관의 한도 오류가 먼저 발생하면 해당 키를 그날 중지합니다. 같은 6시간 구간 재실행은 완료된 목록 확인을 반복하지 않습니다. Actions 동시 실행 제한과 DB 임대 잠금으로 중복 작업을 막습니다.

GitHub의 예약 실행은 대기열로 지연되거나 누락될 수 있고, 공개 저장소가 60일 동안 비활성 상태이면 예약 워크플로가 비활성화될 수 있습니다. [공식 예약 실행 제약](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)

## 웹에서 기존 분석 재사용

웹은 MongoDB에 저장된 조건·서류 추출값과 안내문 난이도를 먼저 사용합니다. 현재 규칙과 맞지 않거나 분석이 없는 서비스만 해당 원문을 분석하고, 같은 원문은 다시 사용할 수 있도록 캐시합니다. 가중치를 바꾸면 추출값으로 기존 점수식을 적용하므로 안내문을 다시 분석할 필요가 없습니다. 점수·이유 문장은 현재 점수식으로 계산하며 같은 입력·가중치는 재사용합니다.

목록 확인 시각만 바뀐 것으로는 전체 분석을 무효화하지 않습니다. 새 항목·변경된 상세만 새 결과를 만들고, 삭제된 항목은 결과 목록에서 빠집니다. 서버가 재시작되어 메모리 캐시가 비어도 DB의 저장된 분석은 유지됩니다. 검색·정렬·통계 집계·화면 출력에 필요한 처리는 계속 수행합니다.

저장 자료는 5분간 캐시하며, 만료 후 새로고침·메뉴 이동 등 전체 화면 실행에서 최신 자료를 확인합니다. 열어 둔 화면에서 반복 조회를 돌리거나 웹 방문으로 수집을 시작하지 않습니다. 질문·상담·검색·필터는 해당 영역만 갱신하고 개인정보는 공개 자료 캐시에 넣지 않습니다.

사이드바의 **최근 목록 변경: 추가·수정·삭제 건수**는 각 API의 마지막 정상 목록 비교 결과를 합산하며, 상세 수집·분석 완료 건수나 하루 누적 건수가 아닙니다. **목록 확인 시각**은 `catalog_checked_at`을 기준으로 마지막 비교 완료 시각을 표시하고, 내용이 같아도 확인 시각은 갱신됩니다.

## 반영 후 확인

- 앱이 `main`의 `app.py`를 실행하며 Secrets의 DB 연결이 적용되었는지 확인합니다.
- `/_stcore/health`와 함께 실제 화면·WebSocket 실행에서 DB 자료가 표시되는지 확인합니다. health 응답만으로 전체 앱 동작이 검증되지는 않습니다.
- 홈·내 복지 찾기·전체 서비스·데이터 분석을 열고 가중치, 상세 안내, 다운로드를 확인합니다.
- HWP·HWPX·PDF 서식 작성과 AI·주소 기능은 필요한 의존성과 선택 키를 등록한 환경에서 각각 확인합니다.
- 예약 수집 로그의 추가·변경·삭제 건수와 다음 웹 실행의 표시 건수를 확인합니다. 변경 없는 수집 때문에 분석 버전이 바뀌지 않는지도 확인합니다.

Streamlit의 서버 메모리와 실행 자원은 유한합니다. 이 변경은 불필요한 원문 분석을 줄이는 것이며, 모든 접속량에서 자원 한도를 넘지 않는다고 보장하지는 않습니다.
