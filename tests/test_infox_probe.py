import asyncio
import json

import httpx

from backend.infox import InfoXClient, InfoXCredentials
from backend.infox.probe import ProbeOptions, main, probe


def test_probe_runs_full_read_only_flow_without_emitting_remote_values():
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path.endswith('/login') or req.url.path.endswith('/refresh-token'):
            return httpx.Response(200, json={'access_token': 'PRIVATE-TOKEN', 'token_type': 'bearer'})
        if '/videostate/' in req.url.path:
            return httpx.Response(200, json={'id': '42', 'video': {'private_url': 'SIGNED-URL'},
                'script': {'private_text': 'PRIVATE-SCRIPT'}, 'can_edit': True, 'updated_at': 'PRIVATE-DATE'})
        if '/recommand/' in req.url.path:
            return httpx.Response(200, json={'total': 1, 'skip': 0, 'limit': 50,
                'items': [{'is_favorite': True, 'id': 'PRIVATE-ID', 'segment': 'SIGNED-URL'}]})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'},
            text='data: {"type":"connection","status":"connected","channel":"infox:42","id":"PRIVATE-ID"}\n\n')

    async def scenario():
        async with InfoXClient(InfoXCredentials(email='private-email', password='PRIVATE-PASSWORD'),
            transport=httpx.MockTransport(handler)) as client:
            result = await probe(client, ProbeOptions(video_id='42', sse_timeout=1))
            assert result['ok'] is True
            assert result['checks']['refresh'] == 'ok'
            assert result['checks']['sse']['body_received'] is True
            assert result['checks']['favorites']['video']['total'] == 1
            assert 'PRIVATE-' not in json.dumps(result)
            assert 'SIGNED-URL' not in json.dumps(result)
    asyncio.run(scenario())
    assert seen == ['/infomaxai/auth/login', '/infomaxai/videos/videostate/42',
        '/infomaxai/auth/refresh-token', '/infomaxai/videos/videostate/42',
        '/infomaxai/recommand/video', '/infomaxai/recommand/uservideo', '/api/stream']


def test_probe_reports_auth_error_without_response_body():
    async def scenario():
        async with InfoXClient(InfoXCredentials(email='e', password='password'),
            transport=httpx.MockTransport(lambda req: httpx.Response(401, text='PRIVATE-ERROR'))) as client:
            result = await probe(client, ProbeOptions(video_id='42'))
            assert result['ok'] is False
            assert result['failed_step'] == 'authenticate'
            assert result['error'] == {'code': 'auth_expired', 'status_code': 401}
            assert 'PRIVATE' not in json.dumps(result)
    asyncio.run(scenario())


def test_probe_missing_video_exits_nonzero_without_network(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv('INFOX_VIDEO_ID', raising=False)
    assert main([], root=tmp_path) == 2
    result = json.loads(capsys.readouterr().out)
    assert result['error']['code'] == 'configuration_error'
    assert result['ok'] is False


def test_probe_eof_without_event_is_failure():
    def handler(req):
        if req.url.path.endswith('/refresh-token'):
            return httpx.Response(200, json={'access_token': 'fresh', 'token_type': 'bearer'})
        if '/videostate/' in req.url.path:
            return httpx.Response(200, json={'id': 42, 'video': {}})
        if '/recommand/' in req.url.path:
            return httpx.Response(200, json={'total': 0, 'skip': 0, 'limit': 50, 'items': []})
        return httpx.Response(200, headers={'content-type': 'text/event-stream'}, text=': heartbeat\n\n')

    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='test'), transport=httpx.MockTransport(handler)) as c:
            result = await probe(c, ProbeOptions(video_id='42'))
            assert result['ok'] is False
            assert result['failed_step'] == 'sse'
            assert result['error']['code'] == 'event_missing'
    asyncio.run(scenario())
