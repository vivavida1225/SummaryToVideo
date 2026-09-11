import asyncio

import pytest

from backend.compression import Compressor, CompressionError, ProviderError


class Transport:
    def __init__(self, answers):
        self.answers = iter(answers)
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        answer = next(self.answers)
        if isinstance(answer, Exception):
            raise answer
        return answer


def execute(tmp_path, serialized, answers, keys=None, **options):
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('Instructions\n{{SCENE_MARKET_DATA}}', encoding='utf-8')
    transport = Transport(answers)
    events, sleeps, invalid = [], [], []

    async def sleep(seconds):
        sleeps.append(seconds)

    runner = Compressor(prompt, keys or [(1, 'secret-a'), (2, 'secret-b'), (3, 'secret-c')],
                        transport=transport, sleep=sleep, **options)
    result = asyncio.run(runner.run(serialized, lambda state, **fields: events.append((state, fields)),
                                    lambda attempt, raw: invalid.append(raw)))
    return result, transport, events, sleeps, invalid


def test_rotates_keys_on_429_and_server_error(tmp_path, serialized, compressed):
    result, transport, events, sleeps, _ = execute(tmp_path, serialized,
        [ProviderError('호출 한도', retryable=True, retry_after=9), ProviderError('서버 오류', retryable=True), compressed])
    assert result.text.startswith('오늘의 AI 시황입니다.')
    assert [c['key'] for c in transport.calls] == ['secret-a', 'secret-b', 'secret-c']
    assert sleeps == [9, 4]
    assert [f['attempt'] for s, f in events if s == 'requesting'] == [1, 2, 3]
    assert all(c['source'] == serialized for c in transport.calls)


def test_one_format_repair_within_same_call_budget(tmp_path, serialized, compressed):
    result, transport, _, _, invalid = execute(tmp_path, serialized, [compressed.replace('835.97', '835.98'), compressed])
    assert result.text.count('835.97') == 1
    assert len(invalid) == 1
    assert '지수' in transport.calls[1]['feedback']
    assert transport.calls[1]['key'] == 'secret-a'


def test_second_invalid_response_stops_even_with_attempt_remaining(tmp_path, serialized):
    with pytest.raises(CompressionError, match='검증'):
        execute(tmp_path, serialized, ['bad', 'bad', 'must not reach'])


def test_three_transient_failures_stop(tmp_path, serialized):
    with pytest.raises(CompressionError, match='3회'):
        execute(tmp_path, serialized, [ProviderError('timeout', retryable=True)] * 3)


def test_non_retryable_model_error_stops_first_attempt(tmp_path, serialized):
    with pytest.raises(CompressionError, match='모델'):
        execute(tmp_path, serialized, [ProviderError('모델을 찾을 수 없습니다', retryable=False)])


def test_no_configured_key_is_actionable(tmp_path, serialized):
    runner = Compressor(tmp_path / 'prompt.txt', [])
    with pytest.raises(CompressionError, match='GEMINI_API_KEY'):
        asyncio.run(runner.run(serialized, lambda *a, **k: None, lambda *a: None))


def test_total_deadline_rejects_unbounded_server_wait(tmp_path, serialized):
    with pytest.raises(CompressionError, match='전체 제한'):
        execute(tmp_path, serialized, [ProviderError('429', retryable=True, retry_after=301)])


def test_unresponsive_transport_times_out_and_rotates(tmp_path, serialized, compressed):
    class SlowTransport(Transport):
        async def generate(self, **kwargs):
            if len(self.calls) == 0:
                self.calls.append(kwargs)
                await asyncio.sleep(1)
            return await super().generate(**kwargs)
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('{{SCENE_MARKET_DATA}}', encoding='utf-8')
    transport = SlowTransport([compressed])
    async def no_wait(_):
        pass
    runner = Compressor(prompt, [(1, 'a'), (2, 'b')], transport=transport, sleep=no_wait, request_timeout=0.02)
    result = asyncio.run(runner.run(serialized, lambda *a, **k: None, lambda *a: None))
    assert result.text.startswith('오늘의 AI 시황입니다.')
    assert [c['key'] for c in transport.calls] == ['a', 'b']
