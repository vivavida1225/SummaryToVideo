import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from backend.config import Settings
from backend.infox.run_models import PrepareRunInput, ProductionSettings, RunError
from backend.infox.service import RunService, creation_body
from backend.infox.store import RunStore
from backend.jobs import JobManager
from tests.test_jobs import setup


def request(revision=0, **kwargs):
    return PrepareRunInput(intent_id=uuid4().hex, revision=revision, **kwargs)


def test_snapshot_uses_latest_edit_preserves_lines_settings_and_diagnostics(tmp_path, tiny_html, compressed):
    async def scenario():
        jobs, _, _ = setup(tmp_path, compressed)
        job_id = jobs.start(html=tiny_html)['id']
        await jobs.wait(job_id)
        jobs.jobs[job_id].update(state='needs_review', validation_issues=['원래 생성 사유'])
        text = '  첫 줄 😀  \n\n \n둘째 줄\n'
        jobs.update_compressed(job_id, text, revision=0)
        service = RunService(jobs, RunStore(tmp_path))
        with pytest.raises(RunError, match='review_required'):
            service.prepare(job_id, request(1))
        record, reused = service.prepare(job_id, request(1, review_confirmed=True,
            settings=ProductionSettings(title='오늘 시황')))
        assert not reused and record.stage == 'prepared'
        snap = record.snapshot
        assert snap.text == text and snap.revision == 1
        assert snap.text_sha256 == hashlib.sha256(text.encode()).hexdigest()
        assert [(s.line_number, s.scene_index, s.text) for s in snap.scenes] == [
            (1, 0, '  첫 줄 😀  '), (4, 1, '둘째 줄')]
        body = creation_body(snap)
        assert body['context']['data']['inputText'] == text
        assert body['context']['data']['stage1_model']['scenes'] == [
            {'voiceover': '  첫 줄 😀  ', 'context': '  첫 줄 😀  '}, {'voiceover': '둘째 줄', 'context': '둘째 줄'}]
        assert body['video'] == {'title': '오늘 시황', 'description': '', 'template_id': 1786}
        assert body['context']['voice_speed'] == '1.0'
        assert body['context']['voice_id'] == 'e9d18b28-937c-42ae-8fe0-de92517c233f'
        assert body['context']['data']['disableAI'] is True
        jobs.update_compressed(job_id, '다음 편집', revision=1)
        assert service.get(record.id).snapshot.text == text
        assert jobs.get(job_id)['state'] == 'needs_review'
        assert list(snap.validation_issues) == ['원래 생성 사유']
        with pytest.raises((AttributeError, TypeError)):
            snap.scenes.append(snap.scenes[0])
        assert (tmp_path / 'outputs' / job_id / 'compressed.txt').read_text(encoding='utf-8') == compressed
    asyncio.run(scenario())


def test_idempotency_survives_restart_lost_response_and_later_edit(tmp_path, tiny_html, compressed):
    async def scenario():
        jobs, _, _ = setup(tmp_path, compressed)
        job_id = jobs.start(html=tiny_html)['id']
        await jobs.wait(job_id)
        first = request()
        service = RunService(jobs, RunStore(tmp_path))
        record, _ = service.prepare(job_id, first)
        jobs.update_compressed(job_id, '나중 수정', revision=0)
        restored = RunService(JobManager(Settings(tmp_path)), RunStore(tmp_path))
        repeated, reused = restored.prepare(job_id, first)
        assert reused and repeated.id == record.id and repeated.snapshot.text == compressed
        with pytest.raises(RunError, match='intent_conflict'):
            restored.prepare(job_id, first.model_copy(update={'revision': 1}))
        next_record, _ = restored.prepare(job_id, request(1))
        assert next_record.id != record.id and next_record.snapshot.text == '나중 수정'
        duplicate, reused = restored.prepare(job_id, request(1))
        assert reused and duplicate.id == next_record.id
    asyncio.run(scenario())


def test_revision_empty_and_running_inputs_do_not_create_runs(tmp_path, tiny_html, compressed):
    async def scenario():
        jobs, _, _ = setup(tmp_path, compressed)
        job_id = jobs.start(html=tiny_html)['id']
        await jobs.wait(job_id)
        service = RunService(jobs, RunStore(tmp_path))
        with pytest.raises(RunError, match='revision_conflict'):
            service.prepare(job_id, request(99))
        jobs.update_compressed(job_id, '\n \n', revision=0)
        with pytest.raises(RunError, match='empty_script'):
            service.prepare(job_id, request(1))
        jobs.jobs[job_id]['state'] = 'requesting'
        with pytest.raises(RunError, match='job_not_ready'):
            service.prepare(job_id, request(1))
        assert service.latest(job_id) is None
    asyncio.run(scenario())


def test_concurrent_different_clicks_create_only_one_record(tmp_path, tiny_html, compressed):
    async def scenario():
        jobs, _, _ = setup(tmp_path, compressed)
        job_id = jobs.start(html=tiny_html)['id']
        await jobs.wait(job_id)
        services = [RunService(jobs, RunStore(tmp_path)) for _ in range(4)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda svc: svc.prepare(job_id, request()), services))
        assert len({r.id for r, _ in results}) == 1
        assert sum(not reused for _, reused in results) == 1
    asyncio.run(scenario())


def test_write_intents_and_external_ids_restore_without_resending(tmp_path, tiny_html, compressed):
    async def scenario():
        jobs, _, _ = setup(tmp_path, compressed)
        job_id = jobs.start(html=tiny_html)['id']
        await jobs.wait(job_id)
        store = RunStore(tmp_path)
        record, _ = RunService(jobs, store).prepare(job_id, request())
        pending = store.begin_request(record.id, 'project', expected_version=0)
        assert pending.project.state == 'intent_recorded' and pending.requires_reconciliation
        restored = RunStore(tmp_path)
        with pytest.raises(RunError, match='request_already_recorded'):
            restored.begin_request(record.id, 'project', expected_version=1)
        accepted = restored.record_outcome(record.id, 'project', 'accepted', external_id='test-video', expected_version=1)
        assert accepted.video_id == 'test-video' and accepted.stage == 'project_created'
        started = restored.begin_request(record.id, 'generation', expected_version=2)
        unknown = restored.record_outcome(record.id, 'generation', 'unknown', expected_version=started.version)
        assert RunStore(tmp_path).get(record.id).requires_reconciliation
        assert unknown.video_id == 'test-video'
        complete = restored.record_outcome(record.id, 'generation', 'accepted', external_id='test-task', expected_version=unknown.version)
        assert complete.stage == 'generating' and complete.task_id == 'test-task'
        assert not complete.requires_reconciliation
        with pytest.raises(RunError, match='record_conflict'):
            restored.begin_request(record.id, 'generation', expected_version=0)
    asyncio.run(scenario())


def test_new_intent_cannot_bypass_unresolved_existing_request(tmp_path, tiny_html, compressed):
    async def scenario():
        jobs, _, _ = setup(tmp_path, compressed)
        job_id = jobs.start(html=tiny_html)['id']
        await jobs.wait(job_id)
        store = RunStore(tmp_path)
        service = RunService(jobs, store)
        record, _ = service.prepare(job_id, request())
        store.begin_request(record.id, 'project', expected_version=0)
        with pytest.raises(RunError, match='run_in_progress'):
            service.prepare(job_id, request(new_run=True))
        same, reused = service.prepare(job_id, request())
        assert reused and same.id == record.id
    asyncio.run(scenario())


@pytest.mark.parametrize('changes', [{'revision': True}, {'revision': -1}, {'intent_id': '../secret'},
    {'settings': {'voice_speed': 1.0}}, {'settings': {'template_id': '1786'}},
    {'settings': {'access_token': 'private'}}, {'review_confirmed': 'true'}])
def test_prepare_rejects_unsafe_or_coerced_settings(changes):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PrepareRunInput.model_validate({'intent_id': uuid4().hex, 'revision': 0, **changes})
