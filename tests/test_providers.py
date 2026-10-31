import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.compression import Compressor, CompressionError
from backend.config import Settings
from backend.gemini import ProviderError
from backend.jobs import JobManager
from backend.models import MODEL, model_candidates
from tests.test_compression import Transport
from tests.test_jobs import Clipboard


GPT = ['gpt-6.1-sol', 'gpt-6.1-terra', 'gpt-6-luna']
GEMINI = ['gemini-3.8-flash', 'gemini-3.6-flash', 'gemini-3.5-flash-lite']


@pytest.mark.parametrize('family', [GPT, GEMINI])
def test_candidates_never_leave_selected_provider(family):
    for index, model in enumerate(family):
        assert model_candidates(model) == tuple(family[index:])
    assert MODEL == GPT[0]
    assert model_candidates('gemini-3.7-flash') == tuple(GEMINI)


def test_openai_key_precedence_and_both_provider_redaction(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('OPENAI_API_KEY=file-secret\nGEMINI_API_KEY_3=gemini-secret', encoding='utf-8')
    settings = Settings(tmp_path)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    assert settings.keys('openai') == [(1, 'file-secret')]
    assert settings.keys() == [(3, 'gemini-secret')]
    monkeypatch.setenv('OPENAI_API_KEY', ' process-secret ')
    assert settings.keys('openai') == [(1, 'process-secret')]
    assert settings.redact('process-secret file-secret gemini-secret') == '[API KEY] [API KEY] [API KEY]'
    assert 'secret' not in repr(settings)
    monkeypatch.setenv('OPENAI_API_KEY', '   ')
    assert settings.keys('openai') == [(1, 'file-secret')]


def test_session_catalog_default_and_key_discovery(tmp_path, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    (tmp_path / '.env').write_text('OPENAI_API_KEY=openai-secret\nGEMINI_API_KEY_3=gemini-secret', encoding='utf-8')
    with TestClient(create_app(Settings(tmp_path)), base_url='http://127.0.0.1:8765') as client:
        response = client.get('/api/session')
        session = response.json()
        assert session['model'] == GPT[0]
        assert [item['id'] for item in session['models']] == GPT + GEMINI
        assert [item['provider'] for item in session['models']] == ['openai'] * 3 + ['gemini'] * 3
        assert session['configured_keys'] == [3]
        assert session['configured_keys_by_provider'] == {'openai': [1], 'gemini': [3]}
        assert 'secret' not in response.text


@pytest.mark.parametrize('start', range(3))
def test_gpt_falls_forward_to_luna_and_stops(tmp_path, serialized, start):
    transport = Transport([ProviderError('quota', retryable=True)] * (3 - start))
    compressor = Compressor(tmp_path / 'unused.txt', [(1, 'openai-key')], model=GPT[start], transport=transport)
    (tmp_path / 'unused.txt').write_text('instructions', encoding='utf-8')
    with pytest.raises(CompressionError) as error:
        asyncio.run(compressor.run(serialized, lambda *a, **k: None, lambda *a: None))
    assert [call['model'] for call in transport.calls] == GPT[start:]
    assert all(call['key'] == 'openai-key' for call in transport.calls)
    assert all(model in str(error.value) for model in GPT[start:])


@pytest.mark.parametrize('success_index', range(3))
def test_gpt_success_stops_fallback(tmp_path, serialized, compressed, success_index):
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('instructions', encoding='utf-8')
    transport = Transport([ProviderError('quota', retryable=True)] * success_index + [compressed])
    compressor = Compressor(prompt, [(1, 'openai-key')], transport=transport)
    result = asyncio.run(compressor.run(serialized, lambda *a, **k: None, lambda *a: None))
    assert result.text == compressed
    assert [call['model'] for call in transport.calls] == GPT[:success_index + 1]


def test_default_job_snapshots_only_openai_key_and_model(tmp_path, tiny_html, compressed, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    key_file = tmp_path / '.env'
    key_file.write_text('OPENAI_API_KEY=original\nGEMINI_API_KEY_1=wrong-provider', encoding='utf-8')
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('instructions', encoding='utf-8')
    transport = Transport([compressed])
    settings = Settings(tmp_path)
    manager = JobManager(settings, clipboard=Clipboard(),
        compressor_factory=lambda **options: Compressor(prompt, transport=transport, **options))

    async def scenario():
        job = manager.start(html=tiny_html)
        key_file.write_text('OPENAI_API_KEY=changed', encoding='utf-8')
        settings.model = GEMINI[0]
        await manager.wait(job['id'])
        result = manager.get(job['id'])
        assert result['state'] == 'completed'
        assert result['max_attempts'] == 4
        assert result['requested_model'] == result['model'] == GPT[0]
        assert transport.calls[0]['key'] == 'original'
        assert transport.calls[0]['model'] == GPT[0]
        metadata = (tmp_path / result['output_dir'] / 'metadata.json').read_text(encoding='utf-8')
        assert 'original' not in metadata and 'wrong-provider' not in metadata
        assert json.loads(metadata)['attempted_models'] == [GPT[0]]
    asyncio.run(scenario())


def test_missing_openai_key_never_uses_gemini_key(tmp_path, tiny_html, monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    (tmp_path / '.env').write_text('GEMINI_API_KEY_1=wrong-provider', encoding='utf-8')
    manager = JobManager(Settings(tmp_path), clipboard=Clipboard())

    async def scenario():
        job = manager.start(html=tiny_html)
        await manager.wait(job['id'])
        result = manager.get(job['id'])
        assert result['state'] == 'failed'
        assert 'OPENAI_API_KEY' in result['error']
        assert result['attempt'] == 0
    asyncio.run(scenario())


def test_api_selected_terra_routes_through_sdk_and_finishes_on_luna(tmp_path, tiny_html, compressed, monkeypatch):
    from backend.gemini import GeminiTransport
    from tests.test_openai import install, response
    (tmp_path / '.env').write_text('OPENAI_API_KEY=test-secret\nGEMINI_API_KEY_1=wrong-provider', encoding='utf-8')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    (tmp_path / 'prompts').mkdir()
    (tmp_path / 'prompts/compress_to_1min.txt').write_text('full contract', encoding='utf-8')

    async def answer(request):
        model = json.loads(request.content)['model']
        if model == GPT[1]:
            return httpx.Response(503, json={'error': {'message': 'test-secret'}})
        assert model == GPT[2]
        return httpx.Response(200, json=response(compressed))

    async def wrong_provider(*args, **kwargs):
        pytest.fail('OpenAI job reached Gemini transport')

    monkeypatch.setattr(GeminiTransport, 'generate', wrong_provider)
    _, requests, _ = install(monkeypatch, answer)
    with TestClient(create_app(Settings(tmp_path), clipboard=Clipboard()), base_url='http://127.0.0.1:8765') as client:
        headers = {'X-App-Token': client.get('/api/session').json()['token']}
        created = client.post('/api/jobs', headers=headers, json={'html': tiny_html, 'model': GPT[1]})
        assert created.status_code == 202
        client.portal.call(client.app.state.manager.wait, created.json()['id'])
        job = client.get('/api/jobs/' + created.json()['id'], headers=headers).json()
        assert job['state'] == 'completed' and job['requested_model'] == GPT[1]
        assert job['model'] == GPT[2] and job['attempted_models'] == GPT[1:]
        assert job['attempt'] == 2 and job['max_attempts'] == 3
        assert len(requests) == 2
        assert 'test-secret' not in json.dumps(job, ensure_ascii=False)


@pytest.mark.parametrize('case', ['repair', 'overlength', 'hanja', 'repair_fallback'])
def test_gpt_sdk_validation_repair_and_archive_flow(tmp_path, tiny_html, compressed, monkeypatch, case):
    from tests.test_length_review import sized_draft
    from tests.test_openai import install, response
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    (tmp_path / '.env').write_text('OPENAI_API_KEY=test-secret', encoding='utf-8')
    (tmp_path / 'prompts').mkdir()
    (tmp_path / 'prompts/compress_to_1min.txt').write_text('full contract', encoding='utf-8')
    draft = {'repair': 'short draft', 'repair_fallback': 'short draft',
             'overlength': sized_draft(compressed, 551), 'hanja': compressed + '漢'}[case]
    raw = draft.replace('\n', '<SCENE_BREAK>')
    answers = iter([response(raw)] + ([None] if case == 'repair_fallback' else []) + [response(compressed)])

    async def answer(request):
        body = next(answers)
        return httpx.Response(503, json={'error': {'message': 'temporary'}}) if body is None else httpx.Response(200, json=body)

    _, requests, _ = install(monkeypatch, answer)
    clipboard = Clipboard()
    manager = JobManager(Settings(tmp_path), clipboard=clipboard)

    async def scenario():
        first = manager.start(html=tiny_html)
        await manager.wait(first['id'])
        result = manager.get(first['id'])
        folder = tmp_path / result['output_dir']
        assert (folder / 'raw_response_1.txt').read_text(encoding='utf-8') == raw
        assert (folder / 'invalid_response_1.txt').read_text(encoding='utf-8') == raw
        restored = JobManager(Settings(tmp_path), clipboard=clipboard)
        if case in ('overlength', 'hanja'):
            assert result['state'] == 'needs_review' and len(requests) == 1
            assert restored.get(first['id'])['compressed'] == draft
            second = restored.retry(first['id'], model=GPT[1])
            await restored.wait(second['id'])
            result = restored.get(second['id'])
            assert result['max_attempts'] == 2 and result['requested_model'] == GPT[1]
            assert restored.get(first['id'])['state'] == 'needs_review'
        assert result['state'] == 'completed' and result['compressed'] == compressed
        assert clipboard.writes == []
        assert requests[0].headers['Authorization'] == 'Bearer test-secret'
        initial, repair = [json.loads(request.content) for request in requests[:2]]
        assert repair['input'][0] == initial['input'][0]
        assert repair['input'][1] == {'role': 'assistant', 'content': draft if case in ('overlength', 'hanja') else raw}
        assert '[원문 표현 보존]' in repair['instructions']
        assert '[한자 사용 금지]' in repair['instructions']
        if case == 'repair_fallback':
            assert len(requests) == 3 and result['model'] == GPT[1]
            assert json.loads(requests[2].content)['input'] == repair['input']

    asyncio.run(scenario())
