import asyncio
import sqlite3
import json

import pytest

from backend.infox.errors import InfoXError
from backend.infox.manager import RunManager
from backend.infox.store import RunStore
from tests.test_infox_save import planned


@pytest.mark.parametrize('boundary', ['draft_ready', 'auditing_assets', 'assets_audited', 'selecting_favorites', 'selected', 'replacement_planned'])
def test_automatic_continuation_survives_every_asset_boundary(tmp_path, boundary):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        # Simulate a persisted automatic owner interrupted at each boundary.
        with store._edit(r.id, r.version) as (_, data):
            data['continuation'] = 'automatic'
            data['stage'] = 'selecting_favorites' if boundary == 'selected' else boundary
            if boundary == 'selected':
                data['replacement_plan']['state'] = 'selected'
                data['replacement_plan']['result_fingerprint'] = None
            if boundary in ('draft_ready', 'auditing_assets'):
                from backend.infox.asset_models import AssetAudit
                from backend.infox.plan_models import ReplacementPlan
                data['asset_audit'] = AssetAudit(state='running' if boundary == 'auditing_assets' else 'not_started').model_dump()
                data['replacement_plan'] = ReplacementPlan().model_dump()
            elif boundary in ('assets_audited', 'selecting_favorites'):
                from backend.infox.plan_models import ReplacementPlan
                data['replacement_plan'] = ReplacementPlan(state='selecting' if boundary == 'selecting_favorites' else 'not_started').model_dump()
        manager = RunManager(RunStore(tmp_path), lambda: fake)
        await manager.recover(); await manager.wait(r.id)
        final = store.get(r.id)
        assert final.asset_save.state == 'saved_verified'
        assert final.snapshot == r.snapshot and final.video_id == r.video_id and final.task_id == r.task_id
        assert final.project == r.project and final.generation == r.generation
        assert len(fake.writes) == 1
        if boundary in ('selected', 'replacement_planned'):
            assert final.replacement_plan.selections == r.replacement_plan.selections
        await manager.close()
    asyncio.run(scenario())


def test_old_database_record_defaults_to_manual_and_restore_never_saves(tmp_path):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        with sqlite3.connect(tmp_path / '.runtime/infox-runs.sqlite3') as conn:
            data = json.loads(conn.execute('select record from runs where id=?', (r.id,)).fetchone()[0])
            data.pop('continuation', None)
            conn.execute('update runs set record=? where id=?', (json.dumps(data), r.id))
        assert RunStore(tmp_path).get(r.id).continuation == 'manual'
        manager = RunManager(store, lambda: fake)
        await manager.recover(); await manager.wait(r.id)
        assert not fake.writes
        await manager.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('action', ['audit', 'plan'])
def test_explicit_read_only_action_clears_automatic_continuation(tmp_path, action):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        r = store.set_continuation(r.id, 'automatic', expected_version=r.version)
        manager = RunManager(store, lambda: fake)
        await getattr(manager, action)(r.id); await manager.wait(r.id)
        assert store.get(r.id).continuation == 'manual'
        restored = RunManager(RunStore(tmp_path), lambda: fake)
        await restored.recover(); await restored.wait(r.id)
        assert not fake.writes
        await manager.close(); await restored.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('status,code', [(401, 'authentication_required'), (403, 'authentication_required'), (409, 'version_conflict')])
def test_write_cause_survives_read_failure_and_restart(tmp_path, status, code):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        async def rejected(*args):
            fake.writes.append('one')
            fake.read_failure = True
            raise InfoXError('http_error', status)
        fake.save_video_context = rejected
        manager = RunManager(store, lambda: fake)
        await manager.save(r.id); await manager.wait(r.id)
        saved = store.get(r.id).asset_save
        assert saved.write_error_code == code and saved.write_status == status
        assert saved.error_code == code and saved.read_error_code == 'read_failed'
        fake.read_failure = False
        restored = RunManager(RunStore(tmp_path), lambda: fake)
        await restored.start(r.id); await restored.wait(r.id)
        saved = store.get(r.id).asset_save
        assert saved.write_error_code == code and saved.write_status == status
        assert saved.error_code == code and saved.read_error_code is None
        assert len(fake.writes) == 1
        await manager.close(); await restored.close()
    asyncio.run(scenario())


def test_late_save_response_survives_another_owners_unapplied_read(tmp_path):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        original = fake.save_video_context
        async def delayed(*args):
            entered.set(); await release.wait(); return await original(*args)
        fake.save_video_context = delayed
        first, second = RunManager(store, lambda: fake), RunManager(RunStore(tmp_path), lambda: fake)
        await first.save(r.id); await entered.wait()
        await second.start(r.id); await second.wait(r.id)
        assert store.get(r.id).stage == 'needs_attention'
        release.set(); await first.wait(r.id)
        final = store.get(r.id)
        assert final.asset_save.receipt_state == 'accepted'
        assert final.asset_save.response_version == fake.value['updated_at']
        assert final.asset_save.state == 'saved_verified' and len(fake.writes) == 1
        await first.close(); await second.close()
    asyncio.run(scenario())


def test_legacy_conflict_cause_survives_failed_read_recovery(tmp_path):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        fake.mode = 'conflict'
        manager = RunManager(store, lambda: fake)
        await manager.save(r.id); await manager.wait(r.id)
        current = store.get(r.id)
        with store._edit(r.id, current.version) as (_, data):
            data['asset_save']['write_error_code'] = None
            data['asset_save']['write_status'] = None
        fake.read_failure = True
        restored = RunManager(RunStore(tmp_path), lambda: fake)
        await restored.start(r.id); await restored.wait(r.id)
        assert store.get(r.id).asset_save.error_code == 'version_conflict'
        assert store.get(r.id).asset_save.read_error_code == 'read_failed'
        assert len(fake.writes) == 1
        await manager.close(); await restored.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('response', ['accepted', 'late_conflict'])
def test_verified_readback_keeps_late_write_result_as_separate_evidence(tmp_path, response):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        applied, release = asyncio.Event(), asyncio.Event()
        original = fake.save_video_context
        async def delayed(*args):
            version = await original(*args)
            applied.set(); await release.wait()
            if response == 'late_conflict': raise InfoXError('http_error', 409)
            return version
        fake.save_video_context = delayed
        first, second = RunManager(store, lambda: fake), RunManager(RunStore(tmp_path), lambda: fake)
        await first.save(r.id); await applied.wait()
        await second.start(r.id); await second.wait(r.id)
        assert store.get(r.id).asset_save.state == 'saved_verified'
        release.set(); await first.wait(r.id)
        save = store.get(r.id).asset_save
        assert save.state == 'saved_verified'
        assert save.receipt_state == ('accepted' if response == 'accepted' else 'rejected')
        assert save.response_version == (fake.value['updated_at'] if response == 'accepted' else None)
        assert save.write_status == (409 if response == 'late_conflict' else None)
        assert save.write_error_code == ('version_conflict' if response == 'late_conflict' else None)
        assert len(fake.writes) == 1
        await first.close(); await second.close()
    asyncio.run(scenario())


def test_two_automatic_owners_and_auth_recovery_never_reselect_or_repeat_save(tmp_path):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        r = store.set_continuation(r.id, 'automatic', expected_version=r.version)
        original = fake.video_state
        async def expired(*args): raise InfoXError('auth_expired', 401)
        fake.video_state = expired
        first = RunManager(store, lambda: fake)
        await first.recover(); await first.wait(r.id)
        failed = store.get(r.id)
        assert failed.asset_save.error_code == 'authentication_required' and not fake.writes
        assert failed.replacement_plan.selections == r.replacement_plan.selections
        fake.video_state = original
        second = RunManager(RunStore(tmp_path), lambda: fake)
        await asyncio.gather(first.start(r.id), second.start(r.id))
        await asyncio.gather(first.wait(r.id), second.wait(r.id))
        # If the investigating owner read before application, reconcile by read only.
        await second.start(r.id); await second.wait(r.id)
        final = store.get(r.id)
        assert final.asset_save.state == 'saved_verified' and len(fake.writes) == 1
        assert final.replacement_plan.selections == r.replacement_plan.selections
        await first.close(); await second.close()
    asyncio.run(scenario())


def test_late_different_ack_requires_recheck_without_duplicating_provenance(tmp_path):
    async def scenario():
        store, r, fake = await planned(tmp_path)
        applied, release = asyncio.Event(), asyncio.Event()
        original = fake.save_video_context
        async def delayed(*args):
            await original(*args)
            applied.set(); await release.wait()
            return '2026-11-02T03:00:00Z'
        fake.save_video_context = delayed
        first, second = RunManager(store, lambda: fake), RunManager(RunStore(tmp_path), lambda: fake)
        await first.save(r.id); await applied.wait()
        await second.start(r.id); await second.wait(r.id)
        history = store.get(r.id).favorite_replacements
        release.set(); await first.wait(r.id)
        assert store.get(r.id).stage == 'needs_attention'
        fake.value['updated_at'] = '2026-11-02T03:00:00Z'
        await second.start(r.id); await second.wait(r.id)
        final = store.get(r.id)
        assert final.asset_save.state == 'saved_verified'
        assert final.favorite_replacements == history and len(fake.writes) == 1
        await first.close(); await second.close()
    asyncio.run(scenario())
