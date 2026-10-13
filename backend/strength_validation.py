"""Conservative surface checks for sentiment emphasis, not a semantic fact checker.

Only declared subjects, modifiers and aliases are understood. Unambiguous local
source evidence can permit a phrase; uncertainty leaves a review diagnostic.
"""

import re
from dataclasses import dataclass


_TARGET = re.compile(
    r'리스크\s*오프|위험\s*(?:자산\s*)?회피|안전\s*자산\s*선호|'
    r'달러\s*선호|채권\s*선호|위험\s*(?:자산\s*)?선호|'
    r'투자\s*심리|시장\s*심리|불안감|불안|우려'
)
_EXPRESSIONS = {
    'magnitude': r'크게|큰\s*폭(?:으로)?|대폭',
    'considerable': r'상당히|상당한',
    'very': r'매우',
    'speed': r'급격히|급격한|급격하게',
    'extreme': r'극도로|극심한|극심하게',
    'spread': (r'(?:번져|퍼져)[가-힣]*(?:\s+(?:나갔|나가|있)[가-힣]*)?|'
               r'번졌[가-힣]*|번지[가-힣]*|번집[가-힣]*|번진[가-힣]*|번짐|'
               r'퍼졌[가-힣]*|퍼지[가-힣]*|퍼집[가-힣]*|퍼진[가-힣]*|퍼짐|'
               r'확산(?:됐[가-힣]*|되[가-힣]*|된[가-힣]*|됩[가-힣]*|됨)?'),
}
_EXPRESSION = re.compile('|'.join(f'(?P<{kind}>{pattern})' for kind, pattern in _EXPRESSIONS.items()))
# Keep the connective in the preceding clause: it may itself be a spread form.
_BOUNDARY = re.compile(
    r'(?<!\d)\.|\.(?!\d)|[!?。！？;,\n]|'
    r'[가-힣]+(?:했고|했으며|됐고|졌고|었고|았고|랐고|렸고|하고|지고|하며|하면서|면서|지만|'
    r'오르며|내리며|늘며|커지며|번지며|퍼지며|되며)|반면|한편'
)
_NEGATIVE = re.compile(r'않|못|아니|없')
_TENTATIVE = re.compile(r'가능성|전망|예상|관측|잠정|수\s*(?:있|도)')
_MORE = re.compile(
    r'심화|강화|강해|커졌|커지|커진|커집|증가|확대|늘어|늘었|악화|위축|확산|'
    r'번지|번졌|번집|번진|번짐|번져|퍼지|퍼졌|퍼집|퍼진|퍼짐|퍼져'
)
_LESS = re.compile(r'완화|약화|약해|감소|축소|줄어|줄었|작아|개선|호전|진정')
_DESCRIPTOR = re.compile(r'^\s*(?:심리|경향|성향|기조)?\s*(?:이|가|은|는|도|을|를)?\s*')
_OTHER_SUBJECT = re.compile(r'[가-힣A-Za-z0-9·/-]+(?:이|가|은|는|을|를)(?:\s|$)')
_POST_QUALIFIER = re.compile(
    r'(?:외국인|기관|개인|투자자)(?:들)?(?:\s*사이(?:에서|의)|\s*중심(?:으로|의)|\s*에서)?|'
    r'[가-힣A-Za-z0-9·/-]+(?:\s+[가-힣A-Za-z0-9·/-]+){0,3}\s*(?:에\s*대해|에\s*대한)'
)
_MARKET_ATTRIBUTIVE = re.compile(r'^\s*(?:하락한|상승한|급락한|급등한|반등한|떨어진|오른|내린)\s*')
_ATTRIBUTIVE = re.compile(
    r'강화된|강해진|커진|증가한|늘어난|확대된|악화된|위축된|확산된|번진|퍼진'
)


@dataclass(frozen=True)
class _Subject:
    kind: str
    qualifier: str
    negative: bool
    tentative: bool
    changes: frozenset[str]
    start: int
    end: int

    @property
    def key(self):
        return self.kind, self.qualifier, self.negative, self.tentative, self.changes


@dataclass(frozen=True)
class _Fact:
    text: str
    subject: _Subject
    expressions: frozenset[str]


def _clauses(text: str):
    start = 0
    for match in _BOUNDARY.finditer(text):
        if (re.fullmatch(r'[가-힣]+', match.group())
                and match.group() not in {'반면', '한편'}
                and _EXPRESSION.match(text[match.end():].lstrip())):
            # An immediately following modifier with no new subject belongs to
            # the same clause: "위험회피가 심화하면서 크게 번졌다".
            continue
        yield text[start:match.end()].strip()
        start = match.end()
    if text[start:].strip():
        yield text[start:].strip()


def _subject_kind(text: str) -> str:
    compact = re.sub(r'\s+', '', text)
    if compact in {'리스크오프', '위험회피', '위험자산회피'}:
        return 'risk_aversion'
    if compact in {'위험선호', '위험자산선호'}:
        return 'risk_preference'
    return compact


def _subjects(clause: str) -> list[_Subject]:
    subjects = []
    for match in _TARGET.finditer(clause):
        kind = _subject_kind(match.group())
        prefix = clause[:match.start()]
        # Preserve actors and concern topics; strip only known front modifiers.
        prefix = _EXPRESSION.sub('', prefix)
        prefix = _ATTRIBUTIVE.sub('', prefix)
        prefix = re.sub(r'^[·•\s]+|[.,;\s]+$', '', prefix)
        qualifier = re.sub(r'\s+', '', prefix)
        if kind == 'risk_aversion' and qualifier == '시장':
            qualifier = ''  # "시장 리스크 오프" is the declared source alias.
        suffix = _DESCRIPTOR.sub('', clause[match.end():], count=1)
        first_expression = _EXPRESSION.search(suffix)
        qualifier_area = suffix[:first_expression.start()] if first_expression else suffix
        post_qualifiers = tuple(re.sub(r'\s+', '', part.group())
                                for part in _POST_QUALIFIER.finditer(qualifier_area))
        if post_qualifiers:
            qualifier += '|' + '|'.join(post_qualifiers)
        changes = frozenset(name for name, pattern in (('more', _MORE), ('less', _LESS))
                            if pattern.search(clause))
        subjects.append(_Subject(kind, qualifier, bool(_NEGATIVE.search(clause)),
                                 bool(_TENTATIVE.search(clause)), changes, match.start(), match.end()))
    return subjects


def _attached(clause: str, subject: _Subject, expression: re.Match) -> bool:
    tail = clause[expression.end():]
    other_owner = _MARKET_ATTRIBUTIVE.match(tail)
    if other_owner and not _TARGET.match(tail[other_owner.end():]):
        return False  # "위험회피 심화 속 크게 하락한 코스피" belongs to the index.
    if expression.start() >= subject.end:
        bridge = clause[subject.end:expression.start()]
        bridge = _DESCRIPTOR.sub('', bridge, count=1)
    elif expression.end() <= subject.start:
        bridge = clause[expression.end():subject.start]
        if re.search(r'(?:하락한|상승한|급락한|급등한|반등한|떨어진|오른|내린)\s*\S+(?:와|과|및)', bridge):
            return False  # "크게 하락한 코스피와 위험회피" has a different owner.
        # Front modifiers must lead to an attributive description of the target.
        bridge = _ATTRIBUTIVE.sub('', bridge)
    else:
        return False
    if _TARGET.search(bridge) or _OTHER_SUBJECT.search(bridge):
        return False
    # A long or verbal bridge cannot establish syntactic ownership reliably.
    if len(re.sub(r'\s+', '', bridge)) > 32:
        return False
    if re.search(r'습니다|했다|한다|됐다|된다|심화로|확대로|증가로|따라|때문', bridge):
        return False
    return True


def _source_units(serialized: str):
    """Keep scene titles/summary bullets separate; join only key/detail pairs."""
    for scene in re.split(r'\n===\n', serialized.replace('\r\n', '\n')):
        lines = [line.strip() for line in scene.splitlines() if line.strip()]
        if not lines:
            continue
        header = re.match(r'^<(\d+)>(.*)', lines[0])
        if not header:
            yield from lines
            continue
        yield header[2]
        if header[1] == '1':
            # Serializer preserves summary text without requiring a bullet.
            # Index cards follow the summaries and are not sentiment evidence.
            for line in lines[1:]:
                if line in {'코스피', '코스닥'}:
                    break
                yield line
        else:
            for i in range(1, len(lines), 2):
                yield ' '.join(lines[i:i + 2])


def _source_facts(serialized: str) -> list[_Fact]:
    facts = []
    for unit in _source_units(serialized):
        for clause in _clauses(unit):
            expressions = list(_EXPRESSION.finditer(clause))
            for subject in _subjects(clause):
                kinds = frozenset(expression.lastgroup for expression in expressions
                                  if _attached(clause, subject, expression))
                fact = _Fact(clause, subject, kinds)
                # Repeated identical evidence does not create an ambiguity.
                if fact not in facts:
                    facts.append(fact)
    return facts


def find_strength_issues(response: str, serialized: str) -> list[str]:
    """Return review reasons for locally attached unsupported emphasis/spread."""
    facts = _source_facts(serialized)
    issues = []
    for scene_number, line in enumerate(response.replace('\r\n', '\n').splitlines(), 1):
        for clause in _clauses(line):
            subjects = _subjects(clause)
            for expression in _EXPRESSION.finditer(clause):
                owners = [subject for subject in subjects if _attached(clause, subject, expression)]
                for subject in owners:
                    matching = [fact for fact in facts if fact.subject.key == subject.key]
                    supported = (len(owners) == 1 and len(matching) == 1
                                 and len(subject.changes) == 1
                                 and expression.lastgroup in matching[0].expressions)
                    if supported:
                        continue
                    label = '확산' if expression.lastgroup == 'spread' else '강도'
                    reason = (f"장면 {scene_number}: ‘{expression.group()}’에 대응하는 원문 "
                              f'{label} 근거를 확인할 수 없습니다.')
                    related = matching or [fact for fact in facts if fact.subject.kind == subject.kind]
                    if related:
                        reference = '대응 원문' if matching else '관련 원문 후보(대응 조건 불일치)'
                        reason += f" {reference}: ‘{related[0].text}’."
                    if len(matching) > 1 or len(owners) > 1:
                        reason += ' 여러 사실 중 대응 관계가 불분명합니다.'
                    if reason not in issues:
                        issues.append(reason)
    return issues
