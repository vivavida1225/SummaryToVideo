import asyncio
import copy
import json
from pathlib import Path

import pytest

from backend.infox.publishing.tracking import normalize_publish_event, verify_publish_stage
from tests.infox_publishing_fakes import settle
from tests.test_infox_publishing_manager import manager_for
from tests.test_infox_publishing_tracking import record

OBSERVED=json.loads((Path(__file__).parent/'fixtures/infox_publishing/initialization-2026-11-05.json').read_text(encoding='utf-8'))

def initial(task='render-task', **extra):
    body=copy.deepcopy(OBSERVED['initial'])
    body['additional_kwargs'].update(taskid=task,**extra)
    return body


def test_observed_missing_status_zero_progress_is_initialization():
    event=normalize_publish_event(initial(),'video-1','render-task')
    assert event.status=='initializing' and event.progress==0 and not event.error_present
    # Preserve shared generation tracking semantics; publication owns this contract.
    from backend.infox.tracking import task_event
    assert task_event(initial(),'video-1','render-task').status=='unrecognized'


@pytest.mark.parametrize('extra', [
    {'task_status':None}, {'task_status':''}, {'task_status':'alien'},
    {'progress':1}, {'progress':100}, {'progress':None}, {'progress':False}, {'progress':'0'},
])
def test_only_the_observed_initializer_shape_is_accepted(extra):
    assert normalize_publish_event(initial(**extra),'video-1','render-task').status=='unrecognized'


def test_initialization_respects_scope_and_failure_precedence():
    assert normalize_publish_event({**initial(),'channel':'infox:other'},'video-1','render-task') is None
    assert normalize_publish_event({**initial(),'video_id':'other'},'video-1','render-task') is None
    assert normalize_publish_event(initial('other'),'video-1','render-task') is None
    assert normalize_publish_event(initial(error='synthetic'),'video-1','render-task').status=='failed'
    batch={'messages':[initial(),initial(task_status='processing',progress=42)]}
    assert normalize_publish_event(batch,'video-1','render-task').status=='running'
    assert normalize_publish_event(batch,'video-1','render-task').progress==42
    batch['messages'].append(initial(task_status='failed'))
    assert normalize_publish_event(batch,'video-1','render-task').status=='failed'
    assert normalize_publish_event({'messages':[initial(),initial(task_status='alien')]},'video-1','render-task').status=='unrecognized'


@pytest.mark.parametrize('state',['start','workerstart'])
def test_observed_render_pending_states_wait_without_inventing_completion(tmp_path,state):
    r=record(tmp_path)
    # Even a prematurely populated file cannot make a pending state complete.
    result=verify_publish_stage(r,{'logs':[{'task_id':r.render_task_id,'state':state,'file_name':'movie.mp4','bucket':'render'}]})
    assert result.status=='waiting'


def test_pending_state_is_phase_and_task_specific(tmp_path):
    r=record(tmp_path,'uploading')
    assert verify_publish_stage(r,{'logs':[{'task_id':r.upload_task_id,'state':'workerstart'}]}).status=='unknown'
    assert verify_publish_stage(r.model_copy(update={'stage':'rendering'}),{'logs':[{'task_id':'other','state':'workerstart'}]}).status=='waiting'
    assert verify_publish_stage(r.model_copy(update={'stage':'rendering'}),{'logs':[{'task_id':r.render_task_id,'state':'unobserved'}]}).status=='unknown'


@pytest.mark.parametrize('state',['generate_metainfo_youtube_start','info'])
def test_observed_metadata_pending_states_are_not_success(tmp_path,state):
    r=record(tmp_path,'generating_metadata')
    h={'logs':[{'task_id':r.metadata_task_id,'state':state,'file_name':'movie.mp4','bucket':'render',
                'sns_upload_info':{'title':'premature','keywords':[]}}]}
    assert verify_publish_stage(r,h).status=='waiting'
    assert verify_publish_stage(r.model_copy(update={'stage':'saving_metadata'}),h).status=='unknown'


def test_initial_message_before_ack_and_pending_history_complete_once(tmp_path):
    manager,run,fake,clipboard,input=manager_for(tmp_path)
    original_operation=fake.operation
    async def operation(name,task,log):
        await fake.queue.put(initial(task))
        await asyncio.sleep(.01)  # Deliberately arrives before the acknowledgement.
        return await original_operation(name,task,log)
    fake.operation=operation
    original_history=fake.render_logs
    pending_reads=0
    metadata_reads=0
    async def history(video_id):
        nonlocal pending_reads,metadata_reads
        result=await original_history(video_id)
        if fake.calls==['render'] and pending_reads<2:
            pending_reads+=1
            result['logs'][0].update(state='start' if pending_reads==1 else 'workerstart',file_name=None)
        if fake.calls[-1:] == ['metadata_generate'] and metadata_reads<2:
            metadata_reads+=1
            result['logs'][1].update(state='generate_metainfo_youtube_start' if metadata_reads==1 else 'info',file_name=None,sns_upload_info=None)
        return result
    fake.render_logs=history
    async def scenario():
        r=await manager.start(run.id,input)
        final=await settle(manager,r.id)
        assert final.stage=='completed',final
        assert final.uploaded_verified and clipboard.values==['https://youtu.be/abcdefghijk']
        assert pending_reads==2
        assert metadata_reads==2
        assert fake.calls==['render','metadata_generate','metadata_update','upload']
        assert all(getattr(final.receipts,op).read_verified for op in ('render','metadata_generate','metadata_update','upload'))
        await manager.close()
    asyncio.run(scenario())
