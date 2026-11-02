"""Snapshot preparation only; no credentials, HTTP calls, or background sends."""

import json
import re
from uuid import uuid4

from ..config import MAX_INPUT_BYTES
from ..jobs import JobManager
from .run_models import PrepareRunInput, RunError, RunRecord, Scene, Snapshot, now, text_hash
from .store import RunStore


def creation_body(snapshot: Snapshot) -> dict:
    """Build phase 3's intended payload from the immutable snapshot, without sending."""
    settings = snapshot.settings.model_dump()
    video = {key: settings.pop(key) for key in ('title', 'description', 'template_id')}
    for key in ('auto_replace', 'replacement_search'):
        settings.pop(key)
    return {'video': video, 'context': {**settings, 'type': 'text',
        'data': {'disableAI': True, 'inputText': snapshot.text, 'stage1_model': {'title': '', 'summary': '',
            'scenes': [{'voiceover': scene.text, 'context': scene.text} for scene in snapshot.scenes]}},
        'user_prompt': '', 'useUserAsset': [], 'prompt_template_id': None}}


def _digest(value: dict) -> str:
    return text_hash(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')))


class RunService:
    def __init__(self, jobs: JobManager, store: RunStore):
        self.jobs, self.store = jobs, store

    def get(self, run_id: str) -> RunRecord:
        return self.store.get(run_id)

    def latest(self, job_id: str) -> RunRecord | None:
        return self.store.latest(job_id)

    def prepare(self, job_id: str, request: PrepareRunInput) -> tuple[RunRecord, bool]:
        digest = _digest({'job_id': job_id, **request.model_dump(mode='json', exclude={'intent_id'})})
        existing = self.store.by_intent(request.intent_id, digest)
        if existing is not None:
            return existing, True  # A response lost before a later edit still identifies the original request.
        job = self.jobs.get(job_id)
        if job['state'] not in ('completed', 'needs_review', 'failed'):
            raise RunError('job_not_ready')
        if job.get('revision', 0) != request.revision:
            raise RunError('revision_conflict')
        text = job.get('compressed')
        if not isinstance(text, str) or not text.strip():
            raise RunError('empty_script', 422)
        if len(text.encode('utf-8')) > MAX_INPUT_BYTES:
            raise RunError('script_too_large', 422)
        if (job['state'] != 'completed' or job.get('validation_issues')) and not request.review_confirmed:
            raise RunError('review_required')
        scenes = [Scene(line_number=i, scene_index=0, text=line)
                  for i, line in enumerate(re.split(r'\r\n|\r|\n', text), 1) if line.strip()]
        scenes = [s.model_copy(update={'scene_index': i}) for i, s in enumerate(scenes)]
        timestamp = now()
        snapshot = Snapshot(job_id=job_id, revision=request.revision, text=text, text_sha256=text_hash(text),
            scenes=tuple(scenes), settings=request.settings, confirmed_at=timestamp,
            review_confirmed=request.review_confirmed, generation_state=job['state'],
            validation_issues=tuple(job.get('validation_issues', [])), warnings=tuple(job.get('warnings', [])))
        key = _digest({'job_id': job_id, 'revision': request.revision, 'text_sha256': snapshot.text_sha256,
                       'settings': request.settings.model_dump(mode='json')})
        record = RunRecord(id=uuid4().hex, created_at=timestamp, updated_at=timestamp, snapshot=snapshot)
        # Synchronous validation + transaction: local async edit requests cannot interleave here.
        return self.store.create(record, snapshot_key=key, intent_id=request.intent_id,
                                 digest=digest, new_run=request.new_run)
