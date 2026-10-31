# Python 3.14 가상 환경 복구 및 런처 수정

## 원인과 조치

기존 Python 3.12 가상 환경에 런처가 Python 3.14.7의 `venv --upgrade`를 적용하면서 3.12용 네이티브 패키지가 남았다. pip가 설치된 패키지 버전만 보고 재설치를 생략하여 `pydantic_core._pydantic_core` 로딩이 실패했다.

- 런처는 `pyvenv.cfg`의 버전·기본 Python 경로와 실제 실행 Python 버전을 확인한다.
- 기준과 다르거나 실행할 수 없는 환경은 `venv --clear`로 재생성하고 패키지를 설치한다. 정상 환경은 재사용한다.
- 기존 혼합 환경은 `.runtime/venv-backup-20261031-232908`에 보관했다. 실제 `.venv`는 프로젝트 전용 Python 3.14.7로 새로 생성했다.
- 전역 Python, `.env`, 입력 자료와 기존 출력 자료는 변경하지 않았다. 유료 생성 API 호출은 수행하지 않았다.

## 검증 결과

| 검사 | 결과 |
| --- | --- |
| 런처 회귀 테스트 | 수정 전 5개 실패·1개 성공, 수정 후 6개 성공. 버전 불일치·버전 누락·경로 불일치·실행 불가·환경 없음은 재생성하고, 정상 환경은 재사용함을 확인 |
| 실제 `install.cmd --no-pause` | Python 3.14.7 패키지 설치와 Node 24.21.0 프런트엔드 빌드 완료, 종료 코드 0 |
| 설치 재실행 | 종료 코드 0, `pyvenv.cfg` 수정 시각이 유지되어 정상 환경을 재생성하지 않음을 확인 |
| 필수 모듈 | FastAPI·Uvicorn·Google GenAI·truststore·OpenAI·백엔드 서버 import 성공 |
| 네이티브 패키지 | `pydantic_core`의 cp314 바이너리 로딩 성공, 현재 `.venv`에 cp312 `.pyd` 파일 0개 |
| 의존성 | `pip check` 통과 |
| 전체 Python 테스트 | Python 3.14.7에서 421 passed, 의존성 deprecation 경고 3건 |
| 프런트엔드 테스트 | 프로젝트 Node 24.21.0에서 49 passed |
| 실제 런처 수명 주기 | 앱 시작·중복 실행 재사용·정상 종료·점유 포트 회피 통과. 검사 후 서버 종료 |
| 변경 검사 | `git diff --check` 통과 |

Python 설치가 성공한 첫 실행에서는 Node 압축 해제 폴더 이동 중 접근 거부가 한 차례 발생했다. 재실행과 그다음 설치 실행은 모두 성공했다. Node 설치 코드는 변경하지 않았으며 해당 일시 오류의 원인은 확정하지 않았다.

## 재현 명령

```powershell
.\install.cmd --no-pause
.\.venv\Scripts\python.exe -m pytest tests -q
$env:PATH = "$PWD\.tools\node;$env:PATH"
.\.tools\node\npm.cmd --prefix frontend test
.\.venv\Scripts\python.exe -m tests.windows_smoke
```

검증 기록은 `.runtime/launcher.log`, `.runtime/python314-tests.log`, `.runtime/python314-frontend-tests.log`에 남아 있다. 커밋·푸시는 수행하지 않았다.
