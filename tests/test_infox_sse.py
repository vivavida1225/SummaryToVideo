import asyncio

import httpx
import pytest

from backend.infox.client import InfoXClient, InfoXError
from backend.infox.config import InfoXCredentials
from backend.infox.sse import parse_events


class Chunks(httpx.AsyncByteStream):
    async def __aiter__(self):
        raw = ('\ufeff: heartbeat\r\nid: first\r\nretry: 1200\r\nevent: update\r\n'
               'data: {"additional_kwargs":\r\ndata: {"taskid":"test","task_status":"running"}}\r\n\r\n'
               'id:\n\ndata: plain text\n\ndata:\n\ndata: incomplete').encode()
        for i in range(0, len(raw), 3):
            yield raw[i:i + 3]


def test_sse_dispatches_bare_cr_delimiter_without_waiting_for_another_chunk():
    async def chunks():
        yield b'data: {"ok":true}\r\r'
        await asyncio.Future()  # A live server keeps the connection open.

    async def scenario():
        stream = parse_events(chunks())
        try:
            async with asyncio.timeout(0.2):
                event = await anext(stream)
            assert event.json() == {'ok': True}
        finally:
            await stream.aclose()
    asyncio.run(scenario())


def test_sse_receives_chunked_multiline_body_and_keeps_auth_separate():
    def handler(req):
        assert req.url.path == '/api/stream'
        assert req.url.params['channel'] == 'infox:42'
        assert req.headers['accept'] == 'text/event-stream'
        assert req.headers['last-event-id'] == 'previous'
        assert 'authorization' not in req.headers
        assert 'x-org-device-token' not in req.headers
        return httpx.Response(200, headers={'content-type': 'text/event-stream; charset=utf-8'}, stream=Chunks())

    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='private', device_token='private-device'),
                               transport=httpx.MockTransport(handler)) as c:
            events = [event async for event in c.events(42, last_event_id='previous')]
            assert len(events) == 3  # EOF without a blank separator is not dispatched.
            assert events[0].event == 'update'
            assert events[0].id == 'first'
            assert events[0].retry_ms == 1200
            assert events[0].json()['additional_kwargs']['task_status'] == 'running'
            assert events[1].id == ''
            assert events[1].data == 'plain text'
            assert events[2].data == ''
            assert 'running' not in repr(events[0])  # Raw bodies never enter diagnostic repr.
    asyncio.run(scenario())


@pytest.mark.parametrize('status,content_type,code', [(401, 'text/event-stream', 'auth_expired'),
    (403, 'text/event-stream', 'permission_denied'), (200, 'text/html', 'invalid_response')])
def test_sse_rejects_auth_failure_or_html_login_page(status, content_type, code):
    async def scenario():
        async with InfoXClient(InfoXCredentials(), transport=httpx.MockTransport(
            lambda req: httpx.Response(status, headers={'content-type': content_type}, text='secret'))) as c:
            with pytest.raises(InfoXError) as err:
                _ = [event async for event in c.events(42)]
            assert err.value.code == code
            assert 'secret' not in str(err.value)
    asyncio.run(scenario())


def test_sse_bearer_mode_refreshes_before_reconnect_and_sends_latest_token():
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path.endswith('/refresh-token'):
            return httpx.Response(200, json={'access_token': 'fresh', 'token_type': 'bearer'})
        if req.headers['authorization'] == 'Bearer old':
            return httpx.Response(401)
        assert req.headers['authorization'] == 'Bearer fresh'
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text='data: {"ok":true}\n\n')

    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='old'), stream_auth='bearer',
                               transport=httpx.MockTransport(handler)) as c:
            events = [event async for event in c.events(42)]
            assert events[0].json() == {'ok': True}
    asyncio.run(scenario())
    assert len(seen) == 3


def test_sse_limits_unterminated_event_size():
    async def scenario():
        async with InfoXClient(InfoXCredentials(), transport=httpx.MockTransport(lambda req:
            httpx.Response(200, headers={'content-type': 'text/event-stream'}, text='data: ' + 'x' * 100 + '\n\n'))) as c:
            with pytest.raises(InfoXError, match='invalid_response'):
                _ = [event async for event in c.events(42, max_event_bytes=64)]
    asyncio.run(scenario())
