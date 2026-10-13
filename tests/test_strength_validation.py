from pathlib import Path

import pytest


FIXTURES = Path(__file__).parent / 'fixtures'


def check(response, source):
    from backend.strength_validation import find_strength_issues
    return find_strength_issues(response, source)


def test_real_october_13_response_reports_emphasis_and_spread_in_scene_two():
    source = (FIXTURES / '1013_strength_serialized.txt').read_text(encoding='utf-8')
    raw = (FIXTURES / '1013_strength_raw.txt').read_text(encoding='utf-8')
    issues = check(raw.replace('<SCENE_BREAK>', '\n'), source)
    assert any('장면 2' in issue and '크게' in issue and '강도' in issue for issue in issues)
    assert any('장면 2' in issue and '번졌습니다' in issue and '확산' in issue for issue in issues)
    assert any('리스크 오프 심화' in issue for issue in issues)


@pytest.mark.parametrize('response,source', [
    ('위험자산 회피 경향이 강해졌습니다.', '시장 리스크 오프 심화'),
    ('안전자산 선호가 커졌습니다.', '안전자산 선호 확대'),
    ('금리 우려가 커졌습니다.', '금리 우려 증가'),
    ('코스피가 크게 하락했습니다.', '코스피 하락'),
    ('삼성전자가 급격히 하락했습니다.', '삼성전자 하락'),
    ('코스피가 크게 하락했고 위험자산 회피 경향이 강해졌습니다.',
     '코스피 하락과 시장 리스크 오프 심화'),
    ('위험자산 회피 경향이 강해졌고 코스피가 크게 하락했습니다.',
     '시장 리스크 오프 심화와 코스피 하락'),
    ('공포지수가 크게 올랐고 불안감이 커졌습니다.', '공포지수 상승과 불안감 확대'),
    ('크게 하락한 코스피와 위험회피 심리가 나타났습니다.', '코스피 하락과 위험회피 심화'),
    ('급격히 상승한 환율과 안전자산 선호가 나타났습니다.', '환율 상승과 안전자산 선호 확대'),
    ('위험회피 심화 속 크게 하락한 코스피가 주목받았습니다.', '위험회피 심화와 코스피 하락'),
    ('위험회피가 심화하면서 코스피가 크게 하락했습니다.', '위험회피 심화와 코스피 하락'),
])
def test_unadorned_change_and_other_market_facts_are_not_flagged(response, source):
    assert check(response, source) == []


@pytest.mark.parametrize('response', [
    '안전자산 선호가 급격히 커졌습니다.',
    '급격히 커진 안전자산 선호가 나타났습니다.',
    '위험자산 회피 심리가 크게 강해졌습니다.',
    '크게 강화된 위험자산 회피 심리가 나타났습니다.',
    '금리 우려가 극도로 커졌습니다.',
    '투자심리가 상당히 위축됐습니다.',
    '시장심리가 매우 악화됐습니다.',
    '불안감이 대폭 커졌습니다.',
    '달러 선호가 큰 폭으로 증가했습니다.',
    '채권 선호가 급격히 증가했습니다.',
    '위험선호가 크게 증가했습니다.',
])
def test_extra_emphasis_is_flagged_without_explicit_support(response):
    issues = check(response, '시장 리스크 오프 심화\n안전자산 선호 확대\n금리 우려 증가')
    assert issues and any('강도' in issue for issue in issues)


@pytest.mark.parametrize('response,source', [
    ('위험자산 회피 심리가 크게 강해졌습니다.', '리스크 오프 큰 폭 심화'),
    ('위험회피가 크게 강화됐습니다.', '위험자산 회피 대폭 강화'),
    ('안전 자산 선호가 대폭 증가했습니다.', '안전자산 선호 큰 폭 확대'),
    ('금리 우려가 상당히 커졌습니다.', '금리 우려 상당한 증가'),
    ('투자심리가 매우 악화됐습니다.', '투자심리 매우 악화'),
    ('달러 선호가 급격히 증가했습니다.', '달러 선호 급격한 증가'),
    ('불안감이 극도로 커졌습니다.', '불안감 극심한 증가'),
    ('크게 강화된 위험회피 심리가 나타났습니다.', '위험자산 회피 대폭 강화'),
])
def test_only_declared_same_target_equivalents_support_emphasis(response, source):
    assert check(response, source) == []


@pytest.mark.parametrize('source', [
    '<1>지수 큰 폭 하락\n· 시장 리스크 오프 심화',
    '<1>시장\n· 시장 리스크 오프 심화\n· 공포지수 대폭 상승',
    '<1>시장\n===\n<2>흐름\n지수 큰 폭 하락\n공포지수 대폭 상승\n위험회피\n심화',
    '코스피가 크게 하락했고 위험회피 심리가 강해졌다',
    '금리 우려 대폭 증가',
    '안전자산 선호 대폭 확대',
    '위험선호 대폭 확대',
    '위험회피 심화로 주가가 크게 하락',
    '위험회피 심화 속 크게 하락한 코스피',
])
def test_other_facts_never_supply_risk_aversion_emphasis(source):
    assert check('위험회피 심리가 크게 강해졌습니다.', source)


@pytest.mark.parametrize('word', ['번졌습니다', '번집니다', '번지며', '번져 나갔습니다',
                                '퍼졌습니다', '퍼지며', '퍼져 나갔습니다', '확산됐습니다', '확산되며'])
def test_spread_forms_require_spread_evidence(word):
    issues = check(f'위험회피 심리가 {word}.', '시장 리스크 오프 심화')
    assert any(word in issue and '확산' in issue for issue in issues)


def test_spread_before_target_is_detected():
    assert check('퍼진 금리 우려가 나타났습니다.', '금리 우려 증가')


@pytest.mark.parametrize('source', ['위험회피 심리 확산', '위험회피 심리 번짐', '위험회피 심리 퍼짐'])
def test_spread_evidence_does_not_support_added_magnitude(source):
    assert check('위험회피 심리가 번졌습니다.', source) == []
    issues = check('위험회피 심리가 크게 번졌습니다.', source)
    assert len(issues) == 1 and '강도' in issues[0]


@pytest.mark.parametrize('response,source', [
    ('금리 우려가 크게 커졌습니다.', '공급망 우려 대폭 확대'),
    ('외국인의 위험회피가 크게 강해졌습니다.', '기관의 위험회피 대폭 확대'),
    ('위험회피가 크게 강해졌습니다.', '기관의 위험회피 대폭 확대'),
    ('달러 선호가 크게 증가했습니다.', '채권 선호 대폭 확대'),
    ('불안감이 크게 증가했습니다.', '금리 불안감 대폭 확대'),
    ('위험회피가 크게 강해졌습니다.', '위험회피 대폭 확대되지 않았다'),
    ('위험회피가 크게 강해졌습니다.', '위험회피 대폭 확대 가능성'),
    ('위험회피가 크게 강해졌습니다.', '위험회피 대폭 확대 전망'),
    ('위험회피가 크게 강해졌습니다.', '위험회피 대폭 확대 관측'),
    ('위험회피가 크게 번졌습니다.', '위험회피 확산되지 않았다'),
    ('위험회피가 크게 강해졌습니다.', '<1>시장\n· 위험회피 대폭 확대\n· 위험회피 심화'),
    ('위험회피가 급격히 증가했습니다.', '위험회피 대폭 증가'),
    ('위험회피가 크게 강해졌습니다.', '위험회피 대폭 완화'),
    ('안전자산 선호가 크게 증가했습니다.', '안전자산 선호 큰 폭 감소'),
    ('불안감이 크게 줄었습니다.', '불안감 큰 폭 증가'),
    ('위험회피가 크게 강해졌습니다.', '위험회피 심리가 강해져 코스피가 크게 하락했다'),
    ('금리 우려가 크게 커졌습니다.', '금리 우려가 커지자 채권 가격이 크게 하락했다'),
    ('위험회피가 외국인 사이에서 크게 강화됐습니다.', '위험회피가 기관 사이에서 대폭 강화됐다'),
    ('우려가 금리 인상에 대해 크게 커졌습니다.', '우려가 공급망에 대해 크게 커졌다'),
])
def test_different_qualifiers_modalities_or_ambiguous_facts_require_review(response, source):
    assert check(response, source)


def test_paired_source_heading_and_detail_can_supply_same_target_evidence():
    source = '<1>시장\n===\n<2>외환\n안전자산 선호\n대폭 확대'
    assert check('안전자산 선호가 크게 증가했습니다.', source) == []


def test_summary_without_bullet_is_evidence_before_index_cards():
    source = ('<1>시장\n위험회피 대폭 확대\n\n코스피\n6035.35\n-348.89 (-5.46%)\n'
              '코스닥\n837.03\n-12.16 (-1.43%)')
    assert check('위험회피가 크게 강해졌습니다.', source) == []


def test_same_scene_number_is_not_required_and_numeric_decimals_do_not_split_facts():
    source = '<1>시장\n· 금리 우려 대폭 확대\n===\n<2>지수\n코스피\n6035.35'
    response = '코스피는 6035.35입니다.\n금리 우려가 크게 커졌습니다.'
    assert check(response, source) == []


def test_multiple_same_topic_events_in_one_clause_cannot_lend_their_emphasis():
    source = '기관의 위험회피 대폭 확대와 개인의 위험회피 심화'
    assert check('개인의 위험회피가 크게 강해졌습니다.', source)


def test_source_negation_attached_to_another_fact_does_not_supply_positive_evidence():
    source = '위험회피 심화와 공포지수 대폭 상승은 없었다'
    assert check('위험회피가 크게 강해졌습니다.', source)


@pytest.mark.parametrize('connective', ['심화하며', '심화하면서'])
def test_same_subject_is_kept_in_immediate_following_clause(connective):
    issues = check(f'위험회피가 {connective} 크게 번졌습니다.', '위험회피 심화')
    assert any('크게' in issue and '강도' in issue for issue in issues)
    assert any('번졌습니다' in issue and '확산' in issue for issue in issues)


@pytest.mark.parametrize('response,source', [
    ('위험회피가 외국인 사이에서 크게 강화됐습니다.', '위험회피가 외국인 사이에서 대폭 강화됐다'),
    ('우려가 금리 인상에 대해 크게 커졌습니다.', '우려가 금리 인상에 대해 대폭 커졌다'),
    ('위험회피 심리가 번져 있습니다.', '위험회피 심리 확산'),
    ('금리 우려가 퍼져 있습니다.', '금리 우려 확산'),
])
def test_matching_suffix_qualifiers_and_contracted_spread_can_be_supported(response, source):
    assert check(response, source) == []
