import asyncio
import json

import httpx
import pytest

from backend.infox.client import InfoXClient, InfoXError
from backend.infox.config import InfoXCredentials


def run(coro):
    return asyncio.run(coro)


def token(value='access-one'):
    return httpx.Response(200, json={'access_token': value, 'token_type': 'bearer'})


def test_login_refresh_preserve_cookies_and_replace_bearer():
    seen = []

    def handler(req):
        seen.append(req.url.path)
        assert req.url.host == 'infox2.infomaxai.com'
        if req.url.path.endswith('/login'):
            assert json.loads(req.content) == {'email': 'user@example.invalid', 'password': 'secret'}
            assert 'authorization' not in req.headers
            return httpx.Response(200, json={'access_token': 'old', 'token_type': 'bearer'},
                                  headers={'set-cookie': 'session=cookie-secret; Path=/; Secure'})
        assert req.headers['x-org-device-token'] == 'device-secret'
        assert 'session=cookie-secret' in req.headers['cookie']
        if req.url.path.endswith('/refresh-token'):
            assert req.headers['authorization'] == 'Bearer old'
            return token('new')
        assert req.headers['authorization'] == 'Bearer new'
        return httpx.Response(200, json={'id': 42, 'video': {}, 'script': None, 'updated_at': 'v1'})

    async def scenario():
        creds = InfoXCredentials(email='user@example.invalid', password='secret', device_token='device-secret')
        assert 'secret' not in repr(creds)
        async with InfoXClient(creds, transport=httpx.MockTransport(handler)) as c:
            await c.authenticate()
            await c.refresh()
            assert (await c.video_state(42))['id'] == 42
    run(scenario())
    assert seen == ['/infomaxai/auth/login', '/infomaxai/auth/refresh-token', '/infomaxai/videos/videostate/42']


def test_expired_read_refreshes_once_then_replays_with_new_token():
    requests = []

    def handler(req):
        requests.append(req.url.path)
        if req.url.path.endswith('/refresh-token'):
            return token('renewed')
        if req.headers['authorization'] == 'Bearer expired':
            return httpx.Response(401, text='secret failure')
        return httpx.Response(200, json={'id': 8, 'video': {}})

    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='expired'), transport=httpx.MockTransport(handler)) as c:
            assert (await c.video_state(8))['id'] == 8
    run(scenario())
    assert len(requests) == 3


def test_transient_failure_after_refresh_still_retries_the_read():
    reads = 0
    refreshes = 0

    def handler(req):
        nonlocal reads, refreshes
        if req.url.path.endswith('/refresh-token'):
            refreshes += 1
            return token('fresh')
        reads += 1
        if reads == 1:
            return httpx.Response(401)
        assert req.headers['authorization'] == 'Bearer fresh'
        if reads == 2:
            return httpx.Response(503)
        return httpx.Response(200, json={'id': 8, 'video': {}})

    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='expired'),
            transport=httpx.MockTransport(handler), retry_delay=0) as c:
            assert (await c.video_state(8))['id'] == 8
    run(scenario())
    assert reads == 3
    assert refreshes == 1


def test_failed_refresh_is_not_implicitly_retried_as_a_read():
    requests = []

    def handler(req):
        requests.append(req.url.path)
        return httpx.Response(503 if req.url.path.endswith('/refresh-token') else 401)

    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='expired'),
            transport=httpx.MockTransport(handler), retry_delay=0) as c:
            with pytest.raises(InfoXError, match='server_error'):
                await c.video_state(8)
    run(scenario())
    assert requests == ['/infomaxai/videos/videostate/8', '/infomaxai/auth/refresh-token']


@pytest.mark.parametrize('status,code', [(401, 'auth_expired'), (403, 'permission_denied'),
                                      (404, 'http_error'), (500, 'server_error'), (429, 'rate_limited')])
def test_safe_classified_errors_have_bounded_attempts(status, code):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        return httpx.Response(status, text='password=DO-NOT-LEAK https://secret.invalid/?signature=private')

    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='DO-NOT-LEAK'),
                               transport=httpx.MockTransport(handler), retry_delay=0) as c:
            with pytest.raises(InfoXError) as err:
                await c.video_state(7)
            assert err.value.code == code
            assert err.value.status_code == status
            assert 'DO-NOT-LEAK' not in str(err.value)
            assert 'signature' not in repr(err.value)
            assert err.value.__cause__ is None
    run(scenario())
    assert len(seen) == (2 if status == 401 else 3 if status in (500, 429) else 1)


def test_device_only_authentication_and_refresh_fallback():
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if req.url.path.endswith('/refresh-token'):
            return httpx.Response(401)
        assert req.url.path.endswith('/org-email/device-refresh')
        assert json.loads(req.content) == {'device_token': 'device'}
        return token()

    async def scenario():
        async with InfoXClient(InfoXCredentials(device_token='device'), transport=httpx.MockTransport(handler)) as c:
            await c.authenticate()
            await c.refresh()
    run(scenario())
    assert len(seen) == 3


@pytest.mark.parametrize('source,path', [('video', '/infomaxai/recommand/video'),
                                       ('uservideo', '/infomaxai/recommand/uservideo')])
def test_favorite_search_preserves_filters_and_pagination(source, path):
    def handler(req):
        assert req.url.path == path
        assert json.loads(req.content) == {'skip': 50, 'limit': 50, 'search': '반도체',
            'include_politics': False, 'sort_by': 'relevance', 'search_mode': 'relevance',
            'favorites_only': True, 'date_from': '2026-01-01'}
        return httpx.Response(200, json={'total': 51, 'items': [{'id': 'segment-51', 'is_favorite': True}],
                                       'skip': 50, 'limit': 50})

    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='test'), transport=httpx.MockTransport(handler)) as c:
            result = await c.search_favorites(source=source, skip=50, date_from='2026-01-01')
            assert result['total'] == 51
            assert result['items'][0]['id'] == 'segment-51'
    run(scenario())


@pytest.mark.parametrize('body', [[], {'items': [], 'total': '0', 'skip': 0, 'limit': 50},
    {'items': [{'is_favorite': False}], 'total': 1, 'skip': 0, 'limit': 50}])
def test_search_rejects_invalid_schema_or_nonfavorite_results(body):
    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='x'),
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))) as c:
            with pytest.raises(InfoXError, match='invalid_response'):
                await c.search_favorites()
    run(scenario())


def test_transient_transport_error_retries_read_without_leaking_details():
    attempts = 0

    def handler(req):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError('token=secret', request=req)
        return httpx.Response(200, json={'id': 7, 'video': {}})

    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='x'), transport=httpx.MockTransport(handler), retry_delay=0) as c:
            assert (await c.video_state(7))['id'] == 7
    run(scenario())
    assert attempts == 3


@pytest.mark.parametrize('body', [{'access_token': ''}, {'access_token': 'a\nb'},
                                       {'access_token': 'x', 'token_type': 'Basic'}])
def test_invalid_login_does_not_accept_malformed_tokens(body):
    async def scenario():
        async with InfoXClient(InfoXCredentials(email='e', password='p'),
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body))) as c:
            with pytest.raises(InfoXError, match='invalid_response'):
                await c.authenticate()
    run(scenario())


def test_credentials_env_priority_and_missing_configuration(tmp_path, monkeypatch):
    (tmp_path / '.env').write_text('INFOX_USERNAME=file@example.invalid\nINFOX_PASSWORD=file-secret\n', encoding='utf-8-sig')
    monkeypatch.setenv('INFOX_PASSWORD', 'environment-secret')
    creds = InfoXCredentials.load(tmp_path)
    assert creds.email == 'file@example.invalid'
    assert creds.password == 'environment-secret'
    assert 'secret' not in repr(creds)

    async def scenario():
        async with InfoXClient(InfoXCredentials()) as c:
            with pytest.raises(InfoXError, match='configuration_error'):
                await c.authenticate()
    run(scenario())


@pytest.mark.parametrize('video_id', ['../auth/login', '1?secret=x', 0, True])
def test_invalid_video_identifier_never_reaches_network(video_id):
    async def scenario():
        def forbidden(req):
            pytest.fail('invalid identifier reached HTTP transport')
        async with InfoXClient(InfoXCredentials(access_token='x'), transport=httpx.MockTransport(forbidden)) as c:
            with pytest.raises(InfoXError, match='configuration_error'):
                await c.video_state(video_id)
    run(scenario())
