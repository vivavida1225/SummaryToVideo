import asyncio
import time
import math
from contextlib import suppress

from ..editor import editor_ready
from ..errors import InfoXError
from ..run_models import RunError
from ..save_models import remote_version
from .models import TERMINAL, PublishTimeouts
from .tracking import history_logs, normalize_publish_event, parse_youtube_share_url, verify_publish_stage
from .preflight import history_block_reason, previous_block_reason


class PublishStop(Exception):
    def __init__(self, code, *, failed=False):
        self.code, self.failed = code, failed


class PublicationManager:
    def __init__(self, store, client_factory, clipboard, *, timeouts=None, poll_interval=2, downloads=None):
        self.store, self.client_factory, self.clipboard = store, client_factory, clipboard
        self.timeouts = timeouts or PublishTimeouts()
        self.poll_interval = poll_interval
        self.tasks = {}
        self.downloads = downloads

    def active(self, id):
        return id in self.tasks and not self.tasks[id].done()

    async def start(self, run_id, input):
        r, created = self.store.create_or_get(run_id, input)
        # Only the creator owns this execution, even across independent managers.
        # A crash after creation is recovered as stopped, never a new sender.
        if created:
            r = self.store.transition(r.id, r.version, {'stage': 'preflighting'})
            self.tasks[r.id] = asyncio.create_task(self._execute(r.id))
        return r

    async def close(self):
        tasks = list(self.tasks.values())
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()

    async def _read(self, request):
        return await asyncio.wait_for(request, self.timeouts.read)

    def _transition(self, r, **changes):
        return self.store.transition(r.id, r.version, changes)

    def _stop(self, id, code, *, failed=False):
        r = self.store.get(id)
        if r.stage in TERMINAL:
            return r
        return self._transition(r, stage='failed' if failed else 'needs_attention',
                                failed_stage=r.stage, error_code=code)

    async def preview(self, run_id):
        run = self.store.run_store.get(run_id)
        if not editor_ready(run):
            raise RunError('editor_not_ready')
        latest = self.store.for_run(run_id)
        context = {'can_start': False, 'blocked_reason': None,
                   'latest_publication_id': latest.id if latest else None, 'has_remote_render': False}
        try:
            async with self.client_factory() as client:
                async with asyncio.timeout(self.timeouts.preflight):
                    _, logs = await self._inspect(client, run, context=context)
                    context['has_remote_render'] = any(log['state'] != 'videosgenerationend' for log in logs)
                    result = await self._read(client.render_charge_preview(run.video_id))
            charge = charge_summary(result)
            return {**charge, **context, 'can_start': charge['available'],
                    'blocked_reason': None if charge['available'] else 'preflight_unconfirmed'}
        except PublishStop as error:
            return {'available': False, 'charged': None, **context, 'blocked_reason': error.code}
        except Exception:
            return {'available': False, 'charged': None, **context, 'blocked_reason': 'preflight_unconfirmed'}

    async def _inspect(self, client, run, ignore_id=None, context=None):
        state = await self._read(client.video_state(run.video_id))
        if not isinstance(state, dict) or str(state.get('id')) != run.video_id or state.get('can_edit') is not True:
            raise PublishStop('preflight_unconfirmed')
        try:
            version = remote_version(state.get('updated_at'))
            history = await self._read(client.render_logs(run.video_id))
            logs = history_logs(history, run.video_id)
        except ValueError:
            raise PublishStop('preflight_unconfirmed') from None
        if context is not None:
            context['has_remote_render'] = any(log['state'] != 'videosgenerationend' for log in logs)
        # Check local acceptance uncertainty even if the remote list looks idle.
        records = [old for old in self.store.history(run.id) if old.id != ignore_id]
        reason = previous_block_reason(records, history) or history_block_reason(logs, run.task_id)
        if reason:
            raise PublishStop(reason)
        return version, logs

    async def _preflight(self, client, r):
        run = self.store.run_store.get(r.run_id)
        if not editor_ready(run) or run.video_id != r.video_id:
            raise PublishStop('preflight_unconfirmed')
        version, logs = await self._inspect(client, run, r.id)
        charge = charge_summary(await self._read(client.render_charge_preview(r.video_id)))
        if not charge['available']:
            raise PublishStop('preflight_unconfirmed')
        return self._transition(r, snapshot={**r.snapshot.model_dump(), 'remote_updated_at': version,
            'baseline_remote_task_ids': tuple(sorted(log['task_id'] for log in logs))})

    async def _pump(self, client, video_id, queue, connected, health):
        last_id = ''
        for attempt in range(3):
            try:
                async for frame in client.events(video_id, last_event_id=last_id):
                    body = frame.json()
                    if not isinstance(body, dict):
                        raise PublishStop('stream_unrecognized')
                    last_id = frame.id or last_id
                    if body.get('type') == 'connection' and body.get('status') == 'connected' and body.get('channel') == f'infox:{video_id}':
                        connected.set()
                        continue
                    if body.get('channel', f'infox:{video_id}') != f'infox:{video_id}':
                        continue
                    try:
                        queue.put_nowait((time.monotonic(), body))
                    except asyncio.QueueFull:
                        health['error'] = 'stream_overflow'
                        return
            except asyncio.CancelledError:
                raise
            except PublishStop as error:
                health['error'] = error.code
                return
            except InfoXError as error:
                if error.code not in ('timeout', 'network_error', 'server_error', 'rate_limited'):
                    health['error'] = ('authentication_required' if error.code in
                        ('auth_expired', 'permission_denied', 'configuration_error') else 'stream_unrecognized')
                    return
            except Exception:
                health['error'] = 'stream_unrecognized'
                return
            finally:
                connected.clear()
            if attempt < 2:
                await asyncio.sleep(self.poll_interval)
        health['error'] = 'stream_unavailable'

    def _events(self, r, queue, health):
        if health.get('error'):
            raise PublishStop(health['error'])
        progress = r.progress
        active = (r.render_task_id if r.stage == 'rendering' else r.metadata_task_id
            if r.stage in ('generating_metadata', 'saving_metadata') else r.upload_task_id)
        while not queue.empty():
            timestamp, body = queue.get_nowait()
            if time.monotonic() - timestamp > self.timeouts.preflight:
                raise PublishStop('stream_unavailable')
            for task_id in (r.render_task_id, r.metadata_task_id, r.upload_task_id):
                if not task_id:
                    continue
                event = normalize_publish_event(body, r.video_id, task_id)
                if event is None:
                    continue
                if event.status == 'failed':
                    raise PublishStop('remote_task_failed', failed=True)
                if event.status == 'unrecognized':
                    raise PublishStop('stream_unrecognized')
                if task_id == active and event.progress is not None:
                    progress = max(progress or 0, event.progress)
        return self._transition(r, progress=progress) if progress != r.progress else r

    async def _send(self, client, r, operation, request, queue, connected, health):
        r = self._events(r, queue, health)
        if not connected.is_set():
            raise PublishStop('stream_unavailable')
        r = self.store.claim_operation(r.id, operation, r.version)
        try:
            task_id = await self._read(request())
        except BaseException:
            # Cancellation/timeout can occur after acceptance, including shutdown.
            with suppress(RunError):
                self.store.finish_operation(r.id, operation, 'unknown', expected_version=r.version)
            raise
        try:
            return self.store.finish_operation(r.id, operation, 'accepted',
                None if operation == 'metadata_update' else task_id, expected_version=r.version)
        except RunError as error:
            if error.code != 'invalid_external_id':
                raise
            self.store.finish_operation(r.id, operation, 'unknown', expected_version=r.version)
            raise PublishStop('acceptance_unknown') from None

    async def _verify(self, client, r, queue, health):
        while True:
            r = self._events(r, queue, health)
            history = await self._read(client.render_logs(r.video_id))
            # Give a failure already delivered by the stream precedence over
            # a concurrently completed history read.
            await asyncio.sleep(0)
            r = self._events(r, queue, health)
            result = verify_publish_stage(r, history)
            if result.status == 'completed':
                operation = {'rendering': 'render', 'generating_metadata': 'metadata_generate',
                             'saving_metadata': 'metadata_update', 'uploading': 'upload'}[r.stage]
                receipts = r.receipts.model_dump()
                receipts[operation]['read_verified'] = True
                evidence = dict(result.evidence)
                if 'keywords' in evidence: evidence['keywords'] = tuple(evidence['keywords'])
                return self._transition(r, receipts=receipts, **evidence)
            if result.status in ('unknown', 'failed'):
                raise PublishStop(result.error_code or 'history_unrecognized', failed=result.status == 'failed')
            await asyncio.sleep(self.poll_interval)

    async def _execute(self, id):
        pump = None
        try:
            async with self.client_factory() as client:
                r = self.store.get(id)
                async with asyncio.timeout(self.timeouts.preflight):
                    r = await self._preflight(client, r)
                    queue, connected, health = asyncio.Queue(maxsize=512), asyncio.Event(), {}
                    pump = asyncio.create_task(self._pump(client, r.video_id, queue, connected, health))
                    try:
                        await asyncio.wait_for(connected.wait(), self.timeouts.connect)
                    except TimeoutError:
                        raise PublishStop(health.get('error', 'stream_unavailable')) from None
                    # Latest saved version immediately before first mutation.
                    state = await self._read(client.video_state(r.video_id))
                    if (str(state.get('id')) != r.video_id or state.get('can_edit') is not True
                            or state.get('updated_at') != r.snapshot.remote_updated_at):
                        raise PublishStop('remote_changed')
                    # Re-read history too: another editor may have started rendering.
                    logs = history_logs(await self._read(client.render_logs(r.video_id)), r.video_id)
                    reason = history_block_reason(logs, self.store.run_store.get(r.run_id).task_id)
                    if reason:
                        raise PublishStop(reason)
                    if tuple(sorted(log['task_id'] for log in logs)) != r.snapshot.baseline_remote_task_ids:
                        raise PublishStop('existing_publication')
                async with asyncio.timeout(self.timeouts.render):
                    r = self._transition(r, stage='starting_render', progress=None)
                    r = await self._send(client, r, 'render', lambda: client.start_render(r.video_id), queue, connected, health)
                    r = self._transition(r, stage='rendering')
                    r = await self._verify(client, r, queue, health)
                # Local downloads have separate state and cannot gate publication.
                if self.downloads is not None:
                    with suppress(Exception):
                        await self.downloads.automatic(r.id)
                async with asyncio.timeout(self.timeouts.metadata):
                    r = self._transition(r, stage='generating_metadata', progress=None)
                    r = await self._send(client, r, 'metadata_generate',
                        lambda: client.generate_youtube_metadata(r.video_id, r.render_task_id), queue, connected, health)
                    r = await self._verify(client, r, queue, health)
                async with asyncio.timeout(self.timeouts.save):
                    r = self._transition(r, stage='saving_metadata', progress=None)
                    r = await self._send(client, r, 'metadata_update', lambda: client.update_youtube_metadata(
                        r.video_id, r.metadata_task_id, title=r.snapshot.title, description=r.snapshot.description,
                        keywords=list(r.keywords)), queue, connected, health)
                    r = await self._verify(client, r, queue, health)
                async with asyncio.timeout(self.timeouts.upload):
                    r = self._transition(r, stage='starting_upload', progress=None)
                    r = await self._send(client, r, 'upload',
                        lambda: client.upload_youtube(r.video_id, r.metadata_task_id), queue, connected, health)
                    r = self._transition(r, stage='uploading')
                    r = await self._verify(client, r, queue, health)
                await self.copy_link(r.id)
        except asyncio.CancelledError:
            raise
        except RunError:
            # CAS conflict or storage failure: preserve the durable owner's state.
            return
        except Exception as error:
            with suppress(RunError):
                r = self.store.get(id)
                uncertain = any(receipt.state in ('intent_recorded', 'unknown')
                                for receipt in (r.receipts.render, r.receipts.metadata_generate,
                                                r.receipts.metadata_update, r.receipts.upload))
                code = (error.code if isinstance(error, PublishStop) else 'acceptance_unknown' if uncertain
                    else 'authentication_required' if isinstance(error, InfoXError) and error.code in ('auth_expired', 'permission_denied', 'configuration_error')
                    else 'stage_timeout' if isinstance(error, TimeoutError) else 'internal_error')
                self._stop(id, code, failed=isinstance(error, PublishStop) and error.failed)
        finally:
            if pump:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)

    async def copy_link(self, publication_id, *, manual=False):
        r = self.store.get(publication_id)
        if not r.uploaded_verified or not r.share_url:
            raise RunError('upload_not_verified')
        try:
            video_id, url = parse_youtube_share_url(r.share_url)
            if video_id != r.youtube_video_id:
                raise ValueError('mismatch')
        except ValueError:
            raise RunError('upload_not_verified') from None
        if manual:
            if r.stage not in ('completed', 'clipboard_failed'):
                raise RunError('copy_in_progress')
        elif r.clipboard_state != 'not_started' or r.stage != 'uploading':
            return r
        r = self._transition(r, stage='copying_link', clipboard_state='intent_recorded')
        try:
            await asyncio.to_thread(self.clipboard.write, url)
        except Exception:
            return self._transition(r, stage='clipboard_failed', clipboard_state='failed', error_code='clipboard_error')
        return self._transition(r, stage='completed', clipboard_state='copied', error_code=None)

    async def recover(self):
        try:
            records = self.store.all()
        except RunError:
            return
        for r in records:
            if r.stage in TERMINAL or self.active(r.id):
                continue
            try:
                if r.stage in ('rendering', 'generating_metadata', 'saving_metadata', 'uploading'):
                    async with self.client_factory() as client:
                        result = verify_publish_stage(r, await self._read(client.render_logs(r.video_id)))
                    if result.status == 'completed' and r.stage == 'uploading':
                        r = self._transition(r, **result.evidence)
                if r.uploaded_verified:
                    self._transition(r, stage='clipboard_failed', clipboard_state='unknown', error_code='server_restarted')
                else:
                    self._stop(r.id, 'server_restarted')
            except Exception:
                with suppress(RunError):
                    self._stop(r.id, 'server_restarted')


def charge_summary(result):
    if not isinstance(result, dict) or result.get('error') or type(result.get('will_charge')) is not bool:
        return {'available': False, 'charged': None}
    summary = {'available': True, 'charged': result['will_charge']}
    for source, target in (('credits', 'credits'), ('duration_sec', 'duration_seconds')):
        value = result.get(source)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            summary[target] = value
    return summary
