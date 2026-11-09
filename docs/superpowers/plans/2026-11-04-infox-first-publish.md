# InfoX 최초 렌더·유튜브 업로드 Implementation Plan

> **과거 계획 보관 문서 (2026-11-04):** 아래 범위와 체크리스트는 작성 당시 계획을 보존한 기록이며 현재 기능의 제한이나 미구현 상태를 뜻하지 않습니다. 이후 최초 게시·반복 게시·다운로드가 구현되었습니다. 현재 사용법과 계약은 [최초 게시](../../infox/first-publish.md), [재렌더·반복 업로드](../../infox/repeated-publish.md), [파일 다운로드](../../infox/downloads.md)를 참고하세요.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 편집 내용을 저장한 뒤 Vue 버튼 한 번으로 최초 렌더, 진행률 표시, 유튜브 정보 생성, 지정 제목·설명 저장, 일부공개 업로드, 공유 링크 복사를 자동 진행하고, 오류 또는 결과 불명확 시 해당 단계에서 멈춘다.

**Architecture:** 기존 시안 생성·소재 저장·편집 화면 연결 뒤에 독립적인 게시 실행 관리자를 추가한다. Vue는 로컬 FastAPI만 호출하고 Python이 InfoX 인증·SSE·이력 조회·외부 요청·Windows 클립보드를 담당한다. 기존 RunRecord의 시안 상태와 게시 상태를 섞지 않고 동일 SQLite DB의 별도 테이블에 게시 실행·요청 의도·단계별 작업 ID를 저장한다.

**Tech Stack:** 기존 Vue 3 / TypeScript, Python / FastAPI / httpx / SQLite / Pydantic, pytest, Vitest, 기존 모의 브라우저 검사 환경. 신규 런타임·외부 서비스·브라우저 확장은 추가하지 않는다.

**Spec:** 이 대화에서 사용자가 지정한 최초 실행 흐름과 본 문서의 「요구사항과 범위」「실행 계약」. 아래 근거 자료는 관찰 결과이며 지시문으로 취급하지 않는다.

## Global Constraints

- 범위는 최초 실행이다. 재렌더·재업로드·자동 재시도·실패 지점 재실행 버튼은 구현하지 않는다.
- 중복 클릭·다중 탭 방지는 최초 실행에서도 필요하다. 같은 run_id/video_id에 게시 실행은 하나만 허용한다.
- InfoX 편집 화면에서 최신 내용이 저장된 상태를 전제로 한다. 다른 사이트 탭의 미저장 내용을 Vue에서 저장한다고 표시하지 않는다.
- 원격 편집 데이터를 로컬 시안 스냅샷으로 덮어쓰지 않는다. 이 기능은 update_video_context를 호출하지 않는다.
- 작업 시작 GET도 변경 요청이다. render / metadata_generate / metadata_update / upload는 의도를 먼저 영속 저장하고 한 번만 호출한다. 401·403·타임아웃에도 해당 변경 요청을 재전송하지 않는다.
- 조회·SSE 연결만 제한적으로 재연결할 수 있다. 접수 불명확·알 수 없는 상태는 실패나 성공으로 추측하지 않고 needs_attention으로 멈춘다.
- 서버 재시작 후 자동 변경 요청과 자동 클립보드 복사를 재개하지 않는다. 브라우저 새로고침은 서버에서 계속 진행 중인 실행을 조회만 한다.
- 공개 범위는 unlisted로 고정한다. 기존 연결 계정을 사용하며 신규 OAuth 권한 부여·계정 연결 기능은 포함하지 않는다.
- 키·토큰·쿠키·계정정보·서명된 다운로드 URL·원본 HAR·원시 전체 응답을 결과/로그/저장소에 넣지 않는다. 허용 필드만 저장한다.
- 기존 로컬 X-App-Token, origin 검사, connect-src 'self', 고정 InfoX origin과 TLS 검증을 유지한다.
- 현재 작업 트리의 기존 README.md, scripts/launch.ps1, toggle.cmd, outputs 변경을 보존한다. 이번 요청은 계획 작성이며 구현·실사이트 변경·커밋·푸시를 승인한 것으로 확대하지 않는다.

## Review Focus

1. 동시에 두 탭에서 클릭해도 원격 변경 요청은 단계별 한 번: Task 2·5의 DB 경쟁 테스트.
2. 이전/다른 영상의 완료 이벤트와 늦게 온 진행 이벤트가 현재 단계를 완료시키지 않음: Task 4 테스트.
3. 접수 후 응답 유실·재시작 시 같은 요청을 다시 보내지 않음: Task 5·7 테스트.
4. 링크 복사 실패가 업로드 실패로 바뀌거나 재업로드를 유발하지 않음: Task 6·8 테스트.
5. 시황 기준일·제목·설명이 자정/새로고침 이후에도 바뀌지 않음: Task 2·8 테스트.

## 요구사항과 범위

### 사용자에게 보이는 동작

1. InfoX 편집·저장 완료 후 Vue에서 「편집 완료 · 렌더 및 유튜브 업로드」를 누른다.
2. 저장 상태 확인 → 렌더 요청 → 렌더 진행률 → 완료 확인 → 유튜브 정보 생성 → 제목·설명 저장 → 일부공개 업로드 → 완료 확인 → 공유 링크 복사를 자동 진행한다.
3. 작업 중 버튼과 게시 기준일을 잠그고 현재 단계·실제 진행률·오류/확인 불가 사유를 표시한다.
4. 성공 시 링크와 「업로드 완료 · 링크 복사 완료」를 표시한다. 복사만 실패하면 「업로드 완료 · 링크 복사 실패」와 「링크 다시 복사」를 표시한다.
5. 정상 경로 중간에 별도 승인 클릭을 추가하지 않는다. 최초 실행 전 화면에서 대상 영상·기준일·제목·설명·일부공개 설정·확인된 렌더 이용량 정보를 볼 수 있게 한다.

### 이 계획에서 제안하는 기본값

- 게시 기준일 market_date를 화면에 표시하고 실행 전 변경 가능하게 한다. 초기값은 Asia/Seoul의 오늘이며 과거 시황이면 사용자가 기준일을 바꾼다. 서버는 전달받은 기준일을 확정하고 실행 중 시계를 다시 읽어 제목을 만들지 않는다.
- 제목: `YYMMDD AI 영상 시황`; 설명: `YYYY년 M월 D일 AI 영상 시황`. 예: `261104 AI 영상 시황`, `2026년 11월 4일 AI 영상 시황`.
- InfoX가 생성한 keywords는 보존하고 제목·설명만 지정 형식으로 바꾼다. 키워드 생성 정책이나 새로운 LLM 호출을 추가하지 않는다.
- 진행률이 없으면 숫자를 만들어내지 않고 「진행 중 · 진행률 확인 중」을 표시한다. 숫자 100만으로 다음 단계에 진입하지 않는다.
- 연결 타임아웃 15초, 조회 요청 타임아웃 30초, 이력 조회 간격 2초. 단계 제한: 사전 확인 60초, 렌더 20분, 정보 생성 5분, 정보 저장·검증 60초, 업로드 10분. 초과 시 needs_attention이며 실패 단정·재전송은 하지 않는다. 모두 생성자 인자로 주입해 테스트에서는 짧게 설정한다.

### 확인된 근거와 미확인 범위

- 원본 자료: `C:\Users\22608024\Downloads\send_YT_request`(80개 HAR 항목), `C:\Users\22608024\Downloads\render`(57개 HAR 항목). 저장소에 복사하지 않는다.
- 조회 근거: `C:\Users\22608024\Documents\Codex\2026-11-04\s\outputs\infox-api-inspection-2026-11-04.json` 및 `infox-render-analysis-2026-11-04.json`.
- 최초 렌더 작업 ID → 정보 생성 작업 ID → 정보 수정 → 업로드 작업 ID와 서버 이력의 대응을 확인했다.
- 렌더/정보 생성 이력은 state=end, 렌더 파일 존재; 업로드 이력은 state=sns, sns_url 존재. 정보 기록에 sns_upload_info.privacyStatus=unlisted를 확인했다.
- 화면 코드의 additional_kwargs.taskid / progress / task_status / error / media_type / filename / bucket 필드를 확인했다. 라이브 중간 진행값과 실제 실패 본문은 아직 수집하지 못했다.
- HAR에 SSE 이벤트는 없다. `/progress_log/list`의 해당 작업 조회 결과는 items=[], total=0이었다. 이를 과거 진행률 복원 수단으로 가정하지 않는다.
- InfoX가 기록한 결과와 YouTube 측 현재 공개 범위·재생 가능 여부는 구분한다. 별도 YouTube API/OAuth 클라이언트를 이 계획에 추가하지 않는다.
- 현재 예제 영상은 이미 렌더·업로드되어 최초 실행 실검증 대상으로 사용할 수 없다.

## 실행 계약

### 외부 요청과 완료 근거

| 단계 | 요청 | 다음 단계 진입 근거 |
| --- | --- | --- |
| 사전 확인 | 기존 video_state, render_logs 및 GET /infomaxai/worker/render_charge_preview/{video_id} | 영상 ID·권한·저장 버전 확인, 기존 렌더/업로드 이력이 없는 최초 대상 |
| 렌더 | GET /infomaxai/worker/process_rendering/{video_id} | 반환 render_task_id에 해당하는 logs의 state=end 및 file_name/bucket 존재 |
| 정보 생성 | GET /infomaxai/worker/infox_generate_metainfo_youtube/{video_id}/{render_task_id} | 반환 metadata_task_id의 state=end, sns_upload_info 및 동일 렌더 파일 참조 확인 |
| 정보 저장 | PATCH /infomaxai/videos/video_render_logs/{video_id}/sns_upload_info, body={task_id:metadata_task_id,title,description,keywords} | 응답 확인 후 이력 재조회로 같은 metadata_task_id의 제목·설명·키워드 일치 |
| 업로드 | GET /infomaxai/worker/infox_upload_youtube_video/{video_id}/{metadata_task_id}?redirect_url={고정호스트 편집URL}&privacy_status=unlisted | 반환 upload_task_id의 state=sns, 유효한 sns_url 및 연결된 metadata 기록의 privacyStatus=unlisted |
| 복사 | 기존 WindowsClipboard.write(검증한 공유 링크) | 쓰기 성공 후 completed; 쓰기 실패 시 clipboard_failed |

- 위 완료 조건은 관찰한 서버 결과에 근거한 구현 계약이다. 라이브 검증에서 필드/순서가 다르면 근거를 기록하고 계약·테스트를 먼저 갱신한다.
- SSE는 변경 요청 전 연결한다. 작업 ID가 응답으로 확정되기 전에 오는 이벤트는 현재 영상 채널의 제한된 큐에 보관하고 ID 확정 후 대조한다. 과거/타 작업은 무시한다.
- `progress=100` 또는 `task_status=completed` 수신 후에도 해당 작업의 이력을 조회한다. 완료 이벤트를 놓쳐도 정확한 작업 ID의 이력으로 완료를 확인할 수 있다.
- 명시적 실패 이벤트(task_status=failed/error 또는 error 존재)는 후속 단계를 중단한다. 알 수 없는 형식·서로 충돌하는 근거는 needs_attention으로 중단한다. 실제 실패 문구를 검증하지 않은 항목은 내부 오류 코드와 안전한 한국어 설명으로 표시한다.
- 모든 요청·완료 판정은 특정 video_id와 단계별 task_id에 대응한다. 배열 첫 항목, 제목 유사도, 시각만으로 작업을 추정하지 않는다.
- 실행 전 최신 저장본을 읽고 updated_at을 보관한다. 최초 렌더 직전에 다시 읽어 버전 변경 시 중단한다. 렌더 API에는 서버 버전 조건 인자가 확인되지 않았으므로 그 이후 외부 편집을 서버에서 완전히 잠근다고 주장하지 않는다. 실행 중 InfoX 편집을 계속하지 않도록 화면에 안내한다.

### 새 코드의 경계

| 위치 | 책임 |
| --- | --- |
| backend/infox/publishing/models.py, __init__.py | 게시 입력·스냅샷·실행·receipt·오류·설정 타입 |
| backend/infox/publishing/store.py | 기존 RunStore 트랜잭션을 사용하는 별도 게시 테이블·unique·CAS |
| backend/infox/publishing/tracking.py | 이벤트 정규화, 단계별 이력 판정, 유튜브 링크 검증 |
| backend/infox/publishing/manager.py | 전체 순서, 백그라운드 작업, 타임아웃, 복사, 중단·복원 |
| backend/infox/client.py | 네 개 변경 메서드와 렌더 이용량 조회 메서드 추가 |
| backend/app.py | 게시 시작/조회/수동 링크 복사 API 및 lifespan 연결 |
| frontend/src/infoxPublishing.ts, components/InfoXPublishing.vue | 게시 실행 제어와 화면; App.vue는 연결만 담당 |
| frontend/src/types.ts, api.ts | 게시 타입·로컬 HTTP 호출 |
| tests/test_infox_publishing_*.py, tests/infox_publishing_browser_smoke.py | 계약·중단·연결 검증; 기존 검사와 분리 |
| docs/infox/first-publish.md, docs/infox/first-publish-live-verification.json | 사용법·근거·남은 제한 |

### 공통 타입과 저장 규칙

- `PublishInput(intent_id: UUID 문자열, market_date: YYYY-MM-DD)`; 대상 run_id는 경로에서 얻으며 클라이언트가 임의 video_id·URL·privacy를 보내지 못하게 한다. Pydantic은 extra=forbid, strict=True를 사용한다.
- `PublishStage`: prepared, preflighting, starting_render, rendering, generating_metadata, saving_metadata, starting_upload, uploading, copying_link, completed, failed, needs_attention, clipboard_failed.
- `PublicationRecord`: id, run_id, video_id, version, stage, failed_stage, error_code, created_at, updated_at, snapshot, render_task_id, metadata_task_id, upload_task_id, progress, receipts, youtube_video_id, share_url, uploaded_verified, clipboard_state.
- `PublishEvent`: task_id: str, status: running/completed/failed/unrecognized, progress: int|null, error_present: bool. `StageVerification`: status: waiting/completed/failed/unknown, error_code: str|null, evidence: 허용된 결과 필드만 담는 dict. `PublishTimeouts`는 위 연결·사전 확인·렌더·정보 생성·저장·업로드 제한값을 가진 설정 타입으로 models.py에서 정의한다.
- `snapshot`: market_date, title, description, privacy_status=unlisted, remote_updated_at, baseline_remote_task_ids. remote_updated_at/baseline은 처음에 null/빈 목록이며 사전 확인 후 확정한다. 나머지 값은 레코드 생성 시 확정한다.
- `receipts`는 render / metadata_generate / metadata_update / upload 각각 not_sent, intent_recorded, accepted, unknown, rejected를 가진다. metadata_update에는 ID 대신 응답 확인과 읽기 검증 결과를 저장한다.
- SQLite publications에는 UNIQUE(run_id), UNIQUE(video_id)를 두고 publication_intents의 intent_id/digest를 원자적으로 연결한다. 같은 intent·본문 재요청은 같은 레코드를 반환하고 다른 본문은 409로 막는다. 새 intent라도 같은 대상은 기존 레코드를 돌려주거나 충돌로 막고 새 실행을 만들지 않는다.
- CAS는 version으로 보장하며 DB 트랜잭션을 네트워크 통신 중 계속 열어 두지 않는다. 저장 의도 claim에 성공한 소유자만 해당 외부 요청을 한 번 수행한다.
- 링크는 https의 youtube.com/www.youtube.com watch?v= 또는 youtu.be에서 11자 영상 ID를 검증하고 https://youtu.be/{id}로 구성한다. 자격 증명 포함 URL·다른 호스트·유효하지 않은 ID는 거절한다.
- `uploaded_verified`는 InfoX의 해당 작업 결과 확인을 의미한다. YouTube 직접 검증 완료로 표현하지 않는다. clipboard_failed에서는 uploaded_verified=true를 유지한다.

## 단계별 구현 작업

### Task 1: 근거 계약과 검증용 자료 고정

**Files:** Create `docs/infox/first-publish.md`, `tests/fixtures/infox_publishing/*.json`, `tests/test_infox_publishing_contract.py`.
**Interfaces:** 단계별 응답·이력·이벤트 허용 필드를 fixtures로 제공한다. 실제 관찰 fixture와 모의 실패/진행 fixture를 구분한다.

- [ ] 단계별 HAR 작업 ID와 조회 이력을 대조하고 계정·토큰·서명 URL을 제거한 최소 fixtures를 작성한다. 최초 렌더와 재렌더 자료를 혼동하지 않는다.
- [ ] `test_evidence_task_chain_matches`에서 최초 렌더 응답 ID = 정보 생성 요청의 입력 ID, 정보 생성 응답 ID = 수정·업로드 입력 ID, 업로드 응답 ID = sns 이력 ID를 단언한다.
- [ ] `.\.venv\Scripts\python.exe -m pytest tests/test_infox_publishing_contract.py -q`를 실행해 계약 검사가 통과하는지 확인한다.
- [ ] 진행/실패 fixture는 화면 코드 근거의 모의 입력이며 실제 수신 결과가 아님을 문서에 명시한다. 현재 자료만으로 실패 발생 실험이나 추가 렌더를 실행하지 않는다.

**완료 기준:** 확인 사실과 가정이 분리되고, 구현자가 원본 HAR/인증값 없이 API 연결 계약을 이해할 수 있다.

### Task 2: 입력·실행 상태와 중복 방지 저장소

**Files:** Create `backend/infox/publishing/__init__.py`, `models.py`, `store.py`, `tests/test_infox_publishing_store.py`.
**Interfaces:** `format_publish_metadata(market_date: date) -> tuple[str,str]`; `PublicationStore(run_store: RunStore)`; `create_or_get(run_id: str, input: PublishInput) -> tuple[PublicationRecord,bool]`; `get(id: str) -> PublicationRecord`; `for_run(run_id: str) -> PublicationRecord|None`; `claim_operation(id: str, operation: str, expected_version: int) -> PublicationRecord`; `finish_operation(id: str, operation: str, outcome: str, task_id: str|None) -> PublicationRecord`; `transition(id: str, expected_version: int, changes: dict) -> PublicationRecord`.

- [ ] 실패할 테스트 작성: `test_date_format_is_frozen`은 2026-11-04의 지정 제목·설명이 다음 날도 유지되는지, `test_duplicate_tabs_share_one_publication`은 두 연결의 동시 생성 결과가 1건인지, `test_receipt_claim_allows_only_one_sender`는 두 번째 요청 의도 획득이 거부되는지 단언한다.
- [ ] `.\.venv\Scripts\python.exe -m pytest tests/test_infox_publishing_store.py -q`를 실행해 미구현으로 실패하는지 확인한다.
- [ ] 위 모델·트랜잭션·CAS를 구현한다. run이 ready_for_editor, 검증된 저장 상태, video_id 존재, reconciliation 없음일 때만 생성할 수 있게 한다. 기존 RunRecord JSON은 변경하지 않는다.
- [ ] 같은 명령을 다시 실행해 원자적 롤백·기존 run 호환·잘못된 입력 거부·비밀값 비공개까지 PASS인지 확인한다.

대표 검증값:

```python
assert format_publish_metadata(date(2026, 11, 4)) == (
    '261104 AI 영상 시황', '2026년 11월 4일 AI 영상 시황'
)
assert first_publication.id == duplicate_publication.id
assert store_row_count == 1
```

**완료 기준:** 최초 실행 의도가 저장되고, 새로고침·다중 탭으로 별도 원격 실행이 생기지 않는다.

### Task 3: InfoX 호출 어댑터 확장

**Files:** Modify `backend/infox/client.py`; Create `tests/test_infox_publishing_client.py`.
**Interfaces:** `render_charge_preview(video_id: str) -> dict`; `start_render(video_id: str) -> str`; `generate_youtube_metadata(video_id: str, render_task_id: str) -> str`; `update_youtube_metadata(video_id: str, metadata_task_id: str, *, title: str, description: str, keywords: list[str]) -> dict`; `upload_youtube(video_id: str, metadata_task_id: str) -> str`. 모두 async 메서드다.

- [ ] HTTP MockTransport로 `test_write_endpoints_are_called_once_on_timeout_or_401`, `test_upload_forces_unlisted_and_fixed_redirect`, `test_metadata_patch_uses_metadata_task_id`를 작성한다. 변경 요청별 호출 1회, privacy_status=unlisted, 고정 origin을 단언한다.
- [ ] `.\.venv\Scripts\python.exe -m pytest tests/test_infox_publishing_client.py -q`로 구현 전 실패를 확인한다.
- [ ] 확인한 경로·본문만 전용 메서드로 구현한다. 응답 message가 render / generate_metainfo_youtube / upload_youtube_video인지 검사하고 잘못된 taskid나 인증 리디렉션을 성공으로 취급하지 않는다. 편집 내용 저장이나 임의 외부 URL 호출은 추가하지 않는다.
- [ ] 같은 명령으로 PASS를 확인하고 기존 `tests/test_infox_client.py`도 통과시킨다.

**완료 기준:** 요청 본문·단계별 ID 연결·일부공개 설정·변경 요청 1회 보장이 모의 HTTP로 검증된다.

### Task 4: 진행률·완료·실패 판정

**Files:** Create `backend/infox/publishing/tracking.py`, `tests/test_infox_publishing_tracking.py`.
**Interfaces:** `normalize_publish_event(body: dict, video_id: str, task_id: str) -> PublishEvent|None`; `verify_publish_stage(record: PublicationRecord, history: dict) -> StageVerification`; `parse_youtube_share_url(value: str) -> tuple[str,str]`. PublishEvent와 StageVerification은 models.py에 추가한다.

- [ ] `test_100_percent_without_result_is_not_completion`, `test_wrong_task_and_stale_progress_are_ignored`, `test_failure_stops_stage`, `test_metadata_matches_render_file`, `test_unlisted_upload_requires_matching_task_and_url`를 작성한다. NaN·범위 밖 진행률은 미확인, 실패는 뒤늦은 성공 메시지로 사라지지 않음, 다른 호스트 URL은 거부됨을 단언한다.
- [ ] `.\.venv\Scripts\python.exe -m pytest tests/test_infox_publishing_tracking.py -q`로 구현 전 실패를 확인한다.
- [ ] 기존 task_event의 알려진 메시지 구조와 video/channel/task 대조 규칙을 재사용한다. progress는 0~100의 유한한 수만 사용하고 없으면 null로 둔다. 완료 근거는 위 「외부 요청과 완료 근거」 표를 적용한다.
- [ ] 같은 명령으로 PASS를 확인한다. 관찰하지 않은 실패 이력의 state 이름을 사실로 고정하지 않는다.

대표 검증값:

```python
assert normalize_publish_event(other_task_event, video_id, render_task_id) is None
assert verify_publish_stage(render_record, history_without_file).status == 'waiting'
assert verify_publish_stage(upload_record, matching_unlisted_sns_history).status == 'completed'
```

**완료 기준:** 진행률과 완료·실패를 구분하고 과거/다른 작업 결과를 사용하지 않는다.

### Task 5: 백엔드 순차 실행과 중단

**Files:** Create `backend/infox/publishing/manager.py`, `tests/test_infox_publishing_manager.py`.
**Interfaces:** `PublicationManager(store, client_factory, clipboard, *, timeouts: PublishTimeouts, poll_interval: float=2)`; async `start(run_id: str, input: PublishInput) -> PublicationRecord`, `recover() -> None`, `close() -> None`; `active(id: str) -> bool`.

- [ ] `test_first_publish_runs_each_operation_once_in_order`에서 render→metadata_generate→metadata_update→upload→copy 순서와 각 1회 호출을 단언한다.
- [ ] `test_each_stage_failure_blocks_following_writes`, `test_lost_ack_and_restart_never_resubmit`, `test_duplicate_start_does_not_spawn_second_sender`, `test_remote_edit_change_blocks_render`, `test_sse_arrives_before_ack_is_matched_after_id`를 작성한다.
- [ ] `.\.venv\Scripts\python.exe -m pytest tests/test_infox_publishing_manager.py -q`로 구현 전 실패를 확인한다.
- [ ] 사전 확인으로 최신 저장본·권한·기존 이력·이용량을 읽는다. 이미 렌더/metadata/sns 결과가 있거나 활동 중 작업을 식별할 수 없는 대상은 needs_attention으로 멈춘다. 현재 예제 영상을 최초 대상에 사용하지 않는다.
- [ ] SSE 연결 확인 후 저장된 의도의 1회 전송→taskid 저장→이벤트·이력 확인을 단계별로 구현한다. SSE가 연결되지 않으면 새 요청을 시작하지 않는다. 버퍼는 최대 512건·만료 시간 적용이며 초과 시 확인 불가로 멈춘다.
- [ ] 단계의 명시적 실패는 failed, 통신·인증·알 수 없는 상태·시간 초과는 상황에 따라 needs_attention으로 두고 failed_stage와 안전한 사유를 저장한다. 시간으로 100%를 만들거나 접수만으로 다음 단계로 넘어가지 않는다.
- [ ] 서버 재시작 시 이미 아는 작업 ID의 조회만 허용하고 미전송 다음 단계를 자동 시작하지 않는다. 서버 실행 중 프런트 새로고침에서는 백그라운드 작업을 계속한다.
- [ ] 같은 명령으로 PASS를 확인한다.

**완료 기준:** 모의 서버에서 최초 정상 경로가 자동 연결되고 모든 실패 지점에서 후속 변경이 0회다.

### Task 6: 업로드 완료와 링크 복사

**Files:** Modify `backend/infox/publishing/models.py`, `store.py`, `manager.py`; Create `tests/test_infox_publishing_clipboard.py`.
**Interfaces:** async `PublicationManager.copy_link(publication_id: str, *, manual: bool=False) -> PublicationRecord`. 기존 clipboard.write(str)를 asyncio.to_thread로 호출한다.

- [ ] `test_auto_copy_only_after_verified_upload`, `test_clipboard_failure_preserves_upload_success`, `test_manual_copy_never_uploads`, `test_restart_does_not_recopy_automatically`를 작성한다. 업로드 전 복사 0회, 복사 실패 시 uploaded_verified=True 유지, 다시 복사 시 원격 요청 0회를 단언한다.
- [ ] `.\.venv\Scripts\python.exe -m pytest tests/test_infox_publishing_clipboard.py -q`로 구현 전 실패를 확인한다.
- [ ] 검증된 링크를 저장하고 자동 복사 의도를 획득한 백엔드만 한 번 쓴다. 복사 중 중단되어 결과가 불명확하면 재시작 시 반복하지 않고 확인 필요 상태를 남긴다. 수동 「링크 다시 복사」는 기존 링크만 사용한다.
- [ ] 같은 명령으로 PASS를 확인한다.

**완료 기준:** 링크 복사 실패가 업로드 재실행으로 이어지지 않고 업로드 결과가 보존된다.

### Task 7: 로컬 API와 앱 생명주기 연결

**Files:** Modify `backend/app.py`; Create `tests/test_infox_publishing_api.py`.
**Interfaces:** POST `/api/infox-runs/{run_id}/publications`, 본문 PublishInput → 202 PublicationRecord; 같은 경로 GET → `{publication: PublicationRecord|null}`; GET `/api/infox-publications/{id}` → PublicationRecord; POST `/api/infox-publications/{id}/copy-link`, 빈 본문 → PublicationRecord.

- [ ] `test_api_requires_local_session_and_origin`, `test_reload_get_is_read_only`, `test_duplicate_post_returns_existing_publication`, `test_restart_keeps_receipts_and_no_writes`, `test_copy_endpoint_rejects_client_supplied_url`를 작성한다.
- [ ] `.\.venv\Scripts\python.exe -m pytest tests/test_infox_publishing_api.py -q`로 구현 전 실패를 확인한다.
- [ ] 기존 run store와 manager.clipboard를 주입하고 lifespan에 recover/close를 연결한다. 공개 응답은 허용 필드·안전한 오류 코드·한국어 메시지로 제한한다. 기존 run API의 예외 처리를 유지하면서 새 경로도 내부값을 노출하지 않는다.
- [ ] 같은 명령으로 PASS를 확인한다.

**완료 기준:** Vue가 인증값이나 원격 URL을 다루지 않고 게시 실행을 시작·조회·링크 복사할 수 있다.

### Task 8: Vue 버튼·진행 패널·오류 표시

**Files:** Create `frontend/src/infoxPublishing.ts`, `frontend/src/infoxPublishing.spec.ts`, `frontend/src/components/InfoXPublishing.vue`, `frontend/src/components/InfoXPublishing.spec.ts`; Modify `frontend/src/api.ts`, `types.ts`, `App.vue`, `App.spec.ts`.
**Interfaces:** `api.startInfoxPublication(runId, input)`, `api.latestInfoxPublication(runId)`, `api.infoxPublication(id)`, `api.copyInfoxPublicationLink(id)`. InfoXPublishing은 run·초기 기준일을 받고 전용 제어 코드가 조회 반복·정리·오래된 응답 차단을 담당한다.

- [ ] `starts_once_and_shows_stage_progress`, `restores_by_get_without_post`, `null_progress_is_not_fake_percent`, `stale_response_cannot_replace_other_run`, `clipboard_failure_shows_uploaded_link`, `date_and_metadata_stay_frozen_across_midnight` 테스트를 작성한다.
- [ ] `npm.cmd --prefix frontend test -- --run src/infoxPublishing.spec.ts src/components/InfoXPublishing.spec.ts`를 실행해 구현 전 실패를 확인한다.
- [ ] 기존 편집 연결 패널 아래에 대상·기준일·제목/설명 미리보기·일부공개 설정·최초 실행 버튼을 추가한다. 입력값을 한 번 확정해 POST하고 이후는 GET으로 상태를 표시한다. 성공/실패/확인 필요/복사 실패를 각각 구분한다.
- [ ] 버튼 문구는 「편집 완료 · 렌더 및 유튜브 업로드」, 안내는 「InfoX에서 편집 내용을 저장한 뒤 실행하세요. 실행 중에는 편집을 계속하지 마세요.」로 한다. 정상 경로의 추가 확인 팝업을 넣지 않는다.
- [ ] 진행 중 기준일·실행 버튼을 잠그고 completed/failed/needs_attention 기록에는 재실행 버튼을 제공하지 않는다. clipboard_failed에만 「링크 다시 복사」를 제공한다. UI watch에서 복사나 원격 변경을 시작하지 않는다.
- [ ] 같은 테스트로 PASS를 확인하고 `npm.cmd --prefix frontend run build`로 타입 검사·빌드를 확인한다.

**완료 기준:** 한 번의 클릭으로 진행 패널이 갱신되고 새로고침이 새 실행·자동 복사를 만들지 않는다.

### Task 9: 통합·실사이트 검증과 인계

**Files:** Create `tests/infox_publishing_browser_smoke.py`, `docs/infox/first-publish-live-verification.json`; Update `docs/infox/first-publish.md`, `docs/implementation.md`, 승인된 구현 변경에 한해 기존 README.md 편집 내용을 보존하며 사용법 추가.
**Interfaces:** 실제 조회/변경을 구분하는 검증 기록: verification_time, 대상·작업 ID 대응, operations/counts, 단계별 결과, 중간 progress 수신 여부, InfoX 측 unlisted 기록, 공유 링크·클립보드 결과, 미검증 항목.

- [ ] 모의 브라우저 검사에서 정상 전체 흐름, 각 단계 실패, 응답 유실, 두 탭 클릭, 새로고침, 서버 재시작, 링크 복사 실패를 확인한다. 유료 생성 API/실제 InfoX 요청 없이 실행한다.
- [ ] `.\.venv\Scripts\python.exe -m pytest tests -q`, `npm.cmd --prefix frontend test`, `npm.cmd --prefix frontend run build`, `.\.venv\Scripts\python.exe -m tests.infox_publishing_browser_smoke`를 실행하고 결과를 기록한다. 기존 런처·Node 환경은 README의 설정을 따른다.
- [ ] 실사이트에서는 먼저 접근 가능한 **미렌더·미업로드의 편집 저장 완료 영상**을 확보한다. 현재 6f730a33-... 영상으로 최초 실행 성공을 주장하거나 검증용 새 시안 생성을 임의로 추가하지 않는다. 대상이 없으면 모의 검증 결과를 제공하고 최초 실행 실검증만 미완료로 기록한다.
- [ ] 그 대상에서 SSE를 먼저 수집하면서 실제 최초 흐름을 한 번 진행한다. 사용자에게 이미 받은 일부공개 업로드 허용 범위를 적용하고, 새 계정 연결/권한 부여가 필요하면 해당 단계에서 멈춘다. 일부러 렌더·업로드를 실패시키거나 재업로드하지 않는다.
- [ ] 실제 중간 렌더 progress가 수신되는지, 정확한 작업별 완료 근거, 지정 제목·설명, InfoX unlisted 기록, 결과 링크, 실제 클립보드 복사를 검증한다. 중간 progress가 없으면 진행률 실검증 미완료로 명시하고 기능 전체 검증 완료로 표시하지 않는다.
- [ ] 실패 처리는 모의 검증과 실수신 증거를 구분해 기록한다. 처음 업로드가 성공했다는 사실로 인증 만료·실패 경로까지 실검증했다고 표시하지 않는다.
- [ ] 단계별 결과·남은 제한·변경 파일을 정리한다. 커밋·푸시는 실행 시점에 별도로 주어진 사용자 지시 범위를 따른다.

**완료 기준:** 최초 정상 경로의 실제 검증 결과 또는 구체적인 미완료 항목이 제공되고, 실패 시 후속 작업 중단이 모의 검사로 보장된다.

## 자체 검토 결과

- 9개 단계에 사용자 흐름 전체와 사전 저장 조건, 최초 대상 제한, 작업 ID 대응, 결과/복사 분리, 자정·새로고침·경합·응답 유실 검사를 배치했다.
- 진행률/실제 실패 본문/YouTube 측 검증의 관찰 한계를 남겼다. 실패 응답을 확보하려고 불필요한 유료 실패 작업을 만들지 않는다.
- 재렌더·재업로드를 제외하더라도 중복 방지·불명확 상태 중단은 유지한다. 운영 복구·새 게시 시도 기능은 후속 범위다.
- 실행 방식은 아직 지정되지 않았다. 실행 요청을 받을 때 이 계획을 검토하고 기존 using-git-worktrees/executing-plans 절차로 별도 구현을 시작한다.
