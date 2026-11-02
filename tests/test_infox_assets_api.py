import asyncio
import copy
import time

import pytest

from backend.infox.run_models import DraftCheck, RunError
from backend.infox.store import RunStore
from tests.test_infox_assets import image, layer
from tests.test_infox_generation import prepared, draft
from tests.test_api import auth, client


def ready_run(root):
    store, r = prepared(root)
    for op, external_id in (('project', 'video-1'), ('generation', 'task-1')):
        r = store.begin_request(r.id, op, expected_version=r.version)
        r = store.record_outcome(r.id, op, 'accepted', external_id=external_id, expected_version=r.version)
    r = store.track(r.id, r.tracking.model_copy(update={'completion_seen': True, 'task_status': 'completed',
        'draft': DraftCheck(identity_matches=True, scenes_match=True, voices_ready=True, captions_ready=True)}),
        stage='draft_ready', expected_version=r.version)
    return store, r


def asset_state(r):
    value = draft(r)
    value['updated_at'] = '2026-11-02T01:00:00Z'
    for i, scene in enumerate(value['video']['scenes']):
        scene.update(id=f'scene-{i}', rootShape={'layers': {'ROOT': {'child': ['media']},
            'media': layer({'image_files': [image()]})}}, rerank_result=[image(tags=['삼성'])])
    return value


class ReadOnlyClient:
    def __init__(self, value):
        self.value, self.reads = value, 0
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def authenticate(self): pass
    async def video_state(self, video_id):
        assert video_id == 'video-1'
        self.reads += 1
        return copy.deepcopy(self.value)
    async def search_favorites(self,**request):
        from tests.test_infox_favorites import favorite
        return {'items':[favorite()], 'total':1,'skip':request['skip'],'limit':request['limit']}
    async def favorite_segment_available(self,raw): return True


def test_audit_store_is_typed_cas_and_preserves_snapshot_and_receipts(tmp_path):
    from backend.infox.assets import audit_assets
    store, r = ready_run(tmp_path)
    claimed = store.begin_audit(r.id, expected_version=r.version)
    assert claimed.stage == 'auditing_assets' and claimed.asset_audit.state == 'running'
    with pytest.raises(RunError, match='record_conflict'):
        store.begin_audit(r.id, expected_version=r.version)
    report = audit_assets(asset_state(r), template_id=1786, run_id=r.id, video_id=r.video_id)
    final = store.record_audit(r.id, report, expected_version=claimed.version)
    assert final.stage == 'assets_audited' and final.asset_audit.counts()['detected'] == 2
    assert final.snapshot == r.snapshot and final.project == r.project and final.generation == r.generation
    assert RunStore(tmp_path).get(r.id) == final
    with pytest.raises(RunError, match='invalid_stage'):
        store.record_audit(r.id, report, expected_version=final.version)


def test_audit_api_is_protected_read_only_and_resumes_existing_video(tmp_path):
    store, r = ready_run(tmp_path)
    fake = ReadOnlyClient(asset_state(r))
    with client(tmp_path) as c:
        c.app.state.infox_manager.client_factory = lambda: fake
        headers = auth(c)
        endpoint = f'/api/infox-runs/{r.id}/audit'
        assert c.post(endpoint).status_code == 403
        assert c.post(endpoint, headers={**headers, 'Origin': 'https://evil.invalid'}).status_code == 403
        assert c.get(f'/api/infox-runs/{r.id}', headers=headers).json()['asset_audit']['state'] == 'not_started'
        assert c.post(endpoint, headers=headers).status_code == 202
        for _ in range(100):
            result = c.get(f'/api/infox-runs/{r.id}', headers=headers).json()
            if not result['tracking_active']: break
            time.sleep(.01)
        assert result['stage'] == 'replacement_planned'
        assert result['asset_counts']['detected'] == 2 and fake.reads == 2
        assert result['snapshot']['text'] == r.snapshot.text
        assert c.post(endpoint, headers=headers, json={'favorite_replacements': []}).status_code == 422
    with client(tmp_path) as c:
        restored = c.get(f'/api/infox-runs/{r.id}', headers=auth(c)).json()
        assert restored['asset_audit'] == result['asset_audit']


def test_unready_runs_cannot_audit_and_changed_draft_is_not_inspected(tmp_path):
    from backend.infox.manager import RunManager
    async def scenario():
        store, unready = prepared(tmp_path)
        manager = RunManager(store, lambda: None)
        with pytest.raises(RunError, match='draft_not_ready'):
            await manager.audit(unready.id)
        store, r = ready_run(tmp_path)
        value = asset_state(r)
        value['video']['scenes'][0]['voiceover']['content'] = '다른 대본'
        fake = ReadOnlyClient(value)
        manager = RunManager(store, lambda: fake)
        await manager.audit(r.id)
        await manager.wait(r.id)
        final = store.get(r.id)
        assert final.stage == 'needs_attention'
        assert final.asset_audit.error_code == 'draft_changed' and final.asset_audit.items == ()
        assert final.video_id == r.video_id and final.task_id == r.task_id
        await manager.close()
    asyncio.run(scenario())


def test_interrupted_audit_recovers_only_reads(tmp_path):
    from backend.infox.manager import RunManager
    async def scenario():
        store, r = ready_run(tmp_path)
        store.begin_audit(r.id, expected_version=r.version)
        fake = ReadOnlyClient(asset_state(r))
        restored = RunManager(RunStore(tmp_path), lambda: fake)
        await restored.recover()
        await restored.wait(r.id)
        assert store.get(r.id).stage == 'replacement_planned' and fake.reads == 2
        await restored.close()
    asyncio.run(scenario())


def test_audit_click_during_automatic_continuation_does_not_steal_cas(tmp_path):
    from backend.infox.manager import RunManager
    from tests.test_infox_generation import FakeClient
    async def scenario():
        store, r = prepared(tmp_path)
        fake = FakeClient(store, r)
        manager = RunManager(store, lambda: fake, retry_delay=.001)
        original = manager._audit_with_client
        at_gate, release = asyncio.Event(), asyncio.Event()
        async def paused(client, record):
            at_gate.set()
            await release.wait()
            return await original(client, record)
        manager._audit_with_client = paused
        await manager.start(r.id)
        await at_gate.wait()
        await manager.audit(r.id)
        assert store.get(r.id).stage == 'draft_ready'
        release.set()
        await manager.wait(r.id)
        assert store.get(r.id).stage == 'assets_audited'
        assert fake.calls == ['project', 'generation']
        await manager.close()
    asyncio.run(scenario())


def test_automatic_audit_has_its_own_read_lifetime_after_tracking_completes(tmp_path):
    from backend.infox.manager import RunManager
    from tests.test_infox_generation import FakeClient
    async def scenario():
        store, r = prepared(tmp_path)
        class SlowAudit(FakeClient):
            async def video_state(self, video_id):
                if self.reads == 2:
                    await asyncio.sleep(.1)
                return await super().video_state(video_id)
        fake = SlowAudit(store, r)
        manager = RunManager(store, lambda: fake, tracking_timeout=.04, retry_delay=.001)
        await manager.start(r.id)
        await manager.wait(r.id)
        assert store.get(r.id).stage == 'assets_audited'
        assert not manager.active(r.id)
        await manager.close()
    asyncio.run(scenario())
