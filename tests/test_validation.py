import pytest

from backend.validation import ValidationError, validate_compressed


def test_valid_response_unwraps_without_changing_indices(compressed, serialized):
    result = validate_compressed(compressed, serialized)
    assert result.text.startswith('<1>')
    assert result.text.count('\n===\n') == 4
    assert '7,051.61\n-0.03 (0.00%)' in result.text
    assert result.body_char_count > 0
    assert result.warnings  # The fixture is below the recommended 400 characters.


@pytest.mark.parametrize('old,new', [('===','===='), ('<3>','<6>'), ('835.97','835.98'), ('-0.03 (0.00%)','+0.03 (0.00%)'), ('\n<2>','\n\n<2>'), ('AI 수요 기대가 반도체 강세를 지지','가' * 71)])
def test_invalid_structure_or_changed_financial_numbers_rejected(compressed, serialized, old, new):
    with pytest.raises(ValidationError):
        validate_compressed(compressed.replace(old, new), serialized)


def test_rejects_explanation_outside_codeblock(compressed, serialized):
    with pytest.raises(ValidationError):
        validate_compressed('결과입니다\n' + compressed, serialized)


def test_rejects_body_over_650_even_if_descriptions_short(compressed, serialized):
    bad = compressed.replace('반도체의 상대 강세', '가' * 651)
    with pytest.raises(ValidationError, match='650'):
        validate_compressed(bad, serialized)
