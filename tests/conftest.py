from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def sample_html():
    return (ROOT / 'src/0910/0910_html.txt').read_text(encoding='utf-8-sig')


@pytest.fixture
def tiny_html():
    return '''<div class="embedded-content"><header> AI&middot;반도체  흐름 </header>
<div class="summary-box"><p class="summary">· 첫 <b>요약</b></p><p class="summary">· 두 번째</p>
<div class="indices"><div class="index-card"><div class="index-name">코스피</div><div class="index-value">7,051.61</div><div class="index-change">-0.03 (0.00%)</div></div>
<div class="index-card"><div class="index-name">코스닥</div><div class="index-value">835.97</div><div class="index-change">+5.60 (0.67%)</div></div></div></div>
<section><h2>수급&middot;원인</h2><ul><li><span class="key-info">AI<b>주도:</b></span><span class="key-detail">외국인  매수 &rarr; 상승</span></li></ul></section></div>'''


@pytest.fixture
def serialized():
    return '<1>시장 흐름\n· 요약\n\n코스피\n7,051.61\n-0.03 (0.00%)\n코스닥\n835.97\n+5.60 (0.67%)\n===\n<2>원인\n수급\n외국인 매도'


@pytest.fixture
def compressed():
    return (ROOT / 'tests/fixtures/narration.txt').read_text(encoding='utf-8').strip()
