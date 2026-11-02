# InfoX 1단계 구현 계획

> **For agentic workers:** Use superpowers:executing-plans. 사용자가 명세 1단계의 구현·검증·인계·커밋·푸시를 요청했다.

**Goal:** 독립 백엔드 클라이언트에서 인증·갱신·기존 영상 조회·즐겨찾기 검색·SSE 본문 수신을 검증한다.

**Architecture:** 기존 FastAPI/Vue 흐름을 유지하고 `backend/infox`에 독립 비동기 HTTP 어댑터와 진단 CLI를 추가한다. 기존 백엔드 전용 `.env` 및 프로세스 환경에서 자격을 읽고 세션 토큰·쿠키는 메모리에만 둔다. 일반 API는 Bearer/선택적 조직 장치 헤더, SSE는 실제 EventSource처럼 쿠키 세션을 기본으로 하며 Bearer 방식은 명시적으로 선택한다.

**Tech Stack:** Python, 기존 httpx·truststore·python-dotenv, pytest.

**Spec:** `docs/infox/automation-implementation-spec.md` §5·6·9·15의 1단계.

## Global Constraints

- 고정 HTTPS 호스트 `https://infox2.infomaxai.com`, 인증값을 URL·로그·문서에 넣지 않는다.
- 프로젝트 생성·제작 시작·편집 저장·프런트엔드 연결은 후속 단계다.
- 변경 전 README·런처·출력물은 건드리거나 커밋하지 않는다. 이번 명세는 인계의 기준으로 함께 보관한다.
- 명세에 이미 설계와 범위가 정해져 있으므로 추가 설계 승인 없이 구현한다. 신규 파일 중심이므로 현 체크아웃에서 경로를 지정해 스테이징한다.

## Review Focus

- 잘못된 자격·만료·접근 거절과 전송/서버/계약 오류를 구분한다.
- 401 후 갱신은 1회이며 읽기만 제한적으로 재시도한다.
- SSE 다중 data 행·주석·BOM·CRLF·빈 프레임·재연결 ID·중단을 처리하고 성공 상태를 추정하지 않는다.
- 응답 본문·토큰·쿠키·서명 URL이 예외와 진단 출력에 노출되지 않는다.
- 즐겨찾기 필터와 페이지·검색 기간을 보존하며 0건·잘못된 스키마를 구분한다.

## Tasks

### 1. HTTP 및 SSE 어댑터

Files: `backend/infox/{__init__,config,client,sse}.py`, `tests/test_infox_client.py`, `tests/test_infox_sse.py`.

- [x] 실패 테스트 작성 및 RED 확인: JSON email/password 로그인, Bearer/장치 헤더, 갱신과 401 복구, 403·네트워크·429·잘못된 응답, 즐겨찾기 필터, SSE 프레이밍.
- [x] `InfoXClient.authenticate()`, `refresh()`, `video_state(video_id)`, `search_favorites(...)`, `events(video_id, last_event_id=...)` 구현. 외부 HTTP만 MockTransport로 대체한다.
- [x] 새 테스트 GREEN 확인.

### 2. 독립 진단 및 응답 예시

Files: `backend/infox/{__main__,probe}.py`, `.env.example`, `docs/infox/phase1-*`, `tests/test_infox_probe.py`.

- [x] 진단 성공/누락 설정/오류 시 비밀 비노출과 종료 코드 실패 테스트 작성 및 RED 확인.
- [x] `python -m backend.infox`에서 로그인→조회→갱신→재조회→두 종류 즐겨찾기 검색→SSE 본문 수신. 타임아웃·이벤트 수 제한, 구조만 출력.
- [x] 실제 자격이 제공되면 기존 영상으로 실검증. 불가하면 미충족 조건을 명시하며 완료로 포장하지 않는다.
- [x] 전체 pytest 실행. 변경 전 PowerShell Get-FileHash 실패는 모듈 검색 경로 환경을 바로잡아 재실행한다.

### 3. 검토·인계·전달

- [x] 전체 변경을 독립 검토하고 중요 문제 수정 및 관련 테스트 실행.
- [x] 다음 작업자의 진입점·명령·확인 근거·미확인 계약·2단계 범위를 인계 문서에 작성.
- 전달 절차: 지정 파일만 스테이징, 비밀·diff 검증, 커밋 및 origin/master 푸시, 원격 SHA 확인.

## Execution record

- Baseline: 415 passed, 6 failed (`tests/test_launcher.py` 모든 매개변수). PowerShell 7에서 상속된 PSModulePath의 앞쪽 Utility 모듈이 Windows PowerShell 자동 로딩을 가렸다. 단순 추가 대신 Windows PowerShell 모듈 경로만 지정하니 해결됐다. 제품 소스 변경 없이 테스트 프로세스 환경만 조정했다.
- 공개 로그인 소스: JSON `{email, password}` 및 조직 장치 갱신 POST `{device_token}` 확인.
- 공개 SSE: 인증 없는 `infox:0` 연결에서 HTTP 200 및 실제 `connection/connected` JSON 본문 수신. 존재하는 계정 영상의 인증·접근 권한 검증을 대체하지 않는다.
- 실제 계정 검증: 로그인·조회·갱신·재조회 성공. 반도체 즐겨찾기 일반 영상 12개, 사용자 영상 0개. 기존 영상 채널에서 실제 connection JSON 및 채널 일치 확인.
- 독립 리뷰 수정: 갱신 후 503도 조회 재시도 적용, 갱신 실패 자체는 재시도 제외, CR로 끝나는 열린 SSE 즉시 dispatch. RED→GREEN 회귀 테스트로 확인, 재검토 차단 문제 없음.
- 최종 검증: InfoX 35 passed, 전체 Python 456 passed (기존 경고 3개), frontend 49 passed, 타입 검사·프로덕션 빌드 성공.
