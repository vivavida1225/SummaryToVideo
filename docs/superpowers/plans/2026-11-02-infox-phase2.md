# InfoX 2단계 구현 계획

> **For agentic workers:** Use superpowers:executing-plans; implement inline with final independent review.

**Goal:** 최신 편집본·revision·설정을 확정하고 실행 기록을 원자적으로 저장해 중복 클릭과 재시작에 안전하게 복원한다.

**Architecture:** Vue는 `flushCompressed()` 후 revision과 명시적 실행 의도를 전송한다. 백엔드는 `JobManager.get()` 최신값을 검증하고 SQLite 트랜잭션으로 불변 스냅샷·실행·멱등 키를 저장한다. 외부 HTTP 쓰기는 연결하지 않는다.

**Tech Stack:** 기존 Vue/TypeScript, FastAPI/Pydantic, Python 표준 sqlite3, pytest/Vitest.

**Spec:** `docs/infox/automation-implementation-spec.md` §4·12·14·15의 2단계.

## Constraints and rulings

- 명세 구현 요청이 있으므로 추가 설계 승인 없이 진행한다. 기존 미커밋 README/런처/출력물은 보존한다.
- 실행 DB는 Git 제외 `.runtime/infox-runs.sqlite3`. `ResultStore` 파일 허용 목록은 변경하지 않는다.
- snapshot의 본문은 그대로 보존한다. CR/LF는 장면 경계, 공백뿐인 줄은 장면에서 제외하고 다른 줄의 공백은 보존한다. 원문 줄 번호와 장면 번호를 기록한다.
- needs_review 또는 실패/검증 사유가 남은 대본은 명시적 검토 확인을 요구하고 원래 진단을 보존한다.
- 동일 intent 재요청은 같은 실행을 반환한다. 같은 snapshot의 별도 클릭도 기존 실행에 연결한다. 명시적 새 실행은 기존 실행이 확실히 종료된 경우만 허용한다.
- 외부 요청 전 의도를 영속화하는 CAS API를 마련한다. 접수 불명확 상태는 재송신을 금지하고 확인 필요로 복원한다. API 생성 요청 자체는 외부 생성이 아니다.

## Review focus

- 저장 실패/한글 조합/revision 충돌이면 확정 요청 금지.
- 최초 생성본 대신 저장된 최신 편집본 사용, 이후 수정과 기존 snapshot 분리.
- 응답 유실·동시 클릭·서버 재시작 후 멱등성과 외부 ID 보존.
- 원자성 실패 시 snapshot·intent의 부분 기록 없음, 경로·입력 검증 및 비밀 제외.
- 저장된 설정의 자료형 보존, unknown 접수 상태 자동 재전송 없음.

## Tasks

1. `run_models.py`, `store.py`, `service.py` 및 백엔드 테스트: 실패 테스트→불변 snapshot/요청 본문 구성→트랜잭션 저장/멱등성→요청 의도/CAS/접수 결과→GREEN.
2. `app.py` 로컬 실행 생성·조회·최신 실행 API 및 테스트: 기존 보안 가드·안전한 오류 보존, 재시작 복원 검증.
3. `App.vue`, `api.ts`, `types.ts`, 준비 패널 및 UI 테스트: 저장 대기·검토 확인·중복 요청 잠금·응답 유실 후 같은 intent·복원·전송본과 현재 편집본 차이 표시.
4. 전체 Python/Vitest·타입 검사·빌드·실제 브라우저 확인 및 독립 코드 리뷰. 인계 문서에 2단계 완료/3단계 진입점을 추가하고 지정 파일만 커밋·푸시.

## Execution record

- Base: `8caf6e4`; 직전 전체 검증 Python 456 / frontend 49 통과. 기존 미커밋 파일은 1단계 종료 상태와 동일하다.
- Backend RED→GREEN: 새로운 모델/서비스 부재 및 API 404를 확인한 뒤 구현. 전용 17개/전체 473개 통과. 동시 journal resolve의 Windows 경로 오탐을 확인하고 직접 링크 검사로 수정했다.
- UI RED→GREEN: 저장 대기/검토 확인/IME·저장 실패·충돌/유실·복원 테스트를 먼저 실패시킨 뒤 구현. 열린 pending 결과는 현재 대본이 비어도 조회할 수 있게 했다.
- 스냅샷 장면·진단은 tuple로 저장해 중첩 모델도 불변으로 유지한다. 본문·설정 전송 payload를 네트워크 없이 검증했다.
- 실제 브라우저: 실제 로컬 API/임시 저장소에서 확정·중복·디스크 복원·추가 편집 분리·390/1440px 검증, 외부 요청 0회.
- 독립 리뷰: 백엔드 차단 문제 없음. 초기 복원 GET 중 설정 편집이 덮이는 경미한 race를 재현 테스트로 확인하여 설정 잠금으로 수정했다.
