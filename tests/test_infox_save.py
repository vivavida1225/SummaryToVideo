import asyncio
import copy
import json

import httpx
import pytest

from backend.infox.client import InfoXClient
from backend.infox.config import InfoXCredentials
from backend.infox.errors import InfoXError
from backend.infox.manager import RunManager
from backend.infox.planner import prepare_plan
from backend.infox.run_models import RunError
from backend.infox.store import RunStore
from backend.infox.saving import url_reference_fingerprint
from tests.test_infox_plan_api import audited_run, PlanningClient


async def planned(root):
    store, r, value = audited_run(root)
    # Real videostate uses context, not contextData.
    value['context'] = {'type': 'text', 'data': {'inputText': r.snapshot.text}, 'preserve': ['all']}
    value['scratch_pad'] = {'url_presigned': 'https://kr.object.iwinv.kr/images/healthy.jpg?signature=OLD'}
    fake = SavingClient(store, r, value)
    r = await prepare_plan(store, fake, r)
    assert r.stage == 'replacement_planned'
    return store, r, fake


class SavingClient(PlanningClient):
    def __init__(self, store, r, value):
        super().__init__(value)
        self.store, self.run_id = store, r.id
        self.writes, self.events = [], []
        self.mode = 'success'
        self.read_failure = False

    async def search_favorites(self, **request):
        from tests.test_infox_favorites import FavoritesClient
        return await FavoritesClient.search_favorites(self, **request)

    async def favorite_segment_available(self, raw):
        from tests.test_infox_favorites import FavoritesClient
        return await FavoritesClient.favorite_segment_available(self, raw)

    async def video_state(self, video_id):
        self.events.append('read')
        if self.read_failure:
            raise InfoXError('timeout')
        return await super().video_state(video_id)

    async def save_video_context(self, video_id, body):
        assert self.store.get(self.run_id).asset_save.receipt_state == 'intent_recorded'
        assert body['updatedAt'] == self.value['updated_at']
        assert body['contextData'] == self.value['context']
        self.events.append('save')
        self.writes.append(copy.deepcopy(body))
        if self.mode == 'conflict':
            self.value['updated_at'] = '2026-11-02T03:00:00Z'
            self.value['context']['other_editor'] = True
            raise InfoXError('http_error', 409)
        if self.mode == 'lost_unapplied':
            raise InfoXError('timeout')
        self.value.update(video=copy.deepcopy(body['video']), context=copy.deepcopy(body['contextData']),
                          updated_at='2026-11-02T02:00:00Z')
        if self.mode == 'tamper':
            self.value['video']['scenes'][0]['voiceover']['filename'] = 'wrong.wav'
        if self.mode in ('wrong_url', 'refreshed_url'):
            media = self.value['video']['scenes'][0]['rootShape']['layers']['media']['props']['media']
            media['segment_url_presigned'] = ('https://kr.object.iwinv.kr/wrong-bucket/wrong.mp4?signature=NEW'
                if self.mode == 'wrong_url' else media['segment_url_presigned'].split('?')[0] + '?signature=NEW')
        if self.mode == 'wrong_normal_url':
            self.value['scratch_pad']['url_presigned'] = 'https://kr.object.iwinv.kr/images/other.jpg?NEW'
        if self.mode == 'lost':
            raise InfoXError('timeout')
        if self.mode == 'read_failure':
            self.read_failure = True
        return self.value['updated_at']


@pytest.mark.parametrize('tamper', [None, 'content', 'caption'])
def test_generated_text_variations_can_be_saved_but_save_must_preserve_server_text(tmp_path, tamper):
    async def scenario():
        store, r, value = audited_run(tmp_path)
        value['context'] = {'data': {'inputText': r.snapshot.text}}
        for scene in value['video']['scenes']:
            scene['voiceover']['content'] = '삼성, SK하이닉스는 이점이사퍼센트 상승했습니다.'
            scene['voiceover']['segment_captions'][0]['text'] = '삼성, SK하이닉스는 2.24% 상승했습니다.'
        fake = SavingClient(store, r, value)
        r = await prepare_plan(store, fake, r)
        assert r.replacement_plan.state == 'ready'
        original_save = fake.save_video_context
        async def save(*args):
            version = await original_save(*args)
            voice = fake.value['video']['scenes'][0]['voiceover']
            if tamper == 'content': voice['content'] = '저장 중 바뀐 대본'
            if tamper == 'caption': voice['segment_captions'][0]['text'] = '저장 중 바뀐 자막'
            return version
        fake.save_video_context = save
        manager = RunManager(store, lambda: fake)
        await manager.save(r.id); await manager.wait(r.id)
        final = store.get(r.id)
        assert final.asset_save.state == ('saved_verified' if tamper is None else 'needs_attention')
        if tamper: assert final.asset_save.error_code == 'verification_failed'
        assert bool(final.favorite_replacements) == (tamper is None)
        assert final.snapshot == r.snapshot and len(fake.writes) == 1
        await manager.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('status', [200, 201, 401, 409, 429, 500])
def test_save_client_is_one_shot_and_validates_status_version(status):
    async def scenario():
        calls = []
        def handler(request):
            calls.append(request)
            assert request.method == 'POST'
            assert request.url.path == '/infomaxai/context/update_video_context/video-1'
            assert json.loads(request.content) == {'video': {}, 'contextData': None, 'updatedAt': '2026-11-02T01:00:00Z'}
            return httpx.Response(status, json={'status': 'Video updated', 'updated_at': '2026-11-02T02:00:00Z'})
        async with InfoXClient(InfoXCredentials(access_token='test'), transport=httpx.MockTransport(handler)) as c:
            body = {'video': {}, 'contextData': None, 'updatedAt': '2026-11-02T01:00:00Z'}
            if status == 200:
                assert await c.save_video_context('video-1', body) == '2026-11-02T02:00:00Z'
            else:
                with pytest.raises(InfoXError): await c.save_video_context('video-1', body)
        assert len(calls) == 1
    asyncio.run(scenario())


@pytest.mark.parametrize('result', [
    {'status': 'ok', 'updated_at': '2026-11-02T02:00:00Z'},
    {'status': 'Video updated'}, {'status': 'Video updated', 'updated_at': 'SECRET?token=bad'},
    {'status': 'Video updated', 'updated_at': '2026-11-02T01:00:00Z'},
])
def test_save_client_rejects_invalid_acknowledgement(result):
    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='test'), transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json=result))) as c:
            with pytest.raises(InfoXError, match='invalid_response'):
                await c.save_video_context('video-1', {'video': {}, 'contextData': {}, 'updatedAt': '2026-11-02T01:00:00Z'})
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['success', 'lost', 'conflict', 'lost_unapplied', 'tamper', 'wrong_url', 'refreshed_url', 'wrong_normal_url'])
def test_save_reads_after_post_and_only_verified_server_content_creates_history(tmp_path, mode):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        fake.mode = mode
        manager = RunManager(store, lambda: fake, retry_delay=0)
        await manager.save(r.id); await manager.wait(r.id)
        final = store.get(r.id)
        assert len(fake.writes) == 1
        assert fake.events[-1] == 'read'
        assert final.snapshot == r.snapshot and final.project == r.project and final.generation == r.generation
        assert final.video_id == r.video_id and final.task_id == r.task_id
        if mode in ('success', 'lost', 'refreshed_url'):
            assert final.stage == 'ready_for_editor' and final.asset_save.state == 'saved_verified'
            assert len(final.favorite_replacements) == len(r.replacement_plan.selections)
            assert all(x.saved_verified and x.reference.filename == 'clip-0.mp4' for x in final.favorite_replacements)
            from backend.infox.assets import audit_assets
            audit = audit_assets(fake.value, template_id=1786, run_id=r.id, video_id=r.video_id,
                                 verified_replacements=final.favorite_replacements)
            assert audit.counts()['favorite_exempt'] == len(final.favorite_replacements)
        else:
            assert final.stage == 'needs_attention' and not final.favorite_replacements
            assert final.asset_save.error_code == {'conflict': 'version_conflict', 'lost_unapplied': 'save_unconfirmed',
                                                   'tamper': 'verification_failed', 'wrong_url': 'verification_failed',
                                                   'wrong_normal_url': 'verification_failed'}[mode]
        # Repeated start/save/recover must never create or repeat a write.
        restored = RunManager(RunStore(tmp_path), lambda: fake, retry_delay=0)
        await restored.start(r.id); await restored.wait(r.id)
        await restored.save(r.id); await restored.wait(r.id)
        await restored.recover(); await restored.wait(r.id)
        assert len(fake.writes) == 1
        assert len(store.get(r.id).favorite_replacements) == len(final.favorite_replacements)
        assert 'PRIVATE' not in store.get(r.id).model_dump_json()
        await manager.close(); await restored.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['version', 'context', 'target', 'candidate', 'missing_context'])
def test_stale_source_or_candidate_never_sends_save(tmp_path, change):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        if change == 'version': fake.value['updated_at'] = '2026-11-02T03:00:00Z'
        if change == 'context': fake.value['context']['concurrent'] = 'preserve'
        if change == 'target': fake.value['video']['scenes'][0]['rootShape']['layers']['media']['props']['media']['image_files'][0]['filename'] = 'changed.jpg'
        if change == 'candidate': fake.items.pop(0)
        if change == 'missing_context': fake.value.pop('context')
        manager = RunManager(store, lambda: fake)
        await manager.save(r.id); await manager.wait(r.id)
        final = store.get(r.id)
        assert final.stage == 'needs_attention' and not final.favorite_replacements and not fake.writes
        await manager.close()
    asyncio.run(scenario())


def test_restart_after_accepted_response_and_read_failure_only_reads(tmp_path):
    async def scenario():
        store, r, fake = await planned(tmp_path); fake.mode = 'read_failure'
        manager = RunManager(store, lambda: fake)
        await manager.save(r.id); await manager.wait(r.id)
        assert store.get(r.id).asset_save.receipt_state == 'accepted'
        assert not store.get(r.id).favorite_replacements
        fake.read_failure = False
        restored = RunManager(RunStore(tmp_path), lambda: fake)
        await restored.start(r.id); await restored.wait(r.id)
        assert store.get(r.id).asset_save.state == 'saved_verified' and len(fake.writes) == 1
        await manager.close(); await restored.close()
    asyncio.run(scenario())


def test_intent_crash_is_reconciled_without_post_and_plan_audit_are_locked(tmp_path):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        r = store.begin_save(r.id, expected_version=r.version)
        r = store.begin_save_request(r.id, request_version=fake.value['updated_at'],
                                    result_url_fingerprint=url_reference_fingerprint(fake.value), expected_version=r.version)
        with pytest.raises(RunError): store.begin_audit(r.id, expected_version=r.version)
        with pytest.raises(RunError): store.begin_plan(r.id, expected_version=r.version)
        with pytest.raises(RunError): store.begin_save_request(r.id, request_version=fake.value['updated_at'],
            result_url_fingerprint=url_reference_fingerprint(fake.value), expected_version=r.version)
        restored = RunManager(RunStore(tmp_path), lambda: fake)
        await restored.recover(); await restored.wait(r.id)
        final = store.get(r.id)
        assert final.stage == 'needs_attention' and final.requires_reconciliation
        assert final.asset_save.error_code == 'save_unconfirmed' and not fake.writes
        assert not final.favorite_replacements
        await restored.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('state', ['disabled', 'needs_attention', 'selecting', 'selected'])
def test_unready_plan_cannot_be_saved_or_marked_verified(tmp_path, state):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        with store._edit(r.id, r.version) as (_, data):
            data.update(stage='needs_attention', replacement_plan={**r.replacement_plan.model_dump(), 'state': state})
        manager = RunManager(store, lambda: fake)
        with pytest.raises(RunError, match='save_not_ready'): await manager.save(r.id)
        assert not store.get(r.id).favorite_replacements and not fake.writes
        await manager.close()
    asyncio.run(scenario())


def test_no_change_is_verified_separately_without_save_or_history(tmp_path):
    async def scenario():
        from backend.infox.assets import audit_assets
        store, r, value = audited_run(tmp_path)
        for scene in value['video']['scenes']:
            scene['rerank_result'][0]['tags'] = ['중립']
        r = store.begin_audit(r.id, expected_version=r.version)
        r = store.record_audit(r.id, audit_assets(value, template_id=1786, run_id=r.id, video_id=r.video_id), expected_version=r.version)
        fake = SavingClient(store, r, value)
        r = await prepare_plan(store, fake, r)
        assert r.replacement_plan.state == 'not_needed'
        manager = RunManager(store, lambda: fake)
        await manager.save(r.id); await manager.wait(r.id)
        final = store.get(r.id)
        assert final.stage == 'ready_for_editor' and final.asset_save.state == 'unchanged_verified'
        assert final.asset_save.receipt_state == 'not_sent' and not final.favorite_replacements and not fake.writes
        await manager.close()
    asyncio.run(scenario())
