import asyncio
import copy
import time
import uuid
from datetime import datetime, timezone

from .clipboard import WindowsClipboard
from .compression import CompressionError, Compressor
from .config import Settings
from .serializer import serialize_html
from .storage import ResultStore, read_source


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobConflict(Exception):
    def __init__(self, job_id):
        self.job_id = job_id
        super().__init__('이미 진행 중인 작업이 있습니다.')


class JobManager:
    def __init__(self, settings: Settings, *, clipboard=None, compressor_factory=None):
        self.settings = settings
        self.clipboard = clipboard if clipboard is not None else WindowsClipboard()
        self.compressor_factory = compressor_factory or (lambda: Compressor(settings.prompt_path, settings.keys(), model=settings.model))
        self.store = ResultStore(settings.root)
        self.jobs, self.tasks, self.started = {}, {}, {}
        self.active_id = None

    def start(self, *, html=None, file_path=None, snapshot=None, parent_id=None) -> dict:
        if self.active_id:
            raise JobConflict(self.active_id)
        job_id = datetime.now().strftime('%Y%m%d_%H%M%S_%f_') + uuid.uuid4().hex[:8]
        job = dict(id=job_id, parent_id=parent_id, state='queued', source=file_path or ('이전 직렬화 결과' if parent_id else '클립보드 / 직접 입력'),
                   created_at=now(), elapsed_seconds=0, attempt=0, max_attempts=3, key_number=None,
                   retry_at=None, serialized=None, compressed=None, scene_count=None, body_char_count=None,
                   warnings=[], error=None, output_dir=f'outputs/{job_id}', events=[], model=self.settings.model)
        self.jobs[job_id] = job
        self.started[job_id] = time.monotonic()
        self.active_id = job_id
        self.tasks[job_id] = asyncio.create_task(self._execute(job_id, html, file_path, snapshot))
        return self.get(job_id)

    def get(self, job_id: str) -> dict:
        if job_id not in self.jobs:
            self.jobs[job_id] = self.store.load(job_id)
        job = copy.deepcopy(self.jobs[job_id])
        if job['state'] not in ('completed', 'failed') and job_id in self.started:
            job['elapsed_seconds'] = round(time.monotonic() - self.started[job_id], 1)
        return job

    async def wait(self, job_id: str):
        if job_id in self.tasks:
            await self.tasks[job_id]

    async def close(self):
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    def retry(self, job_id: str) -> dict:
        original = self.get(job_id)
        if original['state'] != 'failed' or not original['serialized']:
            raise ValueError('직렬화 결과가 보관된 실패 작업만 다시 시도할 수 있습니다.')
        return self.start(snapshot=original['serialized'], parent_id=job_id)

    def artifact(self, job_id: str, name: str) -> str:
        if name not in {'serialized.txt', 'compressed.txt'}:
            raise ValueError('허용되지 않은 결과 파일입니다.')
        text = self.get(job_id)[name.removesuffix('.txt')]
        if not text:
            raise FileNotFoundError('아직 결과가 생성되지 않았습니다.')
        return text

    async def copy_result(self, job_id: str, stage: str):
        text = self.artifact(job_id, stage + '.txt')
        await asyncio.to_thread(self.clipboard.write, text)

    def _event(self, job_id: str, state: str, *, persist=True, **fields):
        job = self.jobs[job_id]
        message = fields.pop('message', None)
        job.update(state=state, **fields)
        if message:
            job['events'].append({'at': now(), 'message': message})
        job['elapsed_seconds'] = round(time.monotonic() - self.started[job_id], 1)
        if persist:
            self.store.metadata(job)

    async def _copy(self, job_id: str, stage: str):
        name = '직렬화' if stage == 'serialized' else '압축'
        self._event(job_id, 'copying_' + stage, message=f'{name} 결과를 클립보드에 복사합니다.')
        try:
            await self.copy_result(job_id, stage)
            self.jobs[job_id]['events'].append({'at': now(), 'message': f'{name} 결과 자동 복사 완료'})
        except (OSError, ValueError):
            self.jobs[job_id]['warnings'].append(f'{name} 결과의 클립보드 자동 복사에 실패했습니다. 결과의 복사 버튼을 다시 누르세요.')

    async def _execute(self, job_id, html, file_path, snapshot):
        job = self.jobs[job_id]
        try:
            self._event(job_id, 'serializing', persist=False,
                        message='HTML을 장면별 평문으로 변환합니다.' if snapshot is None else '보관된 직렬화 결과를 불러옵니다.')
            if snapshot is None:
                source = read_source(self.settings.root, file_path) if file_path else html
                serialized = serialize_html(source or '')
            else:
                serialized = snapshot
            job['serialized'] = serialized
            job['scene_count'] = serialized.count('\n===\n') + 1
            self.store.write_text(job_id, 'serialized.txt', serialized)
            if snapshot is None:
                await self._copy(job_id, 'serialized')
            compressor = self.compressor_factory()
            result = await compressor.run(serialized,
                lambda state, **fields: self._event(job_id, state, **fields),
                lambda attempt, raw: self.store.write_text(job_id, f'invalid_response_{attempt}.txt', raw))
            job.update(compressed=result.text, body_char_count=result.body_char_count)
            job['warnings'].extend(result.warnings)
            self.store.write_text(job_id, 'compressed.txt', result.text)
            await self._copy(job_id, 'compressed')
            self._event(job_id, 'completed', retry_at=None, message='1분 압축이 완료되었습니다.')
        except asyncio.CancelledError:
            job.update(state='failed', error='서버 종료로 작업이 중단되었습니다.', retry_at=None)
        except OSError:
            job.update(state='failed', error='파일을 읽거나 저장하지 못했습니다. 경로, 쓰기 권한, 디스크 공간을 확인하세요.', retry_at=None)
        except (ValueError, CompressionError) as exc:
            job.update(state='failed', error=self.settings.redact(str(exc)), retry_at=None)
        except Exception:
            job.update(state='failed', error='작업 중 예상하지 못한 오류가 발생했습니다. 입력과 설정을 확인하고 다시 시도하세요.', retry_at=None)
        finally:
            job['elapsed_seconds'] = round(time.monotonic() - self.started[job_id], 1)
            if job['error']:
                job['events'].append({'at': now(), 'message': job['error']})
            try:
                self.store.metadata(job)
            except (OSError, ValueError):
                job['warnings'].append('실행 기록을 파일로 저장하지 못했습니다. 화면의 결과를 다운로드하세요.')
            self.active_id = None
