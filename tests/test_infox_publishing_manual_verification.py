import pytest

from backend.infox.run_models import RunError
from tests.test_infox_publishing_store import setup_publication


def test_manual_verification_claim_is_durable_and_never_resurrects_automatic_run(tmp_path):
    from tests.infox_publishing_continue_live import ManualVerificationStore
    store,r=setup_publication(tmp_path)
    r=store.transition(r.id,r.version,{'stage':'needs_attention','failed_stage':'rendering',
        'error_code':'stream_unrecognized','render_task_id':'existing-render',
        'receipts':{**r.receipts.model_dump(),'render':{'state':'accepted'}}})
    manual=ManualVerificationStore(store)
    manual.claim(r.id,'metadata_generate')
    with pytest.raises(RunError): ManualVerificationStore(store).claim(r.id,'metadata_generate')
    with pytest.raises(RunError): manual.claim(r.id,'upload')
    manual.finish(r.id,'metadata_generate','accepted',{'metadata_task_id':'new-metadata'},verified=True)
    manual.claim(r.id,'metadata_update')
    manual.finish(r.id,'metadata_update','unknown',{},verified=False)
    with pytest.raises(RunError): manual.claim(r.id,'upload')
    with pytest.raises(RunError): manual.claim(r.id,'metadata_update')
    assert store.get(r.id)==r
