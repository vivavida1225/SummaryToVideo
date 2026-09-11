import asyncio
from types import SimpleNamespace

import pytest
from google.genai import errors, types

from backend.gemini import GeminiTransport, ProviderError


def install_response(monkeypatch, response):
    class FakeModels:
        async def generate_content(self, **_kwargs):
            if isinstance(response, Exception):
                raise response
            return response

    class FakeAsyncClient:
        models = FakeModels()
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass

    def client(**kwargs):
        assert kwargs['http_options'].retry_options.attempts == 1
        assert kwargs['http_options'].async_client_args['verify'].check_hostname
        return SimpleNamespace(aio=FakeAsyncClient())
    monkeypatch.setattr('backend.gemini.genai.Client', client)


def call():
    return asyncio.run(GeminiTransport().generate(key='test-secret', model='model', prompt='p', source='s', feedback='', timeout=60))


@pytest.mark.parametrize('code,reason,retryable', [
    (400, 'API_KEY_INVALID', True), (400, 'INVALID_ARGUMENT', False),
    (401, 'UNAUTHENTICATED', True), (403, 'PERMISSION_DENIED', True),
    (404, 'NOT_FOUND', False), (429, 'RESOURCE_EXHAUSTED', True), (503, 'UNAVAILABLE', True),
])
def test_sdk_error_classification_and_secret_redaction(monkeypatch, code, reason, retryable):
    exc = errors.APIError(code, {'error': {'code': code, 'message': 'test-secret', 'status': reason,
                'details': [{'reason': reason}, {'retryDelay': '12.5s'}]}})
    install_response(monkeypatch, exc)
    with pytest.raises(ProviderError) as result:
        call()
    assert result.value.retryable is retryable
    assert 'test-secret' not in str(result.value)
    assert result.value.retry_after == 12.5


def test_sdk_blocked_generation_is_not_retried(monkeypatch):
    response = types.GenerateContentResponse(candidates=[types.Candidate(finish_reason=types.FinishReason.SAFETY)])
    install_response(monkeypatch, response)
    with pytest.raises(ProviderError) as exc:
        call()
    assert not exc.value.retryable


def test_sdk_empty_response_is_retryable(monkeypatch):
    install_response(monkeypatch, types.GenerateContentResponse(candidates=[]))
    with pytest.raises(ProviderError) as exc:
        call()
    assert exc.value.retryable
