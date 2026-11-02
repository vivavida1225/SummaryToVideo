import asyncio
import copy

import pytest

from backend.infox.favorites import candidate_from_item
from backend.infox.manager import RunManager
from backend.infox.replacement import apply_replacements, make_choices, validate_replacements
from tests.test_infox_assets import audit, image, layer, state
from tests.test_infox_assets_api import asset_state, ready_run
from tests.test_infox_favorites import favorite
from tests.test_infox_replacement import source, inspected
from tests.test_infox_save import SavingClient


@pytest.mark.parametrize('metadata', [None, {'unexpected':['unreadable']}, {'unexpected':{'nested':'text'}}])
def test_unknown_metadata_does_not_block_and_keeps_its_verdict_and_reason(metadata):
    value = state(candidates=[] if metadata is None else [image(tags=metadata)])
    report = audit(value)
    assert report.state == 'completed' and report.error_code is None
    assert report.items[0].verdict == 'unknown'
    assert report.counts()['unknown'] == 1 and report.counts()['not_detected'] == 0
    assert not report.blocking_issues


def test_unknown_bundle_member_is_preserved_while_only_detected_members_are_replaced():
    value = source()
    value['video']['scenes'][0]['rerank_result'][1].pop('title')
    report = inspected(value)
    assert [x.verdict for x in report.items] == ['detected', 'unknown', 'detected']
    candidate = candidate_from_item(favorite())
    choices = make_choices(value, report, candidate)
    assert [x.target.item_index for x in choices] == [0,2]
    result = apply_replacements(value, choices, ephemeral={candidate.key():favorite()})
    original_unknown = value['video']['scenes'][0]['rootShape']['layers']['assigned']['props']['media']['image_files'][1]
    actual_unknown = result['video']['scenes'][0]['rootShape']['layers']['assigned']['props']['media']['image_files'][1]
    assert actual_unknown == {**original_unknown, 'durationInFrames':120}
    assert validate_replacements(value, result, choices).ready


def mixed_run(root, *, unknown_only=False):
    store, r = ready_run(root)
    value = asset_state(r)
    value['context'] = {'data':{'inputText':r.snapshot.text}}
    for scene in value['video']['scenes']:
        if unknown_only:
            scene['rerank_result'] = []
        else:
            layers = scene['rootShape']['layers']
            layers['ROOT']['child'].append('unknown')
            layers['unknown'] = layer(image('unknown.jpg'))
    return store, r, SavingClient(store, r, value)


@pytest.mark.parametrize('tamper', [False, True])
def test_automatic_run_replaces_detected_and_preserves_unknown_through_verified_save(tmp_path, tamper):
    async def scenario():
        store, r, fake = mixed_run(tmp_path)
        before = copy.deepcopy(fake.value)
        original = fake.save_video_context
        async def save(*args):
            version = await original(*args)
            if tamper:
                fake.value['video']['scenes'][0]['rootShape']['layers']['unknown']['props']['media']['filename'] = 'changed.jpg'
            return version
        fake.save_video_context = save
        manager = RunManager(store, lambda:fake)
        await manager.start(r.id); await manager.wait(r.id)
        final = store.get(r.id)
        assert final.asset_audit.state == 'completed'
        assert final.asset_audit.counts()['unknown'] == len(r.snapshot.scenes)
        assert len(final.replacement_plan.selections) == len(r.snapshot.scenes)
        assert all(x.target.verdict == 'detected' for x in final.replacement_plan.selections)
        assert final.asset_save.state == ('needs_attention' if tamper else 'saved_verified')
        assert len(fake.writes) == 1 and final.snapshot == r.snapshot
        if tamper:
            assert final.asset_save.error_code == 'verification_failed' and not final.favorite_replacements
        else:
            for old,new in zip(before['video']['scenes'],fake.value['video']['scenes']):
                assert old['rootShape']['layers']['unknown'] == new['rootShape']['layers']['unknown']
            assert len(final.favorite_replacements) == len(r.snapshot.scenes)
        await manager.close()
    asyncio.run(scenario())


def test_all_unknown_assets_complete_unchanged_without_save_or_favorite_selection(tmp_path):
    async def scenario():
        store, r, fake = mixed_run(tmp_path, unknown_only=True)
        manager = RunManager(store, lambda:fake)
        await manager.start(r.id); await manager.wait(r.id)
        final = store.get(r.id)
        assert final.asset_audit.state == 'completed' and final.asset_audit.counts()['unknown'] == len(r.snapshot.scenes)
        assert final.replacement_plan.state == 'not_needed'
        assert final.asset_save.state == 'unchanged_verified' and final.asset_save.receipt_state == 'not_sent'
        assert not fake.writes and not final.replacement_plan.selections and not final.favorite_replacements
        await manager.close()
    asyncio.run(scenario())


def test_manual_audit_prepares_detected_plan_with_unknowns_but_never_saves(tmp_path):
    async def scenario():
        store, r, fake = mixed_run(tmp_path)
        manager = RunManager(store, lambda:fake)
        await manager.audit(r.id); await manager.wait(r.id)
        final = store.get(r.id)
        assert final.asset_audit.state == 'completed' and final.replacement_plan.state == 'ready'
        assert final.continuation == 'manual' and final.asset_save.state == 'not_started'
        assert not fake.writes
        await manager.close()
    asyncio.run(scenario())


def test_unknown_verdict_does_not_suppress_structural_or_budget_failures():
    value = state(candidates=[])
    value['video']['scenes'][0]['rootShape']['layers']['ROOT']['child'].append('missing')
    report = audit(value)
    assert report.items[0].verdict == 'unknown'
    assert report.state == 'needs_attention' and report.blocking_issues
