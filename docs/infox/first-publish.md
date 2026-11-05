# 렌더 및 유튜브 업로드

반복 실행은 [재렌더·반복 업로드](repeated-publish.md)를 참고하세요. 아래 실행·완료 검증 계약은 회차마다 적용하며, 뒤의 날짜별 실제 검증 기록은 당시 사실을 보존합니다.

InfoX에서 편집 내용을 저장한 뒤 로컬 앱의 **편집 완료 · 렌더 및 유튜브 업로드**를 한 번 누릅니다. 실행 중에는 InfoX 편집을 계속하지 마세요. 다른 탭의 미저장 내용은 로컬 앱이 저장하거나 확인할 수 없습니다.

실행 전에 대상 영상, 게시 기준일, 지정 제목·설명, 일부공개 설정, 조회한 렌더 이용량을 표시합니다. 기준일 초기값은 서울의 오늘이며 실행 전에 변경할 수 있습니다. 예를 들어 2026-11-04는 제목 ‘261104 AI 영상 시황’, 설명 ‘2026년 11월 4일 AI 영상 시황’으로 고정됩니다. 자정·새로고침 후에도 서버 스냅샷을 사용합니다.

순서는 최신 저장 상태 확인 → 최초 렌더 → 유튜브 정보 생성 → 지정 제목·설명 저장 및 재조회 검증 → 일부공개 업로드 → 완료 결과 확인 → Windows 클립보드 복사입니다. 생성된 keywords는 그대로 보존합니다. 편집 데이터 저장 API를 호출하지 않습니다.

각 단계의 실제 진행률만 표시하며 값이 없으면 ‘진행 중 · 진행률 확인 중’을 표시합니다. 100%나 완료 이벤트만으로 다음 단계로 넘어가지 않습니다. 실패 신호, 알 수 없는 상태, 타임아웃, 접수 불명확이면 후속 변경 요청을 중단합니다. 종료 기록에서는 ‘새 회차 렌더·업로드 준비’를 사용할 수 있지만, 기존 작업 종료를 확인하지 못하면 시작하지 않습니다. 복사 실패에는 검증된 기존 링크의 ‘링크 다시 복사’를 제공합니다.

## 실행 계약

| 작업 | 변경 요청 (각각 최대 한 번) | 완료 판정 |
| --- | --- | --- |
| 렌더 | GET /worker/process_rendering/{video_id} | 해당 render_task_id의 state=end 및 file_name/bucket |
| 정보 생성 | GET /worker/infox_generate_metainfo_youtube/{video_id}/{render_task_id} | metadata_task_id의 state=end, sns_upload_info 및 동일 렌더 파일 |
| 정보 저장 | PATCH /videos/video_render_logs/{video_id}/sns_upload_info | HTTP 200 객체 응답 뒤 같은 작업의 제목·설명·키워드 재조회 일치 |
| 업로드 | GET /worker/infox_upload_youtube_video/{video_id}/{metadata_task_id} | upload_task_id의 state=sns, 유효한 YouTube 링크, 연결된 메타정보의 일부공개·제목·설명·키워드·렌더 파일 재확인 |

원격 API에는 /infomaxai 접두사가 붙습니다. 업로드는 privacy_status=unlisted와 고정 InfoX 편집 주소를 사용합니다. 링크는 https의 youtube.com/www.youtube.com/watch?v= 또는 youtu.be의 11자리 ID만 허용하고 https://youtu.be/{id}로 정규화합니다.

최초 조회와 렌더 직전 저장 버전·권한·기존 작업을 확인합니다. 기존 완료 이력은 허용하고 진행 중·미지원·접수 불명확 작업은 차단합니다. API의 조건부 렌더 버전 인자는 확인되지 않아 렌더 시작 이후 외부 편집을 서버에서 잠그는 기능은 없습니다.

## 저장·복원과 보안

기존 SQLite의 publications, publication_intents에 회차별 기록을 저장합니다. 영상별 진행 회차를 하나로 제한하고 intent와 본문 digest를 하나의 트랜잭션으로 연결합니다. version CAS로 의도를 획득한 소유자만 전송합니다. 401/403/타임아웃에도 변경 요청은 재전송하지 않습니다. 네트워크 요청 중 DB 트랜잭션을 열어두지 않습니다.

브라우저 새로고침은 GET 조회만 수행하며 서버 실행은 계속됩니다. 서버 재시작 시 알려진 ID의 완료 이력 조회만 허용하고 다음 변경 요청·자동 복사를 재개하지 않습니다. 복사 중 중단되면 업로드 결과를 보존하고 복사 결과 불명확으로 표시합니다.

SSE를 먼저 연결하고 작업 응답 전 이벤트도 최대 512건의 유효기간 있는 메모리 큐에 보관합니다. 영상/채널/작업 ID를 대조하고 과거 진행률은 현재 단계에 반영하지 않습니다. 실패 신호는 뒤늦은 성공 신호로 지우지 않습니다. 스트림 재연결은 최대 두 번이며 새 변경 요청은 연결이 확인된 경우에만 보냅니다.

연결 15초, 조회 30초, 이력 조회 간격 2초입니다. 사전 확인 60초, 렌더 20분, 정보 생성 5분, 정보 저장·검증 60초, 업로드 10분으로 제한합니다. 생성자로 단축 설정을 주입할 수 있습니다.

기존 X-App-Token, origin 검사, connect-src 'self', 고정 InfoX origin과 TLS 검증을 유지합니다. 인증값·계정정보·서명된 미디어 URL·원시 응답은 게시 기록에 저장하지 않습니다. 렌더 파일 참조는 해시만 저장합니다. 이용량 응답도 will_charge/credits/duration_sec만 허용된 화면 필드로 투영합니다.

로컬 API:

- POST/GET /api/infox-runs/{run_id}/publications
- GET /api/infox-runs/{run_id}/publications/preview (사전 이용량 조회 전용)
- GET /api/infox-publications/{id}
- POST /api/infox-publications/{id}/copy-link, 본문 {}

시작 본문은 UUID intent_id와 YYYY-MM-DD market_date만 받습니다. 클라이언트의 video_id, 임의 URL, privacy 입력은 거부합니다.

## 근거와 검증

tests/fixtures/infox_publishing/observed.json은 2026-11-04의 인증 조회 조사 자료를 최소 필드로 옮긴 것입니다. 최초 render → metadata → 수정 → upload ID 연결을 보존합니다. 원본 HAR는 구현 시점의 지정 경로에 없어 새로 대조했다고 주장하지 않습니다. 파일·버킷은 동일성을 보존하는 명시적 대체값으로 치환했습니다. 재렌더 자료는 최초 실행 fixture로 사용하지 않습니다.

synthetic-events.json의 진행/실패는 화면 코드의 additional_kwargs 계약에 근거한 **모의 입력**이며 실제 수신 증거가 아닙니다. 정확한 PATCH 성공 본문은 기존 조사 자료에 없으므로 특정 문자열을 추정하지 않고 HTTP 응답과 실제 저장 이력을 함께 검증합니다.

모의 검증은 pytest, Vitest, Vue 타입 검사·빌드, 실제 로컬 FastAPI와 headless Edge의 10개 시나리오로 수행합니다. 정상 흐름·4개 단계 실패·접수 응답 유실·두 탭·새로고침·서버 재시작·복사 실패를 다룹니다. 이 검사는 실사이트 요청·유료 생성·업로드를 하지 않습니다.

검증 명령:

    .\.venv\Scripts\python.exe -m pytest tests -q
    npm.cmd --prefix frontend test
    npm.cmd --prefix frontend run build
    .\.venv\Scripts\python.exe -m tests.infox_publishing_browser_smoke

README의 프로젝트 Node PATH와 NODE_OPTIONS 설정을 사용합니다. PS7의 Utility 모듈이 Windows PowerShell 테스트로 상속되는 환경에서는 PSModulePath를 Windows PowerShell Modules 경로로 한정한 프로세스에서 검사합니다.

실제 검증 결과는 first-publish-live-verification.json에 기록합니다. InfoX의 업로드 완료·일부공개 기록과 YouTube 측 현재 공개 범위·재생 가능 여부는 별개입니다. 신규 OAuth·계정 연결이나 별도 YouTube API 클라이언트는 추가하지 않습니다.

사용자가 지정한 기존 원격 영상에 로컬 run이 없는 경우에만 명시적 실검증 스크립트의 --import-saved-target을 사용할 수 있습니다. 이 경로는 동일 저장본을 두 번 읽어 버전·내용 해시와 실제 시안 완료 이력, 장면·음성·자막 준비를 검증한 후 새 로컬 run을 만들고 경고에 출처를 남깁니다. 이전 run의 대상·스냅샷은 바꾸지 않고 원격 편집 저장·새 시안 생성도 하지 않습니다. 해당 run은 로컬 대본 생성 이력을 갖지 않는 검증용 가져오기 기록이며 일반 Vue 제작 흐름의 자동 복원과는 구분합니다. 이후 재호출은 동일 DB의 게시 기록만 조회하고 변경 요청을 다시 보내지 않습니다.

## 2026-11-05 실제 실행 결과와 남은 한계

사용자가 지정한 fb6b2a24-7acb-4773-b877-ae427bd55b5a 영상에 기준일 2026-11-04를 적용했습니다. 최초 렌더 mw3343f5-7ha02는 한 번 접수되어 실제 완료됐습니다. 초기 SSE 이벤트는 progress=0인 알 수 없는 상태로 정규화되어 자동 실행이 needs_attention으로 중단됐습니다. 그 초기 상태 원문은 보존하지 못했으므로 특정 상태 이름을 추측하거나 허용하지 않았습니다. 이후 읽기 전용 채널에서 processing / 22%를 수신했고 정확한 작업 이력의 end·렌더 파일·버킷을 확인했습니다.

사용자는 이후 ‘기존 렌더 결과로 남은 단계까지 실검증 진행’을 명시적으로 승인했습니다. tests/infox_publishing_continue_live.py는 이 한 번의 운영자 검증을 단계별로 수행했습니다. 원래 자동 실행 기록은 그대로 유지하고 별도 publication_live_verifications 테이블에 각 전송 의도와 완료 검증을 저장했습니다. 이 스크립트는 일반 앱의 재실행 기능이나 자동 복구 API가 아니며, --operator-authorized 인자를 붙이는 행위 자체가 사용자 승인을 대신하지 않습니다.

정보 생성 mw33fyj5-wqrch → 제목·설명 저장 → 일부공개 업로드 mw33hpf1-xev3r → 클립보드 쓰기를 각각 한 번 수행했습니다. 키워드 13개를 보존했으며 제목 ‘261104 AI 영상 시황’, 설명 ‘2026년 11월 4일 AI 영상 시황’, 동일 렌더 파일, InfoX privacyStatus=unlisted, 공유 링크 https://youtu.be/NVrF804XtkE 및 실제 클립보드 읽기 일치를 검증했습니다. 재렌더·재업로드·편집 데이터 저장은 하지 않았습니다.

업로드 중 실제로 관찰한 이력 state=upload_youtube_start는 해당 upload_task_id의 대기로 처리하도록 계약과 회귀 검사를 갱신했습니다. URL이 있더라도 이 상태만으로 완료 판정하지 않습니다. 그 외 관찰하지 못한 초기 이벤트나 이력 상태는 계속 확인 필요로 중단합니다.

따라서 남은 단계의 실제 결과는 검증했지만, 버튼 한 번으로 끝까지 이어지는 **자동 정상 경로 전체의 실사이트 검증은 미완료**입니다. 이 경로의 초기 상태 계약은 새 실제 근거가 있어야 갱신할 수 있습니다. 실패 중단·중복 방지·응답 유실·재시작·복사 실패는 모의 검증 결과이며 실제 실패를 일부러 발생시킨 결과가 아닙니다. YouTube 자체의 현재 공개 범위·재생 가능 여부도 별도 미검증입니다.

## 초기화 중단 수정 (후속 조사·검증)

실제 API 재현에서 현재 작업의 첫 SSE 메시지가 접수 응답보다 8.628ms 먼저 도착했고 task_status 키 없이 progress=0, 오류 없음인 형태임을 확인했습니다. JSON·SSE 파싱 오류는 없었고 렌더는 정상 완료됐습니다. 기존 게시 관리자는 이 정상 초기화를 unrecognized로 분류해 중단했습니다. 기존 시안 추적기는 같은 메시지를 받더라도 다음 이벤트를 기다려 게시 경로에서만 문제가 드러났습니다.

게시 전용 정규화는 같은 영상·채널·작업 ID를 대조한 뒤, task_status 키 자체가 없고 bool이 아닌 숫자 progress=0이며 오류가 없는 관찰된 형태만 initializing으로 분류합니다. 명시적 null·빈 문자열·미지원 상태명, 0 이외 진행률에 상태가 누락된 경우는 계속 unrecognized입니다. 기존 시안 추적기의 공통 task_event 의미는 변경하지 않습니다. 초기화·100%·완료 이벤트만으로 다음 단계에 진입하지 않고 작업별 이력 검증을 유지합니다.

실제 이력 관찰로 다음 대기 상태를 단계별로 구분합니다. 파일·URL이 먼저 있어도 이 상태에서는 완료로 판정하지 않습니다.

| 현재 단계 | 확인된 대기 이력 |
| --- | --- |
| 렌더 | start, workerstart |
| 유튜브 정보 생성 | generate_metainfo_youtube_start, info |
| 업로드 | upload_youtube_start |

정보 저장 단계나 다른 작업 ID에 위 상태를 임의로 적용하지 않습니다. 처음 본 상태, 명시적 실패, 잘못된 JSON·SSE, 중복 접수·응답 유실·재시작에 대한 기존 중단 및 재전송 방지를 유지합니다.

실측 최소 fixture는 tests/fixtures/infox_publishing/initialization-2026-11-05.json이며, 실제 ID·미디어 참조를 대체한 관찰값임을 표시했습니다. 회귀 검사는 초기 메시지의 접수 응답 전 도착, 단계별 대기→완료 전이, 같은 작업의 초기화/진행/실패 배치, 잘못된 상태·진행률·영상·채널을 다룹니다. 브라우저 모의 검사는 이 생명주기 시나리오를 포함해 11개입니다.

후속 실제 화면 버튼 한 번의 전체 자동 검증 결과는 initialization-fix-live-verification.json에 별도로 기록합니다. 앞 절의 첫 실행 중단과 운영자 보완 이력은 당시 사실을 보존한 기록입니다.

2026-11-05 15:34의 후속 검증에서 새 전용 프로젝트 4198bdb5-8abf-40e3-9376-428250e8403f를 실제 생성하고 시안·저장 준비를 검증한 뒤, headless Edge의 실제 로컬 앱에서 게시 기준일 2026-11-04로 버튼을 한 번 눌렀습니다. 수동 단계 보완 없이 completed / uploaded_verified=true / clipboard_state=copied로 끝났습니다. 프로젝트·시안·렌더·정보 생성·정보 저장·업로드·클립보드 쓰기는 해당 흐름에서 각각 1회였습니다.

실제 렌더의 상태 키 없는 0% 메시지는 initializing으로 판정됐고 화면에서도 0→28→37→45→52→60→67→75→83%를 관찰했습니다. 렌더 start→workerstart→end, 정보 생성 generate_metainfo_youtube_start→info→end를 실제 이력에서 확인했습니다. 제목·설명·키워드 10개·같은 렌더 파일·InfoX 일부공개 기록을 재조회 검증했고, https://youtu.be/V52QVJ7aDPc 링크와 Windows 클립보드 읽기 값이 일치했습니다. 완료 후 새로고침에서도 게시 POST는 총 1회, 추가 변경·복사는 0회였습니다.

검사 결과는 Python 740개, 프런트엔드 103개, 빌드 및 브라우저 모의 시나리오 11개 통과입니다. 독립 검토에는 미해결 지적이 없습니다. 실제 정상 자동 경로는 이번 검증으로 확인했으며, 실제 실패를 일부러 만드는 실험과 YouTube 자체의 공개 범위·재생 확인은 여전히 범위 밖입니다. 새로 발생하는 미지원 상태의 무조건 허용이나 자동 재실행 기능은 추가하지 않았습니다.
