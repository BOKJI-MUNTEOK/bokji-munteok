# 서류 미리보기

`src/document_viewer.py`가 전달한 최종 파일 바이트를 Streamlit iframe에서 표시한다.
원본의 내장 썸네일을 사용하지 않는다. 파일 내용은 메모리에서만 사용하며, 원격 변환 서비스·분석 서비스·브라우저 저장소로 전송하거나 보관하지 않는다.

- PDF: PDF.js의 페이지 렌더링. 포함 글꼴, 로컬 CMap·표준 글꼴·WASM 사용.
- HWP/HWPX: rhwp WASM의 SVG 페이지 렌더링. DOMPurify 및 추가 URL 제한 적용.
- 인쇄: 현재 보이는 페이지뿐 아니라 렌더링이 끝난 모든 페이지를 인쇄한다.
- 20 MiB / 40쪽을 넘거나 파서가 실패한 문서는 미리보기 실패를 표시하고 개별 파일 저장 흐름으로 돌아간다.
- `ready`는 화면 렌더링 성공이다. 공식 양식 인증·전체 칸 정확성·한컴오피스와 동일한 배치를 보증하는 상태가 아니다.
- HWP 글꼴은 일부 대체될 수 있다. 미리보기에서 줄바꿈·겹침·빈칸을 확인하도록 안내한다.
- PDF 페이지 래스터의 총 면적은 3천만 픽셀로 제한한다. 긴 문서는 확대·인쇄 해상도가 낮아질 수 있다.

## Python

```python
result = render_document(data, filename, format, key="document_preview")
```

결과: `status` (`loading`, `ready`, `error`, `unsupported`), `page_count`, `format`, `request_id`. `request_id`는 전달 파일의 SHA-256이며 변경 전 결과를 재사용하지 않는다.

## 고정 배포 의존성

정확한 버전은 `vendor-versions.json`에 있다. 런타임 CDN 요청이나 npm 설치는 없다.

- `@rhwp/core` 0.8.7: https://github.com/edwardkim/rhwp (MIT)
- `pdfjs-dist`: https://github.com/mozilla/pdf.js (Apache-2.0)
- `dompurify`: https://github.com/cure53/DOMPurify (Apache-2.0 OR MPL-2.0)
- 나눔고딕: 저장소 기존 `assets/fonts`에서 복사 (SIL OFL)

각 원문 라이선스는 `vendor/*-LICENSE`, `fonts/OFL.txt`에 포함한다. 의존성 갱신 시 실제 PDF·HWP·HWPX 렌더링, 잘못된 문서, 외부 URL 차단, 모바일 탐색·인쇄 CSS를 재검증한다.
