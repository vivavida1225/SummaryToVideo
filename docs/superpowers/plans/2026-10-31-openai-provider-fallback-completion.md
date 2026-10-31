# OpenAI 모델 및 공급자별 fallback 구현 완료 보고

## 구현 결과

- 기본값은 GPT 6.1 Sol이다. 유효한 기존 브라우저 선택은 유지한다.
- GPT는 선택 지점부터 Sol → Terra → Luna 안에서만 fallback한다. Gemini 후보와 키 순환, 3.7 API 별칭 호환을 유지한다.
- 공식 OpenAI SDK 2.44.0의 비동기 Responses API, low 추론, 8192 토큰, store=False, SDK 내부 재시도 금지 및 Windows truststore를 적용했다.
- 요청의 공식 주소와 최종 인증 헤더를 고정해 셸의 다른 SDK 설정이 작업에서 확정한 주소·키를 덮어쓰지 못하게 했다.
- HTTP 오류뿐 아니라 응답 본문의 서버·한도 오류도 분류한다. 인증·요청 설정·생성 거부는 멈추며, 토큰 소진·빈 응답·통신 실패 등은 다음 모델로 전환한다.
- 대본 검증·자동 보정·검토 필요·수동 재생성·원응답 저장과 기록 복원을 공유한다. 세션과 화면은 공급자별 모델·키 상태를 표시한다.
- `.env.example`, README와 HTTP 계약을 갱신했다. 수동 API 검증에 시작 모델을 지정할 수 있게 했으며, 자동 검증에서는 유료 호출을 수행하지 않았다.

## 검증

| 항목 | 명령 또는 확인 | 결과 |
| --- | --- | --- |
| Python 전체 | `.venv/Scripts/python.exe -m pytest tests -q` | 415 passed; 기존 의존성 경고 2건 |
| 프런트엔드 | `npm.cmd --prefix frontend test` | 49 passed |
| Production build | `npm.cmd --prefix frontend run build` | 타입 검사·Vite 빌드 통과 |
| 의존성 | `.venv/Scripts/python.exe -m pip check` | 충돌 없음 |
| 새 공급자 화면 | `python -m tests.provider_browser_smoke` | 6종 모델·기본값·키 상태·잠금·실제 응답 모델·선택 복원, 320/390/1440px 통과 |
| 검토 화면 | `python -m tests.length_review_browser_smoke` | 기록 복원·분량 표시·수동 재생성 통과 |
| myasset 화면 | `python -m tests.myasset_browser_smoke` | 가져오기·자동 제출·설정·반응형 통과 |
| 대본 편집 화면 | `python -m tests.edit_narration_browser_smoke` | 편집·복사·다운로드·저장 복구 통과 |
| 변경 검사 | `git diff --check` | 통과 |
| 인코딩 | 변경·추가 파일 UTF-8 검사, 기존 checkout BOM·개행 비교 | 기존 인코딩·CRLF 유지, 새 파일 BOM 없음 |

검증 환경은 Windows, Python 3.12 가상 환경, Node 26.1.0이다. Node 전역 Web Storage와 jsdom 충돌을 피하려고 프런트엔드 테스트에 `NODE_OPTIONS=--no-experimental-webstorage`를 사용했다. 프로젝트 런처가 관리하는 Python 3.14.7/Node 24.21.0의 설치·기동 검사는 이번에 실행하지 않았다.

SDK 검증은 실제 SDK에 `httpx.MockTransport`를 연결해 수행했다. HTTP 오류, 응답 본문 오류, 생성 거부, 토큰 소진, 연결 실패, 취소 시 클라이언트 정리와 요청 메시지를 확인했다. HTTP API에서 Terra 선택 후 Luna 성공, GPT 보정 중 fallback, 분량·한자 검토 및 수동 재생성도 검증했다.

## 리뷰와 판단

- 독립 리뷰의 Important 지적 2건(암묵적 SDK 환경 설정, 응답 본문의 일시적 오류)을 실패 테스트로 재현한 뒤 수정했다. 수정 후 전체 Python 415개가 통과했다. Critical·Minor 지적은 없었다.
- 기존 myasset 브라우저 검사의 기대 요청에서 `transcribe_numbers=False`가 빠져 있었다. 변경 전 제품 코드도 이미 이 필드를 보냈으므로 기대값만 현행 계약에 맞췄다.
- 처음 checkout은 깨끗했으며 `feat/openai-provider-fallback` 브랜치에서 작업했다. 별도 worktree·커밋·푸시는 만들지 않았다.

## 실제 API 검증 상태

실제 OpenAI/Gemini 생성 요청은 실행하지 않았다. 계정의 모델 접근 권한, 인증 성공, 실제 생성 품질·지연·토큰 사용량은 미검증이다. 키 항목 존재 확인과 모의 테스트 통과를 실제 API 성공으로 간주하지 않는다.

원할 때 다음 명령으로 유료 호출을 포함하는 수동 검사를 실행할 수 있다. 각 명령은 지정 모델부터 같은 공급자 안에서 fallback하며 결과 모델·시도 목록·지연·대본 검증 상태를 저장한다.

```powershell
.\.venv\Scripts\python.exe -m tests.live_smoke --model gpt-6.1-sol
.\.venv\Scripts\python.exe -m tests.live_smoke --model gpt-6.1-terra
.\.venv\Scripts\python.exe -m tests.live_smoke --model gpt-6-luna
```
