"""Phase 7: real local API + Edge, synthetic InfoX responses and routed editor.

No production project, generation, video save, credentials or remote requests.
The editor route is controlled, and cannot establish real login/playback.
"""

import asyncio
import json
import shutil
import socket
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend.app import create_app
from backend.config import Settings
from tests.test_api import Clipboard
from tests.test_infox_generation import FakeClient, prepared


def scenario(playwright, root, mode):
    print(f'Running controlled scenario: {mode}', flush=True)
    with tempfile.TemporaryDirectory(prefix='infox-phase7-') as temporary:
        project = Path(temporary)
        shutil.copytree(root / 'frontend/dist', project / 'frontend/dist')
        store, r = prepared(project, auto_replace=False)
        app = create_app(Settings(project), clipboard=Clipboard())
        app.state.manager.store.metadata(dict(id=r.snapshot.job_id, parent_id=None, state='completed', source='clipboard',
            created_at='2026-11-02T00:00:00Z', elapsed_seconds=1, attempt=1, max_attempts=1,
            key_number=None, retry_at=None, serialized='통제 원문', compressed=r.snapshot.text,
            scene_count=2, body_char_count=20, warnings=[], error=None, validation_issues=[],
            output_dir=f'outputs/{r.snapshot.job_id}', events=[], revision=2, edited_at='2026-11-02T01:00:00Z'))
        fake = FakeClient(store, r, disconnect=True)
        gate = threading.Event()
        original = fake.video_state
        async def gated(video_id):
            if fake.reads >= 1 and store.get(r.id).tracking.completion_seen:
                while not gate.is_set(): await asyncio.sleep(.02)
            return await original(video_id)
        fake.video_state = gated
        app.state.infox_manager.client_factory = lambda: fake
        sock = socket.socket(); sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); sock.bind(('127.0.0.1', 0))
        address = sock.getsockname()
        url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True); thread.start()
        browser = None
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline: time.sleep(.02)
            assert server.started
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context = browser.new_context(viewport={'width': 1440, 'height': 1000})
            editor_requests, unexpected, errors = [], [], []
            lost_ack = False
            def route(request_route):
                nonlocal lost_ack
                request = request_route.request
                if request.url.startswith(url):
                    if (mode == 'lost_ack' and not lost_ack and request.method == 'POST'
                            and '/editor-attempts/' in request.url and request.post_data_json.get('state') == 'navigate_intent'):
                        # Local server committed the intent, but its response is lost.
                        request_route.fetch(); lost_ack = True; request_route.abort()
                    else: request_route.continue_()
                elif request.url == 'https://infox2.infomaxai.com/studio/library/videos/video-1?chatboxView=script':
                    editor_requests.append(request.url)
                    request_route.fulfill(status=200, content_type='text/html', body='<h1>Controlled editor: login required</h1>')
                else:
                    unexpected.append(request.url); request_route.abort()
            context.route('**/*', route)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            context.add_init_script(f"sessionStorage.setItem('market-compressor.job-id', '{r.snapshot.job_id}')")
            page.goto(url, wait_until='networkidle')
            start = page.get_by_role('button', name='확정본으로 InfoX 제작 시작', exact=True)
            expect(start).to_be_enabled()
            if mode == 'blocked':
                page.evaluate('window.open = () => null')
                start.click()
                expect(page.get_by_text('팝업이 차단되었습니다.', exact=False)).to_be_visible()
                assert len(context.pages) == 1
            else:
                if mode == 'assignment_failed':
                    page.evaluate("""() => {
                      const nativeOpen = window.open.bind(window);
                      window.open = (...args) => {
                        const tab = nativeOpen(...args);
                        return tab && {get closed(){return tab.closed}, set opener(v){tab.opener=v},
                          get document(){return tab.document}, close(){tab.close()},
                          location:{replace(){throw new Error('controlled navigation failure')}}};
                      };
                    }""")
                with page.expect_popup() as pending: start.click()
                waiting = pending.value
                expect(waiting.get_by_text('InfoX 서버 검증 대기 중입니다.', exact=False)).to_be_visible()
                page.bring_to_front()
                expect(page.get_by_text('대기 탭 확보', exact=False)).to_be_visible()
                assert not editor_requests
                if mode == 'closed':
                    waiting.close()
                    expect(page.get_by_text('대기 탭이 닫혔습니다.', exact=False)).to_be_visible()
                if mode == 'reload': page.reload(wait_until='networkidle')
                if mode == 'backend_restart':
                    expect(page.get_by_text('완료 신호 수신 · 대본·음성·자막 확인 중', exact=True)).to_be_visible(timeout=10000)
                    server.should_exit = True; thread.join(timeout=10)
                    assert not thread.is_alive()
                    app = create_app(Settings(project), clipboard=Clipboard())
                    app.state.infox_manager.client_factory = lambda: fake
                    sock = socket.socket(); sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); sock.bind(address)
                    server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
                    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True); thread.start()
                    deadline = time.monotonic() + 10
                    while not server.started and time.monotonic() < deadline: time.sleep(.02)
                    assert server.started
                if mode == 'duplicate':
                    # Another real browser tab cannot acquire the automatic claim.
                    other = context.new_page(); other.goto(url, wait_until='networkidle')
                    second = other.evaluate("""async id => {
                      const {token} = await (await fetch('/api/session')).json();
                      return (await fetch(`/api/infox-runs/${id}/editor-attempts`, {method:'POST',
                        headers:{'Content-Type':'application/json','X-App-Token':token},
                        body:JSON.stringify({attempt_id:'b'.repeat(32),mode:'automatic',tab_state:'created'})})).json()
                    }""", r.id)
                    assert not second['claimed']; other.close()
            gate.set()
            expect(page.get_by_text('무변경 재조회 검증 완료 · 서버 저장 없음', exact=True)).to_be_visible(timeout=20000)
            automatic = mode in ('duplicate', 'normal', 'backend_restart')
            if automatic:
                expect(waiting.get_by_role('heading', name='Controlled editor: login required')).to_be_visible()
                page.bring_to_front()
                expect(page.get_by_text('브라우저에 편집 URL 이동을 요청했습니다.', exact=False)).to_be_visible()
                assert len(editor_requests) == 1
                page.get_by_role('button', name='로그인 필요 상태 기록', exact=True).click()
                expect(page.get_by_text('사용자가 로그인 필요 상태를 기록했습니다.', exact=False)).to_be_visible()
            else:
                page.wait_for_timeout(1200)
                assert not editor_requests
                if mode == 'assignment_failed':
                    expect(page.get_by_text('브라우저 이동 요청이 실패했습니다.', exact=False)).to_be_visible()
                if mode == 'lost_ack':
                    expect(page.get_by_text('브라우저 이동 결과가 불명확합니다.', exact=False)).to_be_visible()
            page.reload(wait_until='networkidle')
            expect(page.get_by_text('무변경 재조회 검증 완료 · 서버 저장 없음', exact=True)).to_be_visible()
            page.wait_for_timeout(1200)
            assert len(editor_requests) == (1 if automatic else 0)
            with page.expect_popup() as reopened:
                page.get_by_role('button', name='편집기 다시 열기', exact=True).click()
            expect(reopened.value.get_by_role('heading', name='Controlled editor: login required')).to_be_visible()
            page.bring_to_front()
            try:
                expect(page.get_by_text('브라우저에 편집 URL 이동을 요청했습니다.', exact=False)).to_be_visible(timeout=10000)
            except AssertionError:
                print(json.dumps({'scenario':mode,'latest_browser_attempt':store.latest_editor(r.id),
                                  'page_errors':errors}, ensure_ascii=True), flush=True)
                raise
            page.get_by_role('button', name='이동 실패 상태 기록', exact=True).click()
            expect(page.get_by_text('브라우저 이동 요청이 실패했습니다.', exact=False)).to_be_visible()
            count = len(editor_requests)
            page.reload(wait_until='networkidle'); page.wait_for_timeout(1200)
            assert len(editor_requests) == count
            for width in (390,1440):
                page.set_viewport_size({'width':width,'height':1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                if mode == 'normal': page.screenshot(path=str(root / f'.runtime/infox-phase7-{width}.png'), full_page=True)
            final = store.get(r.id)
            assert final.asset_save.state == 'unchanged_verified' and final.snapshot == r.snapshot
            assert fake.calls == ['project','generation'] and not unexpected and not errors
            browser.close()
            return {'scenario':mode,'passed':True,'editor_requests':count,'remote_writes':0}
        finally:
            if browser and browser.is_connected(): browser.close()
            gate.set(); server.should_exit = True; thread.join(timeout=10); sock.close()


def main():
    root = Path(__file__).resolve().parents[1]
    with sync_playwright() as playwright:
        results = [scenario(playwright, root, mode) for mode in
                   ('normal','duplicate','blocked','closed','reload','assignment_failed','lost_ack','backend_restart')]
    report = {'controlled_local_api_edge': results, 'actual_infox_editor':False,
              'actual_login_or_playback_verified':False, 'uncontrolled_external_network_requests':0,
              'editor_url_requests_intercepted':True}
    (root / 'docs/infox/phase7-browser-verification.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print('PASS: 8 local API/Edge scenarios; waiting/verified navigation, duplicate owner, blocked/closed/reload, assignment failure, lost acknowledgement, backend restart, explicit reopen and failure/login reporting; no external network requests')


if __name__ == '__main__': main()
