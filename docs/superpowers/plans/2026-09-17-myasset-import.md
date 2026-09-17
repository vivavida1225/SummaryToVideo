# myasset 원문 불러오기 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking. 2026-09-17 후속 구현 요청에 따라 실행 완료.

**Goal:** 날짜와 gubun으로 myasset 시황 HTML을 가져와 원문 입력창에 반영하고 기존 변환·압축 작업을 자동 시작한다.

**Architecture:** 기존 FastAPI에 고정 myasset 주소 전용 가져오기 API를 추가한다. 서버가 `div.contWrap` 내부 HTML과 직렬화 가능 여부를 검사하고, Vue가 성공 응답을 입력창에 반영한 뒤 기존 `/api/jobs`로 전달한다. 가져오기와 작업 생성은 분리하되 하나의 사용자 동작으로 연결한다.

**Tech Stack:** 기존 Vue 3 / TypeScript / FastAPI / httpx / BeautifulSoup / pytest / Vitest. 추가 패키지는 필요하지 않다.

**Spec:** 2026-09-17 사용자 요청 및 이 문서의 요구사항·동작 계약. 기존 계약은 `docs/implementation.md`를 참조한다.

## 요구사항과 결정 사항

- `원문 선택` 제목 바로 오른쪽에 `myasset에서 불러오기` 버튼을 추가한다.
- 같은 `.section-heading` 맨 오른쪽에 `불러오기 설정` 버튼을 두고, 클릭하면 날짜·gubun을 편집하는 작은 설정 패널을 연다.
- 날짜 기본값은 화면 진입 시 한국 시간 `Asia/Seoul`의 오늘이다. `gubun` 기본값은 반드시 `30`이다.
- 설정값은 현재 화면에서 유지한다. 새로고침 시 오늘/30으로 초기화하여 테스트용 1이나 과거 날짜가 기본값으로 남지 않게 한다. 자동 자정 갱신은 추가하지 않는다.
- 날짜는 `YYYY-MM-DD`의 유효한 달력 날짜, gubun은 0 이상의 정수로 입력한다. 의미가 확인되지 않은 gubun별 명칭이나 선택 목록은 만들지 않는다.
- 주소·경로·`id=1`·`cond=list`는 서버 상수로 고정한다. 사용자에게 날짜와 gubun만 노출한다.
- `div.contWrap`의 바깥 태그를 제외한 **내부 HTML**을 넣는다. 평문 변환은 기존 직렬화 단계에서 한다.
- 성공 시 기존 클립보드 입력 탭을 선택하고 HTML 입력값을 갱신한다. OS 클립보드를 거칠 필요 없이 입력창 상태에 직접 반영한다.
- “다음 작업”은 현재 `변환 및 1분 압축` 버튼과 동일한 직렬화 → Gemini 압축 → 검증 → 저장·결과 표시로 해석한다.
- 현재 코드의 복사는 사용자가 결과 복사 버튼을 눌렀을 때만 실행된다. 신규 가져오기에도 같은 계약을 적용한다.
- 기존 모델 선택·폴백·분량 확인·재생성 동작을 재사용한다. `needs_review`에서 자동 재생성을 추가하지 않는다.
- 페이지 진입이나 설정 변경만으로 가져오기 또는 Gemini 요청을 실행하지 않는다.
- 가져오기 실패 시 기존 입력·선택 탭·기존 결과를 유지한다. 가져오기 성공 후 작업 생성만 실패하면 새 원문을 남겨 수동 실행할 수 있게 한다.
- 실사이트 성공 검사는 `base_date=2026-09-17&gubun=1`로 한다. 테스트 편의를 위해 앱 기본값을 1로 바꾸거나 30 실패 시 1로 자동 대체하지 않는다.

## 현재 코드와 실사이트 확인 결과

- `frontend/src/App.vue`: `clipboardText`, `clipboardFormat`, `sourceMode`, `runJob()`, `actionBusy`, `isRunning`으로 입력과 작업을 관리한다.
- `frontend/src/api.ts`: 공통 `request()`가 `X-App-Token`을 설정하고 문자열 `detail` 오류를 `ApiError`로 전달한다.
- `backend/app.py`: 로컬 Host/Origin/토큰 검증, CSP의 `connect-src 'self'`, `/api/jobs`를 제공한다. 외부 원문은 백엔드에서 가져오는 구조가 적합하다.
- `backend/serializer.py`: `.embedded-content` 내부의 header·summary·indices·section을 검사한다. 가져온 HTML도 이 함수를 재사용할 수 있다.
- `backend/jobs.py`: 직렬화부터 생성·검증·저장까지 이미 연결되어 있다. 가져오기 API는 작업을 생성하지 않고, 프런트엔드가 기존 작업 생성 API를 호출한다.
- 2026-09-17 직접 HTTP 확인: [gubun=1](https://www.myasset.com/myasset/research/aiNews/marketConditionPopup.cmd?id=1&base_date=2026-09-17&gubun=1&cond=list)은 HTTP 200, UTF-8, `contWrap` 1개, 뉴스 `.embedded-content` 1개였다. 내부 HTML을 기존 직렬화기에 전달해 5개 장면으로 변환되는 것을 확인했다.
- 같은 시점의 [gubun=30](https://www.myasset.com/myasset/research/aiNews/marketConditionPopup.cmd?id=1&base_date=2026-09-17&gubun=30&cond=list)은 HTTP 200이며 `contWrap`도 있지만, 홍보 링크만 있고 뉴스 `.embedded-content`가 없었다. HTTP 성공·컨테이너 존재·비어 있지 않은 문자열만으로는 게시 여부를 판정할 수 없다.
- 이 확인은 원문 조회와 로컬 직렬화만 수행했으며 Gemini를 호출하지 않았다. 게시 상태는 이후 달라질 수 있다.

## API와 오류 계약

`POST /api/sources/myasset` — 기존 로컬 접근 보호와 `X-App-Token` 적용.

요청 예시:

```json
{"base_date":"2026-09-17","gubun":30}
```

- `base_date`: 필수 날짜 문자열. 프런트엔드가 한국 시간 오늘로 초기화하여 보낸다.
- `gubun`: 기본값 30, 엄격한 정수, 0 이상. 추가 필드는 거부한다.
- 성공 200: `{ "html": string, "source_url": string, "base_date": string, "gubun": number }`.
- `html`: 선택된 `contWrap.decode_contents()` 결과. 스크립트를 실행하거나 `v-html`로 렌더링하지 않고 textarea에 문자열로 표시한다.

| 상황 | 상태 | 사용자 안내와 처리 |
| --- | --- | --- |
| 날짜/gubun 검증 실패 | 422 | 날짜와 gubun 입력 형식을 확인하도록 안내. 외부 요청 없음 |
| 뉴스 `.embedded-content` 없음 또는 빈 `contWrap` | 404 | `선택한 날짜와 gubun의 게시글 본문을 찾을 수 없습니다. 설정을 확인하거나 게시 후 다시 시도하세요.` |
| `contWrap` 누락·중복, 뉴스 구조 중복·불완전, 디코딩 실패 | 502 | `원문 페이지 구조를 확인할 수 없습니다. HTML을 직접 붙여넣거나 잠시 후 다시 시도하세요.` |
| 외부 통신 오류, 비정상 HTTP 응답, 리다이렉트 | 502 | `myasset 원문을 불러오지 못했습니다. 잠시 후 다시 시도하세요.` |
| 시간 초과 | 504 | `myasset 응답 시간이 초과되었습니다. 다시 시도하세요.` |
| 응답 또는 추출 HTML이 5 MiB 초과 | 413 | 지원 크기를 초과했다는 안내 |

모든 오류는 기존 프런트엔드가 처리할 수 있도록 `{ "detail": "문자열" }`을 반환한다. 404는 본문 부재를 의미하며 미게시 원인 자체를 단정하지 않는다.

## 파일별 변경 범위

| 파일 | 변경 |
| --- | --- |
| 신규 `backend/myasset.py` | 고정 주소 요청, 크기·인코딩·본문 검사, HTML 추출, 오류 유형 |
| `backend/app.py` | 입력/응답 스키마, 가져오기 라우트, 해당 경로의 422 오류 안내 |
| `frontend/src/types.ts` | `MyassetSource` 응답 타입 |
| `frontend/src/api.ts` | `api.myassetSource(baseDate, gubun)` |
| `frontend/src/App.vue` | 버튼·설정·날짜 초기화·가져오기 상태·공통 작업 제출 연결 |
| `frontend/src/style.css` | 제목 옆 버튼, 우측 설정 패널, 좁은 화면 배치 |
| 신규 `tests/test_myasset.py` | 응답 추출·미게시·통신·제한 검사 |
| `tests/test_api.py` | 인증·요청 검증·성공/오류 응답 계약 |
| `frontend/src/App.spec.ts` | 설정·기본값·자동 실행·실패 복구·중복 방지 |
| `README.md`, `docs/implementation.md` | 구현 완료 후 사용법·API·실패 처리 반영 |

## Task 1: 원문 가져오기 서비스와 API

**Interfaces:** `extract_myasset_html(document: bytes) -> str`, `async fetch_myasset_source(base_date: date, gubun: int) -> dict`, `MyassetError(status_code: int, detail: str)`를 `backend/myasset.py`에서 정의한다. 라우트는 서비스 반환값을 응답 스키마로 검증하고 서비스 오류를 HTTPException으로 변환한다.

- [x] `tests/test_myasset.py`에 기존 `tiny_html` fixture를 이용한 본문 추출·직렬화 호환 테스트를 추가하고 실패를 확인한다.

```python
from backend.myasset import extract_myasset_html
from backend.serializer import serialize_html

def test_extracts_only_container_contents(tiny_html):
    page = ('<html><body><nav>outside</nav><div class="contWrap">'
            + tiny_html + '</div><footer>outside</footer></body></html>')
    fragment = extract_myasset_html(page.encode('utf-8'))
    assert 'outside' not in fragment
    assert 'contWrap' not in fragment
    assert serialize_html(fragment) == serialize_html(tiny_html)
```

- [x] `httpx.AsyncClient`로 고정 HTTPS 주소를 요청한다. `params`에 고정값과 검증된 날짜/gubun을 전달한다. 리다이렉트는 따라가지 않는다.
- [x] 연결 5초·읽기 20초·처리 전체 30초를 상한으로 한다. 스트림 수신 중 압축 해제된 바이트 수를 세어 `MAX_INPUT_BYTES` 초과 시 종료한다. 자동 재시도는 하지 않는다.
- [x] 실사이트에서 확인한 UTF-8을 엄격하게 디코딩하고, BeautifulSoup으로 `div.contWrap`이 정확히 1개인지 검사한다. 내부에 뉴스 `.embedded-content`가 없으면 404로 처리한다.
- [x] `decode_contents()`한 HTML의 UTF-8 크기를 검사하고, 기존 `serialize_html(fragment)`으로 구조를 사전 검증한다. 검증용 평문 대신 입력용 HTML을 반환한다. 실제 작업에서도 기존대로 직렬화한다.
- [x] 위 오류 표를 `MyassetError`에 대응시킨다. 광고만 있는 컨테이너, 빈 내용, 중복, 구조 누락, HTML 이외 응답, 잘못된 UTF-8, 리다이렉트, HTTP 오류, 시간 초과, 크기 초과를 테스트한다.
- [x] `backend/app.py`에 스키마와 라우트를 추가한다. 날짜 문자열의 형식과 실재 날짜를 검증하고, gubun의 bool·소수·숫자 문자열을 거부한다. `RequestValidationError` 처리기는 이 경로에서 날짜/gubun용 문자열 detail을 반환한다.
- [x] `httpx.MockTransport`와 monkeypatch로 외부 통신을 대체하고, `id=1`/`cond=list` 고정, 날짜 전달, gubun 생략 시 30, 명시 시 1, 추가 URL 입력 거부를 검증한다. 기존 `TestClient` 방식으로 토큰 필수와 실패 시 작업 미생성을 검증한다.
- [x] 다음 대상 테스트를 실행하고 통과 후 변경 내용을 검토한다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_myasset.py tests/test_api.py tests/test_serializer.py -q
```

## Task 2: 버튼·설정과 기존 작업 자동 연결

**Interfaces:** `MyassetSource = { html: string; source_url: string; base_date: string; gubun: number }`, `api.myassetSource(baseDate: string, gubun: number): Promise<MyassetSource>`를 추가한다. App 내부에 `submitJob(source: { html: string } | { file_path: string }, model: string): Promise<void>`를 두고, 기존 `runJob()`과 신규 `loadMyasset()`이 공유한다.

- [x] `App.spec.ts`의 기존 fetch 모킹을 확장하고, 초기 gubun=30, 한국 시간 날짜 경계, 설정 변경만으로는 통신하지 않는 테스트를 추가해 실패를 확인한다.
- [x] `Intl.DateTimeFormat(..., {timeZone: 'Asia/Seoul'})`의 연월일 parts로 `YYYY-MM-DD`를 조립한다. `toISOString().slice(0, 10)`은 UTC 날짜가 되므로 사용하지 않는다. 예를 들어 `2026-09-16T15:30:00Z`에서 `2026-09-17`이 되는지 검증한다.
- [x] 제목 행은 “단계 번호 → 제목과 인접한 가져오기 버튼 → 오른쪽 끝 설정 버튼” 순으로 배치한다. 설정 쪽에 `margin-left: auto`를 적용한다. 좁은 화면에서는 버튼 행을 줄바꿈하여 가로 스크롤을 방지한다.
- [x] 설정 패널에 라벨이 있는 `input type="date"`와 `input type="number" min="0" step="1"`을 둔다. 설정 버튼에 `aria-expanded`/`aria-controls`를 적용하고, Escape로 닫으면 포커스를 돌려준다. 빈 값·잘못된 입력은 가져오기를 막고 이유를 표시한다.
- [x] `api.myassetSource()`는 공통 `request()`로 `POST /api/sources/myasset`에 JSON을 보낸다. 모델은 외부 가져오기 API에 보내지 않고 기존 작업 생성 시 지정한다.
- [x] `runJob()`의 작업 전송·409 시 기존 작업 복원·rememberJob·schedulePoll을 `submitJob()`으로 추출한다. busy 설정/해제는 호출부의 `try/finally`에서 담당한다. 공통 함수는 `canRun`이나 `actionBusy`로 다시 차단하지 않는다.
- [x] `loadMyasset()`은 다음 순서를 따른다. `actionBusy=true` 상태에서 기존 `runJob()`을 호출하면 `canRun=false`로 종료되므로, 공유한 `submitJob()`을 직접 호출한다.

```text
1. loading / actionBusy / isRunning / 모델 미선택 / 설정 오류이면 종료
2. 날짜·gubun·모델을 로컬 변수로 확정하고 actionBusy와 fetchingMyasset을 true로 설정
3. api.myassetSource(날짜, gubun)를 await
4. 언마운트되었으면 입력 반영·작업 생성 없이 종료
5. sourceMode='clipboard', clipboardFormat='html', clipboardText=response.html
6. 가져오기 성공을 알리고 Vue의 nextTick()으로 입력창 반영을 기다림
7. submitJob({html: response.html}, 확정된 모델)을 await
8. finally에서 fetchingMyasset과 actionBusy를 false로 설정
```

- [x] `loading || actionBusy || isRunning`으로 가져오기·설정 입력·탭·textarea·파일 선택·모델 선택을 일관되게 잠근다. 기존 textarea와 탭은 actionBusy를 검사하지 않으므로 추가한다. 가져오기 버튼은 대기 중 `myasset에서 불러오는 중…`으로 표시한다.
- [x] 가져오기 실패는 sourceError에 표시하고 기존 입력을 유지한다. 성공 후 작업 전송 실패 시 가져온 HTML을 남긴다. 입력 반영과 작업 전송에 같은 HTML 스냅샷을 사용해 동시 조작에 따른 내용 혼입을 막는다.
- [x] Vitest로 다음을 검증한다: 파일 선택 중에도 성공 시 HTML 입력으로 전환, 가져온 뒤 `/api/jobs`가 1회만 발생하고 html/model이 일치, 가져오기 실패 시 0회, 지연 응답 중 연속 클릭 방지, 404 시 기존 입력 유지, 작업 전송 실패 후 수동 재실행, 409 복원, 언마운트 후 자동 실행 없음.
- [x] `needs_review` 응답에서 추가 재생성을 시작하지 않고 기존 분량 확인 UI를 표시하는지도 확인한다.
- [x] 프런트엔드 테스트와 타입 검사를 포함한 빌드를 실행하고 변경 내용을 검토한다.

```powershell
$env:PATH = "$PWD\.tools\node;$env:PATH"
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

## Task 3: 통합 확인과 사용 문서 동기화

- [x] 구현 후 `README.md`에 버튼 위치, 오늘/30 초기값, 설정 방법, 성공 후 자동 실행, 미게시 안내, 가져온 원문에서 재실행하는 방법을 적는다.
- [x] `docs/implementation.md`에 가져오기 API 입출력·오류, 백엔드 조회, busy 관리, 기존 작업 연결을 추가한다. 가져오기 자체는 클립보드 쓰기를 수행하지 않는 계약을 명시한다.
- [x] 전체 Python 테스트, 프런트엔드 테스트, production build를 실행한다. 외부 사이트·Gemini 검사는 일반 회귀 테스트에 섞지 않는다.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
$env:PATH = "$PWD\.tools\node;$env:PATH"
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

- [x] 브라우저의 일반 너비와 좁은 너비에서 제목·오른쪽 설정·열기/닫기·키보드 조작·가져오는 중 표시를 확인한다.
- [x] 실사이트 조회는 2026-09-17/gubun=1로 확인하고 반환 HTML이 기존 직렬화를 통과하는지 확인한다. gubun=30이 미게시인 동안 오류 표시와 입력 유지를 확인한다. 게시된 상태로 바뀌었다면 미게시 사례는 고정 모킹으로 확인한다.
- [x] 자동 연결 통합 확인은 Gemini를 모킹하여 가져온 HTML → 작업 1회 생성 → 기존 결과 표시까지 검증한다. 실제 Gemini 호출은 일반 검증의 필수 조건으로 두지 않는다.
- [x] 앱 새로고침 후 gubun이 30으로 돌아오는지, 날짜가 한국 시간의 오늘인지 최종 확인한다. 변경 파일과 검증 결과를 보고한다.

## 완료 조건

1. 지정 위치에 가져오기 버튼과 설정 진입점이 있고 날짜/gubun만 변경할 수 있다.
2. 초기값은 오늘/30이고 고정 파라미터는 id=1/cond=list이다.
3. 유효한 `contWrap` 내부 HTML을 입력창에 반영하고 기존 변환·압축을 1회만 자동 시작한다.
4. gubun=1로 실사이트 조회와 기존 직렬화 호환성을 확인할 수 있다.
5. HTTP 200이어도 뉴스가 없는 페이지는 자동 처리하지 않고 기존 입력을 보존하며 이유를 표시한다.
6. 작업 시작 실패 시 가져온 원문에서 재실행할 수 있고, 기존 수동 입력·파일 선택·복구 기능이 동작한다.
7. 가져오기 대기 중 경쟁 동작을 방지하고 기존 결과·분량 확인·수동 재생성 계약을 유지한다.

## 계획 검토

- 사용자 지정 조건을 요구사항, Task 1~3, 완료 조건에 대응시켰다.
- 가져오기 API는 html/source_url/base_date/gubun을 반환하고 프런트엔드는 html을 그대로 기존 jobs에 전달하므로 계약이 일치한다.
- “HTTP 200이지만 본문 없음”과 “busy인 상태로 runJob을 호출하면 자동 실행되지 않음”을 명시적으로 다룬다.
- 최초 작성 시 계획만 생성했고, 후속 요청에서 구현과 검증을 완료했다.

## 실행 결과

- `feat/myasset-import` 브랜치에서 구현했다. 기존 작업 폴더에 변경을 유지한다.
- Python 전체 188개 테스트 통과. 외부 라이브러리의 기존 deprecation 경고 3개가 있다.
- 프런트엔드 30개 테스트와 타입 검사·Vite production build 통과.
- 실제 2026-09-17/gubun=1 조회: 내부 HTML 8,029 bytes, 기존 직렬화 5개 장면. gubun=30은 본문 없음 404 안내 확인.
- 신규 `tests/myasset_browser_smoke.py`: Edge에서 미게시 시 입력 유지, 성공 후 작업 1회 자동 생성, 새로고침 기본값 복원, Escape 포커스와 320/390/1440px 배치 확인.
- 실제 Gemini 호출 대신 모의 응답으로 기존 작업 파이프라인과 화면 자동 연결을 검증했다.
