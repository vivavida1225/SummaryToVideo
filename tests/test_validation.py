import pytest

from backend.validation import ValidationError, _correct_transcribed_closes, validate_compressed
from backend.transcribe_numbers import number_to_korean


def test_plain_narration_preserves_lines_and_counts_all_text(compressed, serialized):
    result = validate_compressed(compressed, serialized)
    assert result.text == compressed
    assert len(result.text.splitlines()) == 5
    assert result.body_char_count == len(compressed) - 4
    assert not result.warnings


@pytest.mark.parametrize('forward_sentence', [
    '다음 장에서는 국제유가와 외국인 수급의 변화를 확인해야겠습니다.',
    '다음 거래일의 핵심 관전 포인트는 국제유가 흐름입니다.',
    '향후 외국인 수급을 주목해야 합니다.',
])
def test_forward_looking_closing_language_is_rejected(compressed, serialized, forward_sentence):
    neutral = '한국은행은 명목성장률과 레버리지 리스크를 지적하며 추가 금리 인상 가능성을 언급했습니다.'
    lines = compressed.splitlines()
    lines[4] = lines[4].replace(neutral, forward_sentence)

    with pytest.raises(ValidationError) as caught:
        validate_compressed('\n'.join(lines), serialized)

    assert any('미래 전망이나 관전·확인 권고' in issue for issue in caught.value.issues)


@pytest.mark.parametrize('word', ['치솟다', '치솟았다', '치솟으며', '치솟는'])
def test_chisotda_and_conjugations_are_rejected(compressed, serialized, word):
    text = compressed.replace('명목성장률과', f'{word} 명목성장률과', 1)
    # Keep the fixture inside the character range while changing the target wording.
    lines = text.splitlines()
    deficit = max(0, 490 - sum(map(len, lines)))
    lines[4] = lines[4].replace(' 오늘의 AI 시황이었습니다.', '가' * deficit + ' 오늘의 AI 시황이었습니다.')
    text = '\n'.join(lines)

    with pytest.raises(ValidationError) as caught:
        validate_compressed(text, serialized)

    assert any('치솟다' in issue for issue in caught.value.issues)


def test_similar_non_banned_rise_expression_is_accepted(compressed, serialized):
    text = compressed.replace('명목성장률과', '급등과 명목성장률과', 1)
    assert validate_compressed(text, serialized).text == text


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


@pytest.mark.parametrize('wrong_close', ['7000.00', number_to_korean('7000.00')])
def test_transcription_mode_repairs_only_wrong_closes_and_counts_final_text(compressed, serialized, wrong_close):
    wrong = compressed.replace('7051.61', wrong_close)

    try:
        result = validate_compressed(wrong, serialized, transcribe_numbers=True)
    except Exception as exc:
        pytest.fail(f'transcription mode should correct a uniquely located close: {exc}')

    assert number_to_korean('7051.61') in result.text
    assert number_to_korean('7000.00') not in result.text
    assert result.body_char_count == len(result.text.replace('\n', ''))
    assert 490 <= result.body_char_count <= 550


def test_transcription_mode_keeps_market_close_mapping_and_uses_final_not_intraday_value(compressed, serialized):
    swapped = compressed.replace('7051.61', 'TEMP').replace('835.97', '7051.61').replace('TEMP', '835.97')
    swapped = swapped.replace('7051.61을 기록하며', '장중 900을 기록한 뒤 7051.61을 기록하며')

    result = validate_compressed(swapped, serialized, transcribe_numbers=True)

    assert number_to_korean('7051.61') in result.text
    assert number_to_korean('835.97') in result.text
    assert number_to_korean('900') in result.text
    assert 'TEMP' not in result.text


def test_transcription_mode_requires_review_when_close_has_no_unique_close_marker(compressed, serialized):
    ambiguous = compressed.replace(
        '7051.61로 보합 마감했고,', '7051.61로 보합하며 999선까지 회복했고,'
    )

    with pytest.raises(ValidationError) as caught:
        validate_compressed(ambiguous, serialized, transcribe_numbers=True)

    assert caught.value.requires_review


def test_transcription_mode_accepts_a_unique_exact_close_without_close_marker(compressed, serialized):
    exact_close = compressed.replace('7051.61로 보합 마감했고,', '7051.61로 보합했고,')

    result = validate_compressed(exact_close, serialized, transcribe_numbers=True)

    assert number_to_korean('7051.61') in result.text


def test_transcription_mode_requires_review_when_an_amount_follows_intraday_index(compressed, serialized):
    ambiguous = compressed.replace(
        '7051.61로 보합 마감했고,',
        '장중 7000선을 회복했고 프로그램 순매수 3200억원으로 마감했습니다,',
    )

    with pytest.raises(ValidationError) as caught:
        validate_compressed(ambiguous, serialized, transcribe_numbers=True)

    assert caught.value.requires_review
    assert '종가와 다른 수치의 경계' in caught.value.issues[0]


def test_transcription_mode_does_not_replace_a_later_record_with_the_close(compressed, serialized):
    later_record = compressed.replace(
        '7051.61로 보합 마감했고,', '7000으로 보합 마감했고 장중 고점 7200을 기록했습니다,'
    )

    corrected = _correct_transcribed_closes(later_record, serialized)

    assert '7200' in corrected
    assert '7,051.61' in corrected


def test_transcription_mode_requires_review_when_close_is_not_linked_to_close_marker(compressed, serialized):
    ambiguous = compressed.replace(
        '7051.61로 보합 마감했고,',
        '7000으로 0.03% 상승했고 장중 고점 7200을 기록한 뒤 마감했습니다,',
    )

    with pytest.raises(ValidationError) as caught:
        validate_compressed(ambiguous, serialized, transcribe_numbers=True)

    assert caught.value.requires_review
    assert '최종 종가 위치' in caught.value.issues[0]


def test_transcription_mode_does_not_replace_preserved_product_digits_with_close(compressed, serialized):
    with_product = compressed.replace(
        '7051.61로 보합 마감했고,', '7051.61로 보합 TIGER S&P500이 강세로 마감했고,'
    )

    corrected = _correct_transcribed_closes(with_product, serialized)

    assert 'TIGER S&P500' in corrected
    assert '7,051.61' in corrected


def test_transcription_mode_rejects_an_extra_conflicting_rate(compressed, serialized):
    conflicting = compressed.replace(
        '0.67% 오른 835.97을 기록하며',
        '0.67% 오른 835.97을 기록하며 0.50%의 추가 변동도 나타났습니다',
    )

    with pytest.raises(ValidationError):
        validate_compressed(conflicting, serialized, transcribe_numbers=True)


@pytest.mark.parametrize('wrong', [
    '0.66% 오른',
    '0.67% 내린',
])
def test_transcription_mode_still_validates_rate_and_direction(compressed, serialized, wrong):
    bad = compressed.replace('0.67% 오른', wrong)
    try:
        validate_compressed(bad, serialized, transcribe_numbers=True)
    except TypeError as exc:
        pytest.fail(f'validation mode parameter is missing: {exc}')
    except ValidationError:
        return
    pytest.fail('transcription mode accepted an incorrect rate or direction')


def test_transcription_mode_does_not_accept_wrong_rate_as_zero_change(compressed, serialized):
    bad = compressed.replace('7051.61로 보합', '7051.61로 0.01% 오른 보합')
    try:
        validate_compressed(bad, serialized, transcribe_numbers=True)
    except TypeError as exc:
        pytest.fail(f'validation mode parameter is missing: {exc}')
    except ValidationError:
        return
    pytest.fail('transcription mode accepted a nonzero rate for the zero-change source')


def test_transcription_mode_accepts_spoken_zero_rate_with_neutral_direction(compressed, serialized):
    explicit_zero = compressed.replace('7051.61로 보합 마감', '7051.61로 0.00% 보합 마감')
    result = validate_compressed(explicit_zero, serialized, transcribe_numbers=True)
    assert f"{number_to_korean('0.00')}%" in result.text
