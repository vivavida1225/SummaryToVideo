"""Validate narration shape and quoted indices without rewriting the script.

These deterministic checks are not a general Korean fact/causality checker.
"""

import re
from dataclasses import dataclass
from decimal import Decimal

from .transcribe_numbers import (NumberTranscriptionError, is_preserved_numeric_label, number_to_korean,
                                 transcribe_numeric_tokens)


INTRO = '오늘의 AI 시황입니다.'
OUTRO = '오늘의 AI 시황이었습니다.'
MIN_CHARS = 490
MAX_CHARS = 550
TARGET_CHARS = 500
NUMBER = r'[+\-−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?'
NUMBER_TOKEN = re.compile(rf'(?<![\d.,+\-−])({NUMBER})(?!\d|[.,]\d)')
RATE_UNIT = re.compile(r'^\s*(?:%|퍼센트)')
# A dot between two digits belongs to a decimal, not a sentence boundary.
SENTENCE_END = re.compile(r'(?<!\d)\.|\.(?!\d)')
PROHIBITED_EXPRESSION = re.compile(r'치솟[가-힣]*')
UP = re.compile(r'상승|급등|폭등|반등|오른|올랐|올라|오르|오름|강세')
DOWN = re.compile(r'하락|급락|폭락|내린|내렸|내려|내리|내림|약세|떨어|떨었')
FORWARD_LOOKING_CLOSE = re.compile(
    r'다음\s*(?:장|거래일)|향후|앞으로|이후\s*시장|관전\s*포인트|'
    r'(?:확인|주목)(?:해야|해\s*볼|할\s*(?:필요|변수|대상|부분|지점))'
)


def count_characters(text: str) -> int:
    return len(text.replace('\r', '').replace('\n', ''))


class ValidationError(ValueError):
    def __init__(self, issues: str | list[str], *, body_char_count: int | None = None,
                 requires_review: bool = False, text: str | None = None):
        self.issues = [issues] if isinstance(issues, str) else issues
        self.body_char_count = body_char_count
        self.excess_char_count = max(0, body_char_count - MAX_CHARS) if body_char_count is not None else 0
        self.requires_review = requires_review
        self.text = text
        super().__init__('\n'.join(self.issues))


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


def _validate_indices(first_line: str, serialized: str) -> list[str]:
    issues = []
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
                issues.append(f'장면 1: {name} 최종 지수가 입력의 {close}와 다릅니다.')
                candidates.append((clause, quoted, numbers))
        if len(candidates) != 1:
            issues.append(f'장면 1: {name} 최종 지수 {close}를 해당 시장의 서술 안에 정확히 표시하세요.')
            continue
        clause, close_match, numbers = candidates[0]
        rates = [m for m in numbers if RATE_UNIT.match(clause[m.end():])]
        if not rates:
            closing = clause[close_match.end():]
            if rate == 0 and '보합' in closing and _directions(closing) <= {direction}:
                continue
            issues.append(f'장면 1: {name} 등락률 {rate}%와 방향이 필요합니다 (0.00%는 보합 허용).')
            continue
        if len(rates) != 1 or abs(_number(rates[0][1])) != rate:
            issues.append(f'장면 1: {name} 등락률을 입력의 {rate}%와 일치시키세요.')
            if len(rates) != 1:
                continue
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
            issues.append(f'장면 1: {name} 등락 방향이 입력과 다르거나 불명확합니다.')
    return issues


_SPOKEN_NUMBER = re.compile(
    r'(?:마이너스 |플러스 )?[영일이삼사오육칠팔구십백천만억조]+(?: 점 [영일이삼사오육칠팔구십백천억조]+)?'
)
_NON_INDEX_MEASURE = re.compile(
    r'^\s*(?:조원|억원|천원|만원|원|달러|계약|주식|주|명|개|회|건|곳|대|년|월|일|분기|분|초|배)'
)
_SPOKEN_NUMBER_SUFFIXES = (
    '으로', '로', '은', '는', '이', '가', '을', '를', '의', '와', '과', '에', '에서', '까지', '보다',
    '퍼센트', '포인트', '달러', '원', '년', '월', '일', '주', '분기', '분', '초', '명', '개', '회', '건',
    '배', '곳', '대', '선',
)


def _market_clauses(first_line: str):
    names = list(re.finditer('코스피|코스닥', first_line))
    return [(match[0], first_line[match.end():names[index + 1].start() if index + 1 < len(names) else len(first_line)],
             match.end()) for index, match in enumerate(names)]


def _close_candidates(clause: str):
    candidates = []
    for pattern in (NUMBER_TOKEN, _SPOKEN_NUMBER):
        for match in pattern.finditer(clause):
            after = clause[match.end():]
            before = clause[:match.start()]
            if is_preserved_numeric_label(clause, match.start(), match.end()):
                continue
            if pattern is _SPOKEN_NUMBER:
                if before and (before[-1].isalnum() or '\uac00' <= before[-1] <= '\ud7a3'):
                    continue
                if after and ('\uac00' <= after[0] <= '\ud7a3') and not any(
                        clause.startswith(suffix, match.end()) for suffix in _SPOKEN_NUMBER_SUFFIXES):
                    continue
            if pattern is NUMBER_TOKEN and match[1].startswith(('+', '-', '−')):
                continue
            if pattern is _SPOKEN_NUMBER and match[0].startswith(('마이너스 ', '플러스 ')):
                continue
            if RATE_UNIT.match(after) or after.startswith(('퍼센트', '%')):
                continue
            if re.match(r'\s*(?:포인트\s*)?(?:상승|하락|오른|내린)', after):
                continue
            # A number directly marked as a rate is not an index level.
            if before.endswith(('상승률', '하락률')):
                continue
            candidates.append(match)
    return sorted(candidates, key=lambda match: match.start())


def _matches_index_value(match: re.Match, close: str) -> bool:
    if match.re == _SPOKEN_NUMBER:
        return match[0] == number_to_korean(close.replace(',', ''))
    return _number(match[0]) == _number(close)


def _correct_transcribed_closes(response: str, serialized: str) -> str:
    first_line = response.replace('\r\n', '\n').split('\n', 1)[0]
    expected = index_triples(serialized)
    clauses = _market_clauses(first_line)
    replacements = []
    for offset in (0, 3):
        name, close = expected[offset], expected[offset + 1]
        market_clauses = [(clause, start) for market, clause, start in clauses if market == name]
        closes = []
        for clause, start in market_clauses:
            endings = list(re.finditer(r'마감', clause)) or list(re.finditer(r'기록', clause))
            if not endings:
                candidates = _close_candidates(clause)
                if not candidates:
                    continue
                exact = [match for match in candidates if _matches_index_value(match, close)]
                if len(candidates) != 1 or len(exact) != 1:
                    raise NumberTranscriptionError(f'1장면에서 {name} 최종 종가 위치를 특정할 수 없습니다.')
                closes.append((exact[0], start))
                continue
            marker = endings[-1].start()
            if any(_NON_INDEX_MEASURE.match(clause[match.end():])
                   for match in NUMBER_TOKEN.finditer(clause[:marker])):
                raise NumberTranscriptionError(f'1장면에서 {name} 종가와 다른 수치의 경계를 확인할 수 없습니다.')
            candidates = [match for match in _close_candidates(clause) if match.end() <= marker]
            if candidates:
                marker_text = clause[endings[-1].start():endings[-1].end()]
                link_pattern = (r'\s*(?:(?:을|를|이|가|로|으로)\s*)?'
                                if marker_text == '기록' else
                                r'\s*(?:(?:을|를|이|가|로|으로)\s*)?(?:(?:보합|상승|하락|강세|약세)\s*)?')
                linked = [match for match in candidates
                          if re.fullmatch(link_pattern, clause[match.end():marker])]
                exact = [match for match in candidates if _matches_index_value(match, close)]
                if len(linked) == 1:
                    closes.append((linked[0], start))
                elif not linked and len(candidates) == 1 and len(exact) == 1:
                    closes.append((exact[0], start))
                else:
                    raise NumberTranscriptionError(f'1장면에서 {name} 최종 종가 위치를 특정할 수 없습니다.')
        if len(closes) != 1:
            raise NumberTranscriptionError(f'1장면에서 {name} 최종 종가 위치를 하나로 특정할 수 없습니다.')
        match, start = closes[0]
        raw = match[0]
        if raw != number_to_korean(close.replace(',', '')):
            replacements.append((start + match.start(), start + match.end(), close))

    result = response
    for start, end, close in sorted(replacements, reverse=True):
        result = result[:start] + close + result[end:]
    return result


def _validate_transcribed_indices(first_line: str, serialized: str) -> list[str]:
    issues = []
    expected = index_triples(serialized)
    clauses = _market_clauses(first_line)
    for offset in (0, 3):
        name, close, change = expected[offset:offset + 3]
        delta, percent = re.fullmatch(rf'({NUMBER})\s*\(({NUMBER})%\)', change).groups()
        clause_matches = [clause for market, clause, _ in clauses if market == name]
        close_reading = number_to_korean(close.replace(',', ''))
        if sum(clause.count(close_reading) for clause in clause_matches) != 1:
            issues.append(f'1장면: {name} 최종 종가 독음이 입력값 {close}와 일치하지 않습니다.')
            continue
        rate_reading = number_to_korean(percent)
        rate_matches = [clause for clause in clause_matches
                        if re.search(re.escape(rate_reading) + r'(?:%|퍼센트)', clause)]
        observed_rates = [match for clause in clause_matches
                          for match in re.finditer(_SPOKEN_NUMBER.pattern + r'(?:%|퍼센트)', clause)]
        rate_value = abs(_number(percent))
        delta_value = _number(delta)
        direction = 1 if delta_value > 0 else -1 if delta_value < 0 else 0
        if not rate_matches:
            has_rate_marker = any('%' in clause or '퍼센트' in clause for clause in clause_matches)
            close_tails = [clause[clause.find(close_reading) + len(close_reading):]
                           for clause in clause_matches if close_reading in clause]
            neutral_close = any('보합' in tail and not _directions(tail) for tail in close_tails)
            if rate_value == 0 and not has_rate_marker and neutral_close:
                continue
            issues.append(f'1장면: {name} 등락률 독음 {percent}%와 방향을 입력값에 맞춰야 합니다.')
            continue
        if len(rate_matches) != 1 or len(observed_rates) != 1:
            issues.append(f'1장면: {name} 등락률이 입력값에 맞게 한 번만 나와야 합니다.')
            continue
        clause = rate_matches[0]
        rate_match = re.search(re.escape(rate_reading) + r'(?:%|퍼센트)', clause)
        tail = clause[rate_match.end():]
        predicate = re.split(r',(?!\d)|[.!?](?!\d)|지만|며', tail, maxsplit=1)[0]
        directions = _directions(predicate)
        signed_direction = -1 if percent.startswith(('-', '−')) else 1 if percent.startswith('+') else None
        if (signed_direction is not None and rate_value != 0 and signed_direction != direction
                or directions and directions != {direction}
                or rate_value != 0 and (not directions or '보합' in predicate)):
            issues.append(f'1장면: {name} 등락 방향이 원문과 다르거나 불명확합니다.')
    return issues


def validate_compressed(response: str, serialized: str, *, transcribe_numbers: bool = False) -> ValidatedText:
    index_triples(serialized)  # Invalid source cannot be repaired by rewriting the response.
    if transcribe_numbers:
        try:
            response = transcribe_numeric_tokens(_correct_transcribed_closes(response, serialized))
        except NumberTranscriptionError as exc:
            raise ValidationError(str(exc), requires_review=True) from None
    issues = []
    body = response.replace('\r\n', '\n').strip('\n')
    lines = body.split('\n')
    if len(lines) != 5 or any(not line.strip() for line in lines):
        issues.append('빈 줄 없이 정확히 5줄인 일반 텍스트 대본이 필요합니다.')
    if not lines[0].startswith(INTRO + ' ') or not lines[-1].endswith(' ' + OUTRO):
        issues.append('첫 줄 시작 인사와 마지막 줄 종료 인사를 본문과 같은 줄에 표시하세요.')
    if body.count(INTRO) != 1 or body.count(OUTRO) != 1:
        issues.append('시작·종료 인사는 지정된 위치에 한 번씩만 허용됩니다.')
    if PROHIBITED_EXPRESSION.search(body):
        issues.append('대본 전체에서 금지 표현 “치솟다”와 그 활용형을 사용하지 마세요.')
    if FORWARD_LOOKING_CLOSE.search(lines[-1].replace(OUTRO, '')):
        issues.append('장면 5: 미래 전망이나 관전·확인 권고를 쓰지 말고, 원문의 미사용 보완 사실을 작성하세요.')
    for i, line in enumerate(lines, 1):
        # Strip only fixed greetings; never count decimal dots as sentences.
        content = line.replace(INTRO, '').replace(OUTRO, '').strip()
        if line != line.strip() or re.search(r'[<>`#*|\r\t]|={3,}|^\s*(?:[·•\-+]\s|\d+[.)]\s|장면\s*\d+\s*[:=])|\[[^\]]+\]', content):
            issues.append(f'장면 {i}: 번호·제목·Markdown 장식과 바깥쪽 공백을 제거하세요.')
        parts = SENTENCE_END.split(content)
        if not content.endswith('.') or parts[-1] != '' or not 1 <= len(parts) - 1 <= 2 or any(not p.strip() for p in parts[:-1]):
            issues.append(f'장면 {i}: 인사말을 제외한 본문은 마침표로 끝나는 1~2문장이어야 합니다.')
        if re.search(r'[!?。！？]', content):
            issues.append(f'장면 {i}: 본문 문장은 마침표로 끝내세요.')
    issues.extend(_validate_transcribed_indices(lines[0], serialized) if transcribe_numbers
                  else _validate_indices(lines[0], serialized))
    chars = count_characters(body)
    if chars < MIN_CHARS:
        issues.append(f'전체 대본은 {MIN_CHARS}~{MAX_CHARS}자여야 합니다. 현재 {chars}자로 '
                              f'{MIN_CHARS - chars}자 부족합니다. 원문의 근거와 시장 영향을 보충하세요 '
                              '(인사말·숫자·공백 포함, 개행 제외).')
    if chars > MAX_CHARS:
        issues.append(f'전체 대본은 {MIN_CHARS}~{MAX_CHARS}자여야 합니다. 현재 {chars}자로 '
                              f'{chars - MAX_CHARS}자 초과합니다. 중복과 세부 정보를 줄이세요 '
                              '(인사말·숫자·공백 포함, 개행 제외).')
    if issues:
        raise ValidationError(issues, body_char_count=chars, text=body)
    return ValidatedText(body, chars, [])
