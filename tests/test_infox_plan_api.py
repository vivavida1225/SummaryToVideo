import asyncio
import time
from unittest.mock import patch

import pytest

from backend.infox.assets import audit_assets
from backend.infox.manager import RunManager
from backend.infox.store import RunStore
from tests.test_api import auth, client
from tests.test_infox_assets_api import ready_run, asset_state, ReadOnlyClient
from tests.test_infox_favorites import favorite, FavoritesClient


def audited_run(root):
    store,r=ready_run(root); value=asset_state(r)
    r=store.begin_audit(r.id,expected_version=r.version)
    report=audit_assets(value,template_id=1786,run_id=r.id,video_id=r.video_id)
    r=store.record_audit(r.id,report,expected_version=r.version)
    return store,r,value


class PlanningClient(ReadOnlyClient,FavoritesClient):
    def __init__(self,value):
        ReadOnlyClient.__init__(self,value)
        FavoritesClient.__init__(self,[favorite(0),favorite(1)])


def test_plan_api_security_fixed_selection_disk_restore_and_no_saved_provenance(tmp_path):
    store,r,value=audited_run(tmp_path); fake=PlanningClient(value)
    with client(tmp_path) as c:
        c.app.state.infox_manager.client_factory=lambda:fake
        endpoint=f'/api/infox-runs/{r.id}/replacement-plan'; headers=auth(c)
        assert c.post(endpoint).status_code==403
        assert c.post(endpoint,headers={**headers,'Origin':'https://evil.invalid'}).status_code==403
        assert c.post(endpoint,headers=headers,json={'candidate_id':'forged'}).status_code==422
        assert c.post(endpoint,headers=headers).status_code==202
        for _ in range(100):
            result=c.get(f'/api/infox-runs/{r.id}',headers=headers).json()
            if not result['tracking_active']: break
            time.sleep(.01)
        assert result['stage']=='replacement_planned'
        plan=result['replacement_plan']
        assert plan['state']=='ready' and len(plan['selections'])==2
        assert {x['candidate']['id'] for x in plan['selections']}=={'item-0'}
        assert result['favorite_replacements']==[]
        assert 'PRIVATE' not in str(result) and 'segment_url_presigned' not in str(plan)
        assert result['snapshot']==r.snapshot.model_dump(mode='json')
    assert RunStore(tmp_path).get(r.id).replacement_plan.state=='ready'


def test_selection_is_committed_before_conversion_and_retry_never_reselects(tmp_path):
    from backend.infox.plan_models import PlanError
    async def scenario():
        store,r,value=audited_run(tmp_path); fake=PlanningClient(value)
        manager=RunManager(store,lambda:fake)
        def fail(*args,**kwargs):
            assert store.get(r.id).replacement_plan.state=='selected'
            assert store.get(r.id).replacement_plan.selections[0].candidate.id=='item-0'
            raise PlanError('transform_invalid')
        with patch('backend.infox.planner.apply_replacements',side_effect=fail):
            await manager.plan(r.id); await manager.wait(r.id)
        failed=store.get(r.id)
        assert failed.stage=='needs_attention' and failed.replacement_plan.error_code=='transform_invalid'
        fake.items.reverse()
        restored=RunManager(RunStore(tmp_path),lambda:fake)
        await restored.plan(r.id); await restored.wait(r.id)
        final=store.get(r.id)
        assert final.replacement_plan.state=='ready'
        assert final.replacement_plan.selections[0].candidate.id=='item-0'
        await restored.close(); await manager.close()
    asyncio.run(scenario())


def test_server_version_change_blocks_plan_and_preserves_snapshot(tmp_path):
    async def scenario():
        store,r,value=audited_run(tmp_path); value['updated_at']='2026-11-02T02:00:00Z'
        fake=PlanningClient(value); manager=RunManager(store,lambda:fake)
        await manager.plan(r.id); await manager.wait(r.id)
        final=store.get(r.id)
        assert final.replacement_plan.error_code=='source_changed'
        assert final.snapshot==r.snapshot and fake.requests==[]
        await manager.close()
    asyncio.run(scenario())


def test_recover_frozen_plan_only_reads_and_reuses_exact_segment(tmp_path):
    from backend.infox.favorites import candidate_from_item
    from backend.infox.plan_models import SearchEvidence
    from backend.infox.replacement import make_choices,state_fingerprint
    async def scenario():
        store,r,value=audited_run(tmp_path)
        claimed=store.begin_plan(r.id,expected_version=r.version)
        choices=make_choices(value,r.asset_audit,candidate_from_item(favorite()))
        store.freeze_choices(r.id,choices,source_fingerprint=state_fingerprint(value),
            source_version=r.asset_audit.source_version,
            evidence=SearchEvidence(fetched_at='2026-11-02T01:00:00Z',total=1,pages=1,usable_schema_count=1,rejected_schema_count=0),
            expected_version=claimed.version)
        fake=PlanningClient(value);fake.items.reverse()
        restored=RunManager(RunStore(tmp_path),lambda:fake)
        await restored.recover();await restored.wait(r.id)
        assert store.get(r.id).replacement_plan.state=='ready'
        assert store.get(r.id).replacement_plan.selections[0].candidate.id=='item-0'
        await restored.close()
    asyncio.run(scenario())
