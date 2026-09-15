import asyncio

import pytest

from backend.compression import Compressor, CompressionError
from backend.gemini import ProviderError
from tests.test_compression import Transport


MODELS = ['gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-3.5-flash-lite']


def runner(tmp_path, answers, **kwargs):
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('instructions', encoding='utf-8')
    transport = Transport(answers)
    compressor = Compressor(prompt, kwargs.pop('keys', [(1, 'a')]), transport=transport, **kwargs)
    return compressor, transport


def run(compressor, source, events=None):
    return asyncio.run(compressor.run(source, lambda state, **fields: events.append((state, fields)) if events is not None else None,
                                      lambda *args: None))


def test_fourth_model_can_repair_and_finish(tmp_path, serialized, compressed):
    compressor, transport = runner(tmp_path, [ProviderError('quota', retryable=True)] * 3 + ['bad', compressed])
    events = []
    assert run(compressor, serialized, events).text == compressed
    assert [call['model'] for call in transport.calls] == MODELS + [MODELS[-1]]
    assert [call['key'] for call in transport.calls] == ['a'] * 5
    assert [f['attempt'] for s, f in events if s == 'requesting'] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize('model,expected', [(MODELS[1], MODELS[1:]), (MODELS[-1], MODELS[-1:])])
def test_selected_model_only_uses_remaining_candidates(tmp_path, serialized, compressed, model, expected):
    compressor, transport = runner(tmp_path, [ProviderError('quota', retryable=True)] * (len(expected) - 1) + [compressed], model=model)
    assert run(compressor, serialized).text == compressed
    assert [call['model'] for call in transport.calls] == expected


def test_authentication_changes_key_without_changing_model(tmp_path, serialized, compressed):
    compressor, transport = runner(tmp_path, [ProviderError('auth', action='next_key'), ProviderError('quota', retryable=True), compressed],
                                   keys=[(1, 'a'), (3, 'b')])
    assert run(compressor, serialized).text == compressed
    assert [(c['model'], c['key']) for c in transport.calls] == [(MODELS[0], 'a'), (MODELS[0], 'b'), (MODELS[1], 'b')]


def test_exhausted_keys_stop_without_model_switch(tmp_path, serialized):
    compressor, transport = runner(tmp_path, [ProviderError('auth', action='next_key')] * 2, keys=[(1, 'a'), (2, 'b')])
    with pytest.raises(CompressionError, match='키'):
        run(compressor, serialized)
    assert [c['model'] for c in transport.calls] == [MODELS[0]] * 2


def test_repair_context_survives_model_fallback(tmp_path, serialized, compressed):
    compressor, transport = runner(tmp_path, ['bad draft', ProviderError('timeout', retryable=True), compressed])
    assert run(compressor, serialized).text == compressed
    assert [c['model'] for c in transport.calls] == [MODELS[0], MODELS[0], MODELS[1]]
    assert transport.calls[2]['feedback'] == transport.calls[1]['feedback']
    assert transport.calls[2]['previous_response'] == 'bad draft'
    assert all(c['source'] == serialized for c in transport.calls)


def test_model_exhaustion_lists_attempted_models(tmp_path, serialized):
    compressor, transport = runner(tmp_path, [ProviderError('quota', retryable=True)] * 4)
    with pytest.raises(CompressionError) as caught:
        run(compressor, serialized)
    assert all(model in str(caught.value) for model in MODELS)
    assert len(transport.calls) == 4


def test_deadline_prevents_next_call(tmp_path, serialized):
    times = iter([0, 0, 301])
    compressor, transport = runner(tmp_path, [ProviderError('timeout', retryable=True)], clock=lambda: next(times))
    with pytest.raises(CompressionError, match='전체 제한'):
        run(compressor, serialized)
    assert len(transport.calls) == 1


def test_remaining_time_caps_transport_timeout(tmp_path, serialized, compressed):
    times = iter([0, 285])
    compressor, transport = runner(tmp_path, [compressed], clock=lambda: next(times))
    assert run(compressor, serialized).text == compressed
    assert transport.calls[0]['timeout'] == 15


def test_combined_key_model_and_repair_budget(tmp_path, serialized, compressed):
    compressor, transport = runner(tmp_path, [ProviderError('auth', action='next_key')] * 2
                                   + [ProviderError('quota', retryable=True)] * 3 + ['bad', compressed],
                                   keys=[(1, 'a'), (2, 'b'), (3, 'c')])
    assert run(compressor, serialized).text == compressed
    assert len(transport.calls) == 7
    assert [c['key'] for c in transport.calls] == ['a', 'b', 'c', 'c', 'c', 'c', 'c']


def test_duplicate_rejected_credentials_are_never_reused(tmp_path, serialized, compressed):
    compressor, transport = runner(tmp_path, [ProviderError('auth', action='next_key'), compressed],
                                   keys=[(1, 'bad'), (2, 'bad'), (3, 'good')])
    assert run(compressor, serialized).text == compressed
    assert [c['key'] for c in transport.calls] == ['bad', 'good']
