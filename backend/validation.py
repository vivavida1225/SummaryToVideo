"""Validate narration shape and quoted indices without rewriting the script.

These deterministic checks are not a general Korean fact/causality checker.
"""

import re
from dataclasses import dataclass
from decimal import Decimal


INTRO = '오늘의 AI 시황입니다.'
OUTRO = '오늘의 AI 시황이었습니다.'
NUMBER = r'[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
NUMBER_TOKEN = re.compile(rf'(?<![\d.,+\-−])({NUMBER})(?!\d|[.,]\d)')
RATE_UNIT = re.compile(r'^\s*(?:%|퍼센트)')
# A dot between two digits belongs to a decimal, not a sentence boundary.
SENTENCE_END = re.compile(r'(?<!\d)\.|\.(?!\d)')
UP = re.compile(r'상승|오른|올랐|올라|오르|강세')
DOWN = re.compile(r'하락|내린|내렸|내려|내리|약세|떨어|떨었')


class ValidationError(ValueError):
    pass


@dataclass
class ValidatedText:
    text: str
    body_char_count: int
    warnings: list[str]


def index_triples(serialized: str) -> list[str]:
    first = serialized.split('\n===\n', 1)[0].splitlines()
    output = []
    for name in ('코스피', '코스닥'):
        positions = [i for i, line in enumerate(first) if line == name]
        if len(positions) != 1 or positions[0] + 2 >= len(first):
            raise ValidationError(f'입력에 {name} 최종 지수와 등락폭·등락률이 필요합니다.')
        i = positions[0]
        if not re.fullmatch(r'[\d,]+(?:\.\d+)?', first[i + 1]) or not re.fullmatch(
            r'[+\-−]?[\d,]+(?:\.\d+)?\s*\([+\-−]?[\d,]+(?:\.\d+)?%\)', first[i + 2]
        ):
            raise ValidationError(f'입력의 {name} 지수/등락 표기를 확인하세요.')
        output.extend(first[i:i + 3])
    return output


def _number(text: str) -> Decimal:
    return Decimal(text.replace(',', '').replace('−', '-'))


def _directions(text: str) -> set[int]:
    return ({1} if UP.search(text) else set()) | ({-1} if DOWN.search(text) else set())


def _closing_quote(clause: str, numbers: list[re.Match]) -> re.Match | None:
    """Prefer the value attached to 마감 over an earlier intraday 기록.

Signed point changes and explicit 'N포인트 상승' are not index levels.
Ambiguous/unsupported prose is returned to the model for repair, not rewritten.
"""
    levels = []
    for m in numbers:
        after = clause[m.end():]
        if m[1].startswith(('+', '-', '−')) or RATE_UNIT.match(after):
            continue
        if levels and re.match(rf'\s*(?:포인트\s*)?(?:{UP.pattern}|{DOWN.pattern})', after):
            continue
        levels.append(m)
    for verb in ('마감', '기록'):
        endings = list(re.finditer(verb, clause))
        if endings:
            preceding = [m for m in levels if m.end() <= endings[-1].start()]
            return preceding[-1] if preceding else None
    return levels[0] if len(levels) == 1 else None


def _validate_indices(first_line: str, serialized: str) -> None:
    expected = index_triples(serialized)
    # Stop at every market name, including repeated references to the same market.
    names = list(re.finditer('코스피|코스닥', first_line))
    clauses = [(m[0], first_line[m.end():names[i + 1].start() if i + 1 < len(names) else len(first_line)])
               for i, m in enumerate(names)]
    for offset in (0, 3):
        name, close, change = expected[offset:offset + 3]
        delta, percent = re.fullmatch(rf'({NUMBER})\s*\(({NUMBER})%\)', change).groups()
        rate, delta_value = abs(_number(percent)), _number(delta)
        direction = 1 if delta_value > 0 else -1 if delta_value < 0 else 0
        candidates = []
        for market, clause in clauses:
            if market != name:
                continue
            if '마감' not in clause and re.match(r'^(?:는|은)?\s*(?:장중|오전|오후)', clause):
                continue
            numbers = list(NUMBER_TOKEN.finditer(clause))
            quoted = _closing_quote(clause, numbers)
            if quoted is not None and _number(quoted[1]) == _number(close):
                candidates.append((clause, quoted, numbers))
            elif quoted is not None:
                raise ValidationError(f'장면 1: {name} 최종 지수가 입력의 {close}와 다릅니다.')
        if len(candidates) != 1:
            raise ValidationError(f'장면 1: {name} 최종 지수 {close}를 해당 시장의 서술 안에 정확히 표시하세요.')
        clause, close_match, numbers = candidates[0]
        rates = [m for m in numbers if RATE_UNIT.match(clause[m.end():])]
        if not rates:
            closing = clause[close_match.end():]
            if rate == 0 and '보합' in closing and _directions(closing) <= {direction}:
                continue
            raise ValidationError(f'장면 1: {name} 등락률 {rate}%와 방향이 필요합니다 (0.00%는 보합 허용).')
        if len(rates) != 1 or abs(_number(rates[0][1])) != rate:
            raise ValidationError(f'장면 1: {name} 등락률을 입력의 {rate}%와 일치시키세요.')
        match = rates[0]
        signed_direction = -1 if match[1].startswith(('-', '−')) else 1 if match[1].startswith('+') else None
        # Read the rate's own predicate, excluding a later intraday transition.
        tail = RATE_UNIT.sub('', clause[match.end():], count=1)
        predicate = re.split(r',(?!\d)|[.!?](?!\d)|지만|뒤|며', tail, maxsplit=1)[0]
        directions = _directions(predicate)
        if not directions:
            # Also accept '상승률 0.67%' and explicit signed percentages.
            prefix = clause[max(0, match.start() - 6):match.start()]
            if '상승률' in prefix:
                directions = {1}
            elif '하락률' in prefix:
                directions = {-1}
            elif signed_direction is not None and rate != 0:
                directions = {signed_direction}
        if (signed_direction is not None and rate != 0 and signed_direction != direction
                or directions and directions != {direction}
                or rate != 0 and (not directions or '보합' in predicate)):
            raise ValidationError(f'장면 1: {name} 등락 방향이 입력과 다르거나 불명확합니다.')


def validate_compressed(response: str, serialized: str) -> ValidatedText:
    body = response.replace('\r\n', '\n').strip()
    lines = body.split('\n')
    if len(lines) != 5 or any(not line.strip() for line in lines):
        raise ValidationError('빈 줄 없이 정확히 5줄인 일반 텍스트 대본이 필요합니다.')
    if not lines[0].startswith(INTRO + ' ') or not lines[4].endswith(' ' + OUTRO):
        raise ValidationError('첫 줄 시작 인사와 마지막 줄 종료 인사를 본문과 같은 줄에 표시하세요.')
    if body.count(INTRO) != 1 or body.count(OUTRO) != 1:
        raise ValidationError('시작·종료 인사는 지정된 위치에 한 번씩만 허용됩니다.')
    for i, line in enumerate(lines, 1):
        content = line.removeprefix(INTRO + ' ') if i == 1 else line.removesuffix(' ' + OUTRO) if i == 5 else line
        if line != line.strip() or re.search(r'[<>`#*|\r\t]|={3,}|^\s*(?:[·•\-+]\s|\d+[.)]\s|장면\s*\d+\s*[:=])|\[[^\]]+\]', content):
            raise ValidationError(f'장면 {i}: 번호·제목·Markdown 장식과 바깥쪽 공백을 제거하세요.')
        parts = SENTENCE_END.split(content)
        if not content.endswith('.') or parts[-1] != '' or not 1 <= len(parts) - 1 <= 2 or any(not p.strip() for p in parts[:-1]):
            raise ValidationError(f'장면 {i}: 인사말을 제외한 본문은 마침표로 끝나는 1~2문장이어야 합니다.')
        if re.search(r'[!?。！？]', content):
            raise ValidationError(f'장면 {i}: 본문 문장은 마침표로 끝내세요.')
    _validate_indices(lines[0], serialized)
    chars = sum(map(len, lines))
    if chars > 650:
        raise ValidationError(f'전체 대본이 650자를 초과합니다 (현재 {chars}자, 인사말·숫자 포함).')
    warnings = []
    if not 400 <= chars <= 550:
        warnings.append(f'대본 {chars}자: 권장 분량은 400~550자입니다 (인사말·숫자 포함, 개행 제외).')
    return ValidatedText(body, chars, warnings)
