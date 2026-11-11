import asyncio
import copy
import random

import pytest

from backend.infox.favorites import candidate_from_item
from backend.infox.replacement import apply_replacements, validate_replacements
from tests.test_infox_favorites import favorite
from tests.test_infox_replacement import source, inspected
from tests.test_infox_assets import image, layer


def test_new_run_uses_new_policy_but_legacy_policy_still_decodes(tmp_path):
    from tests.test_infox_generation import prepared
    from backend.infox.plan_models import ReplacementPlan
    store,run = prepared(tmp_path)
    assert run.snapshot.settings.replacement_search == '중국 대만 반도체'
    assert run.replacement_plan.policy.reuse_policy == 'random_six_second_sequence'
    legacy = ReplacementPlan.model_validate({'policy':{'search':'반도체','reuse_policy':'first_usable_reuse'}})
    assert legacy.policy.search == '반도체'
    assert store.get(run.id).replacement_plan.policy.search == '중국 대만 반도체'


def sequence_source(frames):
    value = source()
    scene = value['video']['scenes'][0]
    scene['durationInFrames'] = frames
    scene['rootShape']['layers']['assigned'] = layer(image(title='삼성'))
    return value


@pytest.mark.parametrize('frames,count', [(1,1),(179,1),(180,1),(181,2),
    (359,2),(360,2),(361,3),(539,3),(540,3),(541,4),(960,6)])
def test_six_second_boundaries_preserve_exact_timeline(frames, count):
    from backend.infox.sequences import make_sequence_choices
    value = sequence_source(frames)
    pool = tuple(candidate_from_item(favorite(i)) for i in range(5))
    choices = make_sequence_choices(value, inspected(value), pool, rng=random.Random(42))
    assert len(choices) == 1 and len(choices[0].clips) == count
    clips = choices[0].clips
    assert sum(c.slot_frames for c in clips) == frames
    assert max(c.slot_frames for c in clips) - min(c.slot_frames for c in clips) <= 1
    assert len({c.candidate.segment for c in clips}) == min(count,5)
    if count > 5:
        assert clips[5].candidate == clips[0].candidate
    result = apply_replacements(value, choices)
    assert validate_replacements(value, result, choices).ready
    node = result['video']['scenes'][0]['rootShape']['layers']['assigned']
    assert node['type']['resolvedName'] == ('MediaLayer' if count > 1 else 'VideoLayer')


def test_expansion_preserves_healthy_items_and_reindexes_following_targets():
    from backend.infox.sequences import make_sequence_choices
    value = source()
    value['video']['scenes'][0]['durationInFrames'] = 1260
    before = copy.deepcopy(value)
    pool = tuple(candidate_from_item(favorite(i)) for i in range(5))
    choices = make_sequence_choices(value, inspected(value), pool, rng=random.Random(7))
    assert [len(c.clips) for c in choices] == [3,3]
    assert [[x.result_item_index for x in c.clips] for c in choices] == [[0,1,2],[4,5,6]]
    result = apply_replacements(value, choices)
    files = result['video']['scenes'][0]['rootShape']['layers']['assigned']['props']['media']['image_files']
    original = before['video']['scenes'][0]['rootShape']['layers']['assigned']['props']['media']['image_files']
    assert files[3] == {**original[1], 'durationInFrames':420}
    assert validate_replacements(value,result,choices).ready
    files[3]['filename'] = 'changed.jpg'
    assert not validate_replacements(value,result,choices).ready
    assert value == before


@pytest.mark.parametrize('available', [1,2])
def test_short_pool_cycles_without_adjacent_duplicates_when_possible(available):
    from backend.infox.sequences import make_sequence_choices
    value = sequence_source(720)
    choices = make_sequence_choices(value, inspected(value),
        tuple(candidate_from_item(favorite(i)) for i in range(available)), rng=random.Random(1))
    choice = choices[0]
    assert choice.available_candidates == available
    assert len(choice.clips) == 4
    if available > 1:
        assert all(a.candidate != b.candidate for a,b in zip(choice.clips,choice.clips[1:]))


def test_short_media_cannot_be_stretched_and_files_are_deduplicated():
    from backend.infox.sequences import make_sequence_choices
    from backend.infox.plan_models import PlanError
    value = sequence_source(360)
    with pytest.raises(PlanError,match='no_usable_candidate'):
        make_sequence_choices(value,inspected(value),(candidate_from_item(favorite(duration=1)),))
    pool = (candidate_from_item(favorite()), candidate_from_item(favorite(id='another-id')))
    choice = make_sequence_choices(value,inspected(value),pool)[0]
    assert choice.available_candidates == 1


@pytest.mark.parametrize('tamper',['layout','timing','voice','segment','clip_duration','extra_clip'])
def test_sequence_verifier_rejects_tampering(tamper):
    from backend.infox.sequences import make_sequence_choices
    value = sequence_source(420)
    pool = tuple(candidate_from_item(favorite(i)) for i in range(5))
    choices = make_sequence_choices(value,inspected(value),pool)
    result = apply_replacements(value,choices)
    scene = result['video']['scenes'][0]
    node = scene['rootShape']['layers']['assigned']
    files = node['props']['media']['image_files']
    if tamper == 'layout': node['props']['position'] = {'x':999}
    if tamper == 'timing': scene['durationInFrames'] += 1
    if tamper == 'voice': scene['voiceover']['content'] = 'changed'
    if tamper == 'segment': files[1]['segment'] = 'changed.mp4'
    if tamper == 'clip_duration': files[1]['endFrameVideo'] -= 1
    if tamper == 'extra_clip': files.append(copy.deepcopy(files[0]))
    assert not validate_replacements(value,result,choices).ready


def test_missing_frozen_clip_requires_attention_without_reselection(tmp_path):
    from backend.infox.planner import prepare_plan
    from tests.test_infox_assets_api import ready_run,asset_state
    from backend.infox.assets import audit_assets
    from tests.test_infox_save import SavingClient
    async def scenario():
        store,run = ready_run(tmp_path)
        value = asset_state(run)
        for scene in value['video']['scenes']: scene['durationInFrames'] = 420
        run = store.begin_audit(run.id,expected_version=run.version)
        run = store.record_audit(run.id,audit_assets(value,template_id=1786,run_id=run.id,video_id=run.video_id),expected_version=run.version)
        fake = SavingClient(store,run,value);fake.items = [favorite(i) for i in range(5)]
        run = await prepare_plan(store,fake,run)
        frozen = run.replacement_plan.selections
        missing = frozen[0].clips[1].candidate.id
        fake.items = [item for item in fake.items if item['id'] != missing]
        run = await prepare_plan(store,fake,run)
        assert run.replacement_plan.error_code == 'selection_unavailable'
        assert run.replacement_plan.selections == frozen
    asyncio.run(scenario())


def test_usable_pool_is_bounded_without_rejecting_available_candidates():
    from backend.infox.favorites import collect_favorites,usable_favorites
    from backend.infox.plan_models import SelectionPolicy
    from tests.test_infox_favorites import FavoritesClient
    async def scenario():
        fake = FavoritesClient([favorite(i) for i in range(25)])
        policy = SelectionPolicy(search='중국 대만 반도체',reuse_policy='random_six_second_sequence')
        pool = await usable_favorites(fake,await collect_favorites(fake,policy=policy),policy=policy)
        assert len(pool) == 20 and len(fake.probes) == 20
    asyncio.run(scenario())


def test_new_plan_freezes_all_clips_and_save_reaudit_verifies_every_placement(tmp_path):
    from backend.infox.plan_models import SelectionPolicy
    from backend.infox.planner import prepare_plan
    from backend.infox.manager import RunManager
    from backend.infox.assets import audit_assets
    from backend.infox.store import RunStore
    from tests.test_infox_plan_api import audited_run
    from tests.test_infox_save import SavingClient
    async def scenario():
        store, run, value = audited_run(tmp_path)
        with store._edit(run.id,run.version) as (_, data):
            data['replacement_plan'] = {'policy':SelectionPolicy(search='중국 대만 반도체',
                reuse_policy='random_six_second_sequence').model_dump()}
        run = store.get(run.id)
        for scene in value['video']['scenes']:
            scene['durationInFrames'] = 420
        value['context'] = {'data':{'inputText':run.snapshot.text}}
        # A fresh audit owns the modified frame budget.
        run = store.begin_audit(run.id,expected_version=run.version)
        run = store.record_audit(run.id,audit_assets(value,template_id=1786,run_id=run.id,video_id=run.video_id),expected_version=run.version)
        fake = SavingClient(store,run,value)
        fake.items = [favorite(i) for i in range(5)]
        run = await prepare_plan(store,fake,run)
        assert run.replacement_plan.state == 'ready'
        frozen = run.replacement_plan.selections
        assert all(len(c.clips) == 3 for c in frozen)
        assert all(x['search'] == '중국 대만 반도체' for x in fake.requests)
        fake.items.reverse()
        restored = RunStore(tmp_path)
        run = await prepare_plan(restored,fake,restored.get(run.id))
        assert run.replacement_plan.selections == frozen
        manager = RunManager(restored,lambda:fake)
        await manager.save(run.id); await manager.wait(run.id)
        final = restored.get(run.id)
        assert final.asset_save.state == 'saved_verified'
        assert len(final.favorite_replacements) == 6
        report = audit_assets(fake.value,template_id=1786,run_id=run.id,video_id=run.video_id,
            verified_replacements=final.favorite_replacements)
        assert report.counts()['favorite_exempt'] == 6
        assert len(fake.writes) == 1
        await manager.close()
    asyncio.run(scenario())
