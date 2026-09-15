import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import MAX_INPUT_BYTES, Settings
from .jobs import JobConflict, JobManager
from .models import MODELS, model_candidates
from .storage import list_sources, source_path


class ModelInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str | None = None

    @field_validator('model')
    @classmethod
    def supported_model(cls, value):
        if value is not None:
            model_candidates(value)
        return value


class JobInput(ModelInput):
    html: str | None = Field(default=None, min_length=1, max_length=MAX_INPUT_BYTES)
    file_path: str | None = Field(default=None, min_length=1, max_length=1024)

    @model_validator(mode='after')
    def exactly_one_source(self):
        if (self.html is None) == (self.file_path is None):
            raise ValueError('HTML 또는 파일 경로 중 하나만 지정하세요.')
        return self


class CopyInput(BaseModel):
    stage: Literal['serialized', 'compressed']


def create_app(settings: Settings | None = None, *, clipboard=None, compressor_factory=None,
               instance_id: str | None = None, shutdown=None) -> FastAPI:
    settings = settings or Settings()
    manager = JobManager(settings, clipboard=clipboard, compressor_factory=compressor_factory)
    token = secrets.token_urlsafe(32)
    identity = instance_id or secrets.token_hex(16)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        await manager.close()

    app = FastAPI(title='Summary to Video', docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.manager = manager

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
        if any('model' in error['loc'] for error in exc.errors()):
            return JSONResponse({'detail': '지원하지 않는 모델입니다. 모델 목록에서 다시 선택하세요.'}, status_code=422)
        return JSONResponse({'detail': '입력 형식을 확인하세요. HTML 또는 파일 경로 중 하나만 지정해야 합니다.'}, status_code=422)

    @app.get('/api/health')
    async def health():
        return {'app': 'summary-to-video', 'ready': True, 'instance_id': identity}

    @app.get('/api/session')
    async def session():
        return {'token': token, 'model': settings.model, 'models': MODELS,
                'configured_keys': [number for number, _ in settings.keys()]}

    @app.get('/api/files')
    async def files():
        return {'files': list_sources(settings.root)}

    @app.post('/api/clipboard/read')
    async def read_clipboard():
        try:
            return await asyncio.to_thread(manager.clipboard.read)
        except (OSError, ValueError):
            raise HTTPException(503, '클립보드를 읽지 못했습니다. 다시 읽기를 누르거나 HTML을 직접 붙여넣으세요.') from None

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
        return manager.start(html=data.html, file_path=data.file_path, model=data.model)

    def find_job(job_id):
        try:
            return manager.get(job_id)
        except (ValueError, OSError, KeyError):
            raise HTTPException(404, '작업 기록을 찾을 수 없습니다.') from None

    @app.get('/api/jobs/{job_id}')
    async def get_job(job_id: str):
        return find_job(job_id)

    @app.post('/api/jobs/{job_id}/retry', status_code=202)
    async def retry_job(job_id: str, data: ModelInput | None = None):
        find_job(job_id)
        try:
            return manager.retry(job_id, model=data.model if data else None)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

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
