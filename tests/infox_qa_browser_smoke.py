"""QA for multi-clip details and pre-upload auto downloads; local route mocks only.

Run after frontend build: python -X utf8 -m tests.infox_qa_browser_smoke
"""

import copy
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from tests import infox_gui_layout_smoke as layout


def main():
    root = Path(__file__).resolve().parents[1]
    layout.RUN = copy.deepcopy(layout.RUN)
    plan = layout.RUN['replacement_plan']
    plan['policy'] = {'search':'중국 대만 반도체','reuse_policy':'random_six_second_sequence'}
    for choice in plan['selections']:
        candidate = choice['candidate']
        second = {**candidate,'id':candidate['id']+'b','segment':'second-'+candidate['segment']}
        choice.update(slot_frames=420,available_candidates=2,clips=[
            {'candidate':candidate,'slot_frames':140,'result_item_index':0},
            {'candidate':second,'slot_frames':140,'result_item_index':1},
            {'candidate':candidate,'slot_frames':140,'result_item_index':2}])
    pub = layout.publication(2,'https://youtu.be/ZYXWVUTSRQP','2026-11-09')
    pub.update(stage='generating_metadata',uploaded_verified=False,share_url=None,clipboard_state='not_started')
    layout.SCENARIOS['qa_after_render'] = {'publication':pub,'history':[pub]}

    def downloads(publication_id, day):
        return [{'id':'auto-three','publication_id':publication_id,'kind':'all','automatic':True,
            'state':'partial','error_code':None,'items':[
                {'kind':kind,'filename':f'{day}_{name}','state':'failed' if kind=='video' else 'completed',
                 'path':None if kind=='video' else f'C:/Users/user/Downloads/{day}_{name}',
                 'size':None if kind=='video' else 1000,'sha256':None,'error_code':'timeout' if kind=='video' else None}
                for kind,name in (('script','대본.txt'),('source','원본데이터.txt'),('video','영상시황.mp4'))]}]
    layout.downloads = downloads
    server,url = layout.serve(root/'frontend/dist')
    problems = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel='msedge',headless=True)
            for width in (1366,390,320):
                context,page = layout.open_page(browser,url,'qa_after_render',width,844,problems)
                for name in ('직렬화 원문 다운로드','압축 결과 다운로드'):
                    button = page.get_by_role('button',name=name,exact=True)
                    expect(button).to_have_attribute('title','마지막 렌더링 단계에서 자동으로 다운로드됩니다.')
                    button.hover()
                downloads_panel = page.locator('.infox-downloads')
                expect(downloads_panel).to_have_count(1)
                expect(downloads_panel.get_by_text('렌더 완료 대본·원본·영상 자동 다운로드',exact=True)).to_be_visible()
                expect(downloads_panel.get_by_text('일부 다운로드 완료',exact=True)).to_be_visible()
                expect(downloads_panel.get_by_role('button',name='실패 파일 다시 다운로드',exact=True)).to_be_enabled()
                expect(page.get_by_role('button',name='대본·원본·영상 3개 파일 다운로드',exact=True)).to_have_count(0)
                page.get_by_role('button',name='펼치기 자동 제작',exact=True).click()
                page.get_by_text('장면별 검사·교체 근거',exact=True).click()
                expect(page.get_by_text('후보 부족 · 사용 가능 2개를 순환 배치합니다.',exact=True).first).to_be_visible()
                expect(page.get_by_text('대체 구간 14초 · 소재 원음 끔',exact=True).first).to_be_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), ('overflow',width)
                downloads_panel.scroll_into_view_if_needed()
                page.screenshot(path=str(root/f'.runtime/infox-qa-{width}.png'))
                context.close()
            browser.close()
    finally:
        server.shutdown()
    assert not problems, problems
    print('PASS: 1366/390/320px, multi-clip details, short pool notice, automatic 3-file partial results before upload, tooltips; mocked API only')


if __name__ == '__main__':
    main()
