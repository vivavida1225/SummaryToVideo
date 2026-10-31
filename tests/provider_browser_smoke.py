"""Built UI provider selection/fallback checks with mocked APIs; no paid calls."""

import threading
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from backend.models import MODEL, MODELS
from tests.length_review_browser_smoke import QuietHandler


def main():
    root = Path(__file__).resolve().parents[1]
    (root / '.runtime').mkdir(exist_ok=True)
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(root / 'frontend/dist')))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    complete, openai_key = False, True
    submissions, errors = [], []
    job = dict(id='provider', parent_id=None, state='requesting', source='clipboard',
        created_at='2026-10-31T00:00:00Z', elapsed_seconds=1, attempt=1, max_attempts=4,
        key_number=1, retry_at=None, serialized='보관된 원문', compressed=None,
        scene_count=5, body_char_count=None, validation_issues=[], warnings=[], error=None,
        output_dir='outputs/provider', events=[], model=MODEL, requested_model=MODEL)

    def api_route(route):
        path = route.request.url.split('/api/')[1]
        if path == 'session':
            data = dict(token='test', model=MODEL, models=MODELS, configured_keys=[1, 3],
                configured_keys_by_provider={'gemini': [1, 3], 'openai': [1] if openai_key else []})
        elif path == 'files':
            data = {'files': []}
        elif path == 'clipboard/read':
            data = {'text': '<article>시장 원문</article>', 'format': 'html'}
        elif path == 'jobs':
            submissions.append(route.request.post_data_json)
            data = job
        elif path == 'jobs/provider':
            data = {**job, 'state': 'completed', 'model': 'gpt-6.1-terra', 'attempt': 2,
                    'compressed': '완성 대본입니다.', 'body_char_count': 9} if complete else job
        else:
            raise AssertionError(path)
        route.fulfill(json=data, status=202 if path == 'jobs' else 200)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/**', api_route)
            page.goto(f'http://127.0.0.1:{server.server_port}', wait_until='networkidle')
            select = page.get_by_role('combobox', name='시작 모델')
            expect(select).to_have_value(MODEL)
            assert select.locator('option').evaluate_all('(items) => items.map(item => item.value)') == [item['id'] for item in MODELS]
            expect(page.get_by_text('OpenAI 키 1', exact=True)).to_be_visible()
            page.get_by_role('button', name='변환 및 1분 압축').click()
            expect(page.get_by_role('heading', name='모델 응답을 기다리는 중')).to_be_visible()
            expect(select).to_be_disabled()
            assert submissions == [{'html': '<article>시장 원문</article>', 'model': MODEL, 'transcribe_numbers': False}]
            complete = True
            expect(page.get_by_text('응답 모델: GPT 6.1 Terra', exact=True)).to_be_visible()
            expect(select).to_have_value(MODEL)
            assert page.evaluate("localStorage.getItem('market-compressor.model')") == MODEL
            for width in (320, 390, 1440):
                page.set_viewport_size({'width': width, 'height': 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), width
                expect(select).to_be_visible()
                if width in (390, 1440):
                    page.screenshot(path=str(root / f'.runtime/provider-{width}.png'), full_page=True)
            select.select_option('gemini-3.6-flash')
            expect(page.get_by_text('Gemini 키 1 · 3', exact=True)).to_be_visible()
            page.reload(wait_until='networkidle')
            expect(select).to_have_value('gemini-3.6-flash')
            openai_key = False
            page.reload(wait_until='networkidle')
            select.select_option(MODEL)
            expect(page.get_by_text('OpenAI 키 없음', exact=True)).to_be_visible()
            assert not errors, errors
            browser.close()
        print('PASS: 6 models, Sol default, provider key status, locked selection, fallback display, saved selection, 320/390/1440px')
    finally:
        server.shutdown()
        server.server_close()


if __name__ == '__main__':
    main()
