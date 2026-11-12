import asyncio
from uuid import uuid4

import pytest

from tests.infox_publishing_fakes import reviewed as verified
from tests.infox_publishing_fakes import PublishClient, PublishClipboard, settle


def manager_for(root, **kwargs):
    from backend.infox.publishing.store import PublicationStore
    from backend.infox.publishing.manager import PublicationManager
    from backend.infox.publishing.models import PublishTimeouts, PublishInput
    runs, run = verified(root)
    fake = PublishClient(**kwargs)
    clipboard = PublishClipboard()
    manager = PublicationManager(PublicationStore(runs), lambda:fake, clipboard,
        timeouts=PublishTimeouts(connect=.1,read=1.,preflight=2.,render=2.,metadata=2.,save=2.,upload=2.),poll_interval=.001)
    return manager, run, fake, clipboard, PublishInput(review_confirmed=True, reviewed_remote_updated_at='2026-11-04T00:00:00Z', intent_id=str(uuid4()),market_date='2026-11-04')


def test_first_publish_runs_each_operation_once_in_order(tmp_path):
    manager, run, fake, clipboard, input = manager_for(tmp_path)
    async def scenario():
        r = await manager.start(run.id,input)
        final = await settle(manager,r.id)
        assert final.stage == 'completed', final
        assert final.uploaded_verified
        assert final.snapshot.title == '261104 AI 영상 시황'
        assert fake.logs[1]['sns_upload_info']['description']=='2026년 11월 4일 AI 영상 시황'
        assert clipboard.values==['https://youtu.be/abcdefghijk']
        await manager.close()
    asyncio.run(scenario())
    assert fake.calls==['render','metadata_generate','metadata_update','upload']


@pytest.mark.parametrize('stage', ['render','metadata_generate','metadata_update','upload'])
def test_each_stage_failure_blocks_following_writes(tmp_path,stage):
    manager,run,fake,clipboard,input = manager_for(tmp_path,fail=stage)
    async def scenario():
        r=await manager.start(run.id,input); final=await settle(manager,r.id)
        assert final.stage in ('failed','needs_attention')
        assert 'SECRET' not in final.model_dump_json()
        assert clipboard.values==[]
        await manager.close()
    asyncio.run(scenario())
    steps=['render','metadata_generate','metadata_update','upload']
    assert fake.calls==steps[:steps.index(stage)+1]


@pytest.mark.parametrize('stage', ['render','metadata_generate','metadata_update','upload'])
def test_lost_ack_and_restart_never_resubmit(tmp_path,stage):
    manager,run,fake,clipboard,input = manager_for(tmp_path,lost=stage)
    async def scenario():
        r=await manager.start(run.id,input); final=await settle(manager,r.id)
        assert final.stage=='needs_attention'
        assert getattr(final.receipts,stage).state=='unknown'
        before=list(fake.calls)
        await manager.recover(); await manager.start(run.id,input)
        assert fake.calls==before and clipboard.values==[]
        await manager.close()
    asyncio.run(scenario())


def test_duplicate_start_does_not_spawn_second_sender(tmp_path):
    manager,run,fake,_,input = manager_for(tmp_path)
    async def scenario():
        records=await asyncio.gather(*(manager.start(run.id,input) for _ in range(8)))
        assert len({r.id for r in records})==1
        await settle(manager,records[0].id)
        assert fake.calls==['render','metadata_generate','metadata_update','upload']
        await manager.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['changed','no_stream'])
def test_remote_edit_change_blocks_render(tmp_path,mode):
    manager,run,fake,_,input=manager_for(tmp_path,**{mode:True})
    async def scenario():
        r=await manager.start(run.id,input)
        assert (await settle(manager,r.id)).stage=='needs_attention'
        assert fake.calls==[]
        await manager.close()
    asyncio.run(scenario())


def test_sse_arrives_before_ack_is_matched_after_id(tmp_path):
    manager,run,fake,_,input=manager_for(tmp_path,early=True,fail='render')
    async def scenario():
        r=await manager.start(run.id,input); final=await settle(manager,r.id)
        assert final.stage=='failed' and final.error_code=='remote_task_failed'
        assert fake.calls==['render']
        await manager.close()
    asyncio.run(scenario())


def test_restart_during_accepted_task_only_reads(tmp_path):
    manager,run,fake,clipboard,input=manager_for(tmp_path)
    async def scenario():
        fake.gate=asyncio.Event()
        r=await manager.start(run.id,input)
        for _ in range(100):
            if manager.store.get(r.id).render_task_id: break
            await asyncio.sleep(.001)
        await manager.close()
        fake.gate.set()
        await manager.recover()
        final=manager.store.get(r.id)
        assert final.stage=='needs_attention' and final.receipts.render.state=='accepted'
        assert fake.calls==['render'] and clipboard.values==[]
    asyncio.run(scenario())


def test_two_managers_share_database_owner(tmp_path):
    from backend.infox.publishing.manager import PublicationManager
    manager,run,fake,clipboard,input=manager_for(tmp_path)
    other=PublicationManager(manager.store,lambda:fake,clipboard,timeouts=manager.timeouts,poll_interval=.001)
    async def scenario():
        a,b=await asyncio.gather(manager.start(run.id,input),other.start(run.id,input))
        assert a.id==b.id
        await settle(manager,a.id); await settle(other,a.id)
        assert fake.calls==['render','metadata_generate','metadata_update','upload']
        await manager.close(); await other.close()
    asyncio.run(scenario())


def test_known_task_without_result_times_out_and_never_advances(tmp_path):
    from backend.infox.publishing.models import PublishTimeouts
    manager,run,fake,clipboard,input=manager_for(tmp_path)
    manager.timeouts=PublishTimeouts(connect=.1,read=1.,preflight=2.,render=.1,metadata=2.,save=2.,upload=2.)
    original=fake.render_logs
    async def no_result(video_id):
        return {'logs':[]} if fake.calls else await original(video_id)
    fake.render_logs=no_result
    async def scenario():
        r=await manager.start(run.id,input); final=await settle(manager,r.id)
        assert final.stage=='needs_attention' and final.error_code=='stage_timeout'
        assert final.receipts.render.state=='accepted'
        assert fake.calls==['render'] and not clipboard.values
        await manager.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('mode',['late_failure','unknown','overflow'])
def test_queued_failure_unknown_or_overflow_blocks_next_operation(tmp_path,mode):
    manager,run,fake,clipboard,input=manager_for(tmp_path)
    original=fake.operation
    async def operation(name,task,log):
        result=await original(name,task,log)
        if (mode=='late_failure' and name=='metadata_generate') or (mode!='late_failure' and name=='render'):
            for _ in range(513 if mode=='overflow' else 1):
                await fake.queue.put({'additional_kwargs':{'taskid':'render-task','task_status':'alien' if mode=='unknown' else 'failed'}})
            await asyncio.sleep(.02)
        return result
    fake.operation=operation
    async def scenario():
        r=await manager.start(run.id,input); final=await settle(manager,r.id)
        assert final.stage in ('failed','needs_attention')
        assert fake.calls==(['render','metadata_generate'] if mode=='late_failure' else ['render'])
        assert not clipboard.values
        await manager.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('operation',['render','metadata_generate','upload'])
def test_reused_task_ack_stops_instead_of_leaving_an_inactive_stage(tmp_path,operation):
    manager,run,fake,clipboard,input=manager_for(tmp_path)
    fake.logs=[{'task_id':run.task_id,'state':'videosgenerationend'}]
    name={'render':'start_render','metadata_generate':'generate_youtube_metadata','upload':'upload_youtube'}[operation]
    original=getattr(fake,name)
    async def reused(*args):
        await original(*args)
        return {'render':run.task_id,'metadata_generate':'render-task','upload':'metadata-task'}[operation]
    setattr(fake,name,reused)
    async def scenario():
        r=await manager.start(run.id,input); final=await settle(manager,r.id)
        assert final.stage=='needs_attention' and final.error_code=='acceptance_unknown'
        assert getattr(final.receipts,operation).state=='unknown'
        steps=['render','metadata_generate','metadata_update','upload']
        assert fake.calls==steps[:steps.index(operation)+1] and not clipboard.values
        await manager.close()
    asyncio.run(scenario())


def test_recovery_stop_cannot_be_resurrected_by_an_inflight_ack(tmp_path):
    from backend.infox.publishing.manager import PublicationManager
    manager,run,fake,clipboard,input=manager_for(tmp_path)
    other=PublicationManager(manager.store,lambda:fake,clipboard,timeouts=manager.timeouts,poll_interval=.001)
    original=fake.start_render
    async def scenario():
        accepted,release=asyncio.Event(),asyncio.Event()
        async def inflight(video_id):
            task=await original(video_id); accepted.set(); await release.wait(); return task
        fake.start_render=inflight
        r=await manager.start(run.id,input)
        await asyncio.wait_for(accepted.wait(),2)
        await other.recover()
        stopped=manager.store.get(r.id)
        assert stopped.stage=='needs_attention' and stopped.error_code=='server_restarted'
        release.set(); final=await settle(manager,r.id)
        assert final.stage=='needs_attention' and final.error_code=='server_restarted'
        assert fake.calls==['render'] and not clipboard.values
        await manager.close(); await other.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('payload',[[], 'INVALID_JSON', 'FRAMING_ERROR'])
def test_malformed_stream_is_a_sticky_stop_not_discarded_on_reconnect(tmp_path,payload):
    from backend.infox.sse import SSEEvent
    manager,run,fake,clipboard,input=manager_for(tmp_path)
    original_events=fake.events; original_start=fake.start_render
    async def events(video_id,**kwargs):
        async for frame in original_events(video_id,**kwargs):
            if frame.data=='"FRAMING_ERROR"':
                from backend.infox.errors import InfoXError
                raise InfoXError('invalid_response')
            yield SSEEvent(data='{bad-json') if frame.data=='"INVALID_JSON"' else frame
    async def start(video_id):
        task=await original_start(video_id)
        await fake.queue.put(payload)
        await asyncio.sleep(.02)
        return task
    fake.events=events; fake.start_render=start
    async def scenario():
        r=await manager.start(run.id,input); final=await settle(manager,r.id)
        assert final.stage=='needs_attention' and final.error_code=='stream_unrecognized'
        assert fake.calls==['render'] and not clipboard.values
        await manager.close()
    asyncio.run(scenario())
