# 렌더·재렌더 및 반복 업로드

InfoX에서 수정 내용을 저장한 다음 앱의 **새 회차 렌더·업로드 준비**를 누릅니다.
이전 게시 기준일을 기본값으로 채우며 실행 전에 날짜를 변경할 수 있습니다.
제목·설명과 일부공개 설정, 새로 조회한 예상 이용량을 확인한 뒤 **편집 완료 · 렌더 및 유튜브 업로드**를 누릅니다.
준비 취소는 원격 요청이나 회차 생성을 하지 않고 이전 결과 화면으로 돌아갑니다.

새 회차는 현재 InfoX 저장본으로 렌더 → 정보 생성 → 지정 제목·설명 저장 → 일부공개 업로드 → 링크 복사를 수행합니다.
과거 대본이나 편집 데이터를 다시 저장하지 않습니다. 수정 중인 미저장 내용은 앱에서 확인할 수 없으므로 실행 전에 저장하세요.
제목은 `YYMMDD AI 영상 시황`, 설명은 `YYYY년 M월 D일 AI 영상 시황`이며 생성 키워드를 유지합니다.
새 YouTube 링크가 생성되고 이전 영상과 링크는 유지됩니다. 이전 로컬 게시 회차는 결과 영역에서 펼쳐 볼 수 있습니다.
로컬 기록이 없어도 원격 완료 이력은 허용하되, 원격 이력을 소급해 로컬 성공 기록으로 만들지는 않습니다.

## 실행 제한

한 영상에서 진행 중인 로컬 회차는 하나입니다. 더블클릭·두 탭·같은 intent 재전송은 같은 회차를 중복 실행하지 않습니다.
새 회차 요청에는 사용자가 확인한 이전 회차 ID를 포함하며 최신 회차가 다르면 409로 거절합니다.
완료·실패·확인 필요 상태에서도 기존 전송 작업들의 종료를 읽기 조회로 확인하면 새 렌더부터 다시 시작할 수 있습니다.
진행 중 상태, 미지원 상태, 작업 ID 없는 접수 불명확, 검증할 수 없는 정보 저장은 계속 차단합니다.
관찰된 과거 `cancel`은 더 늦은 렌더 완료 시각이 확인된 경우만 허용합니다.
실패한 단계부터 이어 실행하거나 불명확한 요청을 다시 보내는 기능은 없습니다.

새로고침은 조회만 수행합니다. 서버 재시작 후 후속 변경 요청은 자동 재개하지 않습니다.
렌더 직전 저장 버전과 이력을 다시 확인하지만 InfoX에서 렌더 시작 이후의 외부 편집을 잠글 수는 없습니다.
과금은 매번 preview와 실제 요청 응답에 따릅니다. 한 번의 0크레딧 관찰을 일반적인 무료 정책으로 해석하지 않습니다.

## 저장과 API

기존 publications는 ID·원본 JSON·작업 ID·intent·수동 검증 참조를 보존하여 1회차로 이관합니다.
이관 전 `.runtime/infox-runs.before-publication-attempts.sqlite3` 백업을 만들며, 트랜잭션과 foreign_key_check가 통과해야 시작합니다.
이관 실패는 기존 DB를 유지하고 서비스 시작을 중단합니다. 새 회차는 이전 회차 ID와 영상별 회차 번호를 갖습니다.

- 기존 POST `/api/infox-runs/{run_id}/publications`: 최초 로컬 회차 생성 또는 기존 최신 기록 반환. 반복 POST로 새 회차를 만들지 않습니다.
- POST `/api/infox-runs/{run_id}/publications/rerender`: `intent_id`, `market_date`, `previous_publication_id`. 반환 202.
- GET `/api/infox-runs/{run_id}/publications`: 기존 `{publication}` 형식으로 최신 회차 반환.
- GET `/api/infox-runs/{run_id}/publications/history`: `{publications:[...]}`, 영상별 최신 회차순.
- GET `/api/infox-runs/{run_id}/publications/preview`: 기존 이용량 필드와 `can_start`, `blocked_reason`, `latest_publication_id`, `has_remote_render` 반환.
- 회차별 GET 및 copy-link API는 기존 형식을 유지합니다.

인증·origin 검사, 고정 원격 주소, TLS 검증, 변경 요청별 한 번 전송과 작업/파일 일치 검증은 유지합니다.
자동 검사는 실제 InfoX·YouTube 호출을 하지 않습니다.

## 근거와 검증

2026-11-05 사용자 승인 조사에서 동일 process_rendering API의 is_rerender=true 응답,
새 작업/파일, start→workerstart→end, 기존 이력 보존 및 새 렌더로 정보 생성·제목/설명 저장·재업로드를 확인했습니다.
tests/fixtures/infox_publishing/rerender-2026-11-05.json은 작업 ID·파일·텍스트를 대체한 관찰 fixture입니다.
구현 후의 일반 UI 통합 경로는 모의 API와 실제 로컬 브라우저로 검사하며 과거 운영자 조사와 구분합니다.
InfoX의 일부공개 완료 기록과 YouTube 자체의 현재 공개 범위·재생 가능 여부는 별개입니다.
