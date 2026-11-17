# 같이 작업하는 규칙

팀원 3명이 이 저장소 하나에서 작업하고, 마감 직전에 각자 Fork해서 제출합니다.

## 1. 시작하기

1. 저장소는 [BOKJI-MUNTEOK](https://github.com/BOKJI-MUNTEOK) 조직에 있습니다. 조직 초대 메일을 받으면 수락합니다.
2. 각자 클론합니다.

   ```bash
   git clone https://github.com/BOKJI-MUNTEOK/bokji-munteok.git
   cd bokji-munteok
   ```

3. API 키는 `.env.example`을 `.env`로 복사해서 넣습니다. `.env`는 올라가지 않습니다.

## 2. 브랜치와 PR

`main`에 직접 push하지 않습니다. 작업마다 브랜치를 만들고 PR로 합칩니다.

```bash
git switch main
git pull
git switch -c data/collect-api      # 브랜치 만들기
# 작업 후
git add 파일
git commit -m "data: 중앙·지자체 서비스 전체 받기"
git push -u origin data/collect-api
```

GitHub에서 PR을 열고 본인이 직접 합칩니다(Squash and merge). 검토는 필수가 아니지만, 남의 코드를 건드렸거나 확인이 필요하면 단톡에 PR 링크를 올립니다. 합친 브랜치는 지웁니다.

브랜치 이름은 `맡은일/할일`로 짓습니다.

| 맡은 일 | 예시 |
| --- | --- |
| data | `data/collect-api` |
| ai | `ai/extract-conditions` |
| eval | `eval/answer-sheet` |
| app | `app/score-page` |
| docs | `docs/readme-team` |

## 3. 커밋 메시지

`종류: 내용` 형식으로 씁니다. 종류는 영어, 내용은 한국어 한 줄입니다.

**기본 종류**

| 종류 | 쓰는 때 | 예시 |
| --- | --- | --- |
| `feat` | 새 기능 추가 | `feat: 문턱 점수 계산 추가` |
| `fix` | 버그 수정 | `fix: 점수 0~100 변환 오류 수정` |
| `docs` | 문서 수정 (README, 계획서, 발표자료, 주석) | `docs: 팀원 소개 추가` |
| `style` | 코드 모양만 변경 (들여쓰기, 줄바꿈, 따옴표) | `style: black으로 포맷 정리` |
| `refactor` | 동작은 그대로, 코드 구조만 정리 | `refactor: 점수 함수 분리` |
| `test` | 테스트 코드 추가·수정 | `test: 숫자 대조 함수 테스트 추가` |
| `chore` | 설정, 패키지 관리 등 기타 | `chore: requirements.txt에 streamlit 추가` |
| `build` | 배포·실행 환경 파일 수정 | `build: Streamlit Cloud 설정 추가` |
| `revert` | 이전 커밋 되돌리기 | `revert: 점수 가중치 변경 되돌리기` |

**이 프로젝트용 종류**

| 종류 | 쓰는 때 | 예시 |
| --- | --- | --- |
| `data` | 데이터 수집·전처리 코드, 샘플 데이터 | `data: 지자체 서비스 전체 받기` |
| `prompt` | 언어 모델 프롬프트 추가·수정 | `prompt: 조건 수 뽑는 프롬프트 추가` |
| `eval` | 정답표, 검증·비교 결과 | `eval: 정답표 50건 작성` |

어느 쪽인지 애매하면 기본 종류를 씁니다. 예를 들어 데이터 받는 코드의 버그를 고쳤으면 `data`가 아니라 `fix`입니다.

`수정`, `ㅇㅇ`, `final_final`처럼 무엇을 했는지 알 수 없는 메시지는 쓰지 않습니다.

## 4. 폴더

| 폴더 | 넣는 것 |
| --- | --- |
| `main.py`, `web/` | Vercel 웹 앱과 브라우저 화면 |
| `app.py` | 기존 Streamlit 화면 (비교·로컬 실행용) |
| `src/` | 데이터 받기, 점수 계산, 숫자 검사 같은 파이썬 코드 |
| `prompts/` | 언어 모델 프롬프트 |
| `data/raw/` | API에서 받은 원본 (올리지 않음) |
| `data/sample/` | 테스트용 작은 샘플 (올림) |
| `eval/` | 직접 읽고 만든 정답표, 비교 결과 |
| `docs/` | 계획서, 발표자료, 시연 대본 |

## 5. 하지 말 것

- API 키, 비밀번호를 코드나 커밋에 넣지 않습니다. 실수로 올렸으면 바로 알리고 키를 새로 발급합니다.
- 큰 원본 데이터(수십 MB)를 올리지 않습니다. 받는 코드를 올립니다.
- 남이 맡은 파일을 말없이 크게 고치지 않습니다. 단톡이나 PR 댓글로 먼저 말합니다.

## 6. 작업 나누기

할 일은 GitHub Issues에 올리고, 맡는 사람이 Assignees에 자기 이름을 넣습니다. PR 본문에 `Closes #번호`를 쓰면 합칠 때 이슈가 닫힙니다.

## 7. 제출 직전 (12월 1일 24:00까지)

1. 모든 PR을 `main`에 합칩니다.
2. 각자 이 저장소를 Fork합니다. 이미 Fork했다면 본인 Fork에서 **Sync fork**를 눌러 최신으로 맞춥니다.
3. 본인 Fork 주소(`https://github.com/본인아이디/bokji-munteok`)를 제출합니다.
