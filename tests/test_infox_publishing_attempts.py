import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from backend.infox.run_models import RunError
from backend.infox.publishing.models import PublishInput
from tests.test_infox_publishing_store import setup_publication


def rerender_input(previous):
    from backend.infox.publishing.models import RerenderInput
    return RerenderInput(intent_id=str(uuid4()), market_date=previous.snapshot.market_date,
                         previous_publication_id=previous.id)


def test_new_attempt_preserves_history_and_old_intent(tmp_path):
    store, first = setup_publication(tmp_path)
    first = store.transition(first.id, first.version, {'stage': 'needs_attention'})
    data = rerender_input(first)
    second, created = store.create_or_get(first.run_id, data)
    assert created and second.id != first.id
    assert second.attempt_number == 2 and second.previous_publication_id == first.id
    assert store.create_or_get(first.run_id, data) == (second, False)
    assert store.for_run(first.run_id).id == second.id
    assert [r.id for r in store.history(first.run_id)] == [second.id, first.id]
    assert store.get(first.id) == first
    assert store.create_or_get(first.run_id, PublishInput(intent_id=str(uuid4()), market_date='2026-11-04')) == (second, False)


def test_concurrent_new_intents_create_only_one_attempt(tmp_path):
    store, first = setup_publication(tmp_path)
    first = store.transition(first.id, first.version, {'stage': 'failed'})
    inputs = [rerender_input(first) for _ in range(6)]
    def create(data):
        try: return store.create_or_get(first.run_id, data)
        except RunError as e: return e.code
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(create, inputs))
    assert sum(isinstance(x, tuple) and x[1] for x in results) == 1
    assert results.count('publication_changed') == 5
    assert len(store.history(first.run_id)) == 2


def test_active_attempt_and_stale_previous_are_rejected(tmp_path):
    store, first = setup_publication(tmp_path)
    with pytest.raises(RunError, match='publication_active'):
        store.create_or_get(first.run_id, rerender_input(first))
    first = store.transition(first.id, first.version, {'stage': 'failed'})
    second, _ = store.create_or_get(first.run_id, rerender_input(first))
    second = store.transition(second.id, second.version, {'stage': 'failed'})
    with pytest.raises(RunError, match='publication_changed'):
        store.create_or_get(first.run_id, rerender_input(first))


def test_legacy_migration_preserves_external_references_and_backup(tmp_path):
    from tests.test_infox_editor import verified
    from backend.infox.publishing.models import PublicationRecord, PublishSnapshot
    from backend.infox.publishing.store import PublicationStore
    runs, run = verified(tmp_path)
    record = PublicationRecord(id=uuid4().hex, run_id=run.id, video_id=run.video_id,
        snapshot=PublishSnapshot(market_date='2026-11-04', title='title', description='description'),
        created_at='2026-11-04T00:00:00Z', updated_at='2026-11-04T00:00:00Z', stage='needs_attention')
    raw = record.model_dump_json()
    db = tmp_path / '.runtime/infox-runs.sqlite3'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE publications (id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),video_id TEXT NOT NULL UNIQUE,record TEXT NOT NULL)')
        c.execute('CREATE TABLE publication_intents (intent_id TEXT PRIMARY KEY,digest TEXT NOT NULL,publication_id TEXT NOT NULL REFERENCES publications(id))')
        c.execute('CREATE TABLE publication_live_verifications (publication_id TEXT PRIMARY KEY REFERENCES publications(id),record TEXT NOT NULL)')
        c.execute('INSERT INTO publications VALUES (?,?,?,?)', (record.id,run.id,run.video_id,raw))
        c.execute('INSERT INTO publication_intents VALUES (?,?,?)', ('legacy-intent','digest',record.id))
        c.execute('INSERT INTO publication_live_verifications VALUES (?,?)', (record.id,'{}'))
    store = PublicationStore(runs)
    assert store.get(record.id).attempt_number == 1
    assert (tmp_path / '.runtime/infox-runs.before-publication-attempts.sqlite3').exists()
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT record FROM publications').fetchone()[0] == raw
        assert c.execute('PRAGMA foreign_key_check').fetchall() == []
        assert c.execute('SELECT publication_id FROM publication_intents').fetchone()[0] == record.id
        assert c.execute('SELECT publication_id FROM publication_live_verifications').fetchone()[0] == record.id
    assert len(PublicationStore(runs).history(run.id)) == 1


def test_two_real_manager_cycles_use_independent_tasks(tmp_path):
    from tests.test_infox_publishing_manager import manager_for
    from tests.infox_publishing_fakes import PublishClient, settle
    manager, run, first_fake, clipboard, data = manager_for(tmp_path)
    async def scenario():
        first = await settle(manager, (await manager.start(run.id, data)).id)
        second_fake = PublishClient(suffix='-2')
        # Old completed history remains, while the next cycle gets its own IDs.
        second_fake.logs = json.loads(json.dumps(first_fake.logs))
        manager.client_factory = lambda: second_fake
        second = await settle(manager, (await manager.start(run.id, rerender_input(first))).id)
        assert second.stage == 'completed', second
        assert second.attempt_number == 2 and second.uploaded_verified
        assert second.render_file_fingerprint != first.render_file_fingerprint
        assert second.share_url != first.share_url
        assert manager.store.get(first.id) == first
        assert second_fake.calls == ['render','metadata_generate','metadata_update','upload']
        assert len(clipboard.values) == 2
        await manager.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('state,allowed', [('end',True),('workerstart',False),('alien',False)])
def test_remote_history_previews_are_read_only_and_explain_blocks(tmp_path,state,allowed):
    from tests.test_infox_publishing_manager import manager_for
    manager,run,fake,_,_=manager_for(tmp_path)
    fake.logs=[{'task_id':'remote-render','state':state,'bucket':'render','file_name':'old.mp4'}]
    async def scenario():
        result=await manager.preview(run.id)
        assert result['can_start'] is allowed
        assert result['has_remote_render'] is True
        assert result['latest_publication_id'] is None
        assert fake.calls==[] and manager.store.history(run.id)==[]
        if not allowed: assert result['blocked_reason']
    asyncio.run(scenario())


def test_unknown_request_without_task_id_stays_blocked(tmp_path):
    from tests.test_infox_publishing_manager import manager_for
    from tests.infox_publishing_fakes import settle
    manager,run,fake,_,data=manager_for(tmp_path,lost='render')
    async def scenario():
        first=await settle(manager,(await manager.start(run.id,data)).id)
        preview=await manager.preview(run.id)
        assert preview['can_start'] is False and preview['blocked_reason']=='previous_unconfirmed'
        second=await settle(manager,(await manager.start(run.id,rerender_input(first))).id)
        assert second.error_code=='previous_unconfirmed'
        assert fake.calls==['render']
        await manager.close()
    asyncio.run(scenario())


def test_failed_known_render_can_start_again_after_remote_completion(tmp_path):
    from tests.test_infox_publishing_manager import manager_for
    from tests.infox_publishing_fakes import PublishClient,settle
    manager,run,fake,_,data=manager_for(tmp_path,fail='render')
    async def scenario():
        first=await settle(manager,(await manager.start(run.id,data)).id)
        assert first.stage=='failed'
        next_fake=PublishClient(suffix='-2');next_fake.logs=json.loads(json.dumps(fake.logs))
        manager.client_factory=lambda:next_fake
        assert (await manager.preview(run.id))['can_start'] is True
        second=await settle(manager,(await manager.start(run.id,rerender_input(first))).id)
        assert second.stage=='completed'
        await manager.close()
    asyncio.run(scenario())


def test_observed_rerender_fixture_preserves_task_and_file_identity(tmp_path):
    from pathlib import Path
    from backend.infox.publishing.preflight import history_block_reason
    from backend.infox.publishing.tracking import verify_publish_stage, file_fingerprint
    from tests.test_infox_publishing_tracking import record
    fixture=json.loads((Path(__file__).parent/'fixtures/infox_publishing/rerender-2026-11-05.json').read_text(encoding='utf-8'))
    assert fixture['render_response']['is_rerender'] is True
    assert history_block_reason(fixture['baseline']['logs'],'draft-old') is None
    r=record(tmp_path).model_copy(update={'stage':'uploading','render_task_id':'render-new','metadata_task_id':'metadata-new',
        'upload_task_id':'upload-new','render_file_fingerprint':file_fingerprint(fixture['history']['logs'][0]),'keywords':('keep','키워드')})
    assert verify_publish_stage(r,fixture['history']).status=='completed'
    assert fixture['metadata_input_render_task_id']==r.render_task_id
    assert fixture['upload_input_metadata_task_id']==r.metadata_task_id


def test_migration_failure_rolls_back_without_losing_original_rows(tmp_path):
    from tests.test_infox_editor import verified
    from backend.infox.publishing.store import PublicationStore
    runs,run=verified(tmp_path)
    db=tmp_path/'.runtime/infox-runs.sqlite3'
    with sqlite3.connect(db) as c:
        c.execute('CREATE TABLE publications (id TEXT PRIMARY KEY,run_id TEXT NOT NULL UNIQUE REFERENCES runs(id),video_id TEXT NOT NULL UNIQUE,record TEXT NOT NULL)')
        c.execute('INSERT INTO publications VALUES (?,?,?,?)',('bad',run.id,run.video_id,'{}'))
    with pytest.raises(RunError,match='publication_migration_failed'):
        PublicationStore(runs)
    with sqlite3.connect(db) as c:
        assert c.execute('SELECT record FROM publications').fetchall()==[('{}',)]
        assert 'attempt_number' not in {r[1] for r in c.execute('PRAGMA table_info(publications)')}
        assert c.execute("SELECT name FROM sqlite_master WHERE name='publications_rebuild'").fetchall()==[]


def test_cancel_requires_a_later_render_not_just_later_metadata():
    from backend.infox.publishing.preflight import history_block_reason
    logs=[{'task_id':'render-old','state':'end','bucket':'render','file_name':'old.mp4','updated_at':'2026-11-05T00:00:00Z'},
          {'task_id':'cancelled','state':'cancel','updated_at':'2026-11-05T00:01:00Z'},
          {'task_id':'metadata-old','state':'end','bucket':'render','file_name':'old.mp4',
           'updated_at':'2026-11-05T00:02:00Z','sns_upload_info':{'keywords':[]}}]
    assert history_block_reason(logs,'draft')=='previous_unconfirmed'
    logs.append({'task_id':'render-new','state':'end','bucket':'render','file_name':'new.mp4',
                 'sns_upload_info':None,'updated_at':'2026-11-05T00:03:00Z'})
    assert history_block_reason(logs,'draft') is None
