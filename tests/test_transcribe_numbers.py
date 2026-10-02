import pytest

from backend.transcribe_numbers import number_to_korean
from backend.transcribe_numbers import NumberTranscriptionError, transcribe_numeric_tokens


@pytest.mark.parametrize(('value', 'expected'), [
    ('0', '영'),
    ('10', '십'),
    ('11', '십일'),
    ('20', '이십'),
    ('100', '백'),
    ('101', '백일'),
    ('1000', '천'),
    ('6884', '육천팔백팔십사'),
    ('7,051.61', '칠천오십일 점 육일'),
    ('10001', '일만일'),
    ('100000000', '일억'),
])
def test_integer_uses_korean_sino_number_reading(value, expected):
    assert number_to_korean(value) == expected


@pytest.mark.parametrize(('value', 'expected'), [
    ('6884.15', '육천팔백팔십사 점 일오'),
    ('12.00', '십이 점 영영'),
    ('0.05', '영 점 영오'),
    ('-5.60', '마이너스 오 점 육영'),
])
def test_decimal_places_are_read_individually_and_preserved(value, expected):
    assert number_to_korean(value) == expected


@pytest.mark.parametrize('value', ['', '1,23', '1.2.3', '1e3'])
def test_invalid_number_text_is_rejected(value):
    with pytest.raises(ValueError):
        number_to_korean(value)


def test_transcribes_general_values_but_preserves_numeric_terms_and_product_names():
    text = 'KOSPI는 6884.15로 올랐고 2차전지와 KODEX 200 상품도 언급됐습니다.'

    assert transcribe_numeric_tokens(text) == (
        'KOSPI는 육천팔백팔십사 점 일오로 올랐고 2차전지와 KODEX 200 상품도 언급됐습니다.'
    )


def test_transcribes_numbers_attached_to_known_units_and_preserves_market_label():
    text = '2026년 2분기 수출은 4.5% 늘었고 KOSPI 200과 KOSPI200은 언급됐습니다. GDP 2026년 자료도 나왔습니다.'

    assert transcribe_numeric_tokens(text) == (
        '이천이십육년 이분기 수출은 사 점 오% 늘었고 KOSPI 200과 KOSPI200은 언급됐습니다. '
        'GDP 이천이십육년 자료도 나왔습니다.'
    )


def test_preserves_common_korean_index_names_with_embedded_numbers():
    text = '코스피200은 약세였고 코스닥150 상품과 TIGER 미국S&P500도 언급됐습니다.'

    assert transcribe_numeric_tokens(text) == text


def test_unicode_minus_is_read_as_korean_negative_sign():
    assert number_to_korean('−0.03') == '마이너스 영 점 영삼'
    assert transcribe_numeric_tokens('지수는 −0.03% 움직였습니다.') == '지수는 마이너스 영 점 영삼% 움직였습니다.'


@pytest.mark.parametrize('text', [
    '수치는 7,051.6.1입니다.', '수치는 1,23입니다.', '수치는 12345678901234567입니다.', '수치는 .5입니다.',
    'GDP 2026 자료입니다.',
])
def test_ambiguous_or_unsupported_numeric_tokens_require_review(text):
    with pytest.raises(NumberTranscriptionError):
        transcribe_numeric_tokens(text)
