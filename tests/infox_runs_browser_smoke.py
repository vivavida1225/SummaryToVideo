"""Real local API + Chromium check for phase 2. No model or InfoX HTTP calls."""

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
from backend.infox.service import RunService
from backend.infox.store import RunStore
from backend.jobs import JobManager
from tests.test_api import Clipboard


def main():
    root = Path(__file__).resolve().parents[1]
    job_id = '20261102_120000_000000_abcdef12'
    with tempfile.TemporaryDirectory(prefix='infox-phase2-') as temporary:
        project = Path(temporary)
        shutil.copytree(root / 'frontend/dist', project / 'frontend/dist')
        app = create_app(Settings(project), clipboard=Clipboard())
        manager = app.state.manager
        job = dict(id=job_id, parent_id=None, state='needs_review', source='clipboard',
            created_at='2026-11-02T00:00:00Z', elapsed_seconds=1, attempt=1, max_attempts=1,
            key_number=None, retry_at=None, serialized='정돈된 원문', compressed='처음 생성한 대본',
            scene_count=1, body_char_count=10, warnings=[], error=None,
            validation_issues=['생성 당시 검토 사유'], output_dir=f'outputs/{job_id}', events=[], revision=0, edited_at=None)
        manager.store.write_text(job_id, 'serialized.txt', job['serialized'])
        manager.store.write_text(job_id, 'compressed.txt', job['compressed'])
        manager.store.metadata(job)
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{sock.getsockname()[1]}'
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(.02)
            assert server.started
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel='msedge', headless=True)
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors, external_requests = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('request', lambda req: external_requests.append(req.url) if not req.url.startswith(url) else None)
                page.add_init_script(f"sessionStorage.setItem('market-compressor.job-id', '{job_id}')")
                page.goto(url, wait_until='networkidle')
                editor = page.get_by_role('textbox', name='압축 결과', exact=True)
                expect(editor).to_be_editable()
                edited = '  최종 편집 😀 대본  \n\n둘째 장면'
                editor.fill(edited)
                confirm = page.get_by_role('checkbox', name='검토 사유를 확인하고 이 대본을 사용합니다')
                button = page.get_by_role('button', name='InfoX 전송본 확정', exact=True)
                expect(button).to_be_disabled()
                confirm.check()
                with page.expect_response(lambda response: response.url.endswith('/infox-runs')
                        and response.request.method == 'POST') as prepared:
                    button.click()
                assert prepared.value.status == 201
                record = prepared.value.json()
                assert record['snapshot']['text'] == edited
                assert record['snapshot']['revision'] == manager.get(job_id)['revision']
                assert len(record['snapshot']['scenes']) == 2
                assert record['project']['state'] == 'not_sent'
                expect(page.get_by_text('전송본이 확정되었습니다.', exact=True)).to_be_visible()
                # Repeated clicks, then disk restoration through fresh manager/store instances.
                with page.expect_response(lambda response: response.url.endswith('/infox-runs')
                        and response.request.method == 'POST') as duplicate:
                    button.click()
                assert duplicate.value.status == 200 and duplicate.value.json()['id'] == record['id']
                app.state.infox_runs = RunService(JobManager(Settings(project)), RunStore(project))
                manager.jobs.clear()
                page.reload(wait_until='networkidle')
                expect(page.get_by_text('전송본이 확정되었습니다.', exact=True)).to_be_visible()
                assert app.state.infox_runs.get(record['id']).snapshot.text == edited
                editor.fill('새로운 수정본')
                expect(page.get_by_text('현재 편집본 또는 설정이 확정본과 다릅니다.', exact=True)).to_be_visible()
                assert app.state.infox_runs.get(record['id']).snapshot.text == edited
                # Verify layout and capture only synthetic test data.
                for width in (390, 1440):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.get_by_role('heading', name='영상 전송본 준비').scroll_into_view_if_needed()
                    page.screenshot(path=str(root / f'.runtime/infox-phase2-{width}.png'), full_page=True)
                assert not errors, errors
                assert not external_requests, 'Unexpected external browser requests'
                browser.close()
            print('PASS: saved edit snapshot, review confirmation, duplicate reuse, disk restore, immutable original, responsive layout, no external requests')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == '__main__':
    main()
