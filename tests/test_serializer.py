from pathlib import Path

import pytest

from backend.serializer import SerializationError, serialize_html


def test_exact_spacing_entities_and_inline_text(tiny_html):
    assert serialize_html(tiny_html) == ('<1>AI·반도체  흐름\n· 첫 요약\n\n· 두 번째\n\n'
        '코스피\n7,051.61\n-0.03 (0.00%)\n코스닥\n835.97\n+5.60 (0.67%)\n===\n'
        '<2>수급·원인\nAI주도:\n외국인  매수 → 상승')


def test_sample_matches_hand_checked_golden(sample_html):
    expected = (Path(__file__).parent / 'fixtures/0910_serialized.txt').read_text(encoding='utf-8')
    result = serialize_html(sample_html)
    assert result == expected.rstrip('\n')
    assert result.count('\n===\n') == 5


def test_ignores_external_wrapper_ads_and_scripts(tiny_html):
    html = '<div class="embedded-content">outside<script>bad</script>' + tiny_html + '<div class="advertisement"><header>ad</header></div></div>'
    assert serialize_html(html) == serialize_html(tiny_html)


def test_removes_ad_section_inside_news(tiny_html):
    html = tiny_html.replace('<section>', '<section class="youtube"><header>video</header></section><section>', 1)
    assert serialize_html(html) == serialize_html(tiny_html)


@pytest.mark.parametrize('old,new', [('<header>', '<div>'), ('class="key-detail"','class="missing"'), ('class="index-change"','class="missing"')])
def test_missing_required_data_fails_instead_of_partial_output(tiny_html, old, new):
    with pytest.raises(SerializationError):
        serialize_html(tiny_html.replace(old, new))


def test_rejects_multiple_news_areas(tiny_html):
    with pytest.raises(SerializationError, match='여러'):
        serialize_html(tiny_html + tiny_html)


def test_line_wrapping_does_not_leak_blank_lines(tiny_html):
    result = serialize_html(tiny_html.replace('외국인  매수 &rarr; 상승', '외국인\n    매수<br>상승'))
    assert result.endswith('외국인 매수 상승')


def test_outer_page_section_does_not_count_as_nested_news(sample_html):
    assert serialize_html('<section class="page">' + sample_html + '</section>') == serialize_html(sample_html)


def test_adjacent_heading_blocks_are_separated(tiny_html):
    html = tiny_html.replace(' AI&middot;반도체  흐름 ', '<h1>Market</h1><h2>Summary</h2>')
    assert serialize_html(html).startswith('<1>Market Summary\n')
