# Vercel 배포

Vercel에서는 Streamlit 서버 대신 `main.py`의 FastAPI 앱과 `web/` 화면을 실행합니다.
`src/`의 샘플 파싱, 규칙 점수, 추천, AI 캐시, 정답표 비교, PDF/ZIP 생성 코드는 두 화면이 공유합니다.

운영 주소: https://bokji-munteok.vercel.app/find

현재 `kdh0381/bokji-munteok` Vercel 프로젝트에 CLI로 배포되어 있습니다.
GitHub 조직 앱 연결은 조직 소유자 승인이 필요해 자동 배포는 아직 연결되지 않았습니다.
승인 전에는 최신 main에서 다음 명령으로 배포합니다.

```bash
vercel login
vercel link --project bokji-munteok --scope kdh0381
vercel deploy --prod --scope kdh0381
```

## 실행

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --reload
```

http://127.0.0.1:8000/find 에서 확인합니다. `/`, `/find`, `/all`, `/analysis`, `/guide`를 직접 열거나 새로고침할 수 있습니다.
기존 화면을 비교할 때는 `python -m streamlit run app.py`를 사용합니다.

## 프로젝트 설정

1. Vercel에서 `BOKJI-MUNTEOK/bokji-munteok` GitHub 저장소를 Import합니다.
2. Framework Preset은 **FastAPI**, Root Directory는 저장소 루트(`./`), Production Branch는 **main**입니다.
3. 별도 Build Command·Output Directory·환경변수는 필요하지 않습니다.
4. `pyproject.toml`의 `tool.vercel.entrypoint = "main:app"`가 기존 `app.py`와 구분해 진입점을 지정합니다.
5. PR의 Preview와 테스트 결과를 확인한 뒤 Squash merge합니다. 이후 main 변경은 연결된 Vercel 프로젝트에서 자동 배포됩니다.

Vercel의 런타임 의존성은 `pyproject.toml`에 정의되어 있습니다. `requirements.txt`에는 기존 Streamlit 비교 실행용 패키지도 포함합니다.
프로덕션 URL은 Vercel 프로젝트의 Domains에서 확정한 주소를 README에 기록합니다.
Streamlit 소유의 `*.streamlit.app` 주소를 Vercel 도메인으로 옮길 수는 없으므로 공유 링크는 새 주소로 교체해야 합니다.

## 입력과 파일 처리

- AI 결과 50건은 `data/ai_cache/`에서 읽습니다. 공개 요청에서 LLM을 호출하거나 캐시를 수정하지 않습니다.
- 상담은 기존 `intake.py`의 키워드 규칙으로 동작합니다. 실시간 생성형 AI 채팅으로 표기하지 않습니다.
- 상담 답은 브라우저 메모리와 해당 POST 요청에서만 사용합니다. 새로고침하면 초기화됩니다. 서버·DB·브라우저 영구 저장소에 보관하지 않습니다.
- 추천·분석·꾸러미 응답은 `Cache-Control: no-store`입니다. 개인정보를 URL이나 오류 응답에 넣지 않습니다.
- 서류 꾸러미는 메모리에서 생성합니다. 외부 첨부 합계는 2.5 MB, 다운로드 시간은 30초로 제한하고 실패·초과한 원본은 ZIP 안에 링크를 남깁니다. 최종 ZIP은 4 MB 이하로 반환합니다.
- 다운로드 URL은 서버의 공식 샘플 데이터에서만 가져옵니다. 클라이언트가 임의 URL을 지정할 수 없습니다.

## 검증

```bash
pip install httpx
python -m unittest discover -s tests -v
node --check web/app.js
```

`tests/test_web.py`는 경로 직접 접근, 기존 점수·추천과의 일치, 요청 간 입력 분리, 가중치 검증, PDF/ZIP 및 외부 다운로드 실패를 검사합니다.
GitHub Actions가 기존 Streamlit 테스트를 포함해 함께 실행합니다.

설정 근거: [Vercel FastAPI 문서](https://vercel.com/docs/frameworks/backend/fastapi), [Python 런타임](https://vercel.com/docs/functions/runtimes/python).
