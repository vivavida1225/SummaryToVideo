import pytest

from backend.validation import ValidationError, validate_compressed


def test_plain_narration_preserves_lines_and_counts_all_text(compressed, serialized):
    result = validate_compressed(compressed, serialized)
    assert result.text == compressed
    assert len(result.text.splitlines()) == 5
    assert result.body_char_count == len(compressed) - 4
    assert not result.warnings


def test_collects_both_markets_rates_direction_and_shape(compressed, serialized):
    bad = compressed.replace('7051.61', '7000.00').replace('835.97', '800.00')
    bad = bad.replace('0.67% 오른', '0.68% 내린')
    bad += '\n추가 장면입니다.'
    with pytest.raises(ValidationError) as caught:
        validate_compressed(bad, serialized)
    for expected in ('5줄', '종료 인사', '코스피 최종 지수', '코스닥 최종 지수', '코스닥 등락률', '코스닥 등락 방향'):
        assert any(expected in issue for issue in caught.value.issues)


def test_normalizes_only_outer_whitespace_and_windows_newlines(compressed, serialized):
    assert validate_compressed('\r\n' + compressed.replace('\n', '\r\n') + '\r\n', serialized).text == compressed


@pytest.mark.parametrize('old,new', [
    ('7051.61', '7,051.61'),
    ('보합 마감했고', '0.00%로 보합 마감했고'),
    ('보합 마감했고', '0.00%로 마감했고'),
    ('0.67% 오른 835.97', '835.97로 0.67% 상승 마감했고'),
    ('7051.61로 보합', '7051.61, -0.03 (0.00%) 보합'),
    ('0.67% 오른 835.97', '835.97, +5.60 (0.67%)로 상승세'),
    ('0.67%', '0.67퍼센트'),
    ('7051.61로 보합', '7051.61, -0.03으로 0.00퍼센트 보합'),
    ('7051.61로 보합', '7051.61로 0.03 하락해 0.00% 보합'),
    ('7051.61로 보합', '7051.61로 0.03 하락하며 보합'),
    ('0.67% 오른 835.97', '835.97로 5.60 상승해 0.67% 상승세'),
    ('0.67% 오른 835.97', '835.97로 5.60 올라 0.67% 상승세'),
    ('0.67% 오른 835.97을 기록하며 장 후반 코스닥 중심의 강세가 나타났습니다.',
     '835.97로 0.67% 강세를 보이며 오전의 약세를 이겨냈습니다.'),
    ('0.67% 오른 835.97을 기록하며 장 후반 코스닥 중심의 강세가 나타났습니다.',
     '835.97로 0.67퍼센트 상승하며 오전 약세에서 오후 반도체 중심 강세로 전환되었습니다.'),
])
def test_equivalent_index_expressions_are_accepted(compressed, serialized, old, new):
    assert validate_compressed(compressed.replace(old, new), serialized).text


@pytest.mark.parametrize('old,new', [
    ('835.97', '835.98'), ('7051.61', '7051.6'),
    ('0.67%', '0.68%'), ('0.67% ', ''),
    ('0.67% 오른', '0.67% 내린'), ('0.67% 오른', '-0.67% 오른'),
    ('보합 마감했고', '상승 마감했고'),
    ('0.67% 오른', '보합인'),
    ('코스피는', '다른 지수는'),
])
def test_wrong_or_missing_index_data_rejected(compressed, serialized, old, new):
    with pytest.raises(ValidationError, match='지수|등락'):
        validate_compressed(compressed.replace(old, new), serialized)


def test_numbers_cannot_be_borrowed_from_other_market(compressed, serialized):
    wrong = compressed.replace('7051.61', 'TEMP').replace('835.97', '7051.61').replace('TEMP', '835.97')
    with pytest.raises(ValidationError, match='지수'):
        validate_compressed(wrong, serialized)


def test_intraday_value_cannot_mask_wrong_close(compressed, serialized):
    wrong = compressed.replace('7051.61로 보합 마감했고', '장중 7051.61을 기록한 뒤 7000으로 보합 마감했고')
    with pytest.raises(ValidationError, match='지수'):
        validate_compressed(wrong, serialized)
    good = compressed.replace('7051.61로 보합 마감했고', '장중 7112를 기록한 뒤 7051.61로 보합 마감했고')
    assert validate_compressed(good, serialized).text


def test_thousands_separator_does_not_hide_direction(compressed, serialized):
    source = serialized.replace('-0.03 (0.00%)', '+7.04 (0.10%)')
    good = compressed.replace('7051.61로 보합 마감했고', '0.10%인 7,051.61로 상승 마감했고')
    assert validate_compressed(good, source).text


def test_body_label_after_greeting_is_rejected(compressed, serialized):
    with pytest.raises(ValidationError):
        validate_compressed(compressed.replace('코스피는', '장면 1: 코스피는'), serialized)


def test_repeated_market_intraday_quote_is_not_a_second_close(compressed, serialized):
    lines = compressed.splitlines()
    lines[0] = ('오늘의 AI 시황입니다. 코스피는 7051.61로 보합 마감했고 코스닥은 0.67% 오른 835.97로 마감했습니다. '
                '코스피는 장중 7112를 기록한 뒤 상승폭을 반납했습니다.')
    source = serialized.replace('· 요약', '· 장중 코스피 7112를 기록한 뒤 상승폭 반납')
    assert validate_compressed('\n'.join(lines), source).text
    lines[0] = lines[0].replace('장중 7112를 기록한 뒤 상승폭을 반납했습니다.', '7000으로 마감했습니다.')
    with pytest.raises(ValidationError, match='지수'):
        validate_compressed('\n'.join(lines), source)


def test_negative_unsigned_percent_uses_change_direction(compressed, serialized):
    source = serialized.replace('+5.60 (0.67%)', '-5.60 (0.67%)')
    good = compressed.replace('0.67% 오른', '0.67% 내린')
    assert validate_compressed(good, source).text
    with pytest.raises(ValidationError, match='등락'):
        validate_compressed(compressed, source)


@pytest.mark.parametrize('kind', ['four', 'six', 'blank', 'fence', 'marker', 'bullet', 'heading', 'label', 'greeting', 'outro', 'misplaced', 'duplicate', 'three', 'unterminated', 'empty_sentence'])
def test_invalid_narration_contract_rejected(compressed, serialized, kind):
    lines = compressed.splitlines()
    if kind == 'four': lines.pop(2)
    elif kind == 'six': lines.insert(2, '추가 설명입니다.')
    elif kind == 'blank': lines[2] = ''
    elif kind == 'fence': lines = ['```text', *lines, '```']
    elif kind == 'marker': lines[2] = '<3>' + lines[2]
    elif kind == 'bullet': lines[2] = '· ' + lines[2]
    elif kind == 'heading': lines[2] = '**주도 업종** ' + lines[2]
    elif kind == 'label': lines[2] = '장면 3: ' + lines[2]
    elif kind == 'greeting': lines[0] = lines[0].removeprefix('오늘의 AI 시황입니다. ')
    elif kind == 'outro': lines[4] = lines[4].removesuffix(' 오늘의 AI 시황이었습니다.')
    elif kind == 'misplaced': lines[1] = '오늘의 AI 시황입니다. ' + lines[1]
    elif kind == 'duplicate': lines[0] = '오늘의 AI 시황입니다. ' + lines[0]
    elif kind == 'three': lines[1] += ' 추가 문장입니다.'
    elif kind == 'unterminated': lines[1] += ' 미완성 문장'
    elif kind == 'empty_sentence': lines[1] = ' .'
    with pytest.raises(ValidationError):
        validate_compressed('\n'.join(lines), serialized)


@pytest.mark.parametrize('size,allowed', [(489, False), (490, True), (500, True), (550, True), (551, False), (650, False)])
def test_length_contract_counts_greetings_and_rejects_both_bounds(compressed, serialized, size, allowed):
    lines = compressed.splitlines()
    lines[1:4] = ['정보가 부족합니다.'] * 3
    base = sum(map(len, lines))
    lines[1] = '가' * (size - base) + lines[1]
    if allowed:
        result = validate_compressed('\n'.join(lines), serialized)
        assert result.body_char_count == size
        assert not result.warnings
    else:
        with pytest.raises(ValidationError, match='490|550'):
            validate_compressed('\n'.join(lines), serialized)


@pytest.mark.parametrize('word', ['급락', '폭락', '내림세'])
def test_downward_synonyms_preserve_direction(compressed, serialized, word):
    source = serialized.replace('+5.60 (0.67%)', '-5.60 (-0.67%)')
    text = compressed.replace('0.67% 오른 835.97', f'835.97로 0.67% {word}를 보인 수준')
    assert validate_compressed(text, source).text
    with pytest.raises(ValidationError, match='등락 방향'):
        validate_compressed(text, serialized)


@pytest.mark.parametrize('word', ['급등', '폭등', '반등', '오름세'])
def test_upward_synonyms_preserve_direction(compressed, serialized, word):
    text = compressed.replace('0.67% 오른 835.97', f'835.97로 0.67% {word}를 보인 수준')
    assert validate_compressed(text, serialized).text
    with pytest.raises(ValidationError, match='등락 방향'):
        validate_compressed(text, serialized.replace('+5.60 (0.67%)', '-5.60 (-0.67%)'))


def test_short_sparse_source_is_not_silently_accepted(compressed, serialized):
    lines = compressed.splitlines()
    lines[1:4] = ['자료에서 확인되지 않습니다.'] * 3
    with pytest.raises(ValidationError, match='490'):
        validate_compressed('\n'.join(lines), serialized)
