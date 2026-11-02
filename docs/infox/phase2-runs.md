# InfoX 2단계: 전송본 확정과 실행 기록

기준: [자동화 명세](automation-implementation-spec.md) §4·12·14·15의 2단계. 다음 작업의 시작점은 [통합 인계 문서](phase1-handoff.md)의 2단계 추가 기록이다.

## 사용자 흐름

대본 결과 아래 **영상 전송본 준비**에서 제목과 반도체 즐겨찾기 자동 대체 설정을 확인하고 **InfoX 전송본 확정**을 누른다. 최신 편집 저장을 완료한 후 해당 revision의 본문과 설정을 보관한다. 이 단계에서 InfoX 영상은 생성하지 않는다.

한글 조합 중이거나 대본 저장 실패·revision 충돌이 있으면 새 확정 요청을 보내지 않는다. 생성 상태가 `needs_review`/`failed`이거나 검증 사유가 남아 있으면 **검토 사유를 확인하고 이 대본을 사용합니다** 체크가 필요하다. 대본을 수정하면 체크를 해제한다. 원래 생성 진단은 유지하고 수정 대본을 자동으로 정상 판정하지 않는다.

서버 응답이 유실되면 **확정 결과 다시 확인**으로 같은 요청을 확인한다. 현재 대본이 추가 수정되거나 비워졌어도 이전 intent·revision·설정을 그대로 사용한다. 저장/생성 결과가 명확히 거절된 4xx와 접수 여부가 불명확한 네트워크/5xx를 구분한다. 화면/백엔드 재시작 시 최신 실행을 조회하여 표시하고 외부 요청은 보내지 않는다.

## 로컬 API

모든 경로는 기존 Host·Origin·`X-App-Token` 가드를 통과해야 한다. InfoX 자격은 이 요청에 포함하지 않는다.

### `POST /api/jobs/{job_id}/infox-runs`

```json
{
  "intent_id": "11111111-1111-4111-8111-111111111111",
  "revision": 2,
  "settings": {"title": "시황 영상", "auto_replace": true},
  "review_confirmed": true,
  "new_run": false
}
```

`intent_id`는 사용자 클릭 의도를 식별하는 UUID다. 브라우저는 전송 전에 요청을 sessionStorage에 기록한다(대본 본문·자격은 저장하지 않음). 같은 intent에 다른 job/revision/설정을 넣으면 409 `intent_conflict`다. 최초 확정 201, 기존 실행 재사용 200이며 응답은 실행 전체다.

백엔드는 요청 본문에 대본을 받지 않고 `JobManager.get()`의 최신 저장값을 읽는다. 기대 revision과 다르면 409 `revision_conflict`, 미종료 작업은 `job_not_ready`, 검토 미확인은 `review_required`, 빈 본문은 422 `empty_script`다. 입력 검증은 알려진 필드만 받고 수치/boolean을 문자열에서 암묵적으로 변환하지 않는다. 오류에는 서버 원문·비밀·요청 본문을 넣지 않는다.

### 조회

- `GET /api/infox-runs/{run_id}`: 실행 전체. 없는/잘못된 ID는 404.
- `GET /api/jobs/{job_id}/infox-runs`: 해당 작업의 최신 실행을 `{"run": ...}`로 반환. 확정 이력이 없으면 `run=null`.

응답은 stage, version, snapshot, project/generation 접수 상태, video_id, task_id, requires_reconciliation을 포함한다. `prepared`는 로컬 전송본이 확정됐다는 뜻이며 InfoX 제작 접수가 아니다.

## 스냅샷과 설정

`snapshot`에는 job ID·revision·본문·UTF-8 SHA-256·확정 시각·장면 대응·전체 설정·검토 확인·생성 당시 진단이 저장된다. Python 모델은 frozen이며 장면/진단은 tuple이다. 이후 편집과 별개로 유지하며 이 스냅샷을 수정하는 API는 없다.

본문은 그대로 보존한다. CRLF/CR/LF는 장면 경계이고 공백만 있는 줄은 장면에서 제외한다. 나머지 줄의 앞뒤 공백과 글자는 유지한다. `line_number`는 원문 1부터, `scene_index`는 장면 0부터다. 장면 수는 고정하지 않는다.

기본값은 명세 관찰값 그대로다: template 1786, elevenlabs의 지정 음성, 문자열 속도 `"1.0"`, 숫자 목표 `60`, 직접 대본 입력과 disableAI, ai_recommend/photo/4:3/ko/v1. 화면은 제목과 자동 대체 여부를 편집하며 나머지는 백엔드 `ProductionSettings`의 검증된 기본값으로 확정한다. API는 모델에 선언된 설정만 허용한다. `creation_body(snapshot)`이 3단계에서 사용할 정확한 `video/context` 본문을 만든다. 추가 요약이나 TTS 기본값 덮어쓰기는 하지 않는다.

## 저장과 중복 방지

Python 표준 SQLite를 사용하며 경로는 Git 제외 **`.runtime/infox-runs.sqlite3`**다. 자격·쿠키·서명 URL을 저장하지 않는다. 대본과 실행 기록은 로컬 평문 데이터이며 기존 결과물과 같은 컴퓨터의 접근 권한에 의존한다. 실행 DB를 일반 문서나 Git에 복사하지 않는다.

실행과 intent 별칭은 하나의 `BEGIN IMMEDIATE` 트랜잭션에 저장한다. 중간 실패는 모두 롤백하고 오류 후 성공 응답을 반환하지 않는다. 경로의 `.runtime` 및 DB/보조 파일 링크를 검증한다. 일반 API는 단일 로컬 서버의 `JobManager`를 사용하며 최신 대본 검사와 DB 저장 사이에 await를 두지 않는다. SQLite는 동시에 들어온 같은 스냅샷의 생성도 하나로 합친다.

중복 기준은 job ID·revision·본문 해시·전체 설정이다. 다른 탭이 새 intent로 같은 스냅샷을 요청해도 기존 실행을 반환한다. 응답을 잃은 원래 intent는 현재 대본 revision이 이후 바뀌었더라도 원래 실행을 반환한다. 영구적으로 같은 대본을 금지하지 않도록 `new_run=true`를 지원하되, 기존 실행이 확실히 `ready_for_editor`/`failed`이며 확인 불가 접수가 없을 때만 새 실행을 만든다. 화면에서 명시적 재제작을 시작하는 UI는 제작이 연결되는 후속 단계에서 추가한다.

## 3단계 쓰기 연결의 필수 순서

```python
# 준비된 실행을 읽고, 외부 쓰기 전에 반드시 의도를 커밋한다.
record = store.get(run_id)
claimed = store.begin_request(run_id, 'project', expected_version=record.version)
# 이 지점 이후에만 생성 HTTP를 한 번 보낸다. 성공 응답의 영상 ID를 저장한다.
accepted = store.record_outcome(run_id, 'project', 'accepted',
    external_id=video_id, expected_version=claimed.version)
# accepted.video_id로 제작 시작 요청을 별도로 준비한다.
```

실제 HTTP 호출은 아직 구현하지 않았다. `begin_request`는 `intent_recorded`와 의도 시각을 영속화하고 stage를 `creating_project`/`starting_generation`으로 변경한다. `record_outcome`은 `accepted`/`unknown`/`rejected`와 접수 결과, 외부 ID를 저장한다. 모든 갱신은 expected_version CAS를 요구한다.

`intent_recorded` 또는 `unknown`은 requires_reconciliation=true다. 프로세스가 외부 전송 전/후 어느 지점에서 죽었는지 알 수 없으므로 복구 시 외부 상태부터 확인한다. 동일 operation의 begin을 다시 호출하면 거절한다. 제작 시작 GET도 이 경계를 사용하고 조회용 `InfoXClient._read()`로 보내지 않는다. 2단계는 수신 기록의 원자적 저장/복원까지 제공하며 외부 상태를 조사하거나 자동 재개하지 않는다.

## 검증 명령

```powershell
.venv/Scripts/python.exe -m pytest tests/test_infox_runs.py tests/test_infox_runs_api.py -q
.tools/node/node.exe frontend/node_modules/vitest/vitest.mjs run --root frontend
.tools/node/node.exe frontend/node_modules/vue-tsc/bin/vue-tsc.js -b frontend/tsconfig.json
.tools/node/node.exe frontend/node_modules/vite/bin/vite.js build frontend
.venv/Scripts/python.exe -m tests.infox_runs_browser_smoke
```

브라우저 검증은 임시 프로젝트/가짜 대본·실제 로컬 API/Edge Chromium을 사용한다. Gemini/OpenAI/InfoX 외부 요청은 0회여야 한다. SQLite 원자성, 재시작과 응답 유실, 동시 클릭, 설정 타입, revision/검토 정책, 열린 UI의 편집 보존을 회귀 테스트로 남긴다.
