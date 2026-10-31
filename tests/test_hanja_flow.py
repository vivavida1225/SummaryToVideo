import asyncio
import json
from pathlib import Path

import pytest

from backend.compression import Compressor, ResponseValidationError
from backend.config import Settings
from backend.jobs import JobManager
from backend.transcribe_numbers import transcribe_numeric_tokens
from tests.test_compression import Transport, execute


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('transcribe_numbers', [False, True])
def test_hanja_stops_without_automatic_repair_and_keeps_raw(
        compressed, serialized, transcribe_numbers):
    raw = compressed.replace('중동 긴장', '中동 긴장').replace('\n', '<SCENE_BREAK>')
    transport = Transport([raw, compressed])
    runner = Compressor(ROOT / 'prompts/compress_to_1min.txt', [(1, 'fake')],
                        transport=transport, transcribe_numbers=transcribe_numbers)
    saved, invalid = [], []
    with pytest.raises(ResponseValidationError) as caught:
        asyncio.run(runner.run(serialized, lambda *a, **k: None,
                               lambda attempt, text: invalid.append(text),
                               save_raw=lambda attempt, text: saved.append(text)))
    assert len(transport.calls) == 1
    assert saved == invalid == [raw]
    assert caught.value.requires_review
    assert '中동 긴장' in caught.value.response
    assert '<SCENE_BREAK>' not in caught.value.response
    assert any('장면 2: 한자' in issue for issue in caught.value.validation_issues)
    assert '[한자 사용 금지]' in transport.calls[0]['prompt']


@pytest.mark.parametrize('transcribe_numbers', [False, True])
def test_hanja_rule_reaches_initial_generation_and_automatic_repair(
        tmp_path, serialized, compressed, transcribe_numbers):
    result, transport, _, _, _ = execute(
        tmp_path, serialized, [compressed.replace('0.67%', '0.68%'), compressed],
        transcribe_numbers=transcribe_numbers)
    assert result.text == (transcribe_numeric_tokens(compressed) if transcribe_numbers else compressed)
    assert len(transport.calls) == 2
    for instructions in (transport.calls[0]['prompt'], transport.calls[1]['prompt'],
                         transport.calls[1]['feedback']):
        assert '[한자 사용 금지]' in instructions
        assert '원문 표기 보존보다 우선' in instructions
        assert '불명확한 고유명사의 독음은 추측하지 않는다' in instructions


def test_hanja_in_automatic_repair_response_stops_with_review(tmp_path, serialized, compressed):
    draft = compressed.replace('중동 긴장', '中동 긴장')
    transport = Transport([compressed.replace('\n', ' '), draft, compressed])
    runner = Compressor(ROOT / 'prompts/compress_to_1min.txt', [(1, 'fake')], transport=transport)
    with pytest.raises(ResponseValidationError) as caught:
        asyncio.run(runner.run(serialized, lambda *a, **k: None, lambda *a: None))
    assert len(transport.calls) == 2
    assert caught.value.requires_review
    assert caught.value.response == draft


@pytest.mark.parametrize('retry_hanja', [False, True])
@pytest.mark.parametrize('transcribe_numbers', [False, True])
def test_hanja_job_persistence_manual_copy_and_explicit_retry(
        tmp_path, serialized, compressed, retry_hanja, transcribe_numbers):
    async def scenario():
        settings = Settings(tmp_path, model='gemini-3.8-flash')
        settings.prompt_path.parent.mkdir(parents=True)
        settings.prompt_path.write_text(
            (ROOT / 'prompts/compress_to_1min.txt').read_text(encoding='utf-8'), encoding='utf-8')
        (tmp_path / '.env').write_text('GEMINI_API_KEY_1=fake', encoding='utf-8')
        draft = compressed.replace('중동 긴장', '中동 긴장')
        raw = draft.replace('\n', '<SCENE_BREAK>')
        expected = transcribe_numeric_tokens(draft) if transcribe_numbers else draft
        transport = Transport([raw, raw if retry_hanja else compressed])

        class Clipboard:
            def __init__(self):
                self.writes = []

            def write(self, text):
                self.writes.append(text)

        clipboard = Clipboard()
        manager = JobManager(settings, clipboard=clipboard, compressor_factory=lambda **options: Compressor(
            settings.prompt_path, transport=transport, **options))
        first = manager.start(snapshot=serialized, transcribe_numbers=transcribe_numbers)
        await manager.wait(first['id'])
        job = manager.get(first['id'])
        assert job['state'] == 'needs_review' and job['error'] is None
        assert job['compressed'] == expected
        assert any('장면 2: 한자' in issue for issue in job['validation_issues'])
        assert len(transport.calls) == 1 and clipboard.writes == []
        folder = tmp_path / job['output_dir']
        for name in ('raw_response_1.txt', 'invalid_response_1.txt'):
            assert (folder / name).read_text(encoding='utf-8') == raw
        assert (folder / 'compressed.txt').read_text(encoding='utf-8') == expected
        metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
        assert metadata['state'] == 'needs_review'
        assert metadata['validation_issues'] == job['validation_issues']
        restored = JobManager(settings)
        assert restored.get(first['id'])['compressed'] == expected
        assert restored.get(first['id'])['validation_issues'] == job['validation_issues']
        assert manager.artifact(first['id'], 'compressed.txt') == expected
        await manager.copy_result(first['id'], 'compressed')
        assert clipboard.writes == [expected]

        second = manager.retry(first['id'])
        await manager.wait(second['id'])
        assert len(transport.calls) == 2
        retry = transport.calls[1]
        assert retry['source'] == serialized and retry['previous_response'] == expected
        assert '한자' in retry['feedback'] and 'U+4E2D' in retry['feedback']
        for instructions in (retry['prompt'], retry['feedback']):
            assert '[한자 사용 금지]' in instructions
            assert '원문 표기 보존보다 우선' in instructions
        final = manager.get(second['id'])
        assert final['state'] == ('needs_review' if retry_hanja else 'completed')
        assert final['transcribe_numbers'] == transcribe_numbers
        assert clipboard.writes == [expected]
        assert ('中' in final['compressed']) == retry_hanja

    asyncio.run(scenario())
