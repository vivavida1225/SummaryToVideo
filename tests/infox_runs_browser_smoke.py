"""Real local API + Chromium check for phases 2/3. External InfoX is controlled."""

import shutil
import asyncio
import copy
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
from tests.test_infox_generation import FakeClient
from tests.test_infox_assets import image, layer
from tests.test_infox_favorites import FavoritesClient, favorite


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
                # Start the previously confirmed snapshot after a later edit;
                # backend manager owns admission and tracking independently of UI.
                run_manager = app.state.infox_manager
                fake = FakeClient(run_manager.store, run_manager.store.get(record['id']))
                generated_read = fake.video_state
                async def original_read(video_id):
                    value = await generated_read(video_id)
                    # The service may return different narration/caption spelling.
                    # Production readiness trusts these texts; snapshots remain fixed.
                    value['video']['scenes'][0]['voiceover']['content'] = 'InfoX가 반환한 장면 대본'
                    value['video']['scenes'][0]['voiceover']['segment_captions'][0]['text'] = 'InfoX가 반환한 표시 자막'
                    return value
                async def incomplete_assets(video_id):
                    value = await original_read(video_id)
                    # Leave metadata incomplete, so automation correctly stops
                    # before any save. Subsequent read-only audit repairs it.
                    for scene in value['video']['scenes']:
                        scene['rootShape']['layers']['media']['props']['media'].pop('title', None)
                    return value
                fake.video_state = incomplete_assets
                run_manager.client_factory = lambda: fake
                start_requests = []
                page.on('request', lambda req: start_requests.append(req.url) if req.url.endswith('/start') else None)
                page.get_by_role('button', name='확정본으로 InfoX 제작 시작', exact=True).click()
                expect(page.get_by_text('확인이 필요합니다. 기존 실행 상태를 확인하세요.', exact=True)).to_be_visible(timeout=10000)
                expect(page.get_by_role('region', name='기업 에셋 검사 결과')).to_be_visible()
                link = page.get_by_role('link', name='InfoX 편집 화면 수동 열기')
                expect(link).to_have_attribute('href', 'https://infox2.infomaxai.com/studio/library/videos/video-1?chatboxView=script')
                assert fake.calls == ['project', 'generation']
                assert run_manager.store.get(record['id']).tracking.draft.ready
                assert run_manager.store.get(record['id']).snapshot.text == edited
                page.reload(wait_until='networkidle')
                expect(page.get_by_text('확인이 필요합니다. 기존 실행 상태를 확인하세요.', exact=True)).to_be_visible()
                assert len(start_requests) == 1 and fake.calls == ['project', 'generation']
                # Reinspection changes only asset metadata, never the transmission snapshot.
                complete_metadata = False
                saved_state = None
                async def problem_assets(video_id):
                    if saved_state is not None:
                        await asyncio.sleep(1.5)
                        return copy.deepcopy(saved_state)
                    value = await original_read(video_id)
                    value['context'] = {'type':'text','data':{'inputText':edited}}
                    scene = value['video']['scenes'][0]
                    scene['rootShape'] = {'layers': {'ROOT': {'child': ['assigned']}, 'assigned': layer({
                        'title': '오래된 삼성 템플릿 제목', 'image_files': [image('hit.jpg'), image('neutral.jpg'), image('missing.jpg')]})}}
                    scene['rerank_result'] = [image('hit.jpg', tags=['삼성전자']), image('neutral.jpg', title='중립 설비')]
                    if complete_metadata:
                        scene['rerank_result'].append(image('missing.jpg',title='중립 자료'))
                    return value
                fake.video_state = problem_assets
                page.get_by_role('button', name='배정 에셋 다시 검사', exact=True).click()
                expect(page.get_by_text('기업명 검출 1 · 메타데이터 미검출 2 · 확인 불가 1 · 즐겨찾기 예외 0', exact=True)).to_be_visible(timeout=10000)
                expect(page.get_by_text('삼성전자 · tags[0]', exact=True)).to_be_visible()
                expect(page.get_by_text('해당 파일의 검사할 메타데이터가 없습니다.', exact=True)).to_be_visible()
                page.reload(wait_until='networkidle')
                expect(page.get_by_text('기업명 검출 1 · 메타데이터 미검출 2 · 확인 불가 1 · 즐겨찾기 예외 0', exact=True)).to_be_visible()
                assert run_manager.store.get(record['id']).stage == 'needs_attention'
                assert run_manager.store.get(record['id']).snapshot.text == edited
                assert fake.calls == ['project', 'generation'] and len(start_requests) == 1
                # Complete the missing metadata, then build a mixed local plan.
                # Favorite titles containing a restricted company remain eligible.
                favorites = FavoritesClient([favorite(),favorite(1)])
                fake.search_favorites = favorites.search_favorites
                fake.favorite_segment_available = favorites.favorite_segment_available
                complete_metadata = True
                page.get_by_role('button', name='배정 에셋 다시 검사', exact=True).click()
                expect(page.get_by_text('교체 계획 검증 완료 · 서버에는 저장하지 않았습니다.', exact=True)).to_be_visible(timeout=10000)
                expect(page.get_by_text('고정 구간 1개 · 대체 대상 1개', exact=True)).to_be_visible()
                chosen = run_manager.store.get(record['id']).replacement_plan.selections[0].candidate
                assert chosen.id == 'item-0'
                page.reload(wait_until='networkidle')
                expect(page.get_by_text('교체 계획 검증 완료 · 서버에는 저장하지 않았습니다.', exact=True)).to_be_visible()
                page.get_by_role('button', name='고정한 교체 계획 다시 확인', exact=True).click()
                expect(page.get_by_text('후보 선택과 로컬 변환을 확인하는 중…', exact=True)).to_be_visible()
                expect(page.get_by_text('교체 계획 검증 완료 · 서버에는 저장하지 않았습니다.', exact=True)).to_be_visible(timeout=10000)
                assert run_manager.store.get(record['id']).replacement_plan.selections[0].candidate == chosen
                assert run_manager.store.get(record['id']).favorite_replacements == ()
                assert fake.calls == ['project','generation'] and len(start_requests) == 1
                saves = []
                async def save_video_context(video_id, body):
                    nonlocal saved_state
                    assert run_manager.store.get(record['id']).asset_save.receipt_state == 'intent_recorded'
                    saves.append(copy.deepcopy(body))
                    await asyncio.sleep(1.5)
                    saved_state = await problem_assets(video_id)
                    saved_state.update(video=copy.deepcopy(body['video']), context=copy.deepcopy(body['contextData']),
                                       updated_at='2026-11-02T02:00:00Z')
                    return saved_state['updated_at']
                fake.save_video_context = save_video_context
                page.get_by_role('button', name='검증한 교체 계획 서버 저장', exact=True).click()
                expect(page.get_by_text('편집 데이터를 저장하는 중…', exact=True)).to_be_visible()
                expect(page.get_by_text('서버를 다시 읽어 적용·보존 결과를 확인하는 중…', exact=True)).to_be_visible()
                expect(page.get_by_text('서버 저장·재조회 검증 완료', exact=True)).to_be_visible(timeout=10000)
                assert len(saves) == 1 and len(run_manager.store.get(record['id']).favorite_replacements) == 1
                page.reload(wait_until='networkidle')
                expect(page.get_by_text('서버 저장·재조회 검증 완료', exact=True)).to_be_visible()
                assert len(saves) == 1 and fake.calls == ['project','generation']
                expect(page.get_by_text('교체 계획 검증 완료 · 서버에는 저장하지 않았습니다.', exact=True)).not_to_be_visible()
                # Verify layout and capture only synthetic test data.
                for width in (390, 1440):
                    page.set_viewport_size({'width': width, 'height': 1000})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                    page.get_by_role('heading', name='영상 전송본 준비').scroll_into_view_if_needed()
                    page.screenshot(path=str(root / f'.runtime/infox-phase6-{width}.png'), full_page=True)
                assert not errors, errors
                assert not external_requests, 'Unexpected external browser requests'
                browser.close()
            print('PASS: snapshot/review, one-shot generation, audit/reinspection, fixed favorite mixed plan, controlled save/read/verify, persisted history after reload, responsive layout, no external requests or navigation')
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == '__main__':
    main()
