import asyncio
from uuid import uuid4

import pytest

from tests.test_api import auth, client
from tests.test_infox_editor import verified
from tests.infox_publishing_fakes import PublishClient, PublishClipboard


def warning_run(root):
    store, run = verified(root)
    with store._edit(run.id, run.version) as (_, data):
        data.update(stage='needs_attention', asset_save={},
                    replacement_plan={**run.replacement_plan.model_dump(), 'state': 'needs_attention',
                                      'error_code': 'source_changed'})
        data['tracking']['draft']['captions_ready'] = False
    return store, store.get(run.id)


def enter(c, headers, run):
    return c.post(f'/api/infox-runs/{run.id}/manual-review', headers=headers,
                  json={'expected_version': run.version})


def test_warning_handoff_preserves_evidence_and_locks_every_automation_api(tmp_path):
    store, run = warning_run(tmp_path)
    with client(tmp_path) as c:
        headers = auth(c)
        response = enter(c, headers, run)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body['review_mode'] == 'manual_review'
        assert body['stage'] == 'needs_attention'
        assert body['replacement_plan']['error_code'] == 'source_changed'
        assert body['can_open_editor'] and body['can_prepare_publication']
        assert enter(c, headers, run).json() == body  # Lost acknowledgement is a lookup.
        for action in ('start', 'audit', 'replacement-plan', 'save-assets'):
            blocked = c.post(f'/api/infox-runs/{run.id}/{action}', headers=headers, json={})
            assert blocked.status_code == 409
            assert blocked.json()['code'] == 'manual_review_active'
        attempt = c.post(f'/api/infox-runs/{run.id}/editor-attempts', headers=headers,
                         json={'attempt_id': 'a'*32, 'mode': 'manual', 'tab_state': 'created'})
        assert attempt.status_code == 200
        assert store.get(run.id).replacement_plan == run.replacement_plan
    with client(tmp_path) as c:
        body = c.get(f'/api/infox-runs/{run.id}', headers=auth(c)).json()
        assert body['review_mode'] == 'manual_review' and not body['tracking_active']


@pytest.mark.parametrize('change', ['unknown', 'active', 'version'])
def test_handoff_preserves_operational_guards(tmp_path, change):
    store, run = warning_run(tmp_path)
    if change == 'unknown':
        with store._edit(run.id, run.version) as (_, data):
            data['generation']['state'] = 'unknown'
        run = store.get(run.id)
    with client(tmp_path) as c:
        if change == 'active':
            c.app.state.infox_manager.tasks[run.id] = type('Busy', (), {'done': lambda _: False, 'cancel': lambda _: None})()
            # Remove the stand-in before lifespan shutdown.
        if change == 'version':
            run = run.model_copy(update={'version': run.version - 1})
        result = enter(c, auth(c), run)
        if change == 'active':
            c.app.state.infox_manager.tasks.pop(run.id)
        assert result.status_code == 409, result.text
        assert store.get(run.id).review_mode != 'manual_review'


def test_manual_warning_can_publish_latest_version_but_every_attempt_needs_confirmation(tmp_path):
    store, run = warning_run(tmp_path)
    with client(tmp_path) as c:
        headers = auth(c)
        assert enter(c, headers, run).status_code == 200
        manager = c.app.state.infox_publications
        fake = PublishClient()
        manager.client_factory = lambda: fake
        manager.clipboard = PublishClipboard()
        path = f'/api/infox-runs/{run.id}/publications'
        preview = c.get(path+'/preview', headers=headers).json()
        assert preview['can_start'] and preview['remote_updated_at']
        body = {'intent_id': str(uuid4()), 'market_date': '2026-11-12'}
        assert c.post(path, headers=headers, json=body).status_code == 409
        body.update(review_confirmed=True, reviewed_remote_updated_at=preview['remote_updated_at'])
        response = c.post(path, headers=headers, json=body)
        assert response.status_code == 202, response.text
        record = response.json()
        assert record['snapshot']['review_confirmed_at']
        assert 'source_changed' in record['snapshot']['review_warning_codes']
        assert c.post(path, headers=headers, json=body).json()['id'] == record['id']
        # The full pipeline is tested with the async fake elsewhere; preserve this owner.
        assert store.get(run.id).stage == 'needs_attention'


def test_confirmed_version_changed_before_render_has_no_remote_writes(tmp_path):
    from backend.infox.publishing.manager import PublicationManager
    from backend.infox.publishing.models import PublishInput
    from backend.infox.publishing.store import PublicationStore
    from tests.infox_publishing_fakes import settle
    store, run = warning_run(tmp_path)
    with client(tmp_path) as c:
        assert enter(c, auth(c), run).status_code == 200
    fake = PublishClient()
    manager = PublicationManager(PublicationStore(store), lambda: fake, PublishClipboard())
    async def scenario():
        result = await manager.start(run.id, PublishInput(intent_id=str(uuid4()), market_date='2026-11-12',
            review_confirmed=True, reviewed_remote_updated_at='2026-11-03T00:00:00Z'))
        final = await settle(manager, result.id)
        assert final.error_code == 'remote_changed' and fake.calls == []
        await manager.close()
    asyncio.run(scenario())


def test_active_publication_blocks_editor_even_after_manual_handoff(tmp_path):
    from backend.infox.publishing.store import PublicationStore
    from backend.infox.publishing.models import PublishInput
    from backend.infox.review import enter_manual_review
    store, run = warning_run(tmp_path)
    run = enter_manual_review(store, run.id, run.version)
    publications = PublicationStore(store)
    with client(tmp_path) as c:
        publications.create_or_get(run.id, PublishInput(intent_id=str(uuid4()), market_date='2026-11-12',
            review_confirmed=True, reviewed_remote_updated_at='2026-11-04T00:00:00Z'))
        headers = auth(c)
        public = c.get(f'/api/infox-runs/{run.id}', headers=headers).json()
        assert not public['can_open_editor']
        result = c.post(f'/api/infox-runs/{run.id}/editor-attempts', headers=headers,
                        json={'attempt_id': 'b'*32, 'mode': 'manual', 'tab_state': 'created'})
        assert result.status_code == 409 and result.json()['code'] == 'publication_active'


def test_publication_snapshot_preserves_informational_asset_warnings(tmp_path):
    from backend.infox.publishing.store import PublicationStore
    from backend.infox.publishing.models import PublishInput
    from backend.infox.review import enter_manual_review
    store, run = verified(tmp_path)
    audit = run.asset_audit.model_dump()
    audit['items'][0].update(verdict='unknown', reason='metadata_unreadable', hits=(), metadata_sources=())
    audit['issues'] = ({'scene_index': 0, 'layer_id': audit['items'][0]['layer_id'], 'code': 'metadata_unreadable'},)
    with store._edit(run.id, run.version) as (_, data):
        data['asset_audit'] = audit
    run = store.get(run.id)
    run = enter_manual_review(store, run.id, run.version)
    publication, created = PublicationStore(store).create_or_get(run.id, PublishInput(intent_id=str(uuid4()),
        market_date='2026-11-12', review_confirmed=True, reviewed_remote_updated_at='2026-11-04T00:00:00Z'))
    assert created  # Informational warnings never deny publication.
    assert 'asset_issue:metadata_unreadable' in publication.snapshot.review_warning_codes
    assert 'asset_unknown:metadata_unreadable' in publication.snapshot.review_warning_codes


def test_legacy_intent_replay_without_review_is_read_only(tmp_path):
    import hashlib
    import json
    from backend.infox.publishing.store import PublicationStore
    from backend.infox.publishing.models import PublicationRecord, PublishSnapshot, PublishInput
    store, run = verified(tmp_path)
    publications = PublicationStore(store)
    intent = uuid4().hex
    old = PublicationRecord(id='c'*32, run_id=run.id, video_id=run.video_id,
        created_at=run.created_at, updated_at=run.updated_at, stage='completed',
        snapshot=PublishSnapshot(market_date='2026-11-04', title='title', description='description'))
    digest = hashlib.sha256(json.dumps([run.id, '2026-11-04']).encode()).hexdigest()
    with store._transaction() as conn:
        publications._tables(conn)
        conn.execute('INSERT INTO publications VALUES (?,?,?,?,?,?,?)',
                     (old.id,run.id,run.video_id,old.model_dump_json(),1,None,0))
        conn.execute('INSERT INTO publication_intents VALUES (?,?,?)', (intent,digest,old.id))
    restored, created = publications.create_or_get(run.id, PublishInput(intent_id=intent,market_date='2026-11-04'))
    assert not created and restored == old
    assert store.get(run.id) == run  # No manual mode or retrospective confirmation.
