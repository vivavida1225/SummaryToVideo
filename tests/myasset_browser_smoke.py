"""Built UI import flow with mocked HTTP; no Gemini or clipboard access."""

import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from .length_review_browser_smoke import QuietHandler


def main():
    root = Path(__file__).resolve().parents[1]
    (root / '.runtime').mkdir(exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(root / 'frontend/dist')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    imports, submissions, errors = [], [], []
    html = '<div class="embedded-content"><header>가져온 원문</header></div>'
    job = dict(id='imported', parent_id=None, state='completed', source='clipboard',
               created_at='2026-09-17T00:00:00Z', elapsed_seconds=1, attempt=1, max_attempts=5,
               key_number=1, retry_at=None, serialized='변환된 원문', compressed='완료된 대본',
               scene_count=5, body_char_count=500, warnings=[], error=None, output_dir='outputs/imported', events=[])

    def route_api(route):
        path = route.request.url.split('/api/')[1]
        if path == 'session':
            data = dict(token='test', model='gemini-3.8-flash', configured_keys=[1],
                        models=[dict(id='gemini-3.8-flash', label='Gemini 3.8 Flash')])
        elif path == 'files':
            data = {'files': []}
        elif path == 'clipboard/read':
            data = {'text': '기존 입력', 'format': 'text'}
        elif path == 'sources/myasset':
            params = route.request.post_data_json
            imports.append(params)
            if params['gubun'] == 30:
                route.fulfill(status=404, json={'detail': '게시글 본문을 찾을 수 없습니다.'})
                return
            data = dict(html=html, source_url='https://www.myasset.com/', **params)
        elif path == 'jobs':
            submissions.append(route.request.post_data_json)
            data = job
        elif path == 'jobs/imported':
            data = job
        else:
            raise AssertionError(path)
        route.fulfill(status=202 if path == 'jobs' else 200, json=data)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/**', route_api)
            page.goto(f'http://127.0.0.1:{server.server_port}', wait_until='networkidle')
            button = page.get_by_role('button', name='myasset에서 불러오기', exact=True)
            expect(button).to_be_enabled()
            button.click()
            expect(page.get_by_role('alert')).to_contain_text('게시글 본문')
            expect(page.get_by_label('클립보드 HTML 원문', exact=True)).to_have_value('기존 입력')
            assert imports[0]['gubun'] == 30 and submissions == []
            settings = page.get_by_role('button', name='불러오기 설정', exact=True)
            settings.click()
            expect(page.get_by_label('gubun', exact=True)).to_have_value('30')
            for width in (320, 390, 1440):
                page.set_viewport_size({'width': width, 'height': 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                button.click(trial=True)
                page.get_by_label('gubun', exact=True).click(trial=True)
                page.screenshot(path=str(root / f'.runtime/myasset-{width}.png'), full_page=True)
            page.get_by_label('날짜', exact=True).fill('2026-09-17')
            page.get_by_label('gubun', exact=True).fill('1')
            page.get_by_label('gubun', exact=True).press('Escape')
            expect(settings).to_be_focused()
            button.click()
            expect(page.get_by_label('클립보드 HTML 원문', exact=True)).to_have_value(html)
            expect(page.get_by_role('textbox', name='압축 결과', exact=True)).to_have_value('완료된 대본')
            assert submissions == [{'html': html, 'model': 'gemini-3.8-flash'}]
            assert imports[-1] == {'base_date': '2026-09-17', 'gubun': 1}
            page.reload(wait_until='networkidle')
            settings.click()
            expect(page.get_by_label('gubun', exact=True)).to_have_value('30')
            today = page.evaluate("new Intl.DateTimeFormat('sv-SE', {timeZone:'Asia/Seoul'}).format(new Date())")
            expect(page.get_by_label('날짜', exact=True)).to_have_value(today)
            assert len(imports) == 2 and len(submissions) == 1
            assert not errors, errors
            browser.close()
        print('PASS: missing article preserves input; import auto-submits once; defaults reset; keyboard and 320/390/1440px layouts')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
