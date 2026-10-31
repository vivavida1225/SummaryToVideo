import asyncio
import json
from pathlib import Path

import pytest

from backend.compression import Compressor, ResponseValidationError
from backend.config import Settings
from backend.jobs import JobManager
from backend.narration import decode_response, validate_response
from backend.transcribe_numbers import transcribe_numeric_tokens
from backend.validation import ValidationError, count_characters
from tests.test_compression import Transport


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures'


@pytest.fixture
def strength_draft(compressed):
    return compressed.replace(
        '중동 긴장으로 국제유가가 다시 100달러대로 오르면서 정유와 해운주에 자금이 몰렸습니다.',
        '원·달러 환율 상승과 함께 위험자산 회피 심리가 크게 번졌고 채권과 달러 선호가 나타났습니다.')


def test_strength_review_is_combined_with_format_and_length_issues(serialized, strength_draft):
    draft = strength_draft + '\n추가 장면입니다. ' + '원문의 근거를 확인해야 합니다. ' * 4
    with pytest.raises(ValidationError) as caught:
        validate_response(draft, serialized + '\n시장 리스크 오프 심화')
    assert caught.value.requires_review
    assert any('강도' in issue for issue in caught.value.issues)
    assert any('확산' in issue for issue in caught.value.issues)
    assert any('5줄' in issue for issue in caught.value.issues)
    assert any('490~550자' in issue for issue in caught.value.issues)
    assert caught.value.text == draft


def test_real_response_keeps_numeric_review_and_strength_diagnostics():
    source = (FIXTURES / '1013_strength_serialized.txt').read_text(encoding='utf-8')
    raw = (FIXTURES / '1013_strength_raw.txt').read_text(encoding='utf-8')
    with pytest.raises(ValidationError) as caught:
        validate_response(raw, source, transcribe_numbers=True)
    assert caught.value.requires_review
    assert any('코스닥' in issue for issue in caught.value.issues)
    assert any('크게' in issue and '강도' in issue for issue in caught.value.issues)
    assert any('번졌습니다' in issue and '확산' in issue for issue in caught.value.issues)
    assert caught.value.text == decode_response(raw)[0]


@pytest.mark.parametrize('transcribe_numbers', [False, True])
def test_strength_candidate_stops_after_one_call_and_preserves_raw(
        serialized, strength_draft, transcribe_numbers):
    assert 490 <= count_characters(strength_draft) <= 550
    transport = Transport([strength_draft])
    compressor = Compressor(ROOT / 'prompts/compress_to_1min.txt', [(1, 'fake')],
                            transport=transport, transcribe_numbers=transcribe_numbers)
    raw_saved, invalid_saved = [], []
    with pytest.raises(ResponseValidationError) as caught:
        asyncio.run(compressor.run(
            serialized + '\n시장 리스크 오프 심화', lambda *args, **kwargs: None,
            lambda attempt, raw: invalid_saved.append(raw),
            save_raw=lambda attempt, raw: raw_saved.append(raw)))
    assert len(transport.calls) == 1
    assert transport.calls[0]['feedback'] == ''
    assert '다른 사실의 급락' in transport.calls[0]['prompt']
    assert '시장 리스크 오프가 심화됐습니다.' in transport.calls[0]['prompt']
    assert raw_saved == invalid_saved == [strength_draft]
    assert caught.value.requires_review
    assert any('강도' in issue for issue in caught.value.validation_issues)


@pytest.mark.parametrize('transcribe_numbers', [False, True])
def test_review_persistence_and_explicit_retry_use_original_source(
        tmp_path, serialized, strength_draft, transcribe_numbers):
    async def scenario():
        settings = Settings(tmp_path, model='gemini-3.8-flash')
        settings.prompt_path.parent.mkdir(parents=True)
        settings.prompt_path.write_text(
            (ROOT / 'prompts/compress_to_1min.txt').read_text(encoding='utf-8'), encoding='utf-8')
        (tmp_path / '.env').write_text('GEMINI_API_KEY_1=fake', encoding='utf-8')
        source = serialized + '\n시장 리스크 오프 심화'
        expected_draft = transcribe_numeric_tokens(strength_draft) if transcribe_numbers else strength_draft
        transport = Transport([strength_draft, strength_draft])
        manager = JobManager(settings, compressor_factory=lambda **options: Compressor(
            settings.prompt_path, transport=transport, **options))
        first = manager.start(snapshot=source, transcribe_numbers=transcribe_numbers)
        await manager.wait(first['id'])
        job = manager.get(first['id'])
        assert job['state'] == 'needs_review'
        assert job['compressed'] == expected_draft
        assert len(transport.calls) == 1
        folder = tmp_path / job['output_dir']
        for name in ('raw_response_1.txt', 'invalid_response_1.txt'):
            assert (folder / name).read_text(encoding='utf-8') == strength_draft
        assert (folder / 'compressed.txt').read_text(encoding='utf-8') == expected_draft
        metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
        assert metadata['state'] == 'needs_review'
        assert metadata['validation_issues'] == job['validation_issues']
        restored = JobManager(settings)
        assert restored.get(first['id'])['validation_issues'] == job['validation_issues']
        assert restored.get(first['id'])['compressed'] == expected_draft
        second = manager.retry(first['id'])
        await manager.wait(second['id'])
        assert len(transport.calls) == 2
        assert transport.calls[1]['source'] == source
        assert transport.calls[1]['previous_response'] == expected_draft
        assert '다른 사실의 급락' in transport.calls[1]['feedback']
        for instructions in (transport.calls[0]['prompt'], transport.calls[1]['prompt'],
                             transport.calls[1]['feedback']):
            assert '[원문 표현 보존]' in instructions
            assert '최초 입력 원문의 표현' in instructions
            assert '우려가 심화했습니다.' in instructions
        assert manager.get(second['id'])['transcribe_numbers'] == transcribe_numbers
        assert manager.get(second['id'])['state'] == 'needs_review'

    asyncio.run(scenario())
