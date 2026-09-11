# Market Brief — 로컬 시황 압축

HTML 시황을 장면별 평문으로 변환하고 Gemini로 1분 영상용 5개 장면을 만드는 Windows 앱입니다. Vue3 화면과 Python 서버가 함께 실행됩니다.

## 실행

1. 탐색기에서 **`start.cmd`**를 실행합니다. 준비가 끝나면 기본 브라우저가 열립니다.
2. 기본 입력은 클립보드의 HTML입니다. **클립보드 다시 읽기**, 직접 붙여넣기, 또는 **저장된 파일** 선택을 사용할 수 있습니다. 파일은 미리 선택하지 않습니다.
3. **변환 및 1분 압축**을 누릅니다. 직렬화 결과가 자동 복사되고, Gemini 처리 후 최종 결과가 다시 자동 복사됩니다.
4. 사용을 마치면 **`stop.cmd`**로 서버를 종료합니다. 브라우저 탭만 닫으면 서버는 계속 실행됩니다.

최초 환경 준비에는 인터넷 연결이 필요합니다. 화면과 HTML 변환은 로컬에서 동작하며, 압축할 때 직렬화된 뉴스 본문을 Gemini로 전송합니다. 화면을 여는 것만으로 API를 호출하지 않습니다.

## API 키와 결과

- 프로젝트의 기존 `.env`에서 `GEMINI_API_KEY_1`, `GEMINI_API_KEY_2`, `GEMINI_API_KEY_3`을 읽습니다. 빈 키는 건너뜁니다. 새 환경이라면 `.env.example`을 `.env`로 복사한 뒤 값을 입력합니다.
- 모델은 `gemini-3.5-flash-lite`입니다. 키 값은 브라우저·결과 파일·로그에 노출하지 않습니다.
- 결과는 `outputs/<실행시각_고유ID>/serialized.txt`와 `compressed.txt`에 저장됩니다. `metadata.json`에는 상태, 모델, 시도 횟수와 오류가 남습니다.
- 형식이 잘못된 응답은 `invalid_response_N.txt`에 진단용으로 보관하며 최종 결과로 복사하지 않습니다.
- 자동 저장에 실패해도 생성된 결과는 현재 화면에서 다운로드할 수 있습니다. 서버를 종료하기 전에 다운로드하세요.
- 결과 파일은 UTF-8, LF 개행, 본문만 저장합니다. Windows 클립보드에는 같은 본문을 Windows 개행으로 복사합니다.

## 오류 처리

응답 대기·연결 오류·인증/권한 오류·호출 한도·서버 오류가 발생하면 다음 키로 전환합니다. 총 호출은 형식 보정을 포함해 **최대 3회**, 요청당 **60초**, 전체 **300초** 이내입니다. 기본 대기는 2초와 4초이며 서버의 대기 지시가 우선합니다. 같은 프로젝트의 여러 키는 호출 한도를 공유합니다.

형식 검증 실패에는 한 번만 보정 요청을 합니다. 모델/요청 설정 오류와 명시적인 생성 차단은 자동 재시도하지 않습니다. **압축 다시 시도**는 원래 직렬화 결과를 사용하며 클립보드나 원본 파일을 다시 읽지 않습니다.

클립보드가 다른 프로그램에 잠겨 있으면 경고를 표시하고 다음 단계를 계속합니다. 결과의 복사 버튼으로 다시 복사할 수 있습니다.

## 입력 형식

`src` 아래의 `.txt` 또는 `.html` 파일을 선택할 수 있습니다. 파일은 UTF-8 또는 UTF-8 BOM이어야 하며 최대 5 MiB입니다. 클립보드는 HTML 소스 텍스트와 Windows HTML clipboard format을 지원합니다.

HTML에는 `<header>`를 포함하는 실제 뉴스 `.embedded-content`가 하나 있어야 합니다. `.summary-box`, `.summary`, `.indices/.index-card`, `section/h2`, `ul/li`의 `.key-info`와 `.key-detail`을 순서대로 변환합니다. 누락·중복 구조는 오류로 표시합니다. 참고 입력은 `src/0910/0910_html.txt`입니다. `src/0907/1.txt`는 이미 평문인 참고 자료여서 HTML 변환 입력으로는 사용할 수 없습니다.

기존 `prompts/html to input text.txt`의 명시된 규칙을 Python으로 구현했습니다. 장면 구분자는 항상 `===`입니다. `prompts/compress_to_1min.txt`는 API 요청마다 다시 읽으므로 편집 내용이 다음 호출에 반영됩니다. 프롬프트의 긴 등호 예제와 빈 줄 충돌만 메모리에서 정정하고 앱 출력 계약을 덧붙입니다.

압축 결과는 5개 장면, 구분자, 설명 길이, 650자 상한 및 두 지수의 원문 보존을 검사합니다. 400~550자와 제목 25자는 권장 기준입니다. 서술의 사실성과 약 1분의 실제 낭독 시간은 최종 편집 단계에서 확인하세요.

## 개발 및 검증

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q
$env:PATH = "$PWD\.tools\node;$env:PATH"
npm.cmd --prefix frontend test
npm.cmd --prefix frontend run build
```

Python 직렬화만 실행할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m backend.serializer src/0910/0910_html.txt -o result.txt
```

`--fenced`를 추가하면 하나의 `text` 코드블록으로 감싼 출력을 생성합니다.

실제 Gemini와 클립보드를 사용하는 검사는 명시적으로 실행합니다. API 사용량이 발생하며 클립보드가 결과로 바뀝니다.

```powershell
.\.venv\Scripts\python.exe -m tests.live_smoke
```

프런트엔드는 `frontend/`, 백엔드는 `backend/`에 있습니다. 세부 HTTP 계약은 `docs/implementation.md`를 참고하세요. API는 루프백에서만 열리며 작업·클립보드·다운로드 요청에는 앱 세션 토큰이 필요합니다.
