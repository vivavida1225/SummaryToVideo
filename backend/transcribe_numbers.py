import re


_NUMBER = re.compile(r'([+\-−]?)([0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.([0-9]+))?\Z')
_DIGITS = '영일이삼사오육칠팔구'
_SMALL_UNITS = ('', '십', '백', '천')
_LARGE_UNITS = ('', '만', '억', '조')
_TOKEN = re.compile(r'(?<![0-9])([+\-−]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?)')
_FIXED_TERMS = re.compile(r'(?<![0-9])2차전지')
_MAGNITUDE_UNITS = ('조', '억', '만', '천', '백', '십')
_REDUNDANT_ONE = re.compile(r'일(?=(?:천|백|십))')
_PRODUCT_PREFIX = re.compile(
    r'(?<![A-Za-z0-9])(?:KODEX|TIGER|ACE|RISE|SOL|HANARO|ARIRANG|KOSEF|KBSTAR|SMART|FOCUS|PLUS|'
    r'TIMEFOLIO|KOSPI|KOSDAQ|코스피|코스닥|S&P|NASDAQ|DOW|DJI|NIKKEI)[ \t]*\Z'
)
_ACRONYM_PREFIX = re.compile(r'(?<![A-Za-z0-9])[A-Z][A-Z0-9&.-]*[ \t]+\Z')
_KOREAN_SUFFIXES = (
    '퍼센트', '포인트', '달러', '계약', '분기', '지수', '조원', '억원', '천원', '만원', '조', '억', '만', '천',
    '원', '년', '월', '일', '주', '분', '초', '개', '명', '회', '건', '배', '곳', '대', '선',
    '으로', '에서', '까지', '보다', '당', '은', '는', '이', '가', '을', '를', '의', '와', '과', '로',
)


def _read_integer(value: str) -> str:
    normalized = value.lstrip('0') or '0'
    if normalized == '0':
        return '영'
    if len(normalized) > 16:
        raise ValueError('정수 자릿수가 너무 커서 안전하게 전사할 수 없습니다.')

    groups = []
    while normalized:
        groups.append(normalized[-4:])
        normalized = normalized[:-4]

    words = []
    for group_index, raw_group in reversed(list(enumerate(groups))):
        chunk = int(raw_group)
        if not chunk:
            continue
        chunk_words = []
        for place, digit_char in enumerate(reversed(raw_group)):
            digit = int(digit_char)
            if not digit:
                continue
            if place and digit == 1:
                chunk_words.append(_SMALL_UNITS[place])
            else:
                chunk_words.append(_DIGITS[digit] + _SMALL_UNITS[place])
        words.append(''.join(reversed(chunk_words)) + _LARGE_UNITS[group_index])
    return ''.join(words)


def number_to_korean(value: str) -> str:
    """Read a signed decimal, preserving every fractional digit."""
    match = _NUMBER.fullmatch(value)
    if not match:
        raise ValueError(f'잘못된 숫자 형식입니다: {value!r}')
    sign, integer, fractional = match.groups()
    result = _read_integer(integer.replace(',', ''))
    if fractional is not None:
        result += ' 점 ' + ''.join(_DIGITS[int(digit)] for digit in fractional)
    if sign in ('-', '−'):
        result = '마이너스 ' + result
    elif sign == '+':
        result = '플러스 ' + result
    return result


class NumberTranscriptionError(ValueError):
    pass


def is_preserved_numeric_label(text: str, start: int, end: int) -> bool:
    if any(left <= start and end <= right for left, right in
           (match.span(0) for match in _FIXED_TERMS.finditer(text))):
        return True
    return bool(_PRODUCT_PREFIX.search(text[:start]))


def transcribe_numeric_tokens(text: str) -> str:
    replacements = []
    for match in _TOKEN.finditer(text):
        start, end = match.span(1)
        raw = match[1]
        # Known index/product labels keep their embedded numeric spelling (e.g. KODEX 200).
        if is_preserved_numeric_label(text, start, end):
            continue

        previous = start
        while previous > 0 and text[previous - 1].isspace():
            previous -= 1
        embedded_magnitude = (
            previous > 0
            and text[previous - 1] in _MAGNITUDE_UNITS
            and any(text.startswith(unit, end) for unit in _MAGNITUDE_UNITS)
        )
        if start and (text[start - 1].isalnum() or '\uac00' <= text[start - 1] <= '\ud7a3'
                       or text[start - 1] in '.,') and not embedded_magnitude:
            raise NumberTranscriptionError(f'숫자 앞 경계를 확인할 수 없습니다: {raw}')
        if end < len(text) and text[end] in '.,' and end + 1 < len(text) and text[end + 1].isdigit():
            raise NumberTranscriptionError(f'숫자 뒤 경계를 확인할 수 없습니다: {raw}')

        suffix = ''
        if end < len(text) and ('\uac00' <= text[end] <= '\ud7a3'):
            suffix = next((item for item in _KOREAN_SUFFIXES if text.startswith(item, end)), '')
            if not suffix:
                # Keep known fixed technical labels such as 5G intact; other identifiers need review.
                if raw == '5' and text[end] == 'G':
                    continue
                raise NumberTranscriptionError(f'숫자와 한글이 붙은 표현을 확인해야 합니다: {raw}')
        elif end < len(text) and text[end].isalpha() and text[end] not in '%':
            if raw == '5' and text[end] == 'G':
                continue
            raise NumberTranscriptionError(f'숫자와 영문자가 붙은 표현을 확인해야 합니다: {raw}')

        if not suffix and _ACRONYM_PREFIX.search(text[:start]):
            raise NumberTranscriptionError(f'약어 뒤 숫자가 일반 수치인지 상품명인지 확인해야 합니다: {raw}')

        if end < len(text) and text[end] == ',' and end + 1 < len(text) and text[end + 1].isdigit():
            raise NumberTranscriptionError(f'쉼표가 포함된 숫자 형식을 확인해야 합니다: {raw}')
        try:
            spoken = number_to_korean(raw.replace(',', ''))
        except ValueError as exc:
            raise NumberTranscriptionError(str(exc)) from None
        if embedded_magnitude and previous == start:
            spoken = ' ' + spoken
        replacements.append((start, end, spoken))

    result = text
    for start, end, spoken in reversed(replacements):
        result = result[:start] + spoken + result[end:]
    return _REDUNDANT_ONE.sub('', result)
