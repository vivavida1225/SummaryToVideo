# InfoX 1단계 완료 및 2단계 인계

작성일: 2026-11-02 (한국 시간). 프로젝트: `C:\WORKS\SummaryToVideo`, 브랜치: `master`.

## 현재 완료 범위

[자동화 명세](automation-implementation-spec.md) §15의 **1단계 InfoX 연결 검증**을 구현했다. 백엔드 독립 클라이언트에서 실제 로그인, 기존 영상 조회, 토큰 갱신, 갱신 후 동일 영상 재조회, `반도체` 즐겨찾기 검색, 해당 기존 영상 채널 SSE의 JSON 본문 수신을 확인했다.

프로젝트 생성·제작 시작·에셋 변경·편집 저장은 이번 구현에 없다. 기존 FastAPI 경로, 화면, 대본 생성·편집 저장 및 `X-App-Token`/Host/Origin/CSP 정책은 변경하지 않았다. 2단계 이상이 구현됐다고 간주하지 않는다.

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

## 2단계 작업자의 범위

다음 작업은 **실행 기록과 대본 확정**이다. 연결이 가능해졌다는 이유로 즉시 생성 요청부터 추가하지 않는다.

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

## 완료 기록

- 실제 독립 CLI: 성공(인증·조회·갱신·재조회·두 검색·SSE JSON 본문). 세부 시각과 결과는 `phase1-live-verification.json`.
- InfoX 회귀 테스트: **35 passed**. 로그인/갱신·401/403·안전한 오류·검색·SSE·진단 출력을 검증했다.
- 전체 백엔드: **456 passed**, 기존 의존성 경고 3개. 위 PSModulePath 환경으로 런처 테스트도 모두 포함해 통과했다.
- 프런트엔드: **49 passed**. `vue-tsc` 타입 검사와 Vite 프로덕션 빌드 성공.
- 독립 코드 리뷰: 갱신 직후 일시 오류의 재시도 및 열린 연결에서 CR로 끝나는 SSE 프레임 지연을 재현 테스트로 확인하고 수정했다. 갱신 자체의 실패가 조회 재시도에 섞이지 않는 회귀 테스트도 추가했다. 재검토에서 차단 문제 없음.
- 커밋 대상 18개 파일을 로컬 설정의 실제 자격·영상 ID와 대조해 일치 없음 확인. 원본 HAR·서명 URL은 추가하지 않았다.
- 이 문서·구현·원문 명세를 하나의 커밋으로 `origin/master`에 전달한다. 정확한 커밋은 `git log -1`로 확인한다.
