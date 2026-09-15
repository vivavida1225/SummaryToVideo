import asyncio

import pytest

from backend.compression import Compressor
from backend.config import Settings
from backend.gemini import ProviderError
from backend.jobs import JobManager
from backend.narration import validate_response
from backend.validation import ValidationError
from tests.test_compression import Transport
from tests.test_jobs import Clipboard


def sized_draft(compressed, size):
    lines = compressed.splitlines()
    lines[1:4] = ['정보가 부족합니다.'] * 3
    lines[1] = '가' * (size - sum(map(len, lines))) + lines[1]
    return '\n'.join(lines)


def manager_for(tmp_path, answers):
    (tmp_path / '.env').write_text('GEMINI_API_KEY_1=fake', encoding='utf-8')
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('instructions', encoding='utf-8')
    transport, clipboard = Transport(answers), Clipboard()
    manager = JobManager(Settings(tmp_path), clipboard=clipboard,
        compressor_factory=lambda **options: Compressor(prompt, transport=transport, **options))
    return manager, transport, clipboard


@pytest.mark.parametrize('size', [551, 650])
@pytest.mark.parametrize('mixed', [False, True])
def test_overlength_stops_and_survives_restore(tmp_path, tiny_html, compressed, size, mixed):
    draft = sized_draft(compressed, size)
    if mixed:
        draft = draft.replace('835.97', '835.98')
    raw = draft.replace('\n', '<SCENE_BREAK>')

    async def scenario():
        manager, transport, clipboard = manager_for(tmp_path, [raw, compressed])
        job = manager.start(html=tiny_html)
        await manager.wait(job['id'])
        result = manager.get(job['id'])
        assert result['state'] == 'needs_review'
        assert len(transport.calls) == 1 and clipboard.writes == []
        assert result['compressed'] == draft
        assert result['body_char_count'] == size and result['excess_char_count'] == size - 550
        assert result['validation_issues']
        if mixed:
            assert any('지수' in issue for issue in result['validation_issues'])
        assert manager.active_id is None
        restored = JobManager(manager.settings, clipboard=clipboard)
        assert restored.get(job['id'])['state'] == 'needs_review'
        assert restored.artifact(job['id'], 'compressed.txt') == draft
        await restored.copy_result(job['id'], 'compressed')
        assert clipboard.writes == [draft]
        assert (tmp_path / result['output_dir'] / 'raw_response_1.txt').read_text(encoding='utf-8') == raw
    asyncio.run(scenario())


@pytest.mark.parametrize('outcome', ['valid', 'long', 'invalid'])
def test_explicit_regeneration_uses_draft_once(tmp_path, tiny_html, compressed, outcome):
    draft = sized_draft(compressed, 551)
    answer = {'valid': compressed, 'long': draft, 'invalid': 'short invalid draft'}[outcome]

    async def scenario():
        manager, transport, clipboard = manager_for(tmp_path, [draft, ProviderError('503', retryable=True), answer])
        first = manager.start(html=tiny_html)
        await manager.wait(first['id'])
        assert len(transport.calls) == 1
        second = manager.retry(first['id'], model='gemini-3.7-flash')
        await manager.wait(second['id'])
        result = manager.get(second['id'])
        assert result['state'] == {'valid': 'completed', 'long': 'needs_review', 'invalid': 'failed'}[outcome]
        assert len(transport.calls) == 3
        assert transport.calls[1]['previous_response'] == draft
        assert '1자' in transport.calls[1]['feedback']
        assert transport.calls[2]['feedback'] == transport.calls[1]['feedback']
        assert transport.calls[2]['model'] == 'gemini-3.6-flash'
        assert transport.calls[1]['source'] == transport.calls[0]['source']
        assert result['parent_id'] == first['id'] and result['max_attempts'] == 3
        assert manager.get(first['id'])['compressed'] == draft
        assert clipboard.writes == []
    asyncio.run(scenario())


def test_count_matches_display_including_outer_spaces_and_unicode(compressed, serialized):
    draft = ' ' + sized_draft(compressed, 550) + '😀\r\n'
    with pytest.raises(ValidationError) as caught:
        validate_response(draft, serialized)
    assert caught.value.excess_char_count == 2
    assert caught.value.body_char_count == 552


def test_exact_upper_bound_finishes_without_repair(tmp_path, tiny_html, compressed):
    async def scenario():
        manager, transport, _ = manager_for(tmp_path, [sized_draft(compressed, 550)])
        job = manager.start(html=tiny_html)
        await manager.wait(job['id'])
        assert manager.get(job['id'])['state'] == 'completed'
        assert len(transport.calls) == 1
    asyncio.run(scenario())


def test_review_retry_api_restores_context_after_restart(tmp_path, tiny_html, compressed, monkeypatch):
    from tests.test_api import client, auth
    from backend.gemini import GeminiTransport
    draft = sized_draft(compressed, 551)
    calls = []

    async def generate(self, **kwargs):
        calls.append(kwargs)
        return draft if len(calls) == 1 else compressed

    monkeypatch.setattr(GeminiTransport, 'generate', generate)
    (tmp_path / '.env').write_text('GEMINI_API_KEY_1=fake', encoding='utf-8')
    (tmp_path / 'prompts').mkdir()
    (tmp_path / 'prompts/compress_to_1min.txt').write_text('instructions', encoding='utf-8')
    with client(tmp_path) as c:
        first = c.post('/api/jobs', headers=auth(c), json={'html': tiny_html}).json()
        c.portal.call(c.app.state.manager.wait, first['id'])
        assert c.get(f"/api/jobs/{first['id']}", headers=auth(c)).json()['state'] == 'needs_review'
    with client(tmp_path) as c:
        headers = auth(c)
        restored = c.get(f"/api/jobs/{first['id']}", headers=headers).json()
        assert restored['state'] == 'needs_review' and restored['excess_char_count'] == 1
        assert c.get(f"/api/jobs/{first['id']}/artifacts/compressed.txt", headers=headers).text == draft
        assert len(calls) == 1
        response = c.post(f"/api/jobs/{first['id']}/retry", headers=headers, json={'model': 'gemini-3.7-flash'})
        assert response.status_code == 202
        second = response.json()
        c.portal.call(c.app.state.manager.wait, second['id'])
        assert c.get(f"/api/jobs/{second['id']}", headers=headers).json()['state'] == 'completed'
        assert calls[1]['previous_response'] == draft and calls[1]['model'] == 'gemini-3.7-flash'


def test_explicit_regeneration_preserves_context_across_key_failure(tmp_path, tiny_html, compressed):
    draft = sized_draft(compressed, 551)

    async def scenario():
        manager, transport, _ = manager_for(tmp_path, [draft, ProviderError('auth', action='next_key'), compressed])
        (tmp_path / '.env').write_text('GEMINI_API_KEY_1=first\nGEMINI_API_KEY_2=second', encoding='utf-8')
        first = manager.start(html=tiny_html)
        await manager.wait(first['id'])
        second = manager.retry(first['id'])
        await manager.wait(second['id'])
        assert manager.get(second['id'])['state'] == 'completed'
        assert [c['key'] for c in transport.calls] == ['first', 'first', 'second']
        assert transport.calls[2]['previous_response'] == draft
        assert transport.calls[2]['feedback'] == transport.calls[1]['feedback']
        assert transport.calls[2]['model'] == transport.calls[1]['model']
    asyncio.run(scenario())
