"""Real local FastAPI + Edge; synthetic InfoX and clipboard, zero external calls."""
import asyncio
import copy
import json
import shutil
import socket
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.app import create_app
from backend.config import Settings
from tests.test_infox_editor import verified
from tests.infox_publishing_fakes import PublishClient, PublishClipboard


def scenario(playwright, root, mode):
    print(f'Controlled publication scenario: {mode}', flush=True)
    with tempfile.TemporaryDirectory(prefix='infox-publish-') as temporary:
        project=Path(temporary)
        shutil.copytree(root/'frontend/dist',project/'frontend/dist')
        with ThreadPoolExecutor(max_workers=1) as pool:
            runs,run=pool.submit(verified,project).result()
        fake=PublishClient(fail=mode.removeprefix('failure_') if mode.startswith('failure_') else None,
                           lost='render' if mode=='lost_ack' else None, early=True)
        if mode=='initialization':
            observed=json.loads((root/'tests/fixtures/infox_publishing/initialization-2026-11-05.json').read_text(encoding='utf-8'))
            operation=fake.operation
            async def initialized(name,task,log):
                body=copy.deepcopy(observed['initial']); body['additional_kwargs']['taskid']=task
                await fake.queue.put(body); await asyncio.sleep(.01)
                return await operation(name,task,log)
            fake.operation=initialized
            read_history=fake.render_logs
            positions={'render':0,'metadata_generate':0}
            async def pending_history(video_id):
                result=await read_history(video_id)
                name=fake.calls[-1] if fake.calls else None
                if name in positions:
                    states=observed['render_pending_states'] if name=='render' else observed['metadata_pending_states']
                    index=positions[name]
                    if index<len(states):
                        positions[name]+=1
                        result['logs'][0 if name=='render' else 1].update(
                            state=states[index],file_name=None,sns_upload_info=None)
                return result
            fake.render_logs=pending_history
        clipboard=PublishClipboard(fail=mode=='clipboard_failure')
        gate=threading.Event()
        original=fake.render_logs
        async def gated(video_id):
            if fake.calls and mode in ('duplicate','reload','restart'):
                while not gate.is_set(): await asyncio.sleep(.02)
            return await original(video_id)
        fake.render_logs=gated
        def make_app():
            app=create_app(Settings(project),clipboard=clipboard)
            app.state.infox_publications.client_factory=lambda:fake
            app.state.infox_publications.poll_interval=.01
            app.state.manager.store.metadata(dict(id=run.snapshot.job_id,parent_id=None,state='completed',source='clipboard',
                created_at='2026-11-04T00:00:00Z',elapsed_seconds=1,attempt=1,max_attempts=1,key_number=None,retry_at=None,
                serialized='통제 원문',compressed=run.snapshot.text,scene_count=len(run.snapshot.scenes),body_char_count=20,
                warnings=[],error=None,validation_issues=[],output_dir=f'outputs/{run.snapshot.job_id}',events=[],
                revision=2,edited_at='2026-11-04T01:00:00Z'))
            return app
        app=make_app()
        sock=socket.socket(); sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); sock.bind(('127.0.0.1',0))
        address=sock.getsockname(); url=f'http://127.0.0.1:{address[1]}'
        def serve(app,sock):
            server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
            thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True); thread.start()
            deadline=time.monotonic()+15
            while not server.started and time.monotonic()<deadline: time.sleep(.02)
            assert server.started
            return server,thread
        server,thread=serve(app,sock)
        browser=None
        try:
            browser=playwright.chromium.launch(channel='msedge',headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1100})
            unexpected,errors,posts=[],[],[]
            def route(reqroute):
                req=reqroute.request
                if not req.url.startswith(url):
                    unexpected.append(req.url); reqroute.abort(); return
                if req.method=='POST' and req.url.endswith('/publications'): posts.append(req.url)
                reqroute.continue_()
            context.route('**/*',route)
            context.add_init_script(f"sessionStorage.setItem('market-compressor.job-id','{run.snapshot.job_id}')")
            page=context.new_page(); page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(url,wait_until='networkidle')
            panel=page.get_by_role('region',name='최초 렌더 및 유튜브 업로드')
            button=panel.get_by_role('button',name='편집 완료 · 렌더 및 유튜브 업로드',exact=True)
            expect(button).to_be_enabled()
            panel.get_by_label('게시 기준일').fill('2026-11-04')
            other=None
            if mode=='duplicate':
                other=context.new_page(); other.goto(url,wait_until='networkidle')
                expect(other.get_by_role('button',name='편집 완료 · 렌더 및 유튜브 업로드',exact=True)).to_be_enabled()
            button.click()
            if mode in ('duplicate','reload','restart'):
                expect(panel.get_by_role('progressbar')).to_have_attribute('value','42',timeout=10000)
                if other:
                    other.get_by_role('button',name='편집 완료 · 렌더 및 유튜브 업로드',exact=True).click()
                    expect(other.get_by_label('게시 기준일')).to_have_value('2026-11-04')
                if mode=='reload': page.reload(wait_until='networkidle')
                if mode=='restart':
                    server.should_exit=True; thread.join(timeout=15); assert not thread.is_alive()
                    gate.set()
                    fake.queue=asyncio.Queue()
                    sock=socket.socket(); sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1); sock.bind(address)
                    app=make_app(); server,thread=serve(app,sock)
                    page.reload(wait_until='networkidle')
                gate.set()
            if mode=='clipboard_failure':
                expect(panel.get_by_text('업로드 완료 · 링크 복사 실패',exact=True)).to_be_visible(timeout=15000)
                before=list(fake.calls); clipboard.fail=False
                panel.get_by_role('button',name='링크 다시 복사',exact=True).click()
                expect(panel.get_by_text('업로드 완료 · 링크 복사 완료',exact=True)).to_be_visible()
                assert before==fake.calls
            elif mode.startswith('failure_') or mode in ('lost_ack','restart'):
                expect(panel.get_by_role('status')).to_contain_text('게시',timeout=15000)
                expect(panel.get_by_role('status')).to_have_text(
                    '게시 실패' if mode.startswith('failure_') and mode!='failure_metadata_update' else '게시 확인 필요',timeout=15000)
                assert not clipboard.values
            else:
                expect(panel.get_by_text('업로드 완료 · 링크 복사 완료',exact=True)).to_be_visible(timeout=15000)
                expect(panel.get_by_role('link',name='https://youtu.be/abcdefghijk')).to_be_visible()
                assert clipboard.values==['https://youtu.be/abcdefghijk']
            expect(panel.get_by_label('게시 기준일')).to_have_value('2026-11-04')
            expect(panel.get_by_label('게시 기준일')).to_be_disabled()
            expect(button).to_be_disabled()
            before=list(fake.calls); copies=list(clipboard.values); postcount=len(posts)
            page.reload(wait_until='networkidle')
            expect(panel.get_by_label('게시 기준일')).to_have_value('2026-11-04')
            assert fake.calls==before and clipboard.values==copies and len(posts)==postcount
            steps=['render','metadata_generate','metadata_update','upload']
            if mode.startswith('failure_'): steps=steps[:steps.index(mode.removeprefix('failure_'))+1]
            if mode in ('lost_ack','restart'): steps=['render']
            assert fake.calls==steps, fake.calls
            assert not unexpected and not errors,(unexpected,errors)
            if mode=='success':
                images=root/'work/first-publish'; images.mkdir(parents=True,exist_ok=True)
                panel.screenshot(path=str(images/'publish-panel.png'))
        finally:
            gate.set()
            if browser: browser.close()
            server.should_exit=True; thread.join(timeout=15)


def main():
    root=Path(__file__).resolve().parents[1]
    with sync_playwright() as playwright:
        for mode in ('success','initialization','failure_render','failure_metadata_generate','failure_metadata_update','failure_upload',
                     'lost_ack','duplicate','reload','restart','clipboard_failure'):
            scenario(playwright,root,mode)
    print('PASS: 11 publication browser scenarios; external requests 0.',flush=True)


if __name__=='__main__': main()
