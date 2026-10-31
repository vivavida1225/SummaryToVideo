# OpenAI 모델 및 공급자별 fallback 구현 계획

사용자가 승인한 2026-10-31 계획의 구현·검증 기준이다.

## 목표

기존 Gemini 대본 생성에 공식 OpenAI Python SDK를 추가하고, 공급자 안에서만 fallback한다. 기본 모델은 GPT 6.1 Sol이며 기존 유효한 브라우저 선택은 유지한다.

## 구현 기준

- 모델 목록은 GPT 6.1 Sol (`gpt-6.1-sol`), GPT 6.1 Terra (`gpt-6.1-terra`), GPT 6 Luna (`gpt-6-luna`), 기존 Gemini 3종 순서다. 각 모델에 `provider`를 추가한다.
- GPT 후보는 Sol → Terra → Luna이며 선택 지점부터 아래로만 시도한다. Gemini 후보와 키 순환은 기존대로 유지한다. 공급자 사이의 자동 전환은 없다.
- 기존 `gemini-3.7-flash` API·과거 작업 재시도는 `gemini-3.8-flash`로 정규화한다. 지원 목록에 없는 브라우저 선택은 새 기본값 Sol로 갱신한다.
- `Settings.keys(provider='gemini')`를 추가한다. OpenAI는 비어 있지 않은 프로세스 `OPENAI_API_KEY`를 우선하고, 없으면 프로젝트 `.env`를 사용한다. 단일 키 번호는 1이다.
- 작업 생성 시 모델과 해당 공급자의 키를 확정한다. SDK의 별도 환경 설정이 공식 API 주소와 확정한 인증 헤더를 바꾸지 못하게 한다. 키 값은 직렬화·로그·메타데이터에 노출하지 않으며 두 공급자의 키를 마스킹한다.
- `openai==2.44.0`의 `AsyncOpenAI.responses.create`를 사용한다. 전체 프롬프트는 `instructions`, 원문·이전 assistant 대본·보정 지침은 분리한 `input` 메시지로 전달한다.
- OpenAI 설정은 `reasoning.effort=low`, `max_output_tokens=8192`, `store=False`, `max_retries=0`이다. temperature·도구·스트리밍은 추가하지 않는다. Windows truststore TLS 및 성공·오류·취소 시 클라이언트 정리를 적용한다.
- 완료된 응답의 텍스트는 SDK `output_text`로 추출한다. 생성 거부와 미완료는 정상 대본으로 처리하지 않는다.
- 연결·타임아웃·408·429·5xx·404·빈 응답·출력 토큰 소진은 다음 GPT 모델로 전환한다. 응답 본문의 `server_error`·`rate_limit_exceeded`도 같은 기준을 적용한다. 401·403·잘못된 요청·명시적 생성 거부·콘텐츠 차단은 종료한다. Luna 실패 후 멈춘다.
- 요청 60초·전체 300초, 후보 수 + 키 수의 호출 상한을 유지한다. 자동 보정은 1회이며, 사용자 재생성은 이 기회를 사용한 상태로 시작한다. 보정 문맥은 통신 fallback을 거쳐 유지한다.
- 기존 프롬프트·수치 전사·분량·한자·강도 검증·검토 필요·원응답 저장·사용자 재생성·기록 복원·수동 복사 흐름을 공유한다.
- `ProviderError`를 공통 모듈로 옮기고 기존 Gemini import 경로는 재노출한다.
- 세션에 모델별 `provider`와 `configured_keys_by_provider`를 추가한다. 기존 `configured_keys`는 Gemini 키 번호 목록으로 유지한다. 작업 요청은 기존 `model`만 사용한다.
- 화면은 공급자별 키 설정 상태와 실제 호출·응답 모델을 표시한다. 실행 중 모델 선택을 잠그고 자동 fallback이 저장된 시작 모델을 변경하지 않게 한다.
- 새 텍스트는 UTF-8 BOM 없이 작성하고 기존 파일의 인코딩·개행을 보존한다. 기존 `.env`와 입력·출력 자료는 수정하지 않는다.

## 순서와 검증

1. 모델·키·작업 경로: 후보 격리, 키 우선순위, 기본값, 별칭, 세션, 키·모델 스냅샷을 검증한다.
2. SDK 어댑터: 실제 SDK에 모의 HTTP transport를 연결해 요청·오류 분류·거부·토큰 소진·클라이언트 정리와 재시도 금지를 검증한다.
3. 통합: API에서 Terra 선택 후 Luna 성공, 자동 보정·보정 중 fallback·검토·수동 재생성·기록 복원을 검증한다.
4. 화면: 프런트엔드 전체 테스트와 production build, 모의 API 브라우저 검사에서 6종 선택·키 상태·잠금·실제 모델·선택 복원과 320/390/1440px을 확인한다.

실제 유료 API 검사는 자동 테스트에서 분리하며, 모델 접근 권한과 실제 결과 품질은 모의 검증 결과와 구분해 보고한다. 구현 중 커밋·푸시는 하지 않는다.
