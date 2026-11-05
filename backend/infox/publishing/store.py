import hashlib
import json
from datetime import date
from uuid import uuid4

from ..editor import editor_ready
from ..run_models import RunError, now
from .models import TERMINAL, PublicationRecord, PublishSnapshot, PublishReceipt, format_publish_metadata

OPERATIONS = {
    'render': ('starting_render', 'render_task_id'),
    'metadata_generate': ('generating_metadata', 'metadata_task_id'),
    'metadata_update': ('saving_metadata', None),
    'upload': ('starting_upload', 'upload_task_id'),
}


class PublicationStore:
    def __init__(self, run_store):
        self.run_store = run_store

    @staticmethod
    def _tables(conn):
        conn.execute('CREATE TABLE IF NOT EXISTS publications (id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE '
                     'REFERENCES runs(id), video_id TEXT NOT NULL UNIQUE, record TEXT NOT NULL)')
        conn.execute('CREATE TABLE IF NOT EXISTS publication_intents (intent_id TEXT PRIMARY KEY, digest TEXT NOT NULL, '
                     'publication_id TEXT NOT NULL REFERENCES publications(id))')

    @staticmethod
    def _decode(row):
        if row is None:
            raise RunError('publication_not_found', 404)
        try:
            return PublicationRecord.model_validate_json(row['record'])
        except ValueError:
            raise RunError('invalid_record', 503) from None

    def create_or_get(self, run_id, input):
        digest = hashlib.sha256(json.dumps([run_id, input.market_date]).encode()).hexdigest()
        self.run_store._run_id(run_id)
        with self.run_store._transaction() as conn:
            self._tables(conn)
            row = conn.execute('SELECT p.record,i.digest FROM publication_intents i JOIN publications p '
                'ON p.id=i.publication_id WHERE i.intent_id=?', (input.intent_id,)).fetchone()
            if row is not None:
                if row['digest'] != digest:
                    raise RunError('intent_conflict')
                return self._decode(row), False
            run = self.run_store._decode(conn.execute('SELECT record FROM runs WHERE id=?', (run_id,)).fetchone())
            row = conn.execute('SELECT record FROM publications WHERE run_id=? OR video_id=?',
                               (run_id, run.video_id)).fetchone()
            if row:
                record = self._decode(row)
                if record.run_id != run_id:
                    raise RunError('publication_exists')
                created = False
            else:
                if not editor_ready(run):
                    raise RunError('editor_not_ready')
                title, description = format_publish_metadata(date.fromisoformat(input.market_date))
                record = PublicationRecord(id=uuid4().hex, run_id=run_id, video_id=run.video_id,
                    snapshot=PublishSnapshot(market_date=input.market_date, title=title, description=description),
                    created_at=now(), updated_at=now())
                conn.execute('INSERT INTO publications VALUES (?,?,?,?)',
                             (record.id, run_id, run.video_id, record.model_dump_json()))
                created = True
            conn.execute('INSERT INTO publication_intents VALUES (?,?,?)', (input.intent_id, digest, record.id))
            return record, created

    def get(self, id):
        with self.run_store._transaction() as conn:
            self._tables(conn)
            return self._decode(conn.execute('SELECT record FROM publications WHERE id=?', (id,)).fetchone())

    def for_run(self, run_id):
        self.run_store.get(run_id)
        with self.run_store._transaction() as conn:
            self._tables(conn)
            row = conn.execute('SELECT record FROM publications WHERE run_id=?', (run_id,)).fetchone()
            return self._decode(row) if row else None

    def all(self):
        with self.run_store._transaction() as conn:
            self._tables(conn)
            return [self._decode(row) for row in conn.execute('SELECT record FROM publications')]

    def _mutate(self, id, expected_version, edit):
        with self.run_store._transaction() as conn:
            self._tables(conn)
            r = self._decode(conn.execute('SELECT record FROM publications WHERE id=?', (id,)).fetchone())
            if expected_version is not None and (type(expected_version) is not int or expected_version != r.version):
                raise RunError('record_conflict')
            changes = edit(r)
            if r.stage in TERMINAL and changes.get('stage', r.stage) != r.stage:
                manual_copy = (r.uploaded_verified and r.stage in ('completed', 'clipboard_failed')
                               and changes.get('stage') == 'copying_link'
                               and changes.get('clipboard_state') == 'intent_recorded')
                if not manual_copy:
                    raise RunError('record_conflict')
            if set(changes) & {'id', 'run_id', 'video_id', 'version', 'created_at'}:
                raise RunError('invalid_operation')
            updated = PublicationRecord.model_validate({**r.model_dump(), **changes,
                'version': r.version + 1, 'updated_at': now()})
            conn.execute('UPDATE publications SET record=? WHERE id=?', (updated.model_dump_json(), id))
            return updated

    def transition(self, id, expected_version, changes):
        return self._mutate(id, expected_version, lambda _: changes)

    def claim_operation(self, id, operation, expected_version):
        if operation not in OPERATIONS:
            raise RunError('invalid_operation')
        def edit(r):
            if r.stage != OPERATIONS[operation][0] or getattr(r.receipts, operation).state != 'not_sent':
                raise RunError('request_already_recorded')
            receipts = r.receipts.model_dump()
            receipts[operation] = PublishReceipt(state='intent_recorded', intent_at=now()).model_dump()
            return {'receipts': receipts}
        return self._mutate(id, expected_version, edit)

    def finish_operation(self, id, operation, outcome, task_id=None, *, expected_version=None):
        if operation not in OPERATIONS or outcome not in ('accepted', 'unknown', 'rejected'):
            raise RunError('invalid_operation')
        def edit(r):
            old = getattr(r.receipts, operation)
            if old.state != 'intent_recorded' or r.stage != OPERATIONS[operation][0]:
                raise RunError('invalid_stage')
            field = OPERATIONS[operation][1]
            if (outcome == 'accepted' and field and not task_id) or (not field and task_id) or (outcome != 'accepted' and task_id):
                raise RunError('invalid_external_id')
            if task_id and (task_id in r.snapshot.baseline_remote_task_ids or
                           task_id in (r.render_task_id, r.metadata_task_id, r.upload_task_id)):
                raise RunError('invalid_external_id')
            receipts = r.receipts.model_dump()
            receipts[operation] = PublishReceipt(state=outcome, intent_at=old.intent_at,
                acknowledged_at=now(), response_verified=outcome == 'accepted').model_dump()
            return {'receipts': receipts, **({field: task_id} if field and task_id else {})}
        return self._mutate(id, expected_version, edit)
