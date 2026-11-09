import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from backend.infox.client import InfoXClient
from backend.infox.config import InfoXCredentials
from backend.infox.errors import InfoXError
from backend.infox.run_models import RunError


def test_files_are_utf8_atomic_and_never_overwrite(tmp_path):
    from backend.infox.downloads.files import FileWriter
    def write(_):
        with FileWriter(tmp_path, '261104_대본.txt') as writer:
            writer.write('확정 대본'.encode())
            return writer.finish()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(write, range(8)))
    assert len({r['path'] for r in results}) == 8
    assert (tmp_path / '261104_대본.txt').read_text(encoding='utf-8') == '확정 대본'
    assert all(r['sha256'] == hashlib.sha256('확정 대본'.encode()).hexdigest() for r in results)
    with pytest.raises(OSError):
        with FileWriter(tmp_path, '261104_영상시황.mp4') as writer:
            writer.write(b'partial')
            raise OSError('disk full')
    assert len(list(tmp_path.iterdir())) == 8


def test_default_downloads_uses_known_folder():
    from backend.infox.downloads.files import downloads_folder
    folder = downloads_folder()
    assert folder.is_absolute() and folder.is_dir()


def test_download_url_string_and_media_have_separate_authentication(tmp_path):
    data = b'\x00\x00\x00\x18ftypmp42' + b'x' * 50
    def api(req):
        assert req.headers['authorization'] == 'Bearer secret'
        assert req.url.params['download_filename'] == '261104_영상시황.mp4'
        return httpx.Response(200, json='https://kr.object.iwinv.kr/infox/a/movie.mp4?signature=secret')
    def media(req):
        assert not any(h in req.headers for h in ['authorization', 'cookie', 'x-org-device-token'])
        return httpx.Response(200, content=data, headers={'content-type':'video/mp4'})
    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='secret'), transport=httpx.MockTransport(api),
                               media_transport=httpx.MockTransport(media)) as client:
            url = await client.get_presigned_download_url('infox','a/movie.mp4','261104_영상시황.mp4')
            result = await client.download_rendered_video(url, 'infox', 'a/movie.mp4', tmp_path, '261104_영상시황.mp4')
            assert Path(result['path']).read_bytes() == data
    asyncio.run(scenario())


@pytest.mark.parametrize('url', ['http://kr.object.iwinv.kr/infox/a.mp4',
    'https://evil.invalid/infox/a.mp4', 'https://kr.object.iwinv.kr/infox/other.mp4',
    'https://user@kr.object.iwinv.kr/infox/a.mp4', 'https://kr.object.iwinv.kr:444/infox/a.mp4'])
def test_untrusted_download_url_rejected_before_media_request(tmp_path,url):
    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='secret'),
            transport=httpx.MockTransport(lambda r: httpx.Response(200,json=url)),
            media_transport=httpx.MockTransport(lambda r: pytest.fail('untrusted request'))) as client:
            with pytest.raises(InfoXError):
                await client.download_rendered_video(url,'infox','a.mp4',tmp_path,'movie.mp4')
    asyncio.run(scenario())
    assert not list(tmp_path.iterdir())


def publication(root, uploaded=True):
    from tests.test_infox_publishing_store import setup_publication
    from backend.infox.publishing.tracking import file_fingerprint
    store, record = setup_publication(root)
    record = store.transition(record.id,record.version,{'stage':'completed' if uploaded else 'generating_metadata',
        'render_task_id':'render-task','render_file_fingerprint':file_fingerprint({'bucket':'infox','file_name':'a.mp4'}),
        'uploaded_verified':uploaded})
    return store, record


class DownloadClient:
    def __init__(self,fail=False): self.fail=fail; self.calls=[]; self.gate=None
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass
    async def render_logs(self,id):
        return {'logs':[{'task_id':'render-task','state':'end','bucket':'infox','file_name':'a.mp4'},
            {'task_id':'other-task','state':'end','bucket':'infox','file_name':'wrong.mp4'}]}
    async def get_presigned_download_url(self,bucket,key,name):
        self.calls.append((bucket,key,name))
        if self.gate: await self.gate.wait()
        if self.fail: raise InfoXError('timeout')
        return 'https://kr.object.iwinv.kr/infox/a.mp4?secret=secret'
    async def download_rendered_video(self,url,bucket,key,folder,name):
        from backend.infox.downloads.files import FileWriter
        with FileWriter(folder,name) as writer:
            writer.write(b'\x00\x00\x00\x18ftypmp42video')
            return writer.finish()


def make_manager(root,store,fake,folder):
    from backend.infox.downloads.manager import DownloadManager
    from backend.infox.downloads.store import DownloadStore
    class Jobs:
        def artifact(self,id,name):
            assert name == 'serialized.txt'
            return '직렬화 원문'
    return DownloadManager(DownloadStore(store),Jobs(),lambda:fake,folder_provider=lambda:folder)


async def wait_download(manager,record):
    await manager.tasks[record.id]
    return manager.store.get(record.id)


def test_all_separate_files_use_frozen_script_and_correct_render(tmp_path):
    store,pub=publication(tmp_path); folder=tmp_path/'downloads';folder.mkdir()
    fake=DownloadClient(); manager=make_manager(tmp_path,store,fake,folder)
    async def scenario():
        intent=str(uuid4())
        record=await manager.start(pub.id,intent,'all')
        assert (await manager.start(pub.id,intent,'all')).id==record.id
        result=await wait_download(manager,record)
        assert result.state=='completed'
        assert len(result.items)==3
        assert {p.name for p in folder.iterdir()}=={'261104_대본.txt','261104_원본데이터.txt','261104_영상시황.mp4'}
        assert (folder/'261104_대본.txt').read_text(encoding='utf-8')==store.run_store.get(pub.run_id).snapshot.text
        assert (folder/'261104_원본데이터.txt').read_text(encoding='utf-8')=='직렬화 원문'
        assert fake.calls==[('infox','a.mp4','261104_영상시황.mp4')]
        assert store.get(pub.id)==pub
        assert 'secret' not in result.model_dump_json()
        again=await manager.start(pub.id,str(uuid4()),'all')
        await wait_download(manager,again)
        assert len(list(folder.iterdir()))==6
        await manager.close()
    asyncio.run(scenario())


def test_partial_retry_preserves_success_and_recovers_missing_file(tmp_path):
    store,pub=publication(tmp_path); folder=tmp_path/'downloads';folder.mkdir()
    fake=DownloadClient(fail=True); manager=make_manager(tmp_path,store,fake,folder)
    async def scenario():
        record=await wait_download(manager,await manager.start(pub.id,str(uuid4()),'all'))
        assert record.state=='partial'
        script=next(x for x in record.items if x.kind=='script')
        source=next(x for x in record.items if x.kind=='source')
        Path(source.path).unlink();fake.fail=False
        retry=await manager.retry(record.id,str(uuid4()))
        result=await wait_download(manager,retry)
        assert result.state=='completed'
        assert next(x for x in result.items if x.kind=='script').path==script.path
        assert len(list(folder.iterdir()))==3
        await manager.close()
    asyncio.run(scenario())


def test_automatic_once_no_historical_replay_and_restart_needs_attention(tmp_path):
    store,pub=publication(tmp_path,uploaded=False);folder=tmp_path/'downloads';folder.mkdir()
    fake=DownloadClient();manager=make_manager(tmp_path,store,fake,folder)
    async def scenario():
        with pytest.raises(RunError): await manager.start(pub.id,str(uuid4()),'all')
        fake.gate=asyncio.Event()
        first=await manager.automatic(pub.id)
        assert (await manager.automatic(pub.id)).id==first.id
        assert (await manager.start(pub.id,str(uuid4()),'video')).id==first.id
        await asyncio.sleep(.01)
        await manager.close()
        await manager.recover()
        assert manager.store.get(first.id).state=='needs_attention'
        assert not manager.tasks
        assert (await manager.automatic(pub.id)).id==first.id
    asyncio.run(scenario())


def test_automatic_download_hook_is_after_render_and_never_stops_upload(tmp_path):
    from tests.test_infox_publishing_manager import manager_for
    from tests.infox_publishing_fakes import settle
    manager,run,fake,_,input=manager_for(tmp_path)
    class Hook:
        def __init__(self): self.ids=[]
        async def automatic(self,id):
            record=manager.store.get(id)
            assert record.render_file_fingerprint and fake.calls==['render']
            self.ids.append(id)
            raise OSError('download storage failed')
    hook=Hook();manager.downloads=hook
    async def scenario():
        record=await manager.start(run.id,input)
        assert (await settle(manager,record.id)).uploaded_verified
        assert hook.ids==[record.id]
        await manager.recover()
        assert hook.ids==[record.id]
        await manager.close()
    asyncio.run(scenario())


def test_download_api_guards_restoration_partial_retry(tmp_path):
    import time
    from tests.test_api import auth, client
    store,pub=publication(tmp_path)
    folder=tmp_path/'downloads';folder.mkdir()
    fake=DownloadClient(fail=True)
    with client(tmp_path) as c:
        manager=c.app.state.infox_downloads
        c.app.state.manager.store.write_text(store.run_store.get(pub.run_id).snapshot.job_id,'serialized.txt','원본')
        manager.folder_provider=lambda:folder
        manager.client_factory=lambda:fake
        headers=auth(c);path=f'/api/infox-publications/{pub.id}/downloads'
        body={'intent_id':str(uuid4()),'kind':'all'}
        assert c.post(path,json=body).status_code==403
        assert c.post(path,headers={**headers,'Origin':'https://evil.invalid'},json=body).status_code==403
        assert c.post(path,headers=headers,json={**body,'folder':'C:/secret'}).status_code==422
        first=c.post(path,headers=headers,json=body)
        assert first.status_code==202,first.text
        assert c.post(path,headers=headers,json=body).json()['id']==first.json()['id']
        for _ in range(100):
            record=c.get(path,headers=headers).json()['downloads'][0]
            if record['state'] not in ('pending','running'): break
            time.sleep(.01)
        assert record['state']=='partial'
        assert len(list(folder.iterdir()))==2
        before=list(fake.calls)
        assert c.get(path,headers=headers).json()['downloads'][0]['id']==record['id']
        assert fake.calls==before
        fake.fail=False
        response=c.post(f"/api/infox-downloads/{record['id']}/retry",headers=headers,json={'intent_id':str(uuid4())})
        assert response.status_code==202,response.text
        for _ in range(100):
            record=c.get(path,headers=headers).json()['downloads'][0]
            if record['state'] not in ('pending','running'): break
            time.sleep(.01)
        assert record['state']=='completed'
        assert len(list(folder.iterdir()))==3
        assert store.get(pub.id)==pub


@pytest.mark.parametrize('mode', ['truncated','html','redirect','expired'])
def test_failed_media_never_leaves_a_final_or_partial_file(tmp_path,mode):
    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'\x00\x00\x00\x18ftypmp42partial'
            raise httpx.ReadError('SECRET SIGNED URL')
    def media(req):
        if mode=='truncated': return httpx.Response(200,stream=BrokenStream(),headers={'content-type':'video/mp4'})
        if mode=='html': return httpx.Response(200,content=b'<html>error',headers={'content-type':'text/html'})
        return httpx.Response(302 if mode=='redirect' else 403,headers={'location':'https://evil.invalid/SECRET'})
    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='secret'),media_transport=httpx.MockTransport(media)) as client:
            with pytest.raises(InfoXError) as error:
                await client.download_rendered_video('https://kr.object.iwinv.kr/infox/a.mp4?secret=secret','infox','a.mp4',tmp_path,'movie.mp4')
            assert 'SECRET' not in str(error.value) and 'secret' not in str(error.value)
    asyncio.run(scenario())
    assert not list(tmp_path.iterdir())


def test_render_mismatch_does_not_download_latest_video(tmp_path):
    store,pub=publication(tmp_path);folder=tmp_path/'downloads';folder.mkdir()
    fake=DownloadClient()
    async def wrong(id): return {'logs':[{'task_id':'render-task','state':'end','bucket':'infox','file_name':'wrong.mp4'}]}
    fake.render_logs=wrong
    manager=make_manager(tmp_path,store,fake,folder)
    async def scenario():
        result=await wait_download(manager,await manager.start(pub.id,str(uuid4()),'all'))
        assert result.state=='partial' and result.items[-1].error_code=='render_file_mismatch'
        assert fake.calls==[]
        await manager.close()
    asyncio.run(scenario())


def test_missing_download_folder_never_falls_back(tmp_path):
    store,pub=publication(tmp_path);fake=DownloadClient()
    manager=make_manager(tmp_path,store,fake,tmp_path/'missing')
    def missing(): raise OSError('SECRET FOLDER')
    manager.folder_provider=missing
    async def scenario():
        result=await wait_download(manager,await manager.start(pub.id,str(uuid4()),'all'))
        assert result.state=='failed' and result.error_code=='downloads_folder_unavailable'
        assert 'SECRET' not in result.model_dump_json() and fake.calls==[]
        await manager.close()
    asyncio.run(scenario())


def test_multiple_database_owners_share_one_download_and_intent_conflicts(tmp_path):
    from backend.infox.downloads.store import DownloadStore
    store,pub=publication(tmp_path)
    intent=str(uuid4())
    def create(_): return DownloadStore(store).create(pub.id,intent,'all')
    with ThreadPoolExecutor(max_workers=4) as pool:
        records=list(pool.map(create,range(8)))
    assert len({r.id for r,created in records})==1 and sum(created for r,created in records)==1
    with pytest.raises(RunError,match='intent_conflict'):
        DownloadStore(store).create(pub.id,intent,'video')


def test_video_disk_operations_run_outside_publication_event_loop(tmp_path,monkeypatch):
    import threading
    from backend.infox.downloads.files import FileWriter
    loop_thread=threading.get_ident()
    calls=[]
    for name in ['__enter__','write','finish','__exit__']:
        original=getattr(FileWriter,name)
        def wrapped(self,*args,_name=name,_original=original):
            assert threading.get_ident()!=loop_thread, f'{_name} blocks publication event loop'
            calls.append(_name)
            return _original(self,*args)
        monkeypatch.setattr(FileWriter,name,wrapped)
    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='secret'),media_transport=httpx.MockTransport(
            lambda req:httpx.Response(200,content=b'\x00\x00\x00\x18ftypmp42video',headers={'content-type':'video/mp4'}))) as client:
            await client.download_rendered_video('https://kr.object.iwinv.kr/infox/a.mp4','infox','a.mp4',tmp_path,'movie.mp4')
    asyncio.run(scenario())
    assert calls==['__enter__','write','finish','__exit__']
