"""Atomic snapshots, idempotency and pre-send receipts. No external requests.

SQLite transactions coordinate multiple requests/processes. The record and all
intent aliases commit together; a failed commit never acknowledges a snapshot.
"""

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from .run_models import Receipt, RunError, RunRecord, Tracking, now
from .asset_models import AssetAudit
from .plan_models import PreservationChecks, ReplacementPlan, ReplacementChoice, SearchEvidence
from .save_store import SaveStoreMixin


class RunStore(SaveStoreMixin):
    def __init__(self, root: Path):
        self.root = root.resolve()

    @contextmanager
    def _transaction(self):
        connection = None
        try:
            base = self.root / '.runtime'
            path = base / 'infox-runs.sqlite3'
            # SQLite creates/removes journal files concurrently. On Windows,
            # resolve() can transiently return an extended-length path for a
            # disappearing journal; inspect links directly under the safe parent.
            if base.resolve() != base or any(p.is_symlink() or p.is_junction() for p in
                    (path, Path(str(path) + '-journal'), Path(str(path) + '-wal'), Path(str(path) + '-shm'))):
                raise RunError('storage_error', 503)
            base.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, timeout=5, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('BEGIN IMMEDIATE')
            connection.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, '
                'snapshot_key TEXT NOT NULL, record TEXT NOT NULL)')
            connection.execute('CREATE INDEX IF NOT EXISTS runs_snapshot ON runs(snapshot_key)')
            connection.execute('CREATE INDEX IF NOT EXISTS runs_job ON runs(job_id)')
            connection.execute('CREATE TABLE IF NOT EXISTS intents (id TEXT PRIMARY KEY, digest TEXT NOT NULL, '
                'run_id TEXT NOT NULL REFERENCES runs(id))')
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError):
            raise RunError('storage_error', 503) from None
        finally:
            if connection is not None:
                connection.close()  # Roll back any uncommitted transaction, including validation errors.

    @staticmethod
    def _decode(row) -> RunRecord:
        if row is None:
            raise RunError('run_not_found', 404)
        try:
            return RunRecord.model_validate_json(row['record'])
        except (ValidationError, ValueError):
            raise RunError('invalid_record', 503) from None

    @staticmethod
    def _run_id(run_id: str):
        if not re.fullmatch(r'[0-9a-f]{32}', run_id):
            raise RunError('run_not_found', 404)

    def _intent(self, conn, intent_id: str, digest: str) -> RunRecord | None:
        row = conn.execute('SELECT r.record, i.digest FROM intents i JOIN runs r ON r.id=i.run_id '
                           'WHERE i.id=?', (intent_id,)).fetchone()
        if row is not None and row['digest'] != digest:
            raise RunError('intent_conflict')
        return self._decode(row) if row is not None else None

    def by_intent(self, intent_id: str, digest: str) -> RunRecord | None:
        with self._transaction() as conn:
            return self._intent(conn, intent_id, digest)

    def get(self, run_id: str) -> RunRecord:
        self._run_id(run_id)
        with self._transaction() as conn:
            return self._decode(conn.execute('SELECT record FROM runs WHERE id=?', (run_id,)).fetchone())

    def latest(self, job_id: str) -> RunRecord | None:
        with self._transaction() as conn:
            row = conn.execute('SELECT record FROM runs WHERE job_id=? ORDER BY rowid DESC LIMIT 1', (job_id,)).fetchone()
            return self._decode(row) if row is not None else None

    def resumable(self) -> list[RunRecord]:
        with self._transaction() as conn:
            return [record for row in conn.execute('SELECT record FROM runs')
                    if (record := self._decode(row)).stage not in
                    ('prepared', 'draft_ready', 'assets_audited', 'replacement_planned', 'ready_for_editor', 'failed', 'needs_attention')]

    def begin_plan(self, run_id: str, *, expected_version: int) -> RunRecord:
        with self._edit(run_id, expected_version) as (record,data):
            if record.asset_save.state != 'not_started':
                raise RunError('save_in_progress')
            if (record.stage not in ('assets_audited','replacement_planned','selecting_favorites','needs_attention')
                    or record.asset_audit.state!='completed' or record.requires_reconciliation
                    or record.project.state!='accepted' or record.generation.state!='accepted'
                    or not record.video_id or not record.task_id or not record.tracking.completion_seen
                    or not record.tracking.draft.ready or record.tracking.task_status!='completed'):
                raise RunError('audit_required')
            data.update(stage='selecting_favorites',replacement_plan={**record.replacement_plan.model_dump(),
                'state':'selecting','error_code':None,'checks':PreservationChecks().model_dump(),
                'result_fingerprint':None,'checked_at':None})
        return self.get(run_id)

    def freeze_choices(self, run_id: str, choices: tuple[ReplacementChoice,...], *, source_fingerprint: str,
                       source_version: str, evidence: SearchEvidence, expected_version: int) -> RunRecord:
        with self._edit(run_id, expected_version) as (record,data):
            if record.stage!='selecting_favorites' or record.replacement_plan.state not in ('selecting','selected'):
                raise RunError('invalid_stage')
            targets=tuple(item for item in record.asset_audit.items if item.verdict=='detected')
            if not choices or tuple(choice.target for choice in choices)!=targets:
                raise RunError('invalid_selection')
            previous=record.replacement_plan
            if previous.selections and (previous.selections!=choices or previous.source_fingerprint!=source_fingerprint
                                        or previous.source_version!=source_version):
                raise RunError('selection_conflict')
            data['replacement_plan']=ReplacementPlan(state='selected',policy=previous.policy,
                source_version=source_version,source_fingerprint=source_fingerprint,selections=choices,
                search_evidence=evidence).model_dump()
        return self.get(run_id)

    def record_plan(self, run_id: str, plan: ReplacementPlan, *, expected_version: int) -> RunRecord:
        if not isinstance(plan,ReplacementPlan) or plan.state not in ('ready','not_needed','disabled','needs_attention'):
            raise RunError('invalid_plan',422)
        with self._edit(run_id, expected_version) as (record,data):
            if record.stage!='selecting_favorites' or record.replacement_plan.state not in ('selecting','selected'):
                raise RunError('invalid_stage')
            if plan.selections!=record.replacement_plan.selections:
                raise RunError('selection_conflict')
            data.update(stage='replacement_planned' if plan.state=='ready' else 'assets_audited'
                        if plan.state in ('not_needed','disabled') else 'needs_attention',replacement_plan=plan.model_dump())
        return self.get(run_id)

    def begin_audit(self, run_id: str, *, expected_version: int) -> RunRecord:
        with self._edit(run_id, expected_version) as (record, data):
            if record.asset_save.state != 'not_started':
                raise RunError('save_in_progress')
            if (record.stage not in ('draft_ready', 'auditing_assets', 'assets_audited', 'replacement_planned', 'needs_attention')
                    or record.requires_reconciliation or record.project.state != 'accepted'
                    or record.generation.state != 'accepted' or not record.video_id or not record.task_id
                    or not record.tracking.completion_seen or not record.tracking.draft.ready
                    or record.tracking.task_status != 'completed'):
                raise RunError('draft_not_ready')
            data.update(stage='auditing_assets', asset_audit=AssetAudit(state='running').model_dump())
            if record.replacement_plan.state!='not_started':
                data['replacement_plan']={**record.replacement_plan.model_dump(),'state':'needs_attention',
                    'error_code':'source_changed','checks':PreservationChecks().model_dump(),'result_fingerprint':None}
        return self.get(run_id)

    def record_audit(self, run_id: str, audit: AssetAudit, *, expected_version: int) -> RunRecord:
        if not isinstance(audit, AssetAudit) or audit.state not in ('completed', 'needs_attention'):
            raise RunError('invalid_audit', 422)
        with self._edit(run_id, expected_version) as (record, data):
            if record.stage != 'auditing_assets' or record.asset_audit.state != 'running':
                raise RunError('invalid_stage')
            data.update(stage='assets_audited' if audit.state == 'completed' else 'needs_attention',
                        asset_audit=audit.model_dump())
        return self.get(run_id)

    def track(self, run_id: str, tracking: Tracking, *, stage: str, expected_version: int) -> RunRecord:
        if stage not in ('prepared', 'project_created', 'generating', 'checking_draft', 'draft_ready', 'needs_attention', 'failed'):
            raise RunError('invalid_stage')
        with self._edit(run_id, expected_version) as (record, data):
            if record.asset_save.state != 'not_started' or record.stage in ('draft_ready', 'auditing_assets', 'assets_audited', 'selecting_favorites', 'replacement_planned', 'ready_for_editor', 'failed'):
                raise RunError('invalid_stage')
            if stage == 'prepared' and (record.project.state != 'not_sent' or record.generation.state != 'not_sent'):
                raise RunError('invalid_stage')
            if stage in ('generating', 'checking_draft', 'draft_ready') and (
                    record.generation.state != 'accepted' or not record.video_id or not record.task_id):
                raise RunError('invalid_stage')
            if stage == 'draft_ready' and (not tracking.completion_seen or not tracking.draft.ready):
                raise RunError('draft_not_ready')
            data.update(tracking=tracking.model_dump(), stage=stage)
        return self.get(run_id)

    def create(self, record: RunRecord, *, snapshot_key: str, intent_id: str,
               digest: str, new_run: bool) -> tuple[RunRecord, bool]:
        with self._transaction() as conn:
            existing = self._intent(conn, intent_id, digest)
            if existing is not None:
                return existing, True
            row = conn.execute('SELECT record FROM runs WHERE snapshot_key=? ORDER BY rowid DESC LIMIT 1',
                               (snapshot_key,)).fetchone()
            if row is not None:
                existing = self._decode(row)
                if new_run:
                    if existing.requires_reconciliation or existing.stage not in ('ready_for_editor', 'failed'):
                        raise RunError('run_in_progress', run_id=existing.id)
                else:
                    conn.execute('INSERT INTO intents VALUES (?, ?, ?)', (intent_id, digest, existing.id))
                    return existing, True
            conn.execute('INSERT INTO runs VALUES (?, ?, ?, ?)',
                (record.id, record.snapshot.job_id, snapshot_key, record.model_dump_json()))
            conn.execute('INSERT INTO intents VALUES (?, ?, ?)', (intent_id, digest, record.id))
            return record, False

    @contextmanager
    def _edit(self, run_id: str, expected_version: int):
        self._run_id(run_id)
        with self._transaction() as conn:
            record = self._decode(conn.execute('SELECT record FROM runs WHERE id=?', (run_id,)).fetchone())
            if type(expected_version) is not int or expected_version != record.version:
                raise RunError('record_conflict', run_id=run_id)
            data = record.model_dump()
            yield record, data
            data.update(version=record.version + 1, updated_at=now())
            updated = RunRecord.model_validate(data)
            conn.execute('UPDATE runs SET record=? WHERE id=?', (updated.model_dump_json(), run_id))

    def begin_request(self, run_id: str, operation: str, *, expected_version: int) -> RunRecord:
        if operation not in ('project', 'generation'):
            raise RunError('invalid_operation', 422)
        with self._edit(run_id, expected_version) as (record, data):
            if getattr(record, operation).state != 'not_sent':
                raise RunError('request_already_recorded', run_id=run_id)
            if (operation == 'project' and record.stage != 'prepared'
                    or operation == 'generation' and (record.stage != 'project_created' or not record.video_id)):
                raise RunError('invalid_stage', run_id=run_id)
            data[operation] = Receipt(state='intent_recorded', intent_at=now()).model_dump()
            data['stage'] = 'creating_project' if operation == 'project' else 'starting_generation'
        return self.get(run_id)

    def record_outcome(self, run_id: str, operation: str, outcome: str, *, expected_version: int,
                       external_id: str | None = None) -> RunRecord:
        if operation not in ('project', 'generation') or outcome not in ('accepted', 'unknown', 'rejected'):
            raise RunError('invalid_operation', 422)
        if (outcome == 'accepted' and (not isinstance(external_id, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', external_id))) or (outcome != 'accepted' and external_id is not None):
            raise RunError('invalid_external_id', 422)
        with self._edit(run_id, expected_version) as (record, data):
            previous = getattr(record, operation)
            if previous.state not in ('intent_recorded', 'unknown'):
                raise RunError('invalid_stage', run_id=run_id)
            data[operation] = Receipt(state=outcome, intent_at=previous.intent_at,
                acknowledged_at=now() if outcome != 'unknown' else None).model_dump()
            if outcome == 'accepted':
                data['video_id' if operation == 'project' else 'task_id'] = external_id
                data['stage'] = 'project_created' if operation == 'project' else 'generating'
            elif outcome == 'rejected':
                data['stage'] = 'failed'
        return self.get(run_id)
