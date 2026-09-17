"""Editable narration browser check against real local APIs and temporary storage.

Gemini is never called; clipboard writes use an in-memory test double.
"""

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


class Clipboard:
    def __init__(self):
        self.writes = []

    def write(self, text):
        self.writes.append(text)

    def read(self):
        return {'text': '', 'format': 'text'}


def main():
    root = Path(__file__).resolve().parents[1]
    job_id = '20260917_130000_000000_abcdef12'
    original = '원본 대본입니다.\n두 번째 줄입니다.'
    clipboard = Clipboard()
    with tempfile.TemporaryDirectory(prefix='narration-edit-') as temporary:
        project = Path(temporary)
        shutil.copytree(root / 'frontend/dist', project / 'frontend/dist')
        app = create_app(Settings(project), clipboard=clipboard)
        manager = app.state.manager
        job = dict(id=job_id, parent_id=None, state='completed', source='clipboard',
                   created_at='2026-09-17T00:00:00Z', elapsed_seconds=1, attempt=1, max_attempts=5,
                   key_number=1, retry_at=None, serialized='정돈된 시장 원문', compressed=original,
                   scene_count=5, body_char_count=len(original.replace('\n', '')), warnings=[], error=None,
                   output_dir=f'outputs/{job_id}', events=[], revision=0, edited_at=None)
        manager.store.write_text(job_id, 'serialized.txt', job['serialized'])
        manager.store.write_text(job_id, 'compressed.txt', original)
        manager.store.metadata(job)
        server = uvicorn.Server(uvicorn.Config(app, log_level='error'))
        sock = socket.socket()
        sock.bind(('127.0.0.1', 0))
        url = f'http://127.0.0.1:{sock.getsockname()[1]}'
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
                page.add_init_script(f"sessionStorage.setItem('market-compressor.job-id', '{job_id}')")
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto(url, wait_until='networkidle')
                editor = page.get_by_role('textbox', name='압축 결과', exact=True)
                expect(editor).to_be_editable()
                expect(page.get_by_role('textbox', name='직렬화 원문', exact=True)).to_have_attribute('readonly', '')
                editor.click()
                editor.evaluate('(element) => element.setSelectionRange(3, 3)')
                page.keyboard.insert_text('수정한 ')
                edited = original[:3] + '수정한 ' + original[3:]
                expect(editor).to_have_value(edited)
                page.get_by_role('button', name='압축 결과 복사', exact=True).click()
                expect(page.get_by_text('압축 결과를 클립보드에 복사했습니다.', exact=True)).to_be_attached()
                assert clipboard.writes[-1] == edited
                with page.expect_download() as download:
                    page.get_by_role('button', name='압축 결과 다운로드', exact=True).click()
                assert Path(download.value.path()).read_text(encoding='utf-8') == edited
                manager.jobs.clear()  # Exercise disk restore rather than cached state.
                page.reload(wait_until='networkidle')
                expect(editor).to_have_value(edited)
                save_edit = manager.store.save_edit
                def fail_save(*args, **kwargs):
                    raise OSError('simulated disk full')
                manager.store.save_edit = fail_save
                editor.fill('저장 실패해도 유지할 문장')
                retry = page.get_by_role('button', name='저장 다시 시도', exact=True)
                expect(retry).to_be_visible()
                expect(editor).to_have_value('저장 실패해도 유지할 문장')
                assert manager.get(job_id)['compressed'] == edited
                manager.store.save_edit = save_edit
                with page.expect_response(lambda response: response.url.endswith('/compressed') and response.request.method == 'POST') as saved:
                    retry.click()
                assert saved.value.status == 200
                assert manager.get(job_id)['compressed'] == '저장 실패해도 유지할 문장'
                for width in (390, 1440):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.screenshot(path=str(root / f'.runtime/narration-edit-{width}.png'), full_page=True)
                editor.fill('')
                with page.expect_download() as download:
                    page.get_by_role('button', name='압축 결과 다운로드', exact=True).click()
                assert Path(download.value.path()).read_text(encoding='utf-8') == ''
                manager.jobs.clear()
                page.reload(wait_until='networkidle')
                expect(editor).to_have_value('')
                expect(editor).to_be_editable()
                assert not errors, errors
                browser.close()
            print('PASS: keyboard editing, immediate copy/download, disk restore, save failure/retry, empty draft, responsive layout')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == '__main__':
    main()
