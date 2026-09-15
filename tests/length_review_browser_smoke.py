"""Built UI check with mocked HTTP responses; no Gemini or clipboard access.

Run after frontend build: python -m tests.length_review_browser_smoke
"""

import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


def main():
    root = Path(__file__).resolve().parents[1]
    (root / '.runtime').mkdir(exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(root / 'frontend/dist')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    job = dict(id='review', parent_id=None, state='needs_review', source='clipboard', created_at='2026-09-15T00:00:00Z',
        elapsed_seconds=2, attempt=1, max_attempts=5, key_number=1, retry_at=None, serialized='보관된 원문',
        compressed='가' * 551, scene_count=5, body_char_count=551, excess_char_count=1,
        validation_issues=['상한보다 1자 초과합니다.', '장면 1: 지수를 확인하세요.'], warnings=[], error=None,
        output_dir='outputs/review', events=[], model='gemini-3.8-flash', requested_model='gemini-3.8-flash')
    requests = []

    def api_route(route):
        path = route.request.url.split('/api/')[1]
        requests.append(path)
        if path == 'session':
            data = dict(token='test', model='gemini-3.8-flash', configured_keys=[1],
                        models=[dict(id='gemini-3.8-flash', label='Gemini 3.8 Flash')])
        elif path == 'files':
            data = {'files': []}
        elif path == 'jobs/review':
            data = job
        elif path == 'jobs/review/retry':
            data = {**job, 'id': 'child', 'parent_id': 'review', 'state': 'queued', 'compressed': None,
                    'excess_char_count': 0, 'validation_issues': []}
        elif path == 'jobs/child':
            data = {**job, 'id': 'child', 'parent_id': 'review', 'state': 'completed', 'compressed': '나' * 500,
                    'body_char_count': 500, 'excess_char_count': 0, 'validation_issues': []}
        else:
            raise AssertionError(path)
        route.fulfill(json=data, status=202 if path.endswith('/retry') else 200)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.add_init_script("sessionStorage.setItem('market-compressor.job-id', 'review')")
            page.route('**/api/**', api_route)
            page.goto(f'http://127.0.0.1:{server.server_port}', wait_until='networkidle')
            expect(page.get_by_text('551자 · 상한 550자보다 1자 초과')).to_be_visible()
            expect(page.get_by_role('textbox', name='압축 결과')).to_have_value('가' * 551)
            expect(page.get_by_role('heading', name='분량 확인 필요', exact=True)).to_be_visible()
            page.wait_for_timeout(1200)
            assert requests.count('jobs/review') == 1
            assert not any(path.endswith('/retry') for path in requests)
            for width in [320, 390, 1440]:
                page.set_viewport_size({'width': width, 'height': 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                # Trial click verifies visibility and pointer interception without submitting.
                page.get_by_role('button', name='대본 재생성').click(trial=True)
                if width in (390, 1440):
                    page.screenshot(path=str(root / f'.runtime/length-review-{width}.png'), full_page=True)
            page.get_by_role('button', name='대본 재생성').click()
            expect(page.get_by_role('heading', name='영상 원고가 완성되었습니다')).to_be_visible()
            expect(page.get_by_role('textbox', name='압축 결과')).to_have_value('나' * 500)
            assert requests.count('jobs/review/retry') == 1
            assert not errors, errors
            browser.close()
        print('PASS: review restore/count, stopped polling, clickable regeneration at 320/390/1440px, manual regeneration')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
