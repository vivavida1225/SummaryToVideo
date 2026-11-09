"""Layout check for the stepped InfoX panel with synthetic, route-mocked API data.

Serves a built frontend (default: frontend/dist) and fulfils every /api request
locally. No backend, InfoX, YouTube, clipboard or other external request is made;
any POST or unknown request fails the check.

    .venv\\Scripts\\python.exe -X utf8 -m tests.infox_gui_layout_smoke [--dist DIR] [--json]
"""

import argparse
import copy
import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

RUN_ID = 'a' * 32
SESSION = {'token': 'session-token', 'model': 'gpt-6.1-sol', 'configured_keys': [1],
           'configured_keys_by_provider': {'gemini': [1], 'openai': [1]},
           'models': [{'id': 'gpt-6.1-sol', 'label': 'GPT 6.1 Sol', 'provider': 'openai'}]}
SCRIPT = '\n'.join(f'{n}번째 장면의 시장 요약 문장입니다. 지수와 업종 흐름을 원문 그대로 전달합니다.' for n in range(1, 6))
JOB = {'id': 'job-1', 'parent_id': None, 'state': 'completed', 'source': 'clipboard', 'created_at': '2026-11-09T00:00:00Z',
       'elapsed_seconds': 12.3, 'attempt': 1, 'max_attempts': 3, 'key_number': 1, 'retry_at': None,
       'serialized': '정돈된 시장 원문', 'compressed': SCRIPT, 'revision': 0, 'scene_count': 5, 'body_char_count': 500,
       'warnings': [], 'error': None, 'output_dir': 'outputs/job-1', 'events': [], 'validation_issues': []}


def item(scene, index, verdict):
    hits = [{'term': '삼성전자', 'field': 'tags[0]', 'source': f'video.scenes[{scene}].rerank_result[0]'}] if verdict == 'detected' else []
    return {'scene_index': scene, 'scene_id': f'scene-{scene}', 'layer_id': 'media', 'item_index': index, 'verdict': verdict,
            'reason': 'metadata_missing' if verdict == 'unknown' else 'metadata_inspected',
            'reference': {'kind': 'image', 'bucket': 'photos', 'filename': f'asset-{scene}-{index}.jpg', 'segment': None},
            'hits': hits, 'metadata_sources': [f'video.scenes[{scene}].rerank_result[{index}].tags']}


def selection(scene, index):
    return {'target': {'scene_index': scene, 'layer_id': 'media', 'item_index': index}, 'slot_frames': 180,
            'candidate': {'id': f'fav-{scene}-{index}', 'title': '반도체 설비 즐겨찾기', 'video_id': 'source-1', 'bucket': 'clips',
                          'segment': f'segment-{scene}.mp4', 'frame_index': 3, 'timestamp': 12.5, 'duration': 6.0}}


VERDICTS = ['detected', 'not_detected', 'unknown', 'not_detected']
RUN = {'id': RUN_ID, 'stage': 'ready_for_editor', 'version': 14, 'requires_reconciliation': False, 'tracking_active': False,
       'continuation': 'automatic', 'video_id': 'video-1', 'task_id': 'task-1',
       'snapshot': {'job_id': 'job-1', 'revision': 0, 'text': SCRIPT, 'text_sha256': '',
                    'scenes': [{'line_number': n + 1, 'scene_index': n, 'text': line} for n, line in enumerate(SCRIPT.split('\n'))],
                    'settings': {'title': '1109_시황영상', 'template_id': 1786, 'voice_id': 'v', 'voice_speed': '1.0', 'auto_replace': True}},
       'tracking': {'completion_seen': True, 'task_status': 'completed', 'progress': 100},
       'asset_audit': {'state': 'completed', 'issues': [], 'items': [item(s, i, VERDICTS[(s + i) % 4]) for s in range(5) for i in range(3)]},
       'replacement_plan': {'state': 'ready', 'selections': [selection(s, i) for s in range(5) for i in range(3) if VERDICTS[(s + i) % 4] == 'detected'],
                            'checks': {k: True for k in ('scope_preserved', 'normal_assets_preserved', 'layout_preserved', 'timing_preserved',
                                                          'references_consistent', 'old_active_references_removed')}},
       'asset_save': {'state': 'saved_verified', 'receipt_state': 'accepted', 'verified_version': '2026-11-09T01:00:00Z'}}


def publication(number, url, date):
    return {'id': f'pub-{number}', 'run_id': RUN_ID, 'video_id': 'video-1', 'attempt_number': number, 'previous_publication_id': None,
            'created_at': '2026-11-09T02:00:00Z', 'version': 3, 'stage': 'completed', 'failed_stage': None, 'error_code': None,
            'progress': None, 'snapshot': {'market_date': date, 'title': f'{date[2:4]}{date[5:7]}{date[8:]} AI 영상 시황',
                                           'description': 'AI 영상 시황', 'privacy_status': 'unlisted'},
            'uploaded_verified': True, 'youtube_video_id': url[-11:], 'share_url': url, 'clipboard_state': 'copied'}


SCENARIOS = {
    'completed': {'publication': publication(2, 'https://youtu.be/ZYXWVUTSRQP', '2026-11-09'),
                  'history': [publication(2, 'https://youtu.be/ZYXWVUTSRQP', '2026-11-09'), publication(1, 'https://youtu.be/abcdefghijk', '2026-11-08')]},
    'editor_ready': {'publication': None, 'history': []},
}
PREVIEW = {'available': True, 'charged': True, 'credits': 3, 'duration_seconds': 61, 'can_start': True,
           'blocked_reason': None, 'latest_publication_id': None, 'has_remote_render': False}


def serve(dist: Path):
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *args): pass
        def send_head(self):
            if not (dist / self.path.lstrip('/').split('?')[0]).is_file(): self.path = '/index.html'
            return super().send_head()
    server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Handler, directory=str(dist)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_address[1]}'


def open_page(browser, url, scenario, width, height, problems):
    data = SCENARIOS[scenario]
    context = browser.new_context(viewport={'width': width, 'height': height})

    def api(route):
        request = route.request
        path = request.url.split('://', 1)[1].split('/', 1)[1].split('?')[0]
        path = '/' + path
        if not request.url.startswith(url):
            problems.append(f'external {request.url}'); return route.abort()
        if not path.startswith('/api/'): return route.continue_()
        if request.method != 'GET':
            problems.append(f'unexpected {request.method} {path}'); return route.abort()
        body = {
            '/api/session': SESSION, '/api/files': {'files': []}, '/api/jobs/job-1': JOB,
            '/api/jobs/job-1/infox-runs': {'run': RUN}, f'/api/infox-runs/{RUN_ID}': RUN,
            f'/api/infox-runs/{RUN_ID}/editor-attempts': {'attempt': None},
            f'/api/infox-runs/{RUN_ID}/publications': {'publication': data['publication']},
            f'/api/infox-runs/{RUN_ID}/publications/history': {'publications': data['history']},
            f'/api/infox-runs/{RUN_ID}/publications/preview': PREVIEW,
        }.get(path)
        if body is None:
            problems.append(f'unknown GET {path}'); return route.fulfill(status=404, json={'detail': 'not found'})
        return route.fulfill(status=200, json=copy.deepcopy(body))

    context.route('**/*', api)
    context.add_init_script("sessionStorage.setItem('market-compressor.job-id', 'job-1')")
    page = context.new_page()
    page.on('pageerror', lambda error: problems.append(f'pageerror {error}'))
    page.goto(url, wait_until='networkidle')
    expect(page.locator('#infox-heading')).to_be_visible()
    page.wait_for_timeout(300)
    return context, page


def infox_height(page):
    return page.evaluate("document.querySelector('.infox-preparation').getBoundingClientRect().height")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dist', default=str(Path(__file__).resolve().parents[1] / 'frontend/dist'))
    parser.add_argument('--baseline-only', action='store_true', help='Only measure the 390px completed height (for an older build).')
    args = parser.parse_args()
    (Path(__file__).resolve().parents[1] / '.runtime').mkdir(exist_ok=True)
    server, url = serve(Path(args.dist))
    problems, metrics = [], {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            context, page = open_page(browser, url, 'completed', 390, 844, problems)
            metrics['completed_390_height'] = round(infox_height(page))
            context.close()
            if args.baseline_only:
                print(json.dumps(metrics)); assert not problems, problems; return

            # PC 1366x768: once the InfoX heading is at the top, the current state
            # and the single primary action are visible without scrolling the records.
            context, page = open_page(browser, url, 'editor_ready', 1366, 768, problems)
            page.locator('#infox-heading').evaluate("el => el.scrollIntoView({block: 'start', behavior: 'instant'})")
            side = page.get_by_role('complementary', name='InfoX 현재 상태와 다음 행동', exact=True)
            primary = side.get_by_role('button', name='편집 완료 · 렌더 및 유튜브 업로드', exact=True)
            expect(primary).to_be_enabled()
            box = primary.bounding_box()
            metrics['pc_primary_bottom'] = round(box['y'] + box['height'])
            assert 0 <= box['y'] and box['y'] + box['height'] <= 768, box
            assert box['height'] >= 48, box
            assert page.get_by_role('button', name='편집 완료 · 렌더 및 유튜브 업로드').count() == 1
            # Keyboard: Tab reaches the primary button, Enter on a step toggle keeps focus.
            primary.focus(); assert page.evaluate('document.activeElement.textContent.includes("렌더 및 유튜브 업로드")')
            toggle = page.get_by_role('button', name='펼치기 자동 제작', exact=True)
            toggle.focus(); page.keyboard.press('Enter')
            expect(page.get_by_role('button', name='접기 자동 제작', exact=True)).to_be_focused()
            expect(page.get_by_role('button', name='접기 자동 제작', exact=True)).to_have_attribute('aria-expanded', 'true')
            evidence = page.get_by_text('장면별 검사·교체 근거', exact=True)
            evidence.focus(); page.keyboard.press('Enter')
            expect(page.get_by_text('삼성전자 · tags[0]').first).to_be_visible()
            # With the long records open, the side panel follows the scroll inside the InfoX area.
            page.locator('#infox-heading').evaluate("el => el.scrollIntoView({block: 'start', behavior: 'instant'})")
            page.evaluate('window.scrollBy({top: 700, behavior: "instant"})'); page.wait_for_timeout(100)
            box = primary.bounding_box()
            assert 0 <= box['y'] and box['y'] + box['height'] <= 768, ('sticky', box)
            expect(side.get_by_role('status')).to_have_text('편집 후 게시 대기')
            context.close()

            # 200% zoom on 1366x768 is a 683x384 CSS viewport: single column, panel first.
            context, page = open_page(browser, url, 'editor_ready', 683, 384, problems)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'zoom overflow'
            side_top = page.get_by_role('complementary', name='InfoX 현재 상태와 다음 행동').bounding_box()['y']
            steps_top = page.locator('.infox-steps').bounding_box()['y']
            assert side_top < steps_top, (side_top, steps_top)
            context.close()

            for width in (320, 390):
                for scenario in SCENARIOS:
                    context, page = open_page(browser, url, scenario, width, 844, problems)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (scenario, width)
                    if scenario == 'completed':
                        expect(page.get_by_role('link', name='https://youtu.be/ZYXWVUTSRQP')).to_be_visible()
                        page.screenshot(path=str(Path(__file__).resolve().parents[1] / f'.runtime/infox-gui-{scenario}-{width}.png'), full_page=True)
                    context.close()
            browser.close()
    finally:
        server.shutdown()
    assert not problems, problems
    print(json.dumps(metrics))
    print('PASS: 1366x768 primary visible and sticky, keyboard toggle focus, 200% zoom single column, 320/390px no overflow; route-mocked API only')


if __name__ == '__main__':
    main()
