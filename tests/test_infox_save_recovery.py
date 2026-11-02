import asyncio
import copy
import time

import pytest

from backend.infox.assets import audit_assets
from backend.infox.manager import RunManager
from backend.infox.planner import prepare_plan
from backend.infox.replacement import apply_replacements, state_fingerprint
from backend.infox.run_models import RunError
from backend.infox.saving import save_body, url_reference_fingerprint
from backend.infox.store import RunStore
from tests.test_api import auth, client
from tests.test_infox_assets import image, layer, video
from tests.test_infox_plan_api import audited_run
from tests.test_infox_save import planned, SavingClient


def test_mapping_preserves_context_and_rejects_ambiguous_missing_context():
    state = {'video': {'scenes': []}, 'context': {'data': {'keep': 1}},
             'contextData': {'wrong': True}, 'updated_at': '2026-11-02T01:00:00+00:00'}
    body = save_body(state)
    assert body == {'video': state['video'], 'contextData': state['context'], 'updatedAt': state['updated_at']}
    body['contextData']['data']['keep'] = 2
    assert state['context']['data']['keep'] == 1
    from backend.infox.plan_models import PlanError
    state.pop('context')
    with pytest.raises(PlanError, match='context_invalid'): save_body(state)
    state['context'] = None
    assert save_body(state)['contextData'] is None


def test_save_api_guards_polling_and_restore_do_not_repeat_post(tmp_path):
    store, r, fake = asyncio.run(planned(tmp_path))
    with client(tmp_path) as c:
        c.app.state.infox_manager.client_factory = lambda: fake
        path = f'/api/infox-runs/{r.id}/save-assets'
        headers = auth(c)
        assert c.post(path).status_code == 403
        assert c.post(path, headers={**headers, 'Origin': 'https://evil.invalid'}).status_code == 403
        assert c.post(path, headers=headers, json={'video': {'forged': True}}).status_code == 422
        assert c.post(path, headers=headers).status_code == 202
        for _ in range(100):
            result = c.get(f'/api/infox-runs/{r.id}', headers=headers).json()
            if not result['tracking_active']: break
            time.sleep(.01)
        assert result['asset_save']['state'] == 'saved_verified'
        assert c.post(path, headers=headers).status_code == 202
        assert len(fake.writes) == 1
        assert 'PRIVATE' not in str(result)
    assert RunStore(tmp_path).get(r.id).asset_save.state == 'saved_verified'


@pytest.mark.parametrize('phase', ['intent', 'accepted', 'verifying'])
def test_crash_after_server_apply_and_timestamp_url_refresh_recovers_read_only(tmp_path, phase):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        r = store.begin_save(r.id, expected_version=r.version)
        with pytest.raises(RunError, match='record_conflict'):
            store.begin_save(r.id, expected_version=r.version-1)
        proposed = apply_replacements(fake.value, r.replacement_plan.selections)
        r = store.begin_save_request(r.id, request_version=fake.value['updated_at'],
                                    result_url_fingerprint=url_reference_fingerprint(proposed), expected_version=r.version)
        fake.value = apply_replacements(fake.value, r.replacement_plan.selections)
        fake.value['updated_at'] = '2026-11-02T02:00:00Z'
        if phase != 'intent':
            r = store.record_save_outcome(r.id, 'accepted', response_version=fake.value['updated_at'], expected_version=r.version)
        if phase == 'verifying':
            r = store.begin_save(r.id, expected_version=r.version)
        restored = RunManager(RunStore(tmp_path), lambda: fake)
        await restored.recover(); await restored.wait(r.id)
        final = store.get(r.id)
        assert final.asset_save.state == 'saved_verified' and not final.requires_reconciliation
        assert final.favorite_replacements and not fake.writes
        await restored.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('duration', [1.0, 60.0])
def test_mixed_bundle_multiple_layers_and_clip_lengths_save_and_preserve_server_data(tmp_path, duration):
    async def scenario():
        store, r, value = audited_run(tmp_path)
        value['context'] = {'keep': ['original context']}
        scene = value['video']['scenes'][0]
        scene['rootShape']['layers'].update(
            media=layer({'image_files': [image('photo.jpg', title='삼성'), image('healthy.jpg', title='중립'),
                                       video('old.mp4', title='SK', media_type='video', duration=3., durationInFrames=20)]}, kind='MediaLayer'),
            second=layer(image('second.jpg', title='삼성')))
        scene['rootShape']['layers']['ROOT']['child'].append('second')
        scene['rerank_result'].extend([image('healthy.jpg', title='중립'), image('second.jpg', title='삼성'), video('old.mp4', title='SK')])
        r = store.begin_audit(r.id, expected_version=r.version)
        r = store.record_audit(r.id, audit_assets(value, template_id=1786, run_id=r.id, video_id=r.video_id), expected_version=r.version)
        fake = SavingClient(store, r, value)
        for item in fake.items: item['duration'] = duration
        r = await prepare_plan(store, fake, r)
        assert r.stage == 'replacement_planned'
        before = copy.deepcopy(fake.value)
        manager = RunManager(store, lambda: fake)
        await manager.save(r.id); await manager.wait(r.id)
        final = store.get(r.id)
        assert final.asset_save.state == 'saved_verified'
        assert len(final.favorite_replacements) >= 3
        # Compare against the independent plan hash with only the server timestamp normalized.
        assert state_fingerprint({**fake.value, 'updated_at': before['updated_at']}) == r.replacement_plan.result_fingerprint
        assert fake.value['context'] == before['context']
        assert fake.value['video']['scenes'][0]['voiceover'] == before['video']['scenes'][0]['voiceover']
        assert len(fake.writes) == 1
        await manager.close()
    asyncio.run(scenario())


def test_two_managers_share_one_durable_save_intent(tmp_path):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        original = fake.save_video_context
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(*args):
            entered.set(); await release.wait()
            return await original(*args)
        fake.save_video_context = delayed
        first = RunManager(store, lambda: fake)
        second = RunManager(RunStore(tmp_path), lambda: fake)
        await first.save(r.id)
        await entered.wait()
        await second.start(r.id); await second.wait(r.id)
        release.set(); await first.wait(r.id)
        # The second owner may have read before the first server application.
        await second.start(r.id); await second.wait(r.id)
        final = store.get(r.id)
        assert len(fake.writes) == 1 and final.asset_save.state == 'saved_verified'
        assert len(final.favorite_replacements) == len(r.replacement_plan.selections)
        await first.close(); await second.close()
    asyncio.run(scenario())


def test_automatic_zero_target_run_verifies_unchanged_even_with_replacement_disabled(tmp_path):
    from tests.test_infox_generation import prepared, FakeClient
    async def scenario():
        store, r = prepared(tmp_path, auto_replace=False)
        fake = FakeClient(store, r)
        manager = RunManager(store, lambda: fake)
        await manager.start(r.id); await manager.wait(r.id)
        final = store.get(r.id)
        assert final.stage == 'ready_for_editor' and final.asset_save.state == 'unchanged_verified'
        assert final.replacement_plan.state == 'not_needed' and final.asset_save.receipt_state == 'not_sent'
        assert not final.favorite_replacements and fake.calls == ['project', 'generation']
        await manager.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('url_change', ['wrong_segment', 'wrong_preview', 'signature_only'])
def test_intent_recovery_checks_candidate_url_paths_and_allows_signature_refresh(tmp_path, url_change):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        choice = r.replacement_plan.selections[0]
        proposed = apply_replacements(fake.value, r.replacement_plan.selections,
            ephemeral={choice.candidate.key(): fake.items[0]})
        r = store.begin_save(r.id, expected_version=r.version)
        r = store.begin_save_request(r.id, request_version=fake.value['updated_at'],
                                    result_url_fingerprint=url_reference_fingerprint(proposed), expected_version=r.version)
        fake.value = proposed
        fake.value['updated_at'] = '2026-11-02T02:00:00Z'
        media = fake.value['video']['scenes'][0]['rootShape']['layers']['media']['props']['media']
        if url_change == 'wrong_segment': media['segment_url_presigned'] = 'https://kr.object.iwinv.kr/assets-videos/other.mp4?NEW'
        if url_change == 'wrong_preview': media['preview_presigned'] = 'https://evil.invalid/clip-0.jpeg?NEW'
        if url_change == 'signature_only':
            for key in ('segment_url_presigned', 'preview_presigned'):
                media[key] = media[key].split('?')[0] + '?signature=REFRESHED'
        restored = RunManager(RunStore(tmp_path), lambda: fake)
        await restored.recover(); await restored.wait(r.id)
        final = store.get(r.id)
        assert final.asset_save.state == ('saved_verified' if url_change == 'signature_only' else 'needs_attention')
        assert bool(final.favorite_replacements) == (url_change == 'signature_only')
        assert not fake.writes
        await restored.close()
    asyncio.run(scenario())
