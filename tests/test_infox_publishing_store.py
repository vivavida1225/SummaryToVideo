from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from backend.infox.run_models import RunError
from backend.infox.store import RunStore
from tests.test_infox_editor import verified


def setup_publication(root):
    from backend.infox.publishing.store import PublicationStore
    from backend.infox.publishing.models import PublishInput
    runs, run = verified(root)
    store = PublicationStore(runs)
    record, created = store.create_or_get(run.id, PublishInput(intent_id=str(uuid4()), market_date='2026-11-04'))
    assert created
    return store, record


def test_date_format_is_frozen(tmp_path):
    from backend.infox.publishing.models import format_publish_metadata
    store, r = setup_publication(tmp_path)
    assert format_publish_metadata(date(2026, 11, 4)) == ('261104 AI 영상 시황', '2026년 11월 4일 AI 영상 시황')
    assert store.get(r.id).snapshot.title == '261104 AI 영상 시황'
    assert store.run_store.get(r.run_id).stage == 'ready_for_editor'


def test_duplicate_tabs_share_one_publication(tmp_path):
    from backend.infox.publishing.store import PublicationStore
    from backend.infox.publishing.models import PublishInput
    runs, run = verified(tmp_path)
    def create(_):
        return PublicationStore(RunStore(tmp_path)).create_or_get(run.id, PublishInput(intent_id=str(uuid4()), market_date='2026-11-04'))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(8)))
    assert len({r.id for r, _ in results}) == 1
    assert sum(created for _, created in results) == 1
    store = PublicationStore(runs)
    r = results[0][0]
    assert len(store.all()) == 1
    assert store.for_run(run.id).id == r.id


def test_receipt_claim_allows_only_one_sender(tmp_path):
    store, r = setup_publication(tmp_path)
    r = store.transition(r.id, r.version, {'stage': 'starting_render'})
    def claim(_):
        try:
            return store.claim_operation(r.id, 'render', r.version)
        except RunError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, range(2)))
    assert sum(c is not None for c in claims) == 1
    claimed = store.get(r.id)
    assert claimed.receipts.render.state == 'intent_recorded'
    store.finish_operation(r.id, 'render', 'unknown', None)
    with pytest.raises(RunError):
        store.claim_operation(r.id, 'render', store.get(r.id).version)


def test_intent_body_conflict_and_invalid_inputs(tmp_path):
    from backend.infox.publishing.models import PublishInput
    store, r = setup_publication(tmp_path)
    intent = str(uuid4())
    body = PublishInput(intent_id=intent, market_date='2026-11-04')
    assert store.create_or_get(r.run_id, body)[0].id == r.id
    with pytest.raises(RunError, match='intent_conflict'):
        store.create_or_get(r.run_id, PublishInput(intent_id=intent, market_date='2026-11-05'))
    for value in ('20261104', '2026-11-40', 42):
        with pytest.raises(ValidationError):
            PublishInput(intent_id=str(uuid4()), market_date=value)
    with pytest.raises(ValidationError):
        PublishInput(intent_id=str(uuid4()), market_date='2026-11-04', url='secret')


@pytest.mark.parametrize('change', ['stage', 'save', 'reconciliation', 'video'])
def test_not_ready_rolls_back(tmp_path, change):
    from backend.infox.publishing.store import PublicationStore
    from backend.infox.publishing.models import PublishInput
    runs, run = verified(tmp_path)
    with runs._edit(run.id, run.version) as (_, data):
        if change == 'stage': data['stage'] = 'needs_attention'
        if change == 'save': data['asset_save'] = {}
        if change == 'reconciliation': data['project'] = {'state': 'unknown'}
        if change == 'video': data['video_id'] = None
    store = PublicationStore(runs)
    with pytest.raises(RunError):
        store.create_or_get(run.id, PublishInput(intent_id=str(uuid4()), market_date='2026-11-04'))
    assert store.all() == []
