import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.infox.manager import RunManager
from backend.infox.run_models import RunError
from tests.test_api import auth, client
from tests.test_infox_save import planned


def verified(root):
    async def setup():
        store, r, fake = await planned(root)
        manager = RunManager(store, lambda: fake)
        await manager.save(r.id); await manager.wait(r.id); await manager.close()
        return store, store.get(r.id)
    return asyncio.run(setup())


def test_editor_claim_is_durable_single_automatic_across_owners_and_cas(tmp_path):
    from backend.infox.editor import EditorClaim, EditorUpdate
    from tests.infox_publishing_fakes import reviewed
    store, r = reviewed(tmp_path)
    def claim(index):
        return store.claim_editor(r.id, EditorClaim(attempt_id=f'{index:032x}', mode='automatic', tab_state='created'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(claim, range(8)))
    assert sum(result['claimed'] for result in results) == 1
    attempt = results[0]['attempt']
    assert store.latest_editor(r.id) == attempt
    from backend.infox.store import RunStore
    assert RunStore(tmp_path).latest_editor(r.id) == attempt
    intended = store.update_editor(r.id, attempt['id'], EditorUpdate(expected_version=0, state='navigate_intent'))
    assert intended['video_id'] == r.video_id
    with pytest.raises(RunError, match='record_conflict'):
        store.update_editor(r.id, attempt['id'], EditorUpdate(expected_version=0, state='navigate_intent'))
    assert store.get(r.id) == r  # Browser bookkeeping never modifies production CAS/evidence.


@pytest.mark.parametrize('alteration', ['stage', 'save', 'reconciliation'])
def test_editor_navigation_requires_all_three_verification_gates(tmp_path, alteration):
    from backend.infox.editor import EditorClaim, EditorUpdate
    store, r = verified(tmp_path)
    attempt = store.claim_editor(r.id, EditorClaim(attempt_id='a'*32, mode='automatic', tab_state='created'))['attempt']
    with store._edit(r.id, r.version) as (_, data):
        if alteration == 'stage': data['stage'] = 'needs_attention'
        if alteration == 'save': data['asset_save'] = {'state': 'not_started'}
        if alteration == 'reconciliation': data['project'] = {'state': 'unknown', 'intent_at': 'now'}
    with pytest.raises(RunError, match='editor_not_ready'):
        store.update_editor(r.id, attempt['id'], EditorUpdate(expected_version=0, state='navigate_intent'))


def test_editor_api_guards_restore_and_explicit_reopen(tmp_path):
    from tests.infox_publishing_fakes import reviewed
    store, r = reviewed(tmp_path)
    with client(tmp_path) as c:
        path = f'/api/infox-runs/{r.id}/editor-attempts'
        headers = auth(c)
        assert c.get(path, headers=headers).json() == {'attempt': None}
        body = {'attempt_id': 'a'*32, 'mode': 'automatic', 'tab_state': 'blocked'}
        assert c.post(path, json=body).status_code == 403
        assert c.post(path, headers=headers, json={**body, 'url': 'https://evil.invalid/?token=secret'}).status_code == 422
        first = c.post(path, headers=headers, json=body).json()
        assert first['claimed'] and first['attempt']['state'] == 'blocked'
        assert not c.post(path, headers=headers, json={**body, 'attempt_id': 'b'*32}).json()['claimed']
        assert c.get(path, headers=headers).json()['attempt'] == first['attempt']
        retry = c.post(path, headers=headers, json={**body, 'mode': 'manual', 'tab_state': 'created', 'attempt_id': 'c'*32}).json()
        assert retry['claimed']
        target = path + '/' + retry['attempt']['id']
        intended = c.post(target, headers=headers, json={'expected_version': 0, 'state': 'navigate_intent'}).json()
        assert intended['editor_url'].endswith('/video-1?chatboxView=script')
        requested = c.post(target, headers=headers, json={'expected_version': 1, 'state': 'navigation_requested'}).json()
        assert requested['editor_status'] == 'unconfirmed'
        login = c.post(target, headers=headers, json={'expected_version': 2, 'state': 'login_required'}).json()
        assert login['editor_status'] == 'login_required_user_reported'
        assert store.get(r.id) == r
