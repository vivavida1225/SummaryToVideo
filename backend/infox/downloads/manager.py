import asyncio
import errno
from contextlib import suppress
from uuid import NAMESPACE_URL, uuid5

from ..errors import InfoXError
from ..publishing.tracking import file_fingerprint, history_logs
from ..run_models import RunError
from .files import downloads_folder, intact, write_text, file_io
from .models import ACTIVE
from .store import ensure_ready


def error_code(error):
    if isinstance(error, InfoXError):
        return error.code
    if isinstance(error, RunError):
        return error.code
    if isinstance(error, PermissionError):
        return 'permission_denied'
    if isinstance(error, OSError) and error.errno == errno.ENOSPC:
        return 'disk_full'
    if isinstance(error, FileNotFoundError):
        return 'source_missing'
    return 'download_failed'


class DownloadManager:
    def __init__(self, store, jobs, client_factory, *, folder_provider=downloads_folder):
        self.store, self.jobs, self.client_factory = store, jobs, client_factory
        self.folder_provider = folder_provider
        self.tasks = {}

    async def start(self, publication_id, intent_id, kind, *, automatic=False, retry_of=None):
        pub = self.store.publications.get(publication_id)
        ensure_ready(pub,kind,automatic=automatic,retry=self.store.get(retry_of) if retry_of else None)
        record, created = self.store.create(publication_id, intent_id, kind, automatic=automatic, retry_of=retry_of)
        if created:
            self.tasks[record.id] = asyncio.create_task(self._execute(record.id, retry_of))
        return record

    async def automatic(self, publication_id):
        existing = next((r for r in self.store.history(publication_id) if r.automatic),None)
        if existing is not None:
            return existing  # Never backfill historical video-only operations.
        return await self.start(publication_id, str(uuid5(NAMESPACE_URL, 'infox-auto-download-all:'+publication_id)),
                                'all', automatic=True)

    async def retry(self, id, intent_id):
        record = self.store.get(id)
        if record.state in ACTIVE:
            raise RunError('download_active')
        return await self.start(record.publication_id, intent_id, record.kind, retry_of=id)

    async def _execute(self, id, retry_of):
        record = self.store.get(id)
        items = list(record.items)
        try:
            if record.state == 'queued':
                while True:
                    claimed = self.store.activate_queued(id)
                    if claimed is None:
                        return
                    if claimed:
                        break
                    await asyncio.sleep(.2)
            self.store.update(id, state='running')
            try:
                folder = await asyncio.to_thread(self.folder_provider)
            except OSError:
                raise RunError('downloads_folder_unavailable') from None
            pub = self.store.publications.get(record.publication_id)
            run = self.store.runs.get(pub.run_id)
            previous = {item.kind:item for item in self.store.get(retry_of).items} if retry_of else {}
            for index, item in enumerate(items):
                try:
                    old = previous.get(item.kind)
                    if old and await asyncio.to_thread(intact, old):
                        items[index] = old
                    else:
                        items[index] = item.model_copy(update={'state':'downloading'})
                        self.store.update(id, items=tuple(items))
                        if item.kind == 'video':
                            async with self.client_factory() as client:
                                logs = history_logs(await client.render_logs(pub.video_id), pub.video_id)
                                log = next((x for x in logs if x['task_id'] == pub.render_task_id), None)
                                if not log or log['state'] != 'end' or file_fingerprint(log) != pub.render_file_fingerprint:
                                    raise RunError('render_file_mismatch')
                                url = await client.get_presigned_download_url(log['bucket'],log['file_name'],item.filename)
                                result = await client.download_rendered_video(url,log['bucket'],log['file_name'],folder,item.filename)
                        else:
                            text = run.snapshot.text if item.kind == 'script' else self.jobs.artifact(run.snapshot.job_id,'serialized.txt')
                            result = await file_io(write_text,folder,item.filename,text)
                        items[index] = item.model_copy(update={**result,'state':'completed','error_code':None})
                except Exception as error:
                    items[index] = item.model_copy(update={'state':'failed','error_code':error_code(error)})
                self.store.update(id, items=tuple(items))
            completed = sum(x.state == 'completed' for x in items)
            self.store.update(id, state='completed' if completed == len(items) else 'partial' if completed else 'failed')
        except asyncio.CancelledError:
            with suppress(Exception):
                self.store.update(id,state='needs_attention',error_code='server_restarted')
            raise
        except Exception as error:
            with suppress(Exception):
                self.store.update(id,state='failed',error_code=error_code(error))

    async def recover(self):
        for record in self.store.interrupted():
            self.store.update(record.id,state='needs_attention',error_code='server_restarted')

    async def close(self):
        for task in self.tasks.values():
            if not task.done(): task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)
        self.tasks.clear()
