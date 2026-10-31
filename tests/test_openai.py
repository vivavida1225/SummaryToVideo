import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from backend.gemini import ProviderError


def response(text='완성 대본', *, status='completed', reason=None, refusal=False):
    content = [{'type': 'refusal', 'refusal': 'test-secret'}] if refusal else [
        {'type': 'output_text', 'text': text, 'annotations': []}]
    return dict(id='resp_test', object='response', created_at=1, status=status,
                incomplete_details={'reason': reason} if reason else None,
                output=[dict(id='msg_test', type='message', role='assistant', status='completed', content=content)])


def install(monkeypatch, answer):
    from backend import openai as module
    requests, clients = [], []

    async def handle(request):
        requests.append(request)
        if isinstance(answer, Exception):
            raise answer
        if callable(answer):
            return await answer(request)
        status, body = answer if isinstance(answer, tuple) else (200, answer)
        return httpx.Response(status, json=body, headers={'retry-after': '10'})

    def client(**kwargs):
        assert kwargs['max_retries'] == 0
        assert kwargs['api_key'] == 'test-secret'
        http_client = kwargs['http_client']
        assert http_client._transport._pool._ssl_context.check_hostname
        http_client._transport = httpx.MockTransport(handle)
        sdk = AsyncOpenAI(**kwargs)
        clients.append(sdk)
        return sdk

    monkeypatch.setattr(module, 'AsyncOpenAI', client)
    return module.OpenAITransport(), requests, clients


def generate(transport, **options):
    return transport.generate(key='test-secret', model='gpt-6.1-sol', prompt='full contract',
        source='original source', feedback=options.pop('feedback', ''), timeout=12, **options)


def test_sdk_request_and_resource_cleanup(monkeypatch):
    transport, requests, clients = install(monkeypatch, response())
    assert asyncio.run(generate(transport)) == '완성 대본'
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == 'https://api.openai.com/v1/responses'
    body = json.loads(request.content)
    assert body['model'] == 'gpt-6.1-sol'
    assert body['instructions'] == 'full contract'
    assert body['input'] == [{'role': 'user', 'content': 'original source'}]
    assert body['reasoning'] == {'effort': 'low'}
    assert body['max_output_tokens'] == 8192 and body['store'] is False
    assert not any(name in body for name in ('temperature', 'top_p', 'tools', 'stream'))
    assert request.extensions['timeout']['read'] == 12
    assert all(client.is_closed() for client in clients)


def test_repair_preserves_original_source_and_assistant_draft(monkeypatch):
    transport, requests, _ = install(monkeypatch, response())
    asyncio.run(generate(transport, feedback='fix all errors', previous_response='previous draft'))
    assert json.loads(requests[0].content)['input'] == [
        {'role': 'user', 'content': 'original source'},
        {'role': 'assistant', 'content': 'previous draft'},
        {'role': 'user', 'content': 'fix all errors'},
    ]


@pytest.mark.parametrize('status,action', [
    (400, 'stop'), (401, 'stop'), (403, 'stop'), (404, 'next_model'),
    (408, 'next_model'), (429, 'next_model'), (500, 'next_model'), (503, 'next_model'),
])
def test_api_errors_are_classified_without_sdk_retries_or_secrets(monkeypatch, status, action):
    transport, requests, clients = install(monkeypatch,
        (status, {'error': {'message': 'test-secret', 'type': 'test', 'code': 'test'}}))
    with pytest.raises(ProviderError) as caught:
        asyncio.run(generate(transport))
    assert caught.value.action == action
    assert 'test-secret' not in str(caught.value)
    assert len(requests) == 1
    assert all(client.is_closed() for client in clients)


@pytest.mark.parametrize('error', [httpx.ReadTimeout('test-secret'), httpx.ConnectError('test-secret')])
def test_connection_errors_fall_forward_without_secrets(monkeypatch, error):
    transport, requests, clients = install(monkeypatch, error)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(generate(transport))
    assert caught.value.action == 'next_model'
    assert 'test-secret' not in str(caught.value)
    assert len(requests) == 1 and all(client.is_closed() for client in clients)


@pytest.mark.parametrize('body,action', [
    (response(text='  '), 'next_model'),
    (response(refusal=True), 'stop'),
    (response(status='incomplete', reason='max_output_tokens'), 'next_model'),
    (response(status='incomplete', reason='content_filter'), 'stop'),
    (response(status='failed'), 'stop'),
])
def test_empty_refused_and_incomplete_output_is_not_accepted(monkeypatch, body, action):
    transport, _, clients = install(monkeypatch, body)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(generate(transport))
    assert caught.value.action == action
    assert 'test-secret' not in str(caught.value)
    assert all(client.is_closed() for client in clients)


def test_cancelled_request_closes_sdk_client(monkeypatch):
    started = asyncio.Event()

    async def wait_forever(request):
        started.set()
        await asyncio.Event().wait()

    transport, _, clients = install(monkeypatch, wait_forever)

    async def scenario():
        task = asyncio.create_task(generate(transport))
        await asyncio.wait_for(started.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(client.is_closed() for client in clients)

    asyncio.run(scenario())


@pytest.mark.parametrize('header_name', ['Authorization', 'authorization', 'AUTHORIZATION'])
def test_sdk_environment_cannot_override_official_endpoint_or_snapshot_key(monkeypatch, header_name):
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://example.invalid/compat/v1')
    monkeypatch.setenv('OPENAI_CUSTOM_HEADERS', f'{header_name}: Bearer wrong-key')
    transport, requests, _ = install(monkeypatch, response())
    asyncio.run(generate(transport))
    assert str(requests[0].url) == 'https://api.openai.com/v1/responses'
    assert requests[0].headers.get_list('Authorization') == ['Bearer test-secret']


@pytest.mark.parametrize('code,action', [
    ('server_error', 'next_model'), ('rate_limit_exceeded', 'next_model'), ('invalid_prompt', 'stop'),
])
def test_response_error_payload_is_classified_without_exposing_message(monkeypatch, code, action):
    body = response(status='failed')
    body['error'] = {'code': code, 'message': 'test-secret'}
    transport, requests, clients = install(monkeypatch, body)
    with pytest.raises(ProviderError) as caught:
        asyncio.run(generate(transport))
    assert caught.value.action == action
    assert 'test-secret' not in str(caught.value)
    assert len(requests) == 1 and all(client.is_closed() for client in clients)
