# InfoX 1·2·3·4·5단계 완료 및 6단계 인계

작성일: 2026-11-02 (한국 시간). 프로젝트: `C:\WORKS\SummaryToVideo`, 브랜치: `master`.

현재는 **1·2·3·4·5단계 완료**다. 아래 기존 단계의 기록은 당시 범위이며, 문서 마지막의 **5단계 구현·실검증·6단계 진입점**이 현재 기준이다. [2단계 실행 문서](phase2-runs.md)의 스냅샷·CAS 계약과 3단계의 실제 완료 이벤트 검증도 계속 적용한다.

## 1단계 완료 기록

[자동화 명세](automation-implementation-spec.md) §15의 **1단계 InfoX 연결 검증**을 구현했다. 백엔드 독립 클라이언트에서 실제 로그인, 기존 영상 조회, 토큰 갱신, 갱신 후 동일 영상 재조회, `반도체` 즐겨찾기 검색, 해당 기존 영상 채널 SSE의 JSON 본문 수신을 확인했다.

1단계에는 프로젝트 생성·제작 시작·에셋 변경·편집 저장을 추가하지 않았으며 기존 앱 흐름과 보안 정책을 유지했다. 2·3단계 변경은 아래 추가 기록에서 구분한다. 에셋 변경은 여전히 후속 단계다.

## 먼저 읽을 파일

| 파일 | 역할 |
| --- | --- |
| `docs/infox/automation-implementation-spec.md` | 7단계 전체 요구와 이번 범위의 기준 |
| `docs/infox/phase1-connection.md` | 설정·실행 명령·오류·인증 계약과 사용 예 |
| `backend/infox/config.py` | Git 제외 `.env`/환경에서 백엔드 자격 로딩, 비밀 repr 제외 |
| `backend/infox/client.py` | 고정 HTTPS 어댑터, 로그인/갱신, 조회/검색, SSE 연결 |
| `backend/infox/errors.py` | 비밀과 서버 원문을 포함하지 않는 오류 코드 |
| `backend/infox/sse.py` | 다중 data/BOM/CRLF/이벤트 ID/retry/프레임 크기 제한 |
| `backend/infox/probe.py`, `__main__.py` | 생성 없이 실연결을 점검하는 독립 명령 |
| `tests/test_infox_{client,sse,probe}.py` | 네트워크 경계 회귀 테스트 |
| `docs/infox/phase1-live-verification.json` | 실제 진단 출력. 자료형·건수·고정 필드명만 저장 |
| `docs/infox/phase1-response-examples.json` | 민감 값을 바꾼 응답 발췌. 전체 편집 JSON fixture가 아님 |

## 실제 연결에서 확인한 계약

- `POST /infomaxai/auth/login`에 JSON `{email, password}`를 보낸다. `INFOX_USERNAME`이 email 필드에 대응한다.
- 로그인과 `GET /infomaxai/auth/refresh-token` 모두 `access_token`, `token_type=bearer`를 반환했고 갱신 후 Bearer로 동일 영상 조회에 성공했다.
- 실제 `videostate`의 `id`는 문자열, `video`·`script`는 객체, `can_edit`는 boolean, `updated_at`은 문자열이었다. 최신 편집 컨텍스트의 세부 매핑은 후속 단계에서 확인한다.
- 일반 영상 검색은 `total=12`, 12개 모두 `is_favorite=true`; 사용자 영상 검색은 0건이었다. 검색어 `반도체`, 날짜 필터 생략, 첫 페이지 `skip=0, limit=50` 조건이다. 결과 건수를 상수로 사용하지 않는다.
- 영상 구간 항목은 `id`, `videoId`, `frameIndex`, `timestamp`, `duration`, `segment`, 미리보기 관련 필드를 포함한다. 같은 원영상의 구간을 합치지 않는다.
- `/api/stream?channel=infox:<기존 영상>`의 실제 메시지 본문은 `{"type":"connection","status":"connected","channel":"infox:<영상>","id":"<연결 ID>"}`였다. SSE의 `data:` 프레임을 파싱해 JSON을 읽었고 채널 일치를 확인했다.
- SSE는 브라우저 EventSource와 동일하게 추가 Bearer/장치 헤더 없이 수신했다. 공개 테스트 채널도 쿠키 없이 connection을 반환했으므로 연결 메시지만으로 영상 접근 권한·작업 이벤트 권한을 확정하지 않는다. 정상 영상 조회는 별도 Bearer로 확인했다.

조직 장치 전용 로그인과 갱신 fallback, 401/403, 잘못된 응답, 통신 중단, Bearer SSE 모드는 통제된 HTTP 테스트로 검증했다. 실제 계정에서 고의로 비밀번호를 틀리거나 조직 정책을 변경하지 않았다. 자연 만료를 기다리는 실험도 수행하지 않았다.

## 검증 재현

사용자가 Git 제외 `.env`에 `INFOX_USERNAME`, `INFOX_PASSWORD`, `INFOX_VIDEO_ID`를 설정했다. 실제 값은 문서·로그·Git에 넣지 않는다. 갱신 토큰과 세션 쿠키는 디스크에 저장하지 않는다.

```powershell
# 프로젝트 루트에서 실행. 새 영상 생성이나 에셋 저장은 하지 않는다.
.venv/Scripts/python.exe -m backend.infox

# 전체 백엔드 회귀 테스트
.venv/Scripts/python.exe -m pytest -q

# 프런트엔드 회귀 및 빌드(프로젝트 전용 Node)
.tools/node/node.exe frontend/node_modules/vitest/vitest.mjs run --root frontend
.tools/node/node.exe frontend/node_modules/vue-tsc/bin/vue-tsc.js -b frontend/tsconfig.json
.tools/node/node.exe frontend/node_modules/vite/bin/vite.js build frontend
```

이번 테스트 환경은 PowerShell 7의 `PSModulePath`가 자식 Windows PowerShell의 Utility 모듈을 가려 `Get-FileHash` 자동 로딩이 실패했다. 제품 런처 수정 없이 테스트 프로세스에 다음 환경을 설정하면 런처 6개 테스트도 실행된다. 단순 경로 추가만으로는 앞쪽 모듈을 계속 선택하므로 Windows PowerShell 경로만 지정해야 한다.

```powershell
$env:PSModulePath = 'C:\Users\22608024\Documents\WindowsPowerShell\Modules;C:\Program Files\WindowsPowerShell\Modules;C:\Windows\System32\WindowsPowerShell\v1.0\Modules'
.venv/Scripts/python.exe -m pytest -q
```

검증 결과와 독립 리뷰의 최종 기록은 아래 완료 기록에서 확인한다. 변경 전에는 백엔드 415개 통과·런처 6개 실패였고 위 환경 정리 후 전체가 통과했다. 기존 라이브러리 deprecation 경고 3개는 남아 있다.

## 2단계 구현 기준 (당시 인계, 현재 완료)

당시 다음 작업으로 지정한 **실행 기록과 대본 확정**의 기준이다. 아래 요구에 따라 구현했으며 생성 요청은 3단계로 유지한다.

1. 최신 `frontend/src/App.vue`의 `flushCompressed()`와 한글 조합/저장 실패/revision 충돌 처리를 다시 읽는다. 저장 완료 전에는 실행을 만들지 않는다.
2. 백엔드는 `JobManager.get()`의 최신 `compressed`, `revision`을 확인하고 대본·해시·줄/장면 대응·설정·확정 시각을 하나의 불변 스냅샷으로 저장한다. 최초 `compressed.txt`를 읽지 않는다.
3. 원자적인 별도 실행 저장소와 관리자를 만든다. 실행 ID, 단계, 전송 스냅샷, 요청 의도/접수 상태 및 이후 확보할 `video_id`, `taskid`를 복구할 구조가 필요하다. 기존 `ResultStore.write_text()`의 허용 파일명을 무분별하게 넓히지 않는다.
4. 중복 클릭과 응답 유실을 구분한다. 접수 여부가 불명확하면 외부 조회 후 판단하며 프로젝트 생성·제작 시작을 자동 재전송하지 않는다. `client._read()`는 조회 전용이므로 제작 시작 GET에도 사용하면 안 된다.
5. 실행 생성/조회 로컬 API를 기존 보안 가드 안에 추가하고 재시작 후 동일 진행 단계와 외부 ID 복원을 검증한다. 실제 제작 연결은 3단계다.
6. `needs_review` 대본의 사용자 확인 정책은 아직 미정이다. 진단을 지우거나 무검토 자동 전송하지 않는다. 2단계의 해당 기능 연결 시 명시적으로 정한다.

## 후속 단계로 남은 검증

- **3단계:** 현재 영상·현재 taskid의 실제 `completed`/실패 본문, 중복/재연결/상태 반영 지연, 입력 대본·장면·음성·자막 일치. 이번 connection 메시지를 완료 판정으로 쓰지 않는다.
- **5단계:** 사용자 영상 결과가 생기는 계정의 스키마, 날짜 필터 의미, 전체 페이지와 사용 가능한 구간 선택, 모든 구간 참조의 일관성.
- **6단계:** 저장 응답 유실·버전 충돌·저장 후 서버 재조회·혼합 묶음·길이별 실제 편집기 재생.
- **7단계:** 백엔드 인증과 사용자 브라우저 인증의 분리, 탭 이동, 전체 중단/재개.

## 작업 트리와 전달 주의

시작 시 미커밋이던 루트 `README.md`, `scripts/launch.ps1`, `toggle.cmd`, 기존 `outputs/` 결과물은 이번 커밋에 포함하지 않는다. 명세 파일은 사용자가 구현 기준으로 지정한 문서이므로 원문 그대로 이번 산출물과 함께 커밋한다. 이번 작업 파일만 명시적으로 스테이징한다.

## 1단계 검증 기록

- 실제 독립 CLI: 성공(인증·조회·갱신·재조회·두 검색·SSE JSON 본문). 세부 시각과 결과는 `phase1-live-verification.json`.
- InfoX 회귀 테스트: **35 passed**. 로그인/갱신·401/403·안전한 오류·검색·SSE·진단 출력을 검증했다.
- 전체 백엔드: **456 passed**, 기존 의존성 경고 3개. 위 PSModulePath 환경으로 런처 테스트도 모두 포함해 통과했다.
- 프런트엔드: **49 passed**. `vue-tsc` 타입 검사와 Vite 프로덕션 빌드 성공.
- 독립 코드 리뷰: 갱신 직후 일시 오류의 재시도 및 열린 연결에서 CR로 끝나는 SSE 프레임 지연을 재현 테스트로 확인하고 수정했다. 갱신 자체의 실패가 조회 재시도에 섞이지 않는 회귀 테스트도 추가했다. 재검토에서 차단 문제 없음.
- 커밋 대상 18개 파일을 로컬 설정의 실제 자격·영상 ID와 대조해 일치 없음 확인. 원본 HAR·서명 URL은 추가하지 않았다.
- 1단계는 `8caf6e4`로 `origin/master`에 전달했다.

## 2단계 추가 기록: 구현 완료

**실행 기록과 대본 확정**을 화면부터 로컬 API·영속 저장까지 연결했다. 사용법과 API 계약은 [phase2-runs.md](phase2-runs.md)를 따른다. 사용자에게 보이는 기능은 **InfoX 전송본 확정**이며, 이 시점에는 외부 생성 요청을 보내지 않는다.

| 위치 | 2단계 변경 |
| --- | --- |
| `backend/infox/run_models.py` | 엄격한 설정/입력 모델, 불변 대본·장면·진단 스냅샷, 실행 단계·접수 기록 |
| `backend/infox/service.py` | 최신 JobManager 값/revision/검토 확인 검증, 스냅샷 확정, `creation_body()` |
| `backend/infox/store.py` | SQLite 원자적 실행+intent 저장, 중복 방지, version CAS, 쓰기 전 의도/접수 결과 |
| `backend/app.py` | 기존 보안 가드 안의 실행 확정·조회·작업별 최신 실행 API |
| `frontend/src/App.vue`, `api.ts`, `types.ts`, `style.css` | 저장 대기, 검토 확인, 설정 표시, 응답 유실 처리, 실행 복원/편집 차이 표시 |
| `tests/test_infox_runs.py`, `test_infox_runs_api.py` | 편집본 일치·트랜잭션/동시성·재시작·접수 상태·API 보안 검증 |
| `frontend/src/App.spec.ts`, `tests/infox_runs_browser_smoke.py` | UI 저장/IME/충돌/유실/복원 회귀, 실제 로컬 API 브라우저 확인 |

### 확정 정책과 보관 내용

- `flushCompressed()`를 기다린 뒤 새 실행을 요청한다. 저장 실패/한글 조합/revision 충돌에서는 새 전송본을 확정하지 않는다.
- 백엔드는 최초 `compressed.txt` 대신 `JobManager.get()`의 최신 `compressed`와 revision을 사용한다. 이후 편집은 기존 실행의 스냅샷을 바꾸지 않는다.
- 원문·UTF-8 SHA-256·revision·확정 시각·줄/장면 대응·전체 설정·검토 확인·생성 당시 진단을 함께 보관한다. 장면 수를 5로 고정하지 않으며 빈 줄만 제외하고 다른 줄의 공백은 보존한다.
- 미정이었던 검토 정책은 **명시적 확인**으로 구현했다. needs_review/failed 또는 검증 사유가 남으면 체크를 요구하고 원래 진단을 유지한다. 편집 시 체크를 해제한다.
- 설정은 명세 관찰값을 타입 그대로 확정한다. 사용자 화면에서는 제목·자동 대체 여부를 변경하며 나머지 전체 기본값도 DB에 함께 저장한다.

### 저장·복원·중복 방지

DB: **`.runtime/infox-runs.sqlite3`**. 기존 `.runtime/` 제외 규칙을 사용하며 자격·쿠키·서명 URL을 넣지 않는다. 대본과 실행 기록이 있으므로 DB 자체를 Git에 추가하지 않는다. `ResultStore.write_text()` 허용 목록은 그대로다.

SQLite `BEGIN IMMEDIATE` 안에서 실행과 intent 별칭을 함께 커밋한다. 부분 저장 실패는 롤백하고 성공으로 응답하지 않는다. 여러 동시 요청도 같은 스냅샷이면 같은 실행을 반환한다. Windows에서 삭제 중인 SQLite journal을 `Path.resolve()`로 검사하면 일시적으로 extended-length 경로가 반환되어 오탐했으므로, 안전한 부모 아래 파일의 symlink/junction 여부를 직접 검사한다.

같은 intent는 같은 실행으로 복원하며 다른 내용으로 재사용하면 409다. 별도 intent의 같은 job·revision·본문 해시·설정도 기본적으로 기존 실행에 연결한다. 동일 대본의 의도적인 새 실행은 `new_run=true`로 구분하되 기존 실행의 확실한 종료와 미확인 접수 부재를 요구한다. 재제작 UI는 아직 추가하지 않았다.

응답을 잃은 화면은 요청 본문(intent/revision/설정/확인값)을 sessionStorage에 보관하고 그대로 다시 확인한다. 이후 편집된 대본이나 빈 대본으로 원래 요청을 바꿔 보내지 않는다. 최신 실행 조회는 재시작 후 진행 상태·외부 ID를 복원하며 POST를 자동 실행하지 않는다. 복원 GET 동안 설정 입력을 잠가 사용자의 수정값이 뒤늦은 응답으로 덮이지 않게 한다.

### 3단계 작업자가 먼저 할 일

1. `RunService.prepare()`가 반환한 **prepared** 실행을 사용한다. 이 상태는 로컬 확정이며 제작 접수가 아니다. `creation_body(record.snapshot)`의 본문을 보내고 현재 편집본을 다시 읽어 덮어쓰지 않는다.
2. 생성 POST 전 `store.begin_request(run_id, 'project', expected_version=...)`를 커밋한다. 그 뒤 외부 요청은 **한 번만** 보낸다. 성공의 영상 ID를 `record_outcome(..., 'accepted', external_id=..., expected_version=...)`로 저장한다.
3. 확보한 video_id로 `begin_request(..., 'generation')` 후 제작 시작 GET을 한 번 보낸다. 반환 task ID를 별도로 저장한다. `client._read()`의 조회 재시도를 이 쓰기에 사용하지 않는다.
4. **intent_recorded/unknown**은 `requires_reconciliation=true`다. 의도 커밋 직후 전송 전에 죽은 경우와 외부 접수 뒤 응답을 잃은 경우를 로컬 기록만으로 구분할 수 없다. 서버 상태를 확인하고 unknown을 해소하며, 같은 operation의 begin을 다시 호출하거나 새 프로젝트를 자동 생성하지 않는다.
5. store의 receipt CAS는 이미 있지만 생성 이후 진행 이벤트/오류/완료 확인의 추가 stage 저장 메서드, 서비스 생명주기와 추적 태스크는 아직 없다. 3단계에서 동일 version 계약으로 확장한다. 범용 임의 JSON 갱신 API를 노출하지 않는다.
6. 일반 API와 SSE 인증은 1단계 어댑터를 사용한다. 현재 영상 채널과 현재 taskid의 실제 completed·실패 본문을 단일 생성에서 확인하고, 전송 대본·장면·음성·자막 준비 상태를 검증한다. connection/connected, progress=100, can_edit=true를 완료로 사용하지 않는다.
7. 화면의 전송본 준비에 실제 제작 버튼·진행 UI를 연결한다. 전송본 revision과 현재 편집본 차이, 로컬 확정과 외부 접수를 계속 구분한다. 접수/추적은 긴 단일 HTTP 응답을 기다리게 하지 않고 실행 ID로 조회한다.

2단계는 외부 쓰기 요청을 실행하지 않았다. `record_outcome`으로 넣은 테스트용 영상/작업 ID는 임시 DB의 통제된 검증값이며 실제 제작이 아니다. 기본 본문 설정은 기존 HAR와 1단계의 실제 조회 근거에 맞춘다.

### 2단계 검증·리뷰·전달

- 전용 백엔드 회귀: **17 passed**. 최신 편집본·설정/장면 일치, 이후 편집 분리, 검토 확인, revision 충돌, 빈 대본, 동시 클릭, 의도/접수/외부 ID 재로드, SQLite 실패 롤백, 안전한 입력/오류·접근 가드를 확인했다.
- 전체 백엔드: **473 passed**, 기존 경고 3개. Windows PowerShell 모듈 경로 환경은 위 재현 절차와 같다.
- 프런트엔드: **58 passed**. `vue-tsc` 타입 검사와 Vite 프로덕션 빌드 성공.
- 독립 코드 리뷰: Critical/Important 문제 없음. 초기 실행 복원 중 설정을 수정하면 늦은 응답이 덮는 Minor race를 재현 테스트로 확인하고 복원 중 설정 잠금으로 수정했다.
- 실제 Edge/Chromium과 로컬 API: 편집 저장 후 확정, 검토 체크, 중복 클릭 재사용, 새 JobManager/RunStore의 디스크 복원, 이후 편집과 스냅샷 분리, 390/1440px 레이아웃을 확인했다. 외부 브라우저 요청은 0회다. 모든 테스트 자료는 임시 프로젝트의 표시용 대본이다.
- 기존 미커밋 루트 README·런처·출력물은 2단계에서도 보존하고 커밋에서 제외한다. 변경된 구현·검증·문서 파일만 origin/master에 전달한다.

## 3단계 추가 기록: 구현·실검증 완료

확정된 전송본으로 프로젝트 생성 POST와 제작 시작 GET을 별도로 보내고, 같은 영상 채널·현재 task ID의 완료 신호와 `videostate` 일치를 확인하는 기능을 구현했다. **시안 준비는 `draft_ready`**다. 기업 에셋 검사·즐겨찾기 교체·저장·자동 브라우저 이동은 아직 수행하지 않으며 `ready_for_editor`로 표시하지 않는다.

### 구현 위치와 사용자 흐름

| 위치 | 책임 |
| --- | --- |
| `backend/infox/client.py` | 생성 POST·제작 시작 GET은 `_json_once()`로 각 1회만 전송. 401·429·5xx·타임아웃도 자동 재전송하지 않음. `render_logs()`는 조회 전용 |
| `backend/infox/manager.py` | 인증, 두 단계 의도/접수 CAS, SSE 선구독·이벤트 버퍼, 백엔드 추적·재연결·종료·재개 |
| `backend/infox/tracking.py` | 현재 영상/작업 이벤트 선택, 오류 우선 처리, 대본·장면·음성·자막 검증 |
| `backend/infox/run_models.py`, `store.py` | 기존 레코드에 기본값으로 호환되는 Tracking/DraftCheck, 제한된 stage CAS 갱신, 복구 대상 열거 |
| `backend/app.py` | 서버 lifespan의 복구/종료, 보호된 시작 API, 실행 조회의 `tracking_active` |
| `frontend/src/App.vue`, `api.ts`, `types.ts` | 확정본 제작 시작, 실행 ID GET 폴링, 단계/원인 표시, 수동 편집 링크 |
| `tests/test_infox_generation.py` | 한 번만 전송·불명확한 접수·복구·이벤트/데이터 검증·CAS 경합·API 가드 |
| `tests/infox_generation_live.py`, `phase3-live-verification.json` | 단일 실제 실행과 식별자/자격/URL을 제거한 관찰 근거 |

기존 **InfoX 전송본 확정**은 계속 로컬 저장만 한다. 이후 **확정본으로 InfoX 제작 시작**을 명시적으로 눌러 외부 제작을 시작한다. 이후 편집본이 달라도 저장된 스냅샷을 덮어쓰지 않으며 그 차이를 표시한다. 기존 최신 편집 저장·revision 충돌·한글 조합·검토 확인·생성 진단 보존 정책을 유지했다.

- `POST /api/infox-runs/{run_id}/start`: 202, 기존 실행 전체와 `tracking_active`를 반환한다. 긴 완료 추적을 HTTP 응답에 매달지 않는다. 같은 실행의 중복 요청은 같은 백엔드 태스크로 합친다.
- `GET /api/infox-runs/{run_id}`와 작업별 최신 실행 GET: 현재 단계·접수 상태·영상/작업 ID·추적 요약·준비 확인·수동 `editor_url`을 반환한다. 화면 재로드는 GET만 수행한다.
- 시작 응답이 유실되면 화면은 같은 실행 ID를 조회한다. POST를 자동 반복하지 않는다. 로컬 가드에서 명확히 거절한 403만 기존 세션 복구 정책을 적용한다.
- 수동 링크는 고정 InfoX 호스트의 `/studio/library/videos/{video_id}?chatboxView=script`이며 `taskpopup` 없이 새 탭으로 사용자가 직접 연다. 자동 이동하지 않는다.

### 영속화와 재개 경계

1. 인증 후 `begin_request(project)`를 커밋하고 스냅샷의 `creation_body()`를 한 번 전송한다. 영상 ID를 `record_outcome(..., accepted, expected_version=...)`으로 저장한다.
2. 해당 영상의 접근을 조회로 확인하고 SSE connection이 확인된 뒤 `begin_request(generation)`을 커밋한다. 제작 시작 GET을 한 번 전송하고 task ID를 별도로 저장한다. 응답보다 빠른 이벤트는 메모리 큐에서 기다린다.
3. 현재 task의 명시적 실패/오류는 `failed`, 완료 신호는 `checking_draft`다. 일치하는 결과까지 확인되면 `draft_ready`로 전이한다. `progress=100`, connection, can_edit, 시간 경과는 완료 근거가 아니다.
4. 외부 응답이 불명확하면 receipt는 `unknown`이다. 프로세스 중단으로 남은 `intent_recorded`도 재전송하지 않는다. 영상 ID가 있으면 동일 영상 상태·이력을 먼저 읽는다. 영상 ID가 없거나 이력에서 task 대응을 입증할 수 없으면 `needs_attention`과 접수 확인 불가 사유를 남긴다. 임의 ID 추정·새 프로젝트 생성·제작 재시작은 없다.
5. 백엔드 시작 시 진행 중 기록을 복원해 조회/추적만 재개한다. 명확히 프로젝트만 접수된 경우 자동 제작 시작은 하지 않고 사용자의 기존 실행 확인 요청에서 미전송 제작 단계만 진행한다. `needs_attention`은 자동 반복하지 않는다.
6. 다른 관리자의 확인이 버전을 올리는 동안 외부 요청이 성공한 경우, 원래 receipt의 `intent_at`이 같은지 검증하고 **로컬 결과 저장 CAS만** 제한적으로 다시 시도한다. 외부 요청은 반복하지 않는다. 디스크 자체가 실패하면 남은 intent에서 수동 접수 확인이 필요하다.

일반 추적은 600초, SSE 읽기는 기존 45초, 재연결 대기는 2초다. 완료 후 준비 재조회는 기본 10회/2초 간격이며 HTTP 읽기의 기존 제한 재시도도 적용된다. 성공 근거가 부족하면 `needs_attention`으로 끝난다. 준비 검증은 장면 수·순서와 `voiceover.content`의 정확한 일치, 음성 bucket/filename·양의 audio_duration·설정 음성/제공자/속도, 비어 있지 않고 순서가 맞는 `segment_captions`의 유효 시간과 공백을 제외한 본문 일치를 요구한다. 기존 `captions=null`은 완료를 막지 않는다.

### 실제 단일 실행에서 확인한 계약

**2026-11-02 11:24:00 한국 시간**에 `draft_ready`를 확인했다. 실제 요청은 백엔드 실행 관리자용 명시적 검증 CLI로 수행했고, 화면 연결은 별도의 실제 로컬 API/브라우저 검증으로 확인했다. 시장 뉴스 대신 검토한 통합 검증용 2장면 대본을 사용했으며, 원래 compressed.txt와 다른 저장 편집본 revision 1을 확정했다. 이후 동일 실행의 대본·영상 ID·task ID를 끝까지 연결했다. 에셋 자동 대체 설정은 false다.

- 프로젝트 생성 응답에서 영상 ID, 별도 제작 시작 응답에서 `message="generate_type_text"`와 task ID를 확보했다. 두 receipt 모두 accepted이며 스냅샷은 바뀌지 않았다.
- `infox:{현재 video_id}` 채널의 connection 뒤 **작업 메시지가 최상위 JSON 객체로 직접** 도착했다. 선택 필드는 `id`, `status`(null), `content`(문자열), `additional_kwargs`다. 본문/식별자/파일 URL은 근거 파일에 복사하지 않았다.
- 현재 task와 일치하는 `additional_kwargs.taskid`를 확인했다. 진행 시 `task_status="processing"`, 마지막에 **`task_status="completed", progress=100, error` 거짓 값**을 관찰했다. 초기 일부 메시지는 task_status가 없었다. 처리 중 메시지는 내부 running으로 표시하고, 없는/미지 상태는 성공으로 해석하지 않는다.
- 일치하는 작업 메시지 28건을 처리했다. 이 실제 실행에서는 연결 중단은 없었다. 중복·재연결·조기 완료·데이터 지연은 통제 테스트에서 확인했다.
- 완료 직후 동일 영상 상태를 다시 읽어 영상 ID·2개 장면의 대본 순서/본문·지정 음성 파일/길이/설정·모든 segment_captions 준비와 텍스트 일치를 확인했다. 네 준비 확인값은 모두 true다.
- **실제 실패 이벤트는 관찰하지 않았다.** `error`가 있는 완료 메시지의 실패 우선 처리, failed/error/cancelled 등 보수적 실패 처리는 통제된 이벤트로 검증했다. 실제 서비스의 모든 실패 상태명/오류 외피까지 확정한 것은 아니다.

실제 ID는 Git 제외 `.runtime/infox-runs.sqlite3`와 `.runtime/infox-phase3-live.json`의 실행 연결로만 확인한다. 실제 영상 재생·최종 MP4 렌더링은 수행하지 않았다. `tests.infox_generation_live` 재실행은 기존 marker의 실행만 사용하며 완료된 근거 파일을 보존한다. **marker를 지우고 새 실행을 만들지 않는다.**

### 남은 제약과 4단계 작업자의 진입점

- 재연결에서 서버가 Last-Event-ID로 누락 이벤트를 재생하는지는 실제 검증되지 않았다. 완료 신호를 놓친 경우 결과가 준비돼 보여도 완료 처리하지 않는다. `video_render_logs`의 실제 완료/task 대응 계약도 미검증이므로 대체 완료 근거로 사용하지 않는다.
- 접수 여부를 모르는 프로젝트를 제목/시간만으로 연결하지 않는다. 원격 멱등 키·프로젝트 목록 조회·task 조회 계약이 없으므로 unknown을 자동 해소하거나 새로 생성하는 기능은 없다.
- 실제 실패 이벤트, 장시간 인증 자연 만료, 서비스 장애, 실제 재연결은 이번 성공 실행으로 입증한 범위가 아니다. API 오류·복구는 통제 테스트 근거다.
- **4단계 입력은 `RunRecord.stage == 'draft_ready'`, `tracking.completion_seen == true`, `tracking.draft.ready == true`인 실행**이다. snapshot/video_id/task_id는 그대로 유지한다. `video_state(video_id)`로 최신 데이터를 새로 읽고 §8의 실제 배정 참조와 검색 메타데이터를 대조하는 검사기를 추가한다.
- 4단계는 `auditing_assets`와 검사 결과용 엄격한 모델/CAS를 추가해야 한다. 현재 `store.track()`는 제작 단계만 허용하므로 임의 JSON 갱신이나 snapshot 변경으로 우회하지 않는다. 자동 대체 설정이 true여도 아직 교체·저장하지 않으며, `draft_ready`를 `ready_for_editor`로 바꾸지 않는다.
- 에셋 목록에서 기업명 검출·출처 예외·검사 불가를 먼저 구분한다. 즐겨찾기 조회/교체/저장과 자동 편집기 이동은 각각 후속 단계에서 연결한다.

### 3단계 검증 결과

- 전체 백엔드: **487 passed**, 기존 의존성 deprecation 경고 3개. 위 PSModulePath 설정으로 런처 테스트도 포함했다. InfoX DB 복구 실패가 기존 대본 앱의 기동까지 막지 않는 회귀도 포함한다.
- 프런트엔드: **60 passed**, vue-tsc 타입 검사·Vite 프로덕션 빌드 성공.
- 실제 Edge Chromium + 로컬 FastAPI: 저장/검토 확인·스냅샷 불변·단일 제작 시작·작업 완료/준비 확인·수동 링크·재로드 후 POST 미반복·390/1440px 레이아웃 성공. InfoX 응답은 통제 클라이언트이며 외부 브라우저 요청은 0회다.
- 외부 실제 검증은 위 단일 실행으로만 수행했다. CLI 재실행/마지막 확인도 같은 `.runtime` marker와 실행 ID를 사용한다.
- 독립 리뷰: Critical 0, Important 1(동시 접수 확인 중 성공 ID 저장 CAS 충돌)을 재현한 실패 테스트 후 로컬 CAS 재저장으로 수정했다. 배치 이벤트의 완료/실패 우선 선택과 완료 후 인증 오류의 구분도 각각 재현 테스트 후 수정했다.

```powershell
.venv/Scripts/python.exe -m pytest -q
.tools/node/node.exe frontend/node_modules/vitest/vitest.mjs run --root frontend
.tools/node/node.exe frontend/node_modules/vue-tsc/bin/vue-tsc.js -b frontend/tsconfig.json
.tools/node/node.exe frontend/node_modules/vite/bin/vite.js build frontend
.venv/Scripts/python.exe -m tests.infox_runs_browser_smoke
# 명시적 실제 검증: 기존 marker 실행만 사용. 새 프로젝트를 만들기 위해 marker를 삭제하지 않는다.
.venv/Scripts/python.exe -m tests.infox_generation_live
```

기존 README·scripts/launch.ps1·toggle.cmd·기존 및 검증 outputs는 이번 커밋에서 제외한다. 자격·쿠키·서명 URL·원본 SSE/편집 JSON·실제 영상/작업 ID는 커밋하지 않는다.

## 4단계 추가 기록: 기업 에셋 검사 구현·검증 완료

현재 영상의 실제 배정 참조를 추출해 기업명 검출·메타데이터 미검출·확인 불가·검증된 즐겨찾기 예외를 구분한다. **외부 영상 생성·제작 재시작·배치 변경·저장·브라우저 자동 이동은 하지 않는다.** 새 제작은 3단계의 완료 신호와 준비 검증을 통과한 뒤 검사를 자동 수행한다. 기존 `draft_ready` 실행에는 **기업 에셋 검사**, 이후 실행에는 **배정 에셋 다시 검사** 버튼을 제공한다. `auto_replace=false`도 읽기 전용 검사는 가능하며 교체를 뜻하지 않는다.

### 구현 파일과 상태 계약

| 파일 | 책임 |
| --- | --- |
| `backend/infox/asset_models.py` | 불변 엄격 모델: 정책, 파일/구간 참조, 항목/검출 근거/구조 문제, 검사 기록, 검증된 즐겨찾기 교체 이력 |
| `backend/infox/assets.py` | 네트워크·원본 변경 없는 순수 검사기, ROOT 연결 탐색, 정확한 메타데이터 연결, 별칭·예외 판정 |
| `backend/infox/run_models.py`, `store.py` | 기존 레코드의 기본값 호환, typed `begin_audit`/`record_audit` CAS, 스냅샷·접수 기록 보존 |
| `backend/infox/manager.py` | 시안 준비 뒤 자동 검사, 기존 시안 재검사, 인증/조회 오류, 중단된 검사의 읽기 전용 복구 |
| `backend/app.py` | 기존 가드 안의 `POST /api/infox-runs/{run_id}/audit` |
| `frontend/src/components/InfoXAssetAudit.vue`, `App.vue`, `api.ts`, `types.ts`, `style.css` | 단계·건수·문제 목록·파일/근거 상세·재검사·복원 |
| `tests/test_infox_assets.py`, `test_infox_assets_api.py` | 순수 검사, 엄격 모델, CAS, 보안/수명/재개와 쓰기 없는 연결 검증 |
| `tests/infox_assets_live.py`, `phase4-live-verification.json` | 기존 3단계 실행만 읽는 실제 검증과 민감 값을 제거한 결과 |

- 4단계 진입에는 기존 영상/작업 ID, 두 accepted receipt, 실제 completed, 준비 확인 네 값이 모두 필요하다. `begin_audit(...expected_version=...)`가 `auditing_assets`를 영속화한다.
- 검사 직전에 같은 영상의 `videostate`를 다시 조회하고 확정 대본·장면·음성·자막을 재검증한다. 달라졌으면 `draft_changed`로 보류하며 스냅샷을 덮어쓰지 않는다.
- 완전한 메타데이터 검사는 **`assets_audited`**다. 기업명 검출이 있어도 검사를 수행했다는 뜻이며 교체/저장 완료가 아니다. 검사 불가 항목·구조·인증/조회 오류는 **`needs_attention`**이다.
- `record_audit(...expected_version=...)`는 검사 전용 필드와 단계만 갱신한다. 범용 JSON 갱신·출처 이력 수정 API는 없다. 재시작 시 `auditing_assets`만 같은 영상을 다시 읽어 복구하며 새 프로젝트/제작을 요청하지 않는다.
- `POST .../audit`는 비동기 202다. 본문은 없거나 빈 객체만 허용하고 클라이언트의 즐겨찾기 이력 등 추가 필드를 거절한다. 이후 기존 실행 ID GET으로 `asset_audit`, `asset_counts`, `tracking_active`를 조회한다. 응답 유실/재로드는 GET으로 복원한다.
- 제작 추적의 600초 제한은 제작 단계에만 적용한다. 검사는 그 제한 밖에서 기존 HTTP 읽기 시간/재시도 제한을 사용한다. 진행 작업과 재검사 클릭이 겹쳐도 새 CAS 소유권을 빼앗지 않는다. 멈춘 검사에는 재검사 버튼도 제공한다.

### 검사와 연결 규칙

1. 장면의 `ROOT.child`를 따라 실제 연결된 노드만 방문한다. 부모 누락·잘못된 child·순환/중복 연결은 구조 문제다. 분리된 레이어 사전 항목은 검사하지 않는다.
2. 현재 검증한 템플릿은 **1786**이다. `ImageLayer`/`VideoLayer`에서 `auto_select is True`만 자동 배정으로 검사한다. 명확한 false는 수동/템플릿 배치로 제외하고, 표식 부재·잘못된 값·미지 레이어는 확인 불가다. 실제 관찰한 `TextLayer4`와 미디어 없는 `ShapeLayer` 벡터 장식은 비미디어로 구분한다. 임의의 미지 타입을 비미디어로 추정하지 않는다.
3. 사진 묶음은 `image_files[]` 각 항목을 검사한다. 부모의 제목/태그는 사용하지 않으며 **연결 메타데이터 안의 묶음 부모도 제외**한다. 직접 영상은 부모의 bucket/filename/segment/videoId를 사용한다. VideoLayer에 사진 묶음이 함께 남으면 어느 참조가 실제인지 임의 선택하지 않고 `ambiguous_media`로 보류한다.
4. 같은 장면의 `rerank_result`, 필요 시 `search_metadata`에서 **bucket+filename 정확 일치**만 연결한다. 영상은 segment 및 알려진 원영상 ID도 일치해야 하며, 함께 존재하는 frameIndex/timestamp/프레임 범위가 충돌하면 연결하지 않는다. 영상 ID만 같은 다른 구간, 대본, 검색어, 다른 장면/파일의 후보는 근거가 아니다.
5. 검사 필드는 명세 §8의 title/title_ko/video_title_raw/tags/description/text_body/img_description_ai/frame_visual_caption/frame_stt다. 문자열·목록과 명시적으로 지원한 텍스트 구조만 읽는다. 알 수 없는 구조나 깊이 제한으로 읽지 못한 정보는 `metadata_unreadable`이며, 다른 제목이 중립적이라는 이유로 미검출을 확정하지 않는다. 빈 문자열·빈 목록·null은 비어 있는 선택 필드로 구분한다.
6. 정책의 기본 별칭은 **삼성전자, SK하이닉스, 삼성, SK, 하이닉스, 삼전닉스**다. `AssetPolicy.aliases`로 관리하며 임의 별칭을 자동 추가하지 않는다. 검사본에서만 NFKC·casefold·별칭 문자 사이 공백을 정규화한다. SK 시작/끝의 ASCII 영숫자 경계를 유지해 sketch/SKY/단어 내부를 오탐하지 않는다. 원본 객체는 수정하지 않는다.
7. 유효한 파일 참조와 검사할 텍스트가 없으면 unknown이다. 미검출은 검사한 메타데이터의 결과이며 시각적 기업명·로고 부재 보증이 아니다. 검사 예산은 그래프·메타데이터·묶음/본문 항목을 포함해 기본 10,000이며 초과를 완료로 숨기지 않는다.

각 항목에는 장면 index/ID, 레이어 ID, 묶음 index, 안전한 파일/구간 식별자, 판정/사유, 검출 별칭·필드·근거 경로를 저장한다. 원문 설명·제목·서명 URL은 기록하지 않는다. 파일 참조에 URL/쿼리/제어문자가 섞이면 식별 불가로 남긴다.

### 즐겨찾기 예외의 신뢰 경계

`FavoriteReplacement`는 이후 **실제 저장 후 검증한 출처 이력**을 위한 모델이다. 현재 4단계는 이를 새로 만들어 저장하는 경로나 API를 제공하지 않는다. 원격 `is_favorite=true`만으로는 면제하지 않는다.

예외는 run/video/장면 index와 ID/레이어/묶음 index/파일·선택 구간 참조 전체가 현재 배치와 같고, favorites_only·저장 검증이 참인 내부 기록에만 적용한다. 출처는 video/uservideo, 검색어는 반도체다. 일치하면 메타데이터 유무/기업명으로 재검열하지 않고 favorite_exempt를 반환한다. 파일·구간·배치가 바뀌면 예외도 사라진다. 5단계 후보 출처와 6단계 실제 저장 검증이 이 기록을 만드는 책임을 이어받는다.

### 실제 확인과 남은 검증

**2026-11-02 13:08:58 한국 시간**에 기존 3단계 검증 실행을 다시 사용했다. 2개 장면의 자동 배정 ImageLayer 3개에서 사진 **9개 배치**를 추출했고, 각 파일을 동일 장면의 메타데이터와 연결했다. 결과는 **검출 0 / 메타데이터 미검출 9 / 확인 불가 0 / 즐겨찾기 예외 0**이며 `assets_audited`다. 같은 파일이 여러 배치에 있어도 배치별 항목으로 보존한다.

검사 전후 snapshot·영상/작업 ID·두 receipt와 서버 updated_at이 같았다. 허용 요청을 인증과 해당 영상 `videostate`로 제한한 검증 클라이언트를 사용했고 **외부 영상 쓰기 0회**였다. 실검증 자료는 원문/파일/장면/레이어/실행 ID를 제거한 `phase4-live-verification.json`이다. 실제 ID 연결은 기존 Git 제외 DB·phase3 marker에만 남긴다.

이 실제 실행에는 직접 자동 배정 영상·기업명 검출·검증된 즐겨찾기 교체 이력이 없었다. 해당 경로의 검증은 통제된 데이터 근거이며 실제 서비스의 모든 스키마를 확인했다는 뜻이 아니다. 실제 장면의 미디어 없는 ShapeLayer 장식도 확인해 타입을 명시적으로 지원했다. OCR·프레임/로고 검사·실제 재생은 이번 범위에 없다.

### 5단계 작업자의 진입점

- `stage == assets_audited`, `asset_audit.state == completed`인 실행의 `items`에서 detected를 대체 검토 대상으로 사용한다. unknown/구조 문제가 있으면 needs_attention을 유지하고 전체 교체 성공으로 넘어가지 않는다. 미검출은 시각적 검수 통과가 아니다.
- 후보를 고르기 전에 동일 video_id의 최신 상태·서버 버전을 읽고 검사 당시 source_version, 배치/파일/구간과 비교한다. 서버에서 바뀌었으면 이 검사 결과로 과거 파일을 덮어쓰지 말고 재검사한다.
- 다음은 §9의 favorites_only=true/search=반도체 조회, 구간 후보 선택과 출처, 로컬 교체 계획/보존 검증이다. 즐겨찾기 후보에 기업명 제한을 다시 적용하지 않는다.
- 현재 저장 모델의 `favorite_replacements`는 비어 있는 것이 정상이다. 실제로 저장되지 않은 계획/선택만으로 saved_verified 이력을 만들지 않는다. 6단계의 서버 재조회까지 확인한 뒤 별도 CAS 메서드로 기록한다.
- stage를 `selecting_favorites`로 바꾸는 typed 계획 저장, 정확한 구간의 통합 참조, 혼합 묶음 보존 검증은 5단계에서 추가한다. 외부 배치 저장과 자동 이동은 각각 6·7단계다. 기존 begin_request/record_outcome 쓰기 경계·불명확한 접수 재전송 금지를 계속 유지한다.

### 4단계 검증·리뷰·전달

- 4단계 전용 백엔드: **30 passed**. 순수 검사 24개와 저장/API/재개/수명 6개다.
- 전체 백엔드: **517 passed**, 기존 의존성 deprecation 경고 3개. 이전 단계의 PSModulePath 설정으로 런처 테스트를 포함했다.
- 프런트엔드: **62 passed**, vue-tsc 타입 검사와 Vite 프로덕션 빌드 성공.
- 실제 로컬 FastAPI + Edge Chromium: 자동 검사, 검출/확인 불가가 섞인 재검사 목록, 스냅샷 불변, 재로드 후 검사 결과 복원, 수동 링크, 390/1440px 레이아웃 성공. 통제된 InfoX 클라이언트를 사용했으며 외부 브라우저 요청과 추가 영상 쓰기는 0회다.
- 독립 리뷰: Critical 0 / Important 5. 미지 레이어 무시, VideoLayer의 잔여 사진 참조, 메타데이터 묶음 부모의 오래된 제목, 읽지 못한 메타데이터의 미검출 오판, 제작 시간 제한의 검사 취소 문제를 모두 실패 테스트로 재현 후 수정했다. 검사 예산·즐겨찾기 예외·자동 검사와 수동 클릭 경합도 회귀로 고정했다.

```powershell
.venv/Scripts/python.exe -m pytest tests/test_infox_assets.py tests/test_infox_assets_api.py -q
.venv/Scripts/python.exe -m pytest -q
.tools/node/node.exe frontend/node_modules/vitest/vitest.mjs run --root frontend
.tools/node/node.exe frontend/node_modules/vue-tsc/bin/vue-tsc.js -b frontend/tsconfig.json
.tools/node/node.exe frontend/node_modules/vite/bin/vite.js build frontend
.venv/Scripts/python.exe -m tests.infox_runs_browser_smoke
# 기존 실행만 읽음. marker가 없으면 실패하며 새 영상을 만들지 않는다.
.venv/Scripts/python.exe -m tests.infox_assets_live
```

기존 README·scripts/launch.ps1·toggle.cmd·outputs와 원본 자격/DB는 보존하고 이번 커밋에서 제외한다. 3단계 실제 이벤트 근거도 그대로 보존한다.

## 5단계 추가 기록: 즐겨찾기 선택·로컬 교체 계획 완료

즐겨찾기 구간을 기술 조건으로 선택해 실행에 고정하고, 실제 서버 저장 전에 로컬 JSON 변환과 전체 데이터 보존을 검증한다. **영상 생성·제작 재시작·외부 배치 저장·자동 편집기 이동은 수행하지 않았다.** `replacement_planned`는 서버에 적용됐다는 뜻이 아니다. 화면에도 서버 미저장을 표시한다.

### 구현과 상태/API

| 파일 | 책임 |
| --- | --- |
| `backend/infox/plan_models.py` | URL 없는 후보·선택 정책·조회 출처·불변 배치별 선택·보존 확인·계획 모델 |
| `backend/infox/favorites.py` | 전체 페이지 검증, 구간별 후보 보존, 사용 가능한 첫 후보 선택, 고정 후보 재확인 |
| `backend/infox/client.py` | 기존 favorites-only 조회와 자격을 분리한 제한된 구간 파일 읽기 |
| `backend/infox/replacement.py` | 순수 로컬 변환, 사진/영상/혼합 묶음과 슬롯 시간, 독립 전체 보존 검증, URL 제외 지문 |
| `backend/infox/planner.py` | 최신 대본/검사/버전 확인 → 선택 CAS 고정 → 변환/검증 → 계획 CAS |
| `backend/infox/store.py`, `manager.py`, `run_models.py`, `assets.py` | 새 단계·기본값 호환, 자동 계획/재시작/재검사와 MediaLayer 개별 참조 검사 |
| `backend/app.py`, `frontend/src/components/InfoXReplacementPlan.vue`, `App.vue`, `api.ts`, `types.ts` | 보호된 계획 API, 단계별 GET, 고정 구간/슬롯/검증 결과·미저장 표시 |
| `tests/test_infox_favorites.py`, `test_infox_replacement.py`, `test_infox_plan_api.py` | 페이지·자격 경계·혼합 변환·시간·CAS·중단/고정 선택 회귀 |
| `tests/infox_plan_live.py`, `phase5-live-verification.json` | 기존 실행의 읽기 전용 확인, 실제 후보 접근, 실제 구조 복사본의 통제된 로컬 변환 |

- 새 제작/재검사에서 `auto_replace=true`이고 에셋 검사가 완전하면 5단계를 자동으로 잇는다. 같은 영상의 최신 `videostate`를 읽고 대본·음성·자막, 검사 당시 버전과 항목 전체를 재검증한다. 달라졌으면 source_changed/draft_changed로 보류한다.
- 기존 완전한 검사에는 **즐겨찾기 교체 계획 만들기**, 이후 **고정한 교체 계획 다시 확인**을 제공한다. `POST /api/infox-runs/{run_id}/replacement-plan`은 없거나 빈 본문만 허용하며 202다. 클라이언트가 후보 ID나 JSON 변환/출처를 주입할 수 없다.
- 진행 중은 `selecting_favorites`, 검증된 로컬 계획은 **`replacement_planned`/plan.state=ready**, 실패/불명확은 needs_attention이다. 검출 대상 없음은 plan.state=not_needed이며 assets_audited를 유지한다. 검출 대상이 있지만 auto_replace=false면 disabled로 선택을 생략한다.
- 계획도 기존 실행 ID GET으로 복원한다. 응답 유실/새로고침은 POST 재실행 없이 조회한다. 재시작 시 selecting_favorites만 읽기 전용으로 재개하며, 저장한 후보가 있으면 그 후보만 확인한다.
- DB에 source_version·source/result SHA-256 지문·검색 조건/페이지/시각·배치별 기존 참조/선택 후보·슬롯 프레임·검증값을 보관한다. **전체 원격 JSON/제안 JSON/서명 URL을 DB나 일반 로그에 저장하지 않는다.** 새 필드는 이전 레코드에 기본값으로 추가되며 스냅샷·외부 receipt는 그대로다.

### 후보·페이지·파일 접근 계약

1. 일반 영상 favorites-only 검색, search=반도체, relevance 순서, limit=50을 사용한다. 날짜 필터는 **의도적으로 생략**한다. 캡처의 2025-11-02를 고정하지 않으며 날짜 필터의 서버 의미를 새로 검증한 것은 아니다.
2. total/skip/limit·누적 항목 수·빈 중간 페이지·변하는 total·중복 구간 ID를 검사하며 끝까지 읽는다. 최대 20페이지/1,000개다. 중간 오류/한계는 명시적으로 보류하고 첫 페이지만 완전한 결과로 간주하지 않는다.
3. 후보는 구간 ID·원영상 ID·bucket·segment·thumbnail·frameIndex·timestamp·duration·해상도·MP4 형식을 가진 즐겨찾기 항목이다. 같은 원영상의 다른 구간을 합치지 않는다. 문자열 제목에 기업명이 있거나 반도체라는 단어가 없어도 탈락시키지 않는다. 추가 시각 검수를 요구하지 않는다.
4. 현재 정책은 **사용 가능한 첫 구간 하나를 모든 대상에 재사용**한다. 무작위 선택/자동 순환은 없다. 30fps에서 한 프레임도 없는 후보는 사용 불가로 분리하고 다음 후보를 확인한다. 파일 확인은 최대 20후보다.
5. 구간 접근은 실제 확인한 `https://kr.object.iwinv.kr/{bucket}/{segment}`의 정확한 파일 경로만 허용한다. 새 전용 HTTP 클라이언트로 Range bytes=0-31을 읽어 HTTP 상태·콘텐츠 종류·MP4 ftyp 표식을 확인한다. API Bearer·장치 토큰·쿠키를 전달하지 않고 리다이렉트/다른 호스트/사용자 정보/다른 파일 경로를 거절한다. 실제 전체 재생/코덱 검사는 6단계다.
6. 후보를 고른 뒤 **freeze_choices(expected_version)**를 커밋하고 나서 변환한다. 선택이 고정된 뒤 접근 불가/목록 변경/변환 실패가 나도 다른 구간으로 자동 변경하지 않는다. 새 조회에서 동일 후보의 안정 참조와 메타데이터를 확인하고 서명 URL만 메모리에 갱신한다. 선택 전 중단으로 아직 고정 기록이 없으면 다시 전체 후보를 조사할 수 있다.
7. 사용자 영상 uservideo의 비어 있지 않은 응답과 교체 스키마는 미검증이므로 현재 선택 소스에 합치지 않는다. 지원하지 않는 source는 unsupported_uservideo다. 일반 검색/새 즐겨찾기 등록으로 우회하지 않는다.

### 로컬 변환과 전체 보존

현재 편집기 JS의 module 12764(hY/Qg)에서 ImageLayer/MediaLayer 프레임 배분을 확인했다. 근거는 `20700-31e582d53acc3cba.js`, `20478-9fbb80a4f22bb5aa.js` 및 `page-a6743b35f70d7639.js`의 미디어 변환 코드와 사용자 `replace3`의 MediaLayer 저장 구조다. 소스는 검증 중 읽어 Git 제외 `.runtime/infox-source`에만 보관했다. HTML/쿠키/계정 값은 산출물에 복사하지 않았다.

- 현재 변환 계약은 **30fps와 명시적 scene.durationInFrames**다. 프레임이 없으면 duration만 반올림해 추정하지 않는다. 편집기의 추가 패딩/오프셋 계약까지 검증하지 않은 장면은 unsupported_timing으로 보류한다.
- 단일 사진·영상은 VideoLayer로 바꾸고 image_files를 비운다. 여러 항목의 묶음은 MediaLayer와 image_files를 유지하며 **문제 항목만 같은 위치에서 영상으로 바꾼다.** 정상 사진/영상의 필드는 보존하고 원래 암묵적 슬롯 프레임만 durationInFrames로 명시한다. 항목 개수·순서·시작 경계를 유지한다. ceil 배분으로 마지막 슬롯이 장면 끝을 넘는 기존 경계도 그대로 보존한다.
- MediaLayer의 `media_type="video"` 항목은 영상, 그 외 지원한 사진 항목은 사진으로 검사한다. 이 구조도 4단계 검사기에 연결했으며, VideoLayer에 모순되는 사진 배열이 남는 경우는 계속 확인 불가다.
- 새 영상 미디어는 기존 객체에 몇 필드만 덧씌우지 않고 동일 후보의 id/videoId/bucket/segment/filename/frameIndex/timestamp/thumbnail/해상도/제목/길이로 구성한다. filename=segment, filename_resized=thumbnail이며 기존 사진/영상의 참조·설명·미리보기는 제거한다. 현재 조회의 서명 URL은 같은 후보·파일 경로일 때만 메모리 제안본에 넣는다.
- startFrameVideo=0, endFrameVideo=min(구간 파일 프레임, 원래 슬롯 프레임)이다. 원영상 timestamp를 구간 파일 재생 시작에 다시 더하지 않는다. durationInFrames는 원래 슬롯 폭, muted=true/volume=0/playbackRate=1이다. 긴 구간은 슬롯까지 자르고 짧은 구간은 슬롯 폭을 유지하는 반복 계획이며 **실제 반복·공백·오디오 재생 확인은 6단계**다.
- 레이어 ID·ROOT 연결·순서, props의 위치/크기/회전/효과, 대상 미디어/개별 항목의 크롭과 배치 속성을 보존한다. 지원하지 않는 대상 미디어 필드를 임의 삭제하지 않고 보류한다.
- 독립 검증기는 변환기를 다시 호출해 비교하지 않는다. 전체 데이터에서 허용한 미디어/종류만 마스킹한 비교와 정상 항목·개별 교체 항목 레이아웃·프레임 배분·동일 후보의 모든 참조·이전 활성 파일 제거를 검사한다. 부모/자식에 오래된 filename/external_url 등이 추가되면 통과하지 않는다. 대본·음성·자막·다른 레이어·contextData 등 무관한 변경도 실패다.

### 실제 검증과 적용 범위

**2026-11-02 13:43:22 한국 시간**에 기존 3단계 영상·작업을 그대로 사용했다. 이 영상은 **기업명 검출 0, auto_replace=false**이므로 실제 실행은 **not_needed, 선택 0개**가 맞다. 실제 배치를 교체 대상으로 꾸미거나 새 영상을 생성하지 않았다.

별도의 읽기 전용 검증에서는 실제 반도체 즐겨찾기 **12개/1페이지**의 일반 영상 스키마와 선택 구간 MP4 접근을 확인했다. 다시 조회해 같은 고정 후보를 재확인했다. 실제 편집 데이터의 **로컬 복사본에만** 검증용 메타데이터 hit 하나를 추가하고, 해당 실제 후보로 혼합 묶음 변환을 수행했다. 여섯 보존 검사(scope/정상 에셋/layout/timing/참조 일관성/이전 활성 참조 제거)가 모두 true였다. 이 복사본/제안본/가상 hit는 서버나 실제 실행의 asset_audit에 저장하지 않았다.

원래 snapshot·영상/작업 ID·두 receipt·검증된 즐겨찾기 이력과 서버 updated_at은 그대로이며 **외부 영상 쓰기 0회**였다. 실제 적용·서버 저장·새로고침 후 적용 유지·편집기 재생은 입증하지 않았다. 실제 ID·파일명·계정·서명 URL은 `phase5-live-verification.json`에서 제거했다. uservideo 비어 있지 않은 응답, 페이지 2 이후, 실패/혼합/짧은 구간의 경계는 통제 테스트 근거이며 이번 실계정의 관찰과 구분한다.

### 6단계 작업자의 진입점

1. `replacement_plan.state=ready`, stage=replacement_planned의 실행을 사용한다. not_needed는 별도 무변경 검증 경로다. needs_attention/disabled/선택 중인 기록을 저장 완료로 처리하지 않는다.
2. 같은 video_id의 최신 상태를 읽고 실제 source_version·`state_fingerprint()`·대본/준비·배치/선택 참조를 확인한다. 버전/지문이 달라졌으면 예전 전체 JSON을 덮어쓰지 않는다. 저장 요청의 updatedAt은 **이번 최신 원격 응답 값**을 사용한다.
3. 전체 favorites-only 목록에서 고정한 후보를 `choose_favorite(...frozen=...)`로 다시 확인하고 URL을 메모리에 갱신한다. `apply_replacements(...ephemeral=...)`와 독립 보존 검증으로 전체 제안본을 재구성한다. URL 없는 result_fingerprint는 로컬 재구성의 동일성 근거이며, 저장 뒤 바뀐 서버 updated_at까지 그대로 같은 지문일 것이라고 가정하지 않는다.
4. `update_video_context`의 video/contextData 매핑을 실제 원격 데이터에서 검증한다. **아직 저장용 클라이언트/receipt operation은 추가하지 않았다.** 저장 전에 typed begin_request intent를 커밋하고 expected_version 결과 저장, 응답 유실/충돌 후 서버 재조회 경계를 추가한다. start/recover에도 saving/verifying 단계를 명시적으로 라우팅해 생성 단계로 돌아가지 않게 한다.
5. 저장 뒤 실제 사용 참조와 정상 에셋/대본/TTS/자막/배치/장면 길이를 재조회한다. 긴/짧은 구간과 혼합 묶음은 실제 편집기에서 재생해 검증한다. 성공 뒤에만 `FavoriteReplacement` saved_verified 이력을 새 배치·파일·구간과 묶어 CAS 저장한다. 계획 단계는 favorite_replacements를 추가하지 않았다.
6. 현재 고정 선택을 자동 초기화하거나 다른 구간으로 대체하는 UI는 없다. 재검사로 원본이 달라졌거나 고정 후보가 사라지면 보류한다. 명시적인 새 선택 의도/재계획이 필요하다면 별도 CAS 계약과 사용자 동작으로 추가한다. 자동 브라우저 이동은 7단계다.

### 5단계 검증·리뷰·전달

- 5단계 전용 백엔드: **34 passed**(즐겨찾기/접근 13, 변환/보존 17, 계획 API/고정/복구 4).
- 전체 백엔드 **551 passed**, 기존 의존성 경고 3개. 프런트엔드 **64 passed**, vue-tsc·Vite 빌드 성공.
- 실제 로컬 API + Edge Chromium: 고정 후보 계획·혼합 로컬 변환·재확인 완료 후 새로고침/선택 유지·미저장 표시·390/1440px 레이아웃 성공. 외부 브라우저 요청/추가 생성/외부 배치 저장은 0회다.
- 독립 리뷰: Critical 0 / Important 3. 개별 교체 항목의 크롭/잔여 참조 검증, 명시적 프레임 없는 장면의 잘못된 시간 추정, subframe 후보가 다음 사용 가능 후보를 막는 문제를 실패 테스트 후 수정했다.

```powershell
.venv/Scripts/python.exe -m pytest tests/test_infox_favorites.py tests/test_infox_replacement.py tests/test_infox_plan_api.py -q
.venv/Scripts/python.exe -m pytest -q
.tools/node/node.exe frontend/node_modules/vitest/vitest.mjs run --root frontend
.tools/node/node.exe frontend/node_modules/vue-tsc/bin/vue-tsc.js -b frontend/tsconfig.json
.tools/node/node.exe frontend/node_modules/vite/bin/vite.js build frontend
.venv/Scripts/python.exe -m tests.infox_runs_browser_smoke
# 기존 실행과 통제된 로컬 복사본만 사용. 영상 쓰기 금지.
.venv/Scripts/python.exe -m tests.infox_plan_live
```

기존 README·scripts/launch.ps1·toggle.cmd·outputs 변경은 보존하고 이번 커밋에서 제외한다. 원본 HAR/편집기 코드/원격 JSON/자격/DB/서명 URL도 추가하지 않는다.
