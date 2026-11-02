import asyncio
import copy
import json

import httpx
import pytest

from backend.infox.client import InfoXClient
from backend.infox.config import InfoXCredentials
from backend.infox.errors import InfoXError
from backend.infox.service import RunService
from backend.infox.store import RunStore
from tests.test_infox_runs_api import seed, JOB_ID, payload
from backend.infox.run_models import PrepareRunInput
from backend.jobs import JobManager
from backend.config import Settings


def prepared(root, *, auto_replace=True):
    seed(root)
    store = RunStore(root)
    data = payload()
    data.setdefault('settings', {})['auto_replace'] = auto_replace
    record, _ = RunService(JobManager(Settings(root)), store).prepare(JOB_ID, PrepareRunInput(**data))
    return store, record


def draft(record):
    return {'id': 'video-1', 'updated_at': '2026-11-02T01:00:00Z', 'can_edit': True, 'video': {'frameRate':30,'duration':len(record.snapshot.scenes)*2,'scenes': [
        {'id': f'scene-{s.scene_index}', 'duration':2,'durationInFrames':60,'offset_durationInFrames':s.scene_index*60,
         'rootShape': {'layers': {'ROOT': {'child': ['media']},
         'media': {'type': {'resolvedName': 'ImageLayer'}, 'child': [], 'props': {
             'aigeneratable': {'auto_select': True}, 'media': {'bucket': 'images', 'filename': 'photo.jpg', 'title': '중립 설비'}}}}},
         'voiceover': {'content': s.text, 'filename': 'voice.wav', 'bucket': 'voice',
         'audio_duration': 2.0, 'voice_id': record.snapshot.settings.voice_id,
         'voice_provider': 'elevenlabs', 'voice_speed': '1.0',
         'segment_captions': [{'text': s.text, 'startMs': 0, 'endMs': 2000}]}}
        for s in record.snapshot.scenes]}}


def test_write_methods_never_retry_even_get_or_401():
    async def scenario(status):
        calls = []
        def handler(req):
            calls.append(req.url.path)
            return httpx.Response(status, json={'id': 'video-1', 'taskid': 'task-1', 'message': 'generate_type_text'})
        async with InfoXClient(InfoXCredentials(access_token='test'), transport=httpx.MockTransport(handler)) as c:
            for method in (lambda: c.create_project({'video': {}, 'context': {}}), lambda: c.start_generation('video-1')):
                if status != 200:
                    with pytest.raises(InfoXError):
                        await method()
                else:
                    assert await method() in ('video-1', 'task-1')
        assert len(calls) == 2
    for status in (200, 401, 429, 500):
        asyncio.run(scenario(status))


def test_draft_checks_required_scenes_audio_and_timed_captions(tmp_path):
    from backend.infox.tracking import verify_draft
    _, r = prepared(tmp_path)
    r = r.model_copy(update={'video_id': 'video-1'})
    state = draft(r)
    assert verify_draft(r, state).ready
    for mutation in ('count', 'voice', 'duration', 'id'):
        bad = copy.deepcopy(state)
        v = bad['video']['scenes'][0]['voiceover']
        if mutation == 'count': bad['video']['scenes'].pop()
        if mutation == 'voice': v['filename'] = ''
        if mutation == 'duration': v['audio_duration'] = 0
        if mutation == 'id': bad['id'] = 'other'
        assert not verify_draft(r, bad).ready, mutation


@pytest.mark.parametrize('generated,caption', [
    ('삼성, SK하이닉스는 이점이사퍼센트 상승했습니다.', '삼성, SK하이닉스는 2.24% 상승했습니다.'),
    ('InfoX가 반환한 다른 대본', 'InfoX가 반환한 다른 자막'),
])
def test_draft_does_not_compare_generated_narration_or_captions_with_snapshot(tmp_path, generated, caption):
    from backend.infox.tracking import verify_draft
    from backend.infox.service import creation_body
    _, r = prepared(tmp_path)
    r = r.model_copy(update={'video_id': 'video-1'})
    original = r.snapshot.model_dump_json()
    body = creation_body(r.snapshot)
    value = draft(r)
    for scene in value['video']['scenes']:
        scene['voiceover']['content'] = generated
        scene['voiceover']['segment_captions'][0]['text'] = caption
    assert verify_draft(r, value).ready
    assert r.snapshot.model_dump_json() == original
    assert creation_body(r.snapshot) == body


@pytest.mark.parametrize('mutation', [
    'missing_content', 'empty_content', 'non_string_content', 'missing_caption', 'empty_caption',
    'non_string_caption', 'empty_segments', 'overlap', 'reversed_time', 'non_numeric_time',
    'wrong_voice_id', 'wrong_provider', 'wrong_speed', 'missing_bucket', 'zero_duration', 'missing_voiceover',
])
def test_relaxed_text_policy_still_rejects_missing_or_invalid_production_data(tmp_path, mutation):
    from backend.infox.tracking import verify_draft
    _, r = prepared(tmp_path)
    r = r.model_copy(update={'video_id': 'video-1'})
    value = draft(r)
    v = value['video']['scenes'][0]['voiceover']
    s = v['segment_captions'][0]
    if mutation == 'missing_content': v.pop('content')
    if mutation == 'empty_content': v['content'] = ' \n '
    if mutation == 'non_string_content': v['content'] = 1
    if mutation == 'missing_caption': s.pop('text')
    if mutation == 'empty_caption': s['text'] = ' \n '
    if mutation == 'non_string_caption': s['text'] = 1
    if mutation == 'empty_segments': v['segment_captions'] = []
    if mutation == 'overlap': v['segment_captions'].append({'text':'다음', 'startMs':1000, 'endMs':2500})
    if mutation == 'reversed_time': s['endMs'] = 0
    if mutation == 'non_numeric_time': s['startMs'] = '0'
    if mutation == 'wrong_voice_id': v['voice_id'] = 'other'
    if mutation == 'wrong_provider': v['voice_provider'] = 'other'
    if mutation == 'wrong_speed': v['voice_speed'] = '2.0'
    if mutation == 'missing_bucket': v.pop('bucket')
    if mutation == 'zero_duration': v['audio_duration'] = 0
    if mutation == 'missing_voiceover': value['video']['scenes'][0].pop('voiceover')
    assert not verify_draft(r, value).ready


def test_timed_empty_caption_placeholder_does_not_block_other_ready_captions(tmp_path):
    from backend.infox.tracking import verify_draft
    _, r = prepared(tmp_path)
    r = r.model_copy(update={'video_id': 'video-1'})
    value = draft(r)
    voice = value['video']['scenes'][0]['voiceover']
    voice['segment_captions'] = [{'text':'', 'startMs':0, 'endMs':1000},
        {'text':r.snapshot.scenes[0].text, 'startMs':1000, 'endMs':2000}]
    assert verify_draft(r, value).ready


def test_event_matching_rejects_false_completion_and_foreign_task(tmp_path):
    from backend.infox.tracking import task_event
    for body in ({'type': 'connection', 'status': 'connected'},
                 {'additional_kwargs': {'taskid': 'other', 'task_status': 'completed'}},
                 {'channel': 'infox:other', 'additional_kwargs': {'taskid': 'task-1', 'task_status': 'completed'}}):
        assert task_event(body, 'video-1', 'task-1') is None
    for status, expected in (('running', 'running'), ('processing', 'running'), ('completed', 'completed'), ('failed', 'failed')):
        b = {'additional_kwargs': {'taskid': 'task-1', 'task_status': status, 'progress': 100}}
        assert task_event(b, 'video-1', 'task-1').status == expected
    b['additional_kwargs'].update(task_status='completed', error='SECRET remote failure')
    event = task_event(b, 'video-1', 'task-1')
    assert event.status == 'failed' and 'SECRET' not in event.model_dump_json()


def test_batch_completion_and_failure_are_not_hidden_by_earlier_progress():
    from backend.infox.tracking import task_event
    def msg(status): return {'additional_kwargs': {'taskid': 'task-1', 'task_status': status}}
    assert task_event({'messages': [msg('processing'), msg('completed')]}, 'video-1', 'task-1').status == 'completed'
    assert task_event({'messages': [msg('completed'), msg('failed')]}, 'video-1', 'task-1').status == 'failed'


class FakeClient:
    def __init__(self, store, record, *, lost=None, failed=False, delayed=0, disconnect=False, missing=False):
        self.store, self.record = store, record
        self.lost, self.failed, self.delayed = lost, failed, delayed
        self.disconnect, self.missing = disconnect, missing
        self.calls, self.reads, self.connections = [], 0, 0
        self.started = asyncio.Event()

    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def authenticate(self): pass

    async def create_project(self, body):
        assert self.store.get(self.record.id).project.state == 'intent_recorded'
        assert body['context']['data']['inputText'] == self.record.snapshot.text
        self.calls.append('project')
        if self.lost == 'project': raise InfoXError('timeout')
        return 'video-1'

    async def start_generation(self, video_id):
        assert video_id == 'video-1'
        assert self.connections > 0
        assert self.store.get(self.record.id).generation.state == 'intent_recorded'
        self.calls.append('generation')
        self.started.set()
        # Permit the completion event to beat the HTTP response.
        await asyncio.sleep(.002)
        if self.lost == 'generation': raise InfoXError('timeout')
        return 'task-1'

    async def video_state(self, video_id):
        self.reads += 1
        value = draft(self.record)
        if self.reads <= self.delayed:
            value['video']['scenes'] = []
        return value

    async def render_logs(self, video_id): return {'logs': []}

    async def events(self, video_id, **kwargs):
        from backend.infox.sse import SSEEvent
        self.connections += 1
        yield SSEEvent(data=json.dumps({'type': 'connection', 'status': 'connected', 'channel': 'infox:video-1'}))
        await self.started.wait()
        if self.disconnect and self.connections == 1:
            raise InfoXError('network_error')
        if not self.missing:
            body = {'channel': 'infox:video-1', 'additional_kwargs': {'taskid': 'task-1',
                    'task_status': 'failed' if self.failed else 'completed', 'progress': 100}}
            for _ in range(2):
                yield SSEEvent(data=json.dumps(body), id='duplicate')
        await asyncio.Event().wait()


def test_manager_one_shot_admission_early_event_reconnect_and_delayed_data(tmp_path):
    from backend.infox.manager import RunManager
    async def scenario():
        store, record = prepared(tmp_path)
        fake = FakeClient(store, record, delayed=2, disconnect=True)
        manager = RunManager(store, lambda: fake, tracking_timeout=.4, retry_delay=.001)
        await asyncio.gather(manager.start(record.id), manager.start(record.id))
        await manager.wait(record.id)
        final = store.get(record.id)
        assert final.stage == 'ready_for_editor' and final.asset_audit.state == 'completed'
        assert final.asset_save.state == 'unchanged_verified' and final.favorite_replacements == ()
        assert final.video_id == 'video-1' and final.task_id == 'task-1'
        assert final.tracking.completion_seen and final.tracking.draft.ready
        assert final.tracking.event_count == 1 and final.tracking.reconnects >= 1
        assert fake.calls == ['project', 'generation']
        assert fake.reads >= 3
        await manager.start(record.id)
        await manager.wait(record.id)
        assert fake.calls == ['project', 'generation']
        await manager.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('lost', ['project', 'generation'])
def test_manager_unknown_receipt_survives_restart_without_write(tmp_path, lost):
    from backend.infox.manager import RunManager
    async def scenario():
        store, record = prepared(tmp_path)
        fake = FakeClient(store, record, lost=lost)
        manager = RunManager(store, lambda: fake, tracking_timeout=.05, retry_delay=.001)
        await manager.start(record.id)
        await manager.wait(record.id)
        final = store.get(record.id)
        assert final.requires_reconciliation and getattr(final, lost).state == 'unknown'
        calls = list(fake.calls)
        restored = RunManager(RunStore(tmp_path), lambda: fake, tracking_timeout=.05, retry_delay=.001)
        await restored.recover()
        await restored.wait(record.id)
        assert fake.calls == calls
        assert store.get(record.id).stage == 'needs_attention'
        await manager.close()
        await restored.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('failed,missing,expected', [(True, False, 'failed'), (False, True, 'needs_attention')])
def test_manager_never_claims_complete_on_failure_or_missing_event(tmp_path, failed, missing, expected):
    from backend.infox.manager import RunManager
    async def scenario():
        store, record = prepared(tmp_path)
        fake = FakeClient(store, record, failed=failed, missing=missing)
        manager = RunManager(store, lambda: fake, tracking_timeout=.04, retry_delay=.001)
        await manager.start(record.id)
        await manager.wait(record.id)
        assert store.get(record.id).stage == expected
        await manager.close()
    asyncio.run(scenario())


def test_start_api_is_guarded_and_background_run_can_be_polled(tmp_path):
    import time
    from tests.test_api import client, auth
    seed(tmp_path)
    with client(tmp_path) as c:
        headers = auth(c)
        r = c.post(f'/api/jobs/{JOB_ID}/infox-runs', headers=headers, json=payload()).json()
        manager = c.app.state.infox_manager
        fake = FakeClient(manager.store, manager.store.get(r['id']))
        manager.client_factory = lambda: fake
        url = f'/api/infox-runs/{r["id"]}/start'
        assert c.post(url).status_code == 403
        assert c.post(url, headers={**headers, 'Origin': 'https://evil.invalid'}).status_code == 403
        assert c.post(url, headers=headers).status_code == 202
        assert c.post(url, headers=headers).status_code == 202
        for _ in range(100):
            result = c.get(f'/api/infox-runs/{r["id"]}', headers=headers).json()
            if result['stage'] == 'ready_for_editor': break
            time.sleep(.01)
        assert result['stage'] == 'ready_for_editor'
        assert result['asset_save']['state'] == 'unchanged_verified'
        assert result['editor_url'].endswith('/video-1?chatboxView=script')
        assert fake.calls == ['project', 'generation']


def test_auth_failure_before_send_can_resume_same_snapshot(tmp_path):
    from backend.infox.manager import RunManager
    async def scenario():
        store, record = prepared(tmp_path)
        fake = FakeClient(store, record)
        async def expired(): raise InfoXError('auth_expired')
        good_auth = fake.authenticate
        fake.authenticate = expired
        manager = RunManager(store, lambda: fake, tracking_timeout=.1, retry_delay=.001)
        await manager.start(record.id)
        await manager.wait(record.id)
        assert store.get(record.id).tracking.error_code == 'authentication_required'
        assert store.get(record.id).project.state == 'not_sent'
        fake.authenticate = good_auth
        await manager.start(record.id)
        await manager.wait(record.id)
        assert store.get(record.id).stage == 'ready_for_editor'
        await manager.close()
    asyncio.run(scenario())


def test_successful_external_id_is_not_lost_when_another_manager_reconciles_intent(tmp_path):
    from backend.infox.manager import RunManager
    async def scenario():
        store, record = prepared(tmp_path)
        fake = FakeClient(store, record)
        original = fake.create_project
        in_flight, release = asyncio.Event(), asyncio.Event()
        async def slow(body):
            in_flight.set()
            await release.wait()
            return await original(body)
        fake.create_project = slow
        first = RunManager(store, lambda: fake, tracking_timeout=.1, retry_delay=.001)
        second = RunManager(RunStore(tmp_path), lambda: fake, tracking_timeout=.1, retry_delay=.001)
        await first.start(record.id)
        await in_flight.wait()
        await second.start(record.id)
        await second.wait(record.id)
        release.set()
        await first.wait(record.id)
        final = store.get(record.id)
        assert final.project.state == 'accepted' and final.video_id == 'video-1'
        assert final.generation.state == 'accepted' and final.task_id == 'task-1'
        assert fake.calls == ['project', 'generation']
        await first.close()
        await second.close()
    asyncio.run(scenario())


def test_persisted_completion_recovers_read_only_and_auth_error_is_distinct(tmp_path):
    from backend.infox.manager import RunManager
    async def scenario():
        store, r = prepared(tmp_path)
        r = store.begin_request(r.id, 'project', expected_version=r.version)
        r = store.record_outcome(r.id, 'project', 'accepted', external_id='video-1', expected_version=r.version)
        r = store.begin_request(r.id, 'generation', expected_version=r.version)
        r = store.record_outcome(r.id, 'generation', 'accepted', external_id='task-1', expected_version=r.version)
        store.track(r.id, r.tracking.model_copy(update={'completion_seen': True, 'task_status': 'completed'}),
                    stage='checking_draft', expected_version=r.version)
        fake = FakeClient(store, r)
        good_read = fake.video_state
        reads = 0
        async def read(video_id):
            nonlocal reads
            reads += 1
            if reads > 1: raise InfoXError('auth_expired')
            return await good_read(video_id)
        fake.video_state = read
        manager = RunManager(store, lambda: fake, tracking_timeout=.1, retry_delay=.001)
        await manager.recover()
        await manager.wait(r.id)
        assert store.get(r.id).tracking.error_code == 'authentication_required'
        fake.video_state = good_read
        await manager.start(r.id)
        await manager.wait(r.id)
        assert store.get(r.id).stage == 'ready_for_editor' and fake.calls == []
        await manager.close()
    asyncio.run(scenario())


def test_unavailable_run_database_does_not_block_narration_app_start(tmp_path):
    from unittest.mock import patch
    from tests.test_api import client
    from backend.infox.run_models import RunError
    with patch.object(RunStore, 'resumable', side_effect=RunError('storage_error', 503)):
        with client(tmp_path) as c:
            assert c.get('/api/health').status_code == 200
