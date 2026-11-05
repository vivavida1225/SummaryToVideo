import asyncio
import secrets
import re
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import MAX_INPUT_BYTES, Settings
from .jobs import EditConflict, JobConflict, JobManager
from .infox.run_models import PrepareRunInput, RunError, public_record
from .infox.service import RunService
from .infox.store import RunStore
from .infox.manager import RunManager
from .infox.client import InfoXClient
from .infox.config import InfoXCredentials
from .infox.editor import EditorClaim, EditorUpdate
from .infox.publishing.models import PublishInput, RerenderInput, PublishTimeouts
from .infox.publishing.store import PublicationStore
from .infox.publishing.manager import PublicationManager
from .models import MODELS, model_candidates
from .myasset import MyassetError, fetch_myasset_source
from .storage import list_sources, source_path


class ModelInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str | None = None

    @field_validator('model')
    @classmethod
    def supported_model(cls, value):
        if value is not None:
            return model_candidates(value)[0]
        return value


class JobInput(ModelInput):
    html: str | None = Field(default=None, min_length=1, max_length=MAX_INPUT_BYTES)
    file_path: str | None = Field(default=None, min_length=1, max_length=1024)
    transcribe_numbers: bool = Field(default=False, strict=True)

    @model_validator(mode='after')
    def exactly_one_source(self):
        if (self.html is None) == (self.file_path is None):
            raise ValueError('HTML 또는 파일 경로 중 하나만 지정하세요.')
        return self


class CopyInput(BaseModel):
    stage: Literal['serialized', 'compressed']


class EditCompressedInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    text: str = Field(max_length=MAX_INPUT_BYTES)
    revision: int = Field(ge=0, strict=True)


class InfoXAuditInput(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class MyassetInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_date: date
    gubun: int = Field(default=30, strict=True, ge=0)

    @field_validator('base_date', mode='before')
    @classmethod
    def calendar_date(cls, value):
        if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
            raise ValueError('날짜는 YYYY-MM-DD 형식이어야 합니다.')
        return date.fromisoformat(value)


class MyassetSource(BaseModel):
    html: str
    source_url: str
    base_date: str
    gubun: int


def create_app(settings: Settings | None = None, *, clipboard=None, compressor_factory=None,
               instance_id: str | None = None, shutdown=None) -> FastAPI:
    settings = settings or Settings()
    manager = JobManager(settings, clipboard=clipboard, compressor_factory=compressor_factory)
    infox_runs = RunService(manager, RunStore(settings.root))
    infox_manager = RunManager(infox_runs.store, lambda: InfoXClient(InfoXCredentials.load(settings.root)))
    publish_timeouts = PublishTimeouts()
    infox_publications = PublicationManager(PublicationStore(infox_runs.store),
        lambda: InfoXClient(InfoXCredentials.load(settings.root), timeout=publish_timeouts.read,
                           connect_timeout=publish_timeouts.connect), manager.clipboard, timeouts=publish_timeouts)
    token = secrets.token_urlsafe(32)
    identity = instance_id or secrets.token_hex(16)

    @asynccontextmanager
    async def lifespan(_app):
        await infox_manager.recover()
        await infox_publications.recover()
        try:
            yield
        finally:
            await infox_publications.close()
            await infox_manager.close()
            await manager.close()

    app = FastAPI(title='Summary to Video', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.manager = manager
    app.state.infox_runs = infox_runs
    app.state.infox_manager = infox_manager
    app.state.infox_publications = infox_publications

    @app.middleware('http')
    async def local_guard(request: Request, call_next):
        host = request.headers.get('host', '')
        try:
            parsed = urlsplit('http://' + host)
            valid_host = parsed.hostname in ('127.0.0.1', 'localhost') and not parsed.username and not parsed.password
        except ValueError:
            valid_host = False
        if not valid_host:
            return JSONResponse({'detail': '로컬 앱 주소로 접속하세요.'}, status_code=403)
        origin = request.headers.get('origin')
        if (origin is not None and origin != 'http://' + host) or request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '외부 사이트에서는 이 앱을 호출할 수 없습니다.'}, status_code=403)
        if request.url.path.startswith('/api/'):
            if request.url.path not in ('/api/session', '/api/health'):
                if not secrets.compare_digest(request.headers.get('x-app-token', ''), token):
                    return JSONResponse({'detail': '앱 연결이 만료되었습니다. 화면을 새로고침하세요.'}, status_code=403)
            if request.method == 'POST':
                max_body = MAX_INPUT_BYTES + 4096
                content_length = request.headers.get('content-length', '')
                if content_length and (not content_length.isdigit() or int(content_length) > max_body):
                    return JSONResponse({'detail': '입력은 5 MiB 이하만 지원합니다.'}, status_code=413)
                chunks, size = [], 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > max_body:
                        return JSONResponse({'detail': '입력은 5 MiB 이하만 지원합니다.'}, status_code=413)
                    chunks.append(chunk)
                request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
        return response

    @app.exception_handler(JobConflict)
    async def conflict_handler(_request, exc):
        return JSONResponse({'detail': str(exc), 'job_id': exc.job_id}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, exc):
        if '/infox-runs' in _request.url.path or '/infox-publications' in _request.url.path:
            return JSONResponse({'detail': '대본 버전, 실행 식별자와 InfoX 설정 형식을 확인하세요.'}, status_code=422)
        if _request.url.path.endswith('/compressed'):
            return JSONResponse({'detail': '대본 문자열과 저장 버전을 확인하세요. 대본은 5 MiB 이하만 지원합니다.'}, status_code=422)
        if _request.url.path == '/api/sources/myasset':
            return JSONResponse({'detail': '날짜(YYYY-MM-DD)와 gubun(0 이상의 정수) 입력 형식을 확인하세요.'}, status_code=422)
        if any('model' in error['loc'] for error in exc.errors()):
            return JSONResponse({'detail': '지원하지 않는 모델입니다. 모델 목록에서 다시 선택하세요.'}, status_code=422)
        return JSONResponse({'detail': '입력 형식을 확인하세요. HTML 또는 파일 경로 중 하나만 지정해야 합니다.'}, status_code=422)

    @app.exception_handler(RunError)
    async def infox_run_error(_request, exc):
        messages = {
            'revision_conflict': '다른 화면에서 대본이 변경되었습니다. 수정 내용을 보관한 뒤 최신 대본을 불러오세요.',
            'review_required': '생성 당시 검토 사유를 확인하고 대본 사용에 동의해 주세요.',
            'intent_conflict': '이미 다른 내용으로 사용한 실행 요청입니다. 기존 전송본을 확인하세요.',
            'run_in_progress': '동일 대본의 실행이 진행 중입니다. 기존 실행을 확인하세요.',
            'empty_script': '비어 있지 않은 대본을 저장한 뒤 확정하세요.',
            'job_not_ready': '대본 생성이 끝난 뒤 전송본을 확정하세요.',
            'run_not_found': 'InfoX 실행 기록을 찾을 수 없습니다.',
            'publication_not_found': '게시 실행 기록을 찾을 수 없습니다.',
            'publication_exists': '이 영상의 게시 기록이 이미 있습니다. 기존 기록을 확인하세요.',
            'editor_not_ready': '영상 저장·검증을 완료한 뒤 실행하세요.',
            'upload_not_verified': '업로드 완료가 확인된 링크만 복사할 수 있습니다.',
            'copy_in_progress': '링크 복사 결과를 확인하는 중입니다.',
            'storage_error': 'InfoX 실행 기록을 읽거나 저장하지 못했습니다. 같은 요청으로 다시 확인하세요.',
            'draft_not_ready': '현재 작업의 완료 신호와 장면·음성·자막의 필수 데이터를 확인한 뒤 에셋을 검사할 수 있습니다.',
            'audit_required': '현재 영상의 기업 에셋 검사를 완료한 뒤 교체 계획을 만들 수 있습니다.',
        }
        return JSONResponse({'detail': messages.get(exc.code, 'InfoX 실행 기록과 설정을 확인하세요.'),
                             'code': exc.code, 'run_id': exc.run_id}, status_code=exc.status_code)

    @app.get('/api/health')
    async def health():
        return {'app': 'summary-to-video', 'ready': True, 'instance_id': identity}

    @app.get('/api/session')
    async def session():
        return {'token': token, 'model': settings.model, 'models': MODELS,
                'configured_keys': [number for number, _ in settings.keys()],
                'configured_keys_by_provider': {
                    provider: [number for number, _ in settings.keys(provider)]
                    for provider in ('gemini', 'openai')}}

    @app.get('/api/files')
    async def files():
        return {'files': list_sources(settings.root)}

    @app.post('/api/clipboard/read')
    async def read_clipboard():
        try:
            return await asyncio.to_thread(manager.clipboard.read)
        except (OSError, ValueError):
            raise HTTPException(503, '클립보드를 읽지 못했습니다. 다시 읽기를 누르거나 HTML을 직접 붙여넣으세요.') from None

    @app.post('/api/sources/myasset', response_model=MyassetSource)
    async def myasset_source(data: MyassetInput):
        try:
            return await fetch_myasset_source(data.base_date, data.gubun)
        except MyassetError as exc:
            raise HTTPException(exc.status_code, exc.detail) from None

    @app.post('/api/jobs', status_code=202)
    async def start_job(data: JobInput):
        if data.html is not None and (not data.html.strip() or len(data.html.encode('utf-8')) > MAX_INPUT_BYTES):
            raise HTTPException(422, '비어 있지 않은 5 MiB 이하 HTML을 입력하세요.')
        if data.file_path:
            try:
                path = source_path(settings.root, data.file_path)
                if not path.is_file():
                    raise ValueError('선택한 파일이 없습니다. 목록을 새로고침하세요.')
            except (ValueError, OSError) as exc:
                raise HTTPException(422, str(exc)) from None
        return manager.start(html=data.html, file_path=data.file_path, model=data.model,
                             transcribe_numbers=data.transcribe_numbers)

    def find_job(job_id):
        try:
            return manager.get(job_id)
        except (ValueError, OSError, KeyError):
            raise HTTPException(404, '작업 기록을 찾을 수 없습니다.') from None

    def infox_view(record):
        return {**public_record(record), 'tracking_active': infox_manager.active(record.id)}

    @app.get('/api/jobs/{job_id}')
    async def get_job(job_id: str):
        return find_job(job_id)

    @app.post('/api/jobs/{job_id}/infox-runs')
    async def prepare_infox_run(job_id: str, data: PrepareRunInput):
        find_job(job_id)
        record, reused = infox_runs.prepare(job_id, data)
        return JSONResponse(infox_view(record), status_code=200 if reused else 201)

    @app.get('/api/jobs/{job_id}/infox-runs')
    async def latest_infox_run(job_id: str):
        find_job(job_id)
        record = infox_runs.latest(job_id)
        return {'run': infox_view(record) if record is not None else None}

    @app.get('/api/infox-runs/{run_id}')
    async def get_infox_run(run_id: str):
        return infox_view(infox_runs.get(run_id))

    @app.get('/api/infox-runs/{run_id}/publications/preview')
    async def preview_infox_publication(run_id: str):
        return await infox_publications.preview(run_id)

    @app.post('/api/infox-runs/{run_id}/publications', status_code=202)
    async def start_infox_publication(run_id: str, data: PublishInput):
        return await infox_publications.start(run_id, data)

    @app.post('/api/infox-runs/{run_id}/publications/rerender', status_code=202)
    async def rerender_infox_publication(run_id: str, data: RerenderInput):
        return await infox_publications.start(run_id, data)

    @app.get('/api/infox-runs/{run_id}/publications/history')
    async def infox_publication_history(run_id: str):
        return {'publications': infox_publications.store.history(run_id)}

    @app.get('/api/infox-runs/{run_id}/publications')
    async def latest_infox_publication(run_id: str):
        return {'publication': infox_publications.store.for_run(run_id)}

    @app.get('/api/infox-publications/{publication_id}')
    async def get_infox_publication(publication_id: str):
        return infox_publications.store.get(publication_id)

    @app.post('/api/infox-publications/{publication_id}/copy-link')
    async def copy_infox_publication_link(publication_id: str, data: InfoXAuditInput):
        return await infox_publications.copy_link(publication_id, manual=True)

    @app.post('/api/infox-runs/{run_id}/start', status_code=202)
    async def start_infox_run(run_id: str):
        return infox_view(await infox_manager.start(run_id))

    @app.get('/api/infox-runs/{run_id}/editor-attempts')
    async def latest_editor_attempt(run_id: str):
        return {'attempt': infox_runs.store.latest_editor(run_id)}

    @app.post('/api/infox-runs/{run_id}/editor-attempts')
    async def claim_editor_attempt(run_id: str, data: EditorClaim):
        return infox_runs.store.claim_editor(run_id, data)

    @app.post('/api/infox-runs/{run_id}/editor-attempts/{attempt_id}')
    async def update_editor_attempt(run_id: str, attempt_id: str, data: EditorUpdate):
        return infox_runs.store.update_editor(run_id, attempt_id, data)

    @app.post('/api/infox-runs/{run_id}/audit', status_code=202)
    async def audit_infox_run(run_id: str, data: InfoXAuditInput | None = None):
        return infox_view(await infox_manager.audit(run_id))

    @app.post('/api/infox-runs/{run_id}/replacement-plan', status_code=202)
    async def plan_infox_replacements(run_id: str, data: InfoXAuditInput | None = None):
        return infox_view(await infox_manager.plan(run_id))

    @app.post('/api/infox-runs/{run_id}/save-assets', status_code=202)
    async def save_infox_replacements(run_id: str, data: InfoXAuditInput | None = None):
        return infox_view(await infox_manager.save(run_id))

    @app.post('/api/jobs/{job_id}/retry', status_code=202)
    async def retry_job(job_id: str, data: ModelInput | None = None):
        find_job(job_id)
        try:
            return manager.retry(job_id, model=data.model if data else None)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post('/api/jobs/{job_id}/compressed')
    async def edit_compressed(job_id: str, data: EditCompressedInput):
        find_job(job_id)
        try:
            return manager.update_compressed(job_id, data.text, revision=data.revision)
        except EditConflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError:
            raise HTTPException(503, '수정한 대본을 저장하지 못했습니다. 입력 내용을 유지한 채 다시 저장하세요.') from None

    @app.post('/api/jobs/{job_id}/copy')
    async def copy_result(job_id: str, data: CopyInput):
        find_job(job_id)
        try:
            await manager.copy_result(job_id, data.stage)
            return {'ok': True}
        except FileNotFoundError:
            raise HTTPException(404, '아직 결과가 생성되지 않았습니다.') from None
        except OSError:
            raise HTTPException(503, '클립보드가 사용 중입니다. 잠시 후 복사 버튼을 다시 누르세요.') from None

    @app.get('/api/jobs/{job_id}/artifacts/{name}')
    async def download(job_id: str, name: str):
        find_job(job_id)
        try:
            text = manager.artifact(job_id, name)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except FileNotFoundError:
            raise HTTPException(404, '아직 결과가 생성되지 않았습니다.') from None
        return Response(text.encode('utf-8'), media_type='text/plain; charset=utf-8',
                        headers={'Content-Disposition': f'attachment; filename="{name}"'})

    @app.post('/api/shutdown')
    async def shutdown_server():
        if shutdown:
            asyncio.get_running_loop().call_later(0.2, shutdown)
        return {'ok': True}

    dist = settings.root / 'frontend/dist'
    if (dist / 'assets').is_dir():
        app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')

    @app.get('/')
    async def index():
        if not (dist / 'index.html').is_file():
            return JSONResponse({'detail': '화면 빌드가 없습니다. start.cmd로 실행하세요.'}, status_code=503)
        return FileResponse(dist / 'index.html')

    return app
