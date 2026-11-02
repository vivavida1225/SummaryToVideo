# InfoX 1단계 연결 어댑터

기준 명세: [automation-implementation-spec.md](automation-implementation-spec.md) §5·6·9·15.

`backend/infox`는 기존 앱과 별도로 사용할 수 있는 비동기 HTTP 클라이언트다. 이 단계는 로그인·갱신·기존 영상 조회·즐겨찾기 검색·SSE 본문 수신만 구현한다. 프로젝트 생성, 제작 시작, 편집 저장은 호출하지 않는다.

## 설정과 실행

Git 제외된 프로젝트 루트 `.env`에 `INFOX_USERNAME`(이메일), `INFOX_PASSWORD`, `INFOX_VIDEO_ID`(접근 가능한 기존 영상)를 설정한다. 조직에서 요구할 경우 `INFOX_ORG_DEVICE_TOKEN`을 추가한다. 기존 토큰을 사용할 때는 `INFOX_ACCESS_TOKEN`도 지원한다. 토큰이 설정되어 있으면 비밀번호 로그인보다 우선한다. 프로세스 환경변수는 `.env`보다 우선하며 비어 있는 환경변수도 명시적인 설정으로 취급한다.

`.env`는 기존 Gemini/OpenAI 키와 같은 백엔드 전용 저장 방식이다. 암호화된 OS 자격 증명 보관소는 아니다. 브라우저로 전달하거나 실행 기록에 복사하지 않는다. 갱신된 세션 토큰과 서버가 설정한 쿠키는 메모리에만 보관한다. 재시작하면 `.env` 자격으로 재로그인하거나 현재 토큰을 다시 제공한다. `.env`를 덮어쓰지 말고 필요한 항목을 추가한다.

프로젝트 루트 PowerShell에서:

```powershell
.venv/Scripts/python.exe -m backend.infox
.venv/Scripts/python.exe -m backend.infox --sse-timeout 20 --date-from 2026-01-01
```

기본 검색어는 `반도체`, `favorites_only=true`, `skip=0`, `limit=50`이다. 날짜 필터는 기본적으로 보내지 않는다. `--date-from`은 검색 기간을 의도적으로 제한할 때만 사용한다. 진단은 첫 페이지만 읽고 `more_pages`로 후속 페이지 존재를 알린다. 전체 페이지 순회와 후보 선택은 5단계 작업이다. 일반 영상과 사용자 영상 검색은 분리하여 반환한다.

진단 순서는 인증→기존 영상 조회→명시적 갱신→재조회→두 종류 즐겨찾기 검색→SSE 본문 수신이다. 성공은 종료 코드 0, 실행 실패 1, 영상 설정 누락 2, 사용자 중단 130이다. SSE는 기본 15초, 최대 60초 안에 실제 JSON 본문을 받아야 성공한다. HTTP 200만으로 성공 처리하지 않는다. 출력에는 값 대신 자료형·건수·고정된 필드명만 담긴다. 실제 영상/작업 ID, 계정, 대본, 토큰, 쿠키, 파일·서명 URL, 서버 오류 본문은 출력하지 않는다.

## 다음 단계에서 사용할 인터페이스

```python
from backend.infox import InfoXClient, InfoXCredentials, InfoXError

async with InfoXClient(InfoXCredentials.load()) as client:
    await client.authenticate()
    state = await client.video_state(existing_video_id)
    await client.refresh()
    page = await client.search_favorites(search='반도체', source='video', skip=0, limit=50)
    async for event in client.events(existing_video_id, last_event_id=''):
        body = event.json()
        # 수신 후 현재 영상/작업 매칭과 상태 판정은 3단계에서 구현한다.
        break
```

`events()`의 소비자는 조기 중단·취소 때 async generator를 `aclose()`하여 HTTP 연결을 닫는다. 진단 구현이 그 예시다. `SSEEvent.data`는 원문 문자열이며 `json()`은 JSON 구조를 반환한다. 이벤트 이름·ID·다중 data 행·retry 값을 전달한다. 댓글/heartbeat와 data 없는 프레임은 이벤트로 내보내지 않는다. EOF의 미완성 이벤트는 버린다. 파서 프레임 크기 한도는 기본 1 MiB다.

## 인증과 오류 계약

- 고정 HTTPS origin만 사용하고 리디렉션을 따라가지 않는다. 기존 truststore로 회사 Windows 신뢰 루트를 사용한다.
- 일반 API: Bearer 및 선택적 `X-Org-Device-Token`. 서버의 Set-Cookie는 같은 HTTP 세션에서 유지한다.
- SSE 기본 `cookie`: 브라우저의 EventSource와 같이 세션 쿠키만 사용하며 Bearer·조직 헤더를 추가하지 않는다. 실제 서비스는 쿠키 없는 연결에서도 connection 본문을 반환했다. 이것만으로 영상 접근 권한이나 후속 작업 이벤트 수신 권한을 보증하지 않는다.
- SSE `bearer`: `InfoXClient(..., stream_auth='bearer')` 또는 CLI `--stream-auth bearer`로 명시적으로 선택한다. 이 방식의 실제 서버 요구 조건은 확인되지 않았다.
- 401은 `auth_expired`, 403은 `permission_denied`다. 일반 조회의 401은 한 번만 갱신 후 재요청한다. 갱신도 거절되고 조직 장치 자격이 있으면 별도 device-refresh를 사용한다. 비밀번호 자동 재로그인은 하지 않는다.
- `timeout`, `network_error`, `rate_limited`, `server_error`, `http_error`, `invalid_response`, `invalid_event_json`, `configuration_error`를 구분한다. `InfoXError`는 안전한 code와 HTTP 상태만 담고 원본 예외 체인·서버 본문을 표시하지 않는다.
- 조회와 검색만 일시적 통신/429/5xx 오류에 최대 3회 시도한다. 401 후 인증 갱신에 따른 재요청 1회는 이 횟수와 별도다(조회 요청 총 최대 4회). 로그인·갱신은 암묵적으로 재시도하지 않는다. SSE는 무한 재연결하지 않는다. Bearer 모드 401에 한해서 한 번 갱신하여 다시 연결한다. 이후 재연결/Last-Event-ID 선택은 실행 관리자의 책임이다.

## 검증 자료

- [민감 값을 제거한 응답 예시](phase1-response-examples.json)
- [실제 연결 검증 결과](phase1-live-verification.json)
- [2단계 작업 인계](phase1-handoff.md)

로그인·갱신·조회·검색·기존 채널 SSE 본문을 독립 클라이언트에서 확인한다. `connection/connected`는 제작 완료 이벤트가 아니다. 현재 작업의 `completed`·실패 상태·완료 후 편집 데이터 일치는 명세대로 3단계의 단일 생성에서 검증해야 한다.
