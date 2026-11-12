"""Browser observations, not proof of remote login or playback.

A unique automatic claim and CAS navigate intent prevent concurrent owners from
opening repeatedly. A crash between intent, browser assignment and acknowledgement
is deliberately uncertain; restored pages can only inspect and explicitly reopen.
"""

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .run_models import RunError, now, public_record


class EditorClaim(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    attempt_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    mode: Literal['automatic', 'manual']
    tab_state: Literal['created', 'blocked']


class EditorUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    expected_version: int = Field(ge=0)
    state: Literal['navigate_intent', 'navigation_requested', 'closed', 'failed', 'unknown',
                   'login_required', 'ready_reported']


def editor_ready(r):
    from .review import publication_ready
    return publication_ready(r)


class EditorStoreMixin:
    @staticmethod
    def _editor_table(conn):
        conn.execute('CREATE TABLE IF NOT EXISTS editor_attempts '
                     '(id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), mode TEXT NOT NULL, record TEXT NOT NULL)')
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS editor_one_auto ON editor_attempts(run_id) WHERE mode='automatic'")

    def latest_editor(self, run_id):
        self.get(run_id)
        with self._transaction() as conn:
            self._editor_table(conn)
            row = conn.execute('SELECT record FROM editor_attempts WHERE run_id=? ORDER BY rowid DESC LIMIT 1', (run_id,)).fetchone()
            return json.loads(row['record']) if row else None

    def claim_editor(self, run_id, claim: EditorClaim):
        self._run_id(run_id)
        with self._transaction() as conn:
            r = self._decode(conn.execute('SELECT record FROM runs WHERE id=?', (run_id,)).fetchone())
            from .review import publication_active
            if publication_active(conn, r.video_id):
                raise RunError('publication_active')
            self._editor_table(conn)
            row = conn.execute('SELECT run_id, record FROM editor_attempts WHERE id=?', (claim.attempt_id,)).fetchone()
            if row:
                if row['run_id'] != run_id:
                    raise RunError('intent_conflict')
                # A repeated claim is evidence lookup, never renewed ownership.
                return {'attempt': json.loads(row['record']), 'claimed': False}
            if claim.mode == 'automatic':
                row = conn.execute("SELECT record FROM editor_attempts WHERE run_id=? AND mode='automatic'", (run_id,)).fetchone()
                if row:
                    return {'attempt': json.loads(row['record']), 'claimed': False}
            elif not editor_ready(r):
                raise RunError('editor_not_ready')
            attempt = dict(id=claim.attempt_id, run_id=run_id, mode=claim.mode, version=0,
                           state='waiting' if claim.tab_state == 'created' else 'blocked',
                           tab_state=claim.tab_state, video_id=None, editor_url=None,
                           editor_status='unconfirmed', created_at=now(), updated_at=now())
            conn.execute('INSERT INTO editor_attempts VALUES (?, ?, ?, ?)',
                         (claim.attempt_id, run_id, claim.mode, json.dumps(attempt)))
            return {'attempt': attempt, 'claimed': True}

    def update_editor(self, run_id, attempt_id, update: EditorUpdate):
        self._run_id(run_id)
        with self._transaction() as conn:
            self._editor_table(conn)
            r = self._decode(conn.execute('SELECT record FROM runs WHERE id=?', (run_id,)).fetchone())
            row = conn.execute('SELECT record FROM editor_attempts WHERE id=? AND run_id=?', (attempt_id, run_id)).fetchone()
            if not row:
                raise RunError('run_not_found', 404)
            attempt = json.loads(row['record'])
            if attempt['version'] != update.expected_version:
                raise RunError('record_conflict')
            state, previous = update.state, attempt['state']
            if state == 'navigate_intent':
                from .review import publication_active
                if publication_active(conn, r.video_id):
                    raise RunError('publication_active')
                if not editor_ready(r):
                    raise RunError('editor_not_ready')
                if previous != 'waiting' or attempt['tab_state'] != 'created':
                    raise RunError('invalid_stage')
                attempt.update(video_id=r.video_id, editor_url=public_record(r)['editor_url'])
            elif state == 'navigation_requested':
                if previous != 'navigate_intent':
                    raise RunError('invalid_stage')
            elif state in ('login_required', 'ready_reported'):
                if previous not in ('navigation_requested', 'login_required', 'ready_reported'):
                    raise RunError('invalid_stage')
                attempt['editor_status'] = 'login_required_user_reported' if state == 'login_required' else 'ready_user_reported'
            elif previous not in ('waiting', 'navigate_intent', 'navigation_requested', 'login_required', 'ready_reported'):
                raise RunError('invalid_stage')
            attempt.update(state=state, version=attempt['version'] + 1, updated_at=now())
            conn.execute('UPDATE editor_attempts SET record=? WHERE id=?', (json.dumps(attempt), attempt_id))
            return attempt
