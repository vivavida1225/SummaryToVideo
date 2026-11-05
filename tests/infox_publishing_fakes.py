import asyncio
import copy
import json

from backend.infox.errors import InfoXError
from backend.infox.sse import SSEEvent


class PublishClient:
    """External boundary double: no authentication, network, or OS clipboard."""
    def __init__(self, *, fail=None, lost=None, changed=False, early=False, no_stream=False):
        self.calls = []
        self.logs = []
        self.queue = asyncio.Queue()
        self.fail, self.lost, self.changed, self.early, self.no_stream = fail, lost, changed, early, no_stream
        self.reads = 0
        self.gate = None
        self.privacy = 'unlisted'

    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def authenticate(self): pass
    async def video_state(self, video_id):
        self.reads += 1
        return {'id':video_id,'can_edit':True,'updated_at':'2026-11-04T00:00:01Z' if self.changed and self.reads > 1 else '2026-11-04T00:00:00Z','video':{}}
    async def render_charge_preview(self, video_id): return {'will_charge':True}
    async def render_logs(self, video_id):
        if self.gate and self.calls: await self.gate.wait()
        return {'logs':copy.deepcopy(self.logs)}
    async def events(self, video_id, **kwargs):
        if not self.no_stream:
            yield SSEEvent(data=json.dumps({'type':'connection','status':'connected','channel':f'infox:{video_id}'}))
        while True:
            yield SSEEvent(data=json.dumps(await self.queue.get()))
    async def operation(self, name, task, log):
        self.calls.append(name)
        if self.lost == name: raise InfoXError('timeout')
        if log: self.logs.append(log)
        if self.fail == name:
            await self.queue.put({'additional_kwargs':{'taskid':task,'task_status':'failed','error':'SECRET'}})
        elif self.early:
            await self.queue.put({'additional_kwargs':{'taskid':task,'task_status':'processing','progress':42}})
        await asyncio.sleep(0)
        return task
    async def start_render(self, video_id):
        return await self.operation('render','render-task',{'task_id':'render-task','state':'end','file_name':'movie.mp4','bucket':'render'})
    async def generate_youtube_metadata(self, video_id, render_task_id):
        assert render_task_id == 'render-task'
        return await self.operation('metadata_generate','metadata-task',{'task_id':'metadata-task','state':'end','file_name':'movie.mp4','bucket':'render',
            'sns_upload_info':{'title':'generated','description':'generated','keywords':['keep','키워드']}})
    async def update_youtube_metadata(self, video_id, metadata_task_id, **kwargs):
        assert metadata_task_id == 'metadata-task' and kwargs['keywords']==['keep','키워드']
        await self.operation('metadata_update',metadata_task_id,None)
        if self.fail == 'metadata_update': raise InfoXError('invalid_response')
        self.logs[-1]['sns_upload_info'].update(kwargs)
        return {}
    async def upload_youtube(self, video_id, metadata_task_id):
        assert metadata_task_id == 'metadata-task'
        self.logs[-1]['sns_upload_info']['privacyStatus']=self.privacy
        return await self.operation('upload','upload-task',{'task_id':'upload-task','state':'sns','sns_url':'https://www.youtube.com/watch?v=abcdefghijk'})


class PublishClipboard:
    def __init__(self, fail=False):
        self.values = []
        self.fail = fail
    def write(self, value):
        self.values.append(value)
        if self.fail: raise OSError('SECRET')


async def settle(manager, id):
    task = manager.tasks.get(id)
    if task: await asyncio.wait_for(asyncio.shield(task), 10)
    return manager.store.get(id)
