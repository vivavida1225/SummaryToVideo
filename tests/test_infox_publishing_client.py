import asyncio
import json

import httpx
import pytest

from backend.infox.client import InfoXClient, ORIGIN
from backend.infox.config import InfoXCredentials
from backend.infox.errors import InfoXError


def methods(c):
    return [lambda: c.start_render('video-1'), lambda: c.generate_youtube_metadata('video-1', 'render-1'),
        lambda: c.update_youtube_metadata('video-1', 'meta-1', title='title', description='desc', keywords=['kept']),
        lambda: c.upload_youtube('video-1', 'meta-1')]


@pytest.mark.parametrize('outcome', [401, 403, 500, 'timeout', 'wrong_message', 'bad_id', 'redirect'])
def test_write_endpoints_are_called_once_on_timeout_or_401(outcome):
    calls = []
    def handler(req):
        calls.append(req)
        if outcome == 'timeout': raise httpx.ReadTimeout('secret', request=req)
        if outcome == 'redirect': return httpx.Response(302, headers={'location': 'https://evil.invalid'})
        if outcome == 'wrong_message': return httpx.Response(200, json={'message': 'other', 'taskid': 'ok', 'success': False})
        if outcome == 'bad_id': return httpx.Response(200, json={'message': 'render', 'taskid': '../secret'})
        return httpx.Response(outcome, text='secret')
    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='test'), transport=httpx.MockTransport(handler)) as c:
            for method in methods(c):
                with pytest.raises(InfoXError): await method()
    asyncio.run(scenario())
    assert len(calls) == 4
    assert all(req.url.host == 'infox2.infomaxai.com' for req in calls)


def test_upload_forces_unlisted_and_fixed_redirect_and_metadata_patch_uses_metadata_task_id():
    calls = []
    def handler(req):
        calls.append(req)
        if req.method == 'PATCH':
            assert json.loads(req.content) == {'task_id': 'meta-1', 'title': 'title', 'description': 'desc', 'keywords': ['kept']}
            return httpx.Response(200, json={'message': 'updated'})
        if '/render_charge_preview/' in req.url.path: return httpx.Response(200, json={'will_charge': True})
        if '/process_rendering/' in req.url.path: message = 'render'
        elif '/infox_generate_metainfo_youtube/video-1/render-1' in req.url.path: message = 'generate_metainfo_youtube'
        else:
            assert req.url.path.endswith('/infox_upload_youtube_video/video-1/meta-1')
            assert req.url.params['privacy_status'] == 'unlisted'
            assert req.url.params['redirect_url'] == ORIGIN + '/studio/library/videos/video-1?chatboxView=script'
            message = 'upload_youtube_video'
        return httpx.Response(200, json={'message': message, 'taskid': 'accepted-id'})
    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='test'), transport=httpx.MockTransport(handler)) as c:
            for method in methods(c): await method()
            assert (await c.render_charge_preview('video-1'))['will_charge']
    asyncio.run(scenario())
    assert len(calls) == 5
