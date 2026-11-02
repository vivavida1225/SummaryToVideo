"""Durable one-shot admission and bounded task tracking, owned by the backend.

No raw remote payloads are logged or saved. Uncertain writes remain uncertain;
recovery only reads and never guesses an ID or repeats an external write.
"""

import asyncio
import hashlib
from contextlib import suppress

from .errors import InfoXError
from .run_models import RunError, RunRecord, now
from .service import creation_body
from .store import RunStore
from .tracking import task_event, verify_draft
from .asset_models import AssetAudit
from .assets import audit_assets
from .planner import prepare_plan
from .plan_models import ReplacementPlan, PreservationChecks
from .saving import save_plan
from .save_store import SAVE_STAGES


class RunManager:
    def __init__(self, store: RunStore, client_factory, *, tracking_timeout: float = 600,
                 retry_delay: float = 2, check_attempts: int = 10, connect_timeout: float = 15):
        self.store, self.client_factory = store, client_factory
        self.tracking_timeout, self.retry_delay = tracking_timeout, retry_delay
        self.check_attempts, self.connect_timeout = check_attempts, connect_timeout
        self.tasks: dict[str, asyncio.Task] = {}

    async def start(self, run_id: str, *, recovery: bool = False) -> RunRecord:
        record = self.store.get(run_id)
        if record.stage in SAVE_STAGES or record.asset_save.state not in ('not_started', 'saved_verified', 'unchanged_verified'):
            return await self.save(run_id)
        if record.stage in ('draft_ready', 'auditing_assets', 'assets_audited', 'selecting_favorites', 'replacement_planned', 'ready_for_editor', 'failed'):
            return record
        if record.asset_audit.state=='completed' and record.replacement_plan.state=='needs_attention':
            return await self.plan(run_id)
        if record.asset_audit.state == 'needs_attention':
            return await self.audit(run_id)
        if run_id not in self.tasks or self.tasks[run_id].done():
            self.tasks[run_id] = asyncio.create_task(self._execute(run_id, recovery=recovery))
        return record

    async def save(self, run_id: str) -> RunRecord:
        record = self.store.get(run_id)
        if record.asset_save.state in ('saved_verified', 'unchanged_verified'):
            return record
        if self.active(run_id):
            if record.stage in SAVE_STAGES:
                return record
            raise RunError('save_not_ready')
        claimed = self.store.begin_save(run_id, expected_version=record.version)
        self.tasks[run_id] = asyncio.create_task(self._save_execute(claimed))
        return claimed

    async def _save_execute(self, r):
        try:
            async with self.client_factory() as client:
                await save_plan(self.store, client, r)
        except asyncio.CancelledError:
            raise
        except RunError:
            return  # Durable intent remains for read-only reconciliation.
        except Exception:
            with suppress(RunError):
                self.store.fail_save(r.id, 'read_failed', expected_version=r.version)

    async def _finish_assets(self, client, r):
        audited = await self._audit_with_client(client, r)
        # New automatic runs continue through storage. Explicit audit/plan APIs
        # remain read-only; existing plans have a dedicated save action.
        if audited.replacement_plan.state in ('ready', 'not_needed'):
            claimed = self.store.begin_save(audited.id, expected_version=audited.version)
            return await save_plan(self.store, client, claimed)
        return audited

    async def audit(self, run_id: str) -> RunRecord:
        record = self.store.get(run_id)
        if self.active(run_id):
            if record.stage in ('draft_ready', 'auditing_assets', 'assets_audited'):
                return record
            raise RunError('draft_not_ready')
        claimed = self.store.begin_audit(run_id, expected_version=record.version)
        if not self.active(run_id):
            self.tasks[run_id] = asyncio.create_task(self._audit_execute(claimed))
        return claimed

    async def plan(self,run_id: str) -> RunRecord:
        record=self.store.get(run_id)
        if self.active(run_id):
            if record.stage in ('assets_audited','selecting_favorites','replacement_planned'):
                return record
            raise RunError('audit_required')
        claimed=self.store.begin_plan(run_id,expected_version=record.version)
        self.tasks[run_id]=asyncio.create_task(self._plan_execute(claimed))
        return claimed

    async def _plan_execute(self,r):
        try:
            async with self.client_factory() as client:
                await client.authenticate()
                await prepare_plan(self.store,client,r)
        except asyncio.CancelledError:
            raise
        except RunError:
            return
        except Exception as error:
            with suppress(RunError):
                code='authentication_required' if isinstance(error,InfoXError) and error.code in (
                    'auth_expired','permission_denied','configuration_error') else 'read_failed'
                self.store.record_plan(r.id,ReplacementPlan(**{**r.replacement_plan.model_dump(),
                    'state':'needs_attention','error_code':code,'checks':PreservationChecks(),
                    'result_fingerprint':None,'checked_at':now()}),expected_version=r.version)

    async def recover(self):
        try:
            records = self.store.resumable()
        except RunError:
            # InfoX storage must not prevent the existing narration app starting.
            # Protected InfoX endpoints still report its explicit safe 503 error.
            return
        for record in records:
            if record.stage in SAVE_STAGES:
                await self.save(record.id)
            elif record.stage == 'auditing_assets':
                await self.audit(record.id)
            elif record.stage=='selecting_favorites':
                await self.plan(record.id)
            else:
                await self.start(record.id, recovery=True)

    async def _audit_with_client(self, client, r):
        if r.stage != 'auditing_assets':
            r = self.store.begin_audit(r.id, expected_version=r.version)
        try:
            state = await client.video_state(r.video_id)
            if not verify_draft(r, state).ready:
                report = AssetAudit(state='needs_attention', audited_at=now(), error_code='draft_changed')
            else:
                report = audit_assets(state, template_id=r.snapshot.settings.template_id,
                    run_id=r.id, video_id=r.video_id, verified_replacements=r.favorite_replacements)
        except InfoXError as error:
            code = 'authentication_required' if error.code in ('auth_expired', 'permission_denied', 'configuration_error') else 'read_failed'
            report = AssetAudit(state='needs_attention', audited_at=now(), error_code=code)
        except Exception:
            report = AssetAudit(state='needs_attention', audited_at=now(), error_code='read_failed')
        recorded=self.store.record_audit(r.id, report, expected_version=r.version)
        if recorded.asset_audit.state=='completed' and (recorded.snapshot.settings.auto_replace
                or recorded.asset_audit.counts()['detected'] == 0):
            return await prepare_plan(self.store,client,recorded)
        return recorded

    async def _audit_execute(self, r):
        try:
            async with self.client_factory() as client:
                await client.authenticate()
                await self._audit_with_client(client, r)
        except asyncio.CancelledError:
            raise
        except RunError:
            return
        except Exception as error:
            with suppress(RunError):
                code = 'authentication_required' if isinstance(error, InfoXError) and error.code in (
                    'auth_expired', 'permission_denied', 'configuration_error') else 'read_failed'
                self.store.record_audit(r.id, AssetAudit(state='needs_attention', audited_at=now(), error_code=code),
                                        expected_version=r.version)

    async def wait(self, run_id: str):
        if task := self.tasks.get(run_id):
            await task

    async def close(self):
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()

    def active(self, run_id: str) -> bool:
        return run_id in self.tasks and not self.tasks[run_id].done()

    def _outcome(self, claimed, operation, outcome, external_id=None):
        current = claimed
        for attempt in range(3):
            receipt = getattr(current, operation)
            if receipt.intent_at != getattr(claimed, operation).intent_at:
                raise RunError('record_conflict')
            if receipt.state == 'accepted' and external_id == getattr(current, 'video_id' if operation == 'project' else 'task_id'):
                return current
            try:
                return self.store.record_outcome(claimed.id, operation, outcome, external_id=external_id,
                                                 expected_version=current.version)
            except RunError as error:
                if error.code != 'record_conflict' or attempt == 2:
                    raise
                # Reconciliation/progress may have advanced the record during HTTP.
                # Retry local CAS only, with the exact same durable request intent.
                current = self.store.get(claimed.id)

    def _update(self, r: RunRecord, stage: str | None = None, **changes) -> RunRecord:
        tracking = r.tracking.model_copy(update=changes)
        return self.store.track(r.id, tracking, stage=stage or r.stage, expected_version=r.version)

    async def _send(self, client, r: RunRecord, operation: str) -> RunRecord:
        claimed = self.store.begin_request(r.id, operation, expected_version=r.version)
        try:
            external_id = await (client.create_project(creation_body(r.snapshot)) if operation == 'project'
                                 else client.start_generation(r.video_id))
        except InfoXError as error:
            # Even HTTP errors may come from middleware after a side effect.
            # Preserve uncertainty rather than allowing a duplicate admission.
            self._outcome(claimed, operation, 'unknown')
            raise error
        return self._outcome(claimed, operation, 'accepted', external_id)

    async def _reconcile(self, client, r):
        if r.video_id:
            # Logs have no verified task mapping contract yet; useful reads, never proof of acceptance/completion.
            await client.video_state(r.video_id)
            await client.render_logs(r.video_id)
        code = 'project_acceptance_unverifiable' if r.project.state in ('intent_recorded', 'unknown') else 'generation_acceptance_unverifiable'
        return self._update(r, 'needs_attention', error_code=code, last_checked_at=now())

    async def _pump(self, client, video_id, queue, connected):
        last_id = ''
        while True:
            try:
                async for frame in client.events(video_id, last_event_id=last_id):
                    if frame.id:
                        last_id = frame.id  # Memory only; never persist arbitrary server strings.
                    body = frame.json()
                    if not isinstance(body, dict):
                        continue
                    if body.get('type') == 'connection' and body.get('status') == 'connected' and body.get('channel') == f'infox:{video_id}':
                        connected.set()
                    queue.put_nowait(('event', body))
            except InfoXError as error:
                if error.code in ('auth_expired', 'permission_denied'):
                    await queue.put(('auth', None))
                    return
            except asyncio.QueueFull:
                # Bounded memory: no silent loss followed by a success claim.
                await queue.put(('overflow', None))
                return
            queue.put_nowait(('disconnect', None))
            await asyncio.sleep(self.retry_delay)

    async def _check(self, client, r):
        for attempt in range(self.check_attempts):
            try:
                check = verify_draft(r, await client.video_state(r.video_id))
                r = self._update(r, 'checking_draft', draft=check, last_checked_at=now())
                if check.ready:
                    return self._update(r, 'draft_ready', error_code=None)
            except InfoXError as error:
                if error.code in ('auth_expired', 'permission_denied'):
                    raise
            if attempt + 1 < self.check_attempts:
                await asyncio.sleep(self.retry_delay)
        return self._update(r, 'needs_attention', error_code='draft_not_ready')

    async def _track(self, client, r, queue):
        if r.tracking.completion_seen:
            return await self._check(client, r)
        seen = set()
        while True:
            kind, body = await queue.get()
            if kind == 'auth':
                return self._update(r, 'needs_attention', error_code='authentication_required')
            if kind == 'overflow':
                return self._update(r, 'needs_attention', error_code='connection_interrupted')
            if kind == 'disconnect':
                r = self._update(r, 'generating', reconnects=r.tracking.reconnects + 1,
                                 error_code='connection_interrupted')
                # Recheck same video after interruption; readiness alone never implies completion.
                try:
                    check = verify_draft(r, await client.video_state(r.video_id))
                    r = self._update(r, draft=check, last_checked_at=now())
                except InfoXError as error:
                    if error.code in ('auth_expired', 'permission_denied'):
                        raise
                continue
            event = task_event(body, r.video_id, r.task_id)
            if event is None:
                continue
            digest = hashlib.sha256(str(body).encode()).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            if len(seen) > 2048:
                return self._update(r, 'needs_attention', error_code='connection_interrupted')
            r = self._update(r, 'checking_draft' if event.status == 'completed' else 'generating',
                task_status=event.status, progress=event.progress, last_event_at=now(),
                event_count=r.tracking.event_count + 1, error_code=None,
                completion_seen=event.status == 'completed')
            if event.status == 'failed':
                return self._update(r, 'failed', error_code='remote_task_failed')
            if event.status == 'completed':
                return await self._check(client, r)

    async def _execute(self, run_id, *, recovery=False):
        pump = None
        try:
            async with self.client_factory() as client:
                await client.authenticate()  # No side effect intent if credentials cannot be established.
                r = self.store.get(run_id)
                if r.requires_reconciliation:
                    await self._reconcile(client, r)
                    return
                if recovery and r.generation.state != 'accepted':
                    if r.video_id:
                        await client.video_state(r.video_id)
                    self._update(r, 'needs_attention', error_code='generation_acceptance_unverifiable', last_checked_at=now())
                    return
                if r.project.state == 'not_sent':
                    if r.stage != 'prepared':
                        r = self._update(r, 'prepared', error_code=None)
                    r = await self._send(client, r, 'project')
                if r.video_id is None:
                    return
                # Verify access to the already admitted video before any continuation.
                await client.video_state(r.video_id)
                if r.tracking.completion_seen:
                    ready = await self._check(client, r)
                    if ready.stage == 'draft_ready':
                        await self._finish_assets(client, ready)
                    return
                queue = asyncio.Queue(maxsize=512)
                connected = asyncio.Event()
                pump = asyncio.create_task(self._pump(client, r.video_id, queue, connected))
                if r.generation.state == 'not_sent':
                    await asyncio.wait_for(connected.wait(), self.connect_timeout)
                    if r.stage != 'project_created':
                        r = self._update(r, 'project_created', error_code=None)
                    r = await self._send(client, r, 'generation')
                async with asyncio.timeout(self.tracking_timeout):
                    ready = await self._track(client, r, queue)
                # Draft tracking's deadline must not cancel the next stage's read.
                if ready.stage == 'draft_ready':
                    await self._finish_assets(client, ready)
        except asyncio.CancelledError:
            raise  # Receipts/events already durable; shutdown never replays a write.
        except RunError:
            # A different owner/CAS or unavailable storage: don't overwrite their evidence.
            return
        except Exception as error:
            with suppress(RunError):
                r = self.store.get(run_id)
                if r.requires_reconciliation:
                    code = 'project_acceptance_unverifiable' if r.project.state in ('intent_recorded', 'unknown') else 'generation_acceptance_unverifiable'
                elif isinstance(error, InfoXError) and error.code in ('auth_expired', 'permission_denied', 'configuration_error'):
                    code = 'authentication_required'
                elif isinstance(error, TimeoutError):
                    code = 'completion_unconfirmed'
                else:
                    code = 'internal_error'
                self._update(r, 'needs_attention', error_code=code)
        finally:
            if pump:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
