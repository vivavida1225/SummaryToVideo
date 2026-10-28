import pytest

from backend import validation
from backend.narration import validate_response
from backend.transcribe_numbers import transcribe_numeric_tokens
from backend.validation import ValidationError, count_characters, validate_compressed


@pytest.mark.parametrize('char,code', [
    ('美', 'U+7F8E'), ('中', 'U+4E2D'), ('日', 'U+65E5'), ('漢', 'U+6F22'),
    ('\U00020000', 'U+20000'), ('\uF900', 'U+F900'),
    ('\U0002F800', 'U+2F800'), ('〇', 'U+3007'),
])
def test_hanja_requires_review_with_original_character_and_context(compressed, serialized, char, code):
    draft = compressed.replace('중동 긴장', char + '동 긴장')
    with pytest.raises(ValidationError) as caught:
        validate_compressed(draft, serialized)
    assert caught.value.requires_review
    assert caught.value.text == draft
    assert caught.value.body_char_count == count_characters(draft)
    assert len(caught.value.issues) == 1
    issue = caught.value.issues[0]
    assert '장면 2:' in issue and f"'{char}'({code})" in issue
    assert '문맥:' in issue and char + '동 긴장' in issue


def test_hangul_latin_numbers_and_emoji_are_not_hanja(compressed, serialized):
    draft = compressed.replace('중동 긴장', '중동 긴장😀')
    result = validate_compressed(draft, serialized + '\n美 中 日 漢 〇')
    assert result.text == draft
    assert result.warnings == []


def test_hanja_issues_group_characters_per_scene_and_clip_first_context():
    issues = validation._find_hanja_issues('가' * 20 + '美中美' + '나' * 20 + '\n日 시장')
    assert len(issues) == 2
    assert issues[0].startswith('장면 1:')
    assert issues[0].count("'美'(U+7F8E)") == 1
    assert issues[0].index("'美'") < issues[0].index("'中'")
    context = issues[0].split('문맥: ', 1)[1]
    assert context == '…' + '가' * 12 + '美中美' + '나' * 10 + '…'
    assert issues[1].startswith('장면 2:') and '日 시장' in issues[1]


@pytest.mark.parametrize('transcribe_numbers', [False, True])
@pytest.mark.parametrize('separator', ['\n', '<SCENE_BREAK>'])
def test_hanja_review_survives_decoding_and_numeric_mode(
        compressed, serialized, transcribe_numbers, separator):
    draft = compressed.replace('중동 긴장', '中동 긴장')
    expected = transcribe_numeric_tokens(draft) if transcribe_numbers else draft
    with pytest.raises(ValidationError) as caught:
        validate_response(draft.replace('\n', separator), serialized,
                          transcribe_numbers=transcribe_numbers)
    assert caught.value.requires_review
    assert caught.value.text == expected
    assert caught.value.body_char_count == count_characters(expected)
    assert any('장면 2: 한자' in issue for issue in caught.value.issues)


def test_hanja_combines_with_separator_format_and_length_issues(compressed, serialized):
    raw = '<SCENE_BREAK>'.join(compressed.replace('중동 긴장', '中동 긴장').splitlines()[:4])
    raw += '가' * 150
    with pytest.raises(ValidationError) as caught:
        validate_response(raw, serialized)
    assert caught.value.requires_review
    for fragment in ('한자', '구분자는 정확히 4개', '정확히 5줄', '490~550자'):
        assert any(fragment in issue for issue in caught.value.issues)


def test_hanja_combines_with_numeric_boundary_review(compressed, serialized):
    draft = compressed.replace('중동 긴장', '中동 긴장').replace('100달러', '1,23달러')
    with pytest.raises(ValidationError) as caught:
        validate_response(draft, serialized, transcribe_numbers=True)
    assert caught.value.requires_review
    assert any('한자' in issue for issue in caught.value.issues)
    assert len(caught.value.issues) >= 2
    assert caught.value.text == draft
