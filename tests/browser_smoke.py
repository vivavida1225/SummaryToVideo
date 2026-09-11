"""Browser check against a running local server; no Gemini calls."""

import json
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8765'
    live = json.loads(Path('.runtime/live-smoke.json').read_text(encoding='utf-8'))
    assert live['state'] == 'completed', 'Run the explicit live check first.'
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1100})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(url, wait_until='networkidle')
        page.get_by_text('저장된 파일', exact=True).click()
        expect(page.get_by_role('combobox', name='HTML 파일 선택')).to_have_value('')
        expect(page.get_by_role('button', name='변환 및 1분 압축')).to_be_disabled()
        page.get_by_text('클립보드', exact=True).click()
        page.get_by_role('textbox', name='클립보드 HTML 원문').fill('invalid HTML input')
        page.get_by_role('button', name='변환 및 1분 압축').click()
        expect(page.get_by_text('작업 중 문제가 생겼습니다')).to_be_visible()
        expect(page.get_by_role('button', name='압축 다시 시도')).to_have_count(0)
        page.evaluate('(id) => sessionStorage.setItem("market-compressor.job-id", id)', live['id'])
        page.reload(wait_until='networkidle')
        expect(page.get_by_role('textbox', name='압축 결과', exact=True)).to_have_value(live['compressed'])
        expect(page.get_by_text('입력 6개 → 최종 5줄 대본')).to_be_visible()
        with page.expect_download() as downloaded:
            page.get_by_role('button', name='압축 결과 다운로드').click()
        assert Path(downloaded.value.path()).read_text(encoding='utf-8') == live['compressed']
        page.get_by_role('button', name='압축 결과 복사').click()
        expect(page.get_by_text('압축 결과를 클립보드에 복사했습니다.')).to_be_attached()
        page.screenshot(path='.runtime/ui-results.png', full_page=True)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.screenshot(path='.runtime/ui-mobile.png', full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
        browser.close()
    print('browser source selection / invalid input / archive recovery / authenticated download / clipboard / responsive layout: PASS')


if __name__ == '__main__':
    main()
