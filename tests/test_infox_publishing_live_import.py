import copy
from tests.test_infox_generation import draft, prepared
import pytest


def test_live_import_uses_only_current_saved_snapshot_and_observed_history(tmp_path):
    from tests.infox_publishing_live import imported_saved_record
    _, r=prepared(tmp_path)
    state=draft(r)
    history={'logs':[{'task_id':'observed-task','state':'videosgenerationend'}]}
    record=imported_saved_record(state,copy.deepcopy(state),history,'2026-11-04')
    assert record.video_id=='video-1' and record.task_id=='observed-task'
    assert record.snapshot.text=='\n'.join(s['voiceover']['content'] for s in state['video']['scenes'])
    assert record.stage=='ready_for_editor' and record.asset_save.state=='unchanged_verified'
    assert 'imported_user_designated_saved_target' in record.snapshot.warnings
    bad=copy.deepcopy(state); bad['updated_at']='2026-11-04T00:00:00Z'
    with pytest.raises(ValueError): imported_saved_record(state,bad,history,'2026-11-04')
    with pytest.raises(ValueError): imported_saved_record(state,state,{'logs':[{'task_id':'render','state':'end'}]},'2026-11-04')
