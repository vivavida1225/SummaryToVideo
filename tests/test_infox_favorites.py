import asyncio

import pytest


def favorite(index=0, **changes):
    return {'id': f'item-{index}', 'videoId': 'source-1', 'bucket': 'assets-videos',
        'segment': f'clip-{index}.mp4', 'thumbnail': f'clip-{index}.jpeg',
        'frameIndex': index, 'timestamp': index * 6.0, 'duration': 6.01,
        'mime_type': 'video/mp4', 'resolution_width': 1280, 'resolution_height': 720,
        'is_favorite': True, 'title': '삼성전자 영상',
        'segment_url_presigned': f'https://kr.object.iwinv.kr/assets-videos/clip-{index}.mp4?signature=PRIVATE',
        'preview_presigned': f'https://kr.object.iwinv.kr/assets-videos/clip-{index}.jpeg?signature=PRIVATE', **changes}


class FavoritesClient:
    def __init__(self, items, *, unavailable=()):
        self.items, self.requests, self.probes = items, [], []
        self.unavailable = unavailable
    async def search_favorites(self, **request):
        self.requests.append(request)
        skip, limit = request['skip'], request['limit']
        return {'items': self.items[skip:skip+limit], 'total': len(self.items), 'skip': skip, 'limit': limit}
    async def favorite_segment_available(self, raw):
        self.probes.append(raw['id'])
        return raw['id'] not in self.unavailable


def test_complete_pagination_preserves_segments_and_accepts_company_titles():
    from backend.infox.favorites import collect_favorites, choose_favorite
    async def scenario():
        client = FavoritesClient([favorite(i) for i in range(53)], unavailable=('item-0',))
        result = await collect_favorites(client)
        assert len(result.candidates) == 53
        assert [x['skip'] for x in client.requests] == [0, 50]
        assert all(x['source'] == 'video' and x['search'] == '반도체' and x.get('date_from') is None for x in client.requests)
        chosen = await choose_favorite(client, result)
        assert chosen.id == 'item-1' and chosen.title == '삼성전자 영상'
        assert 'PRIVATE' not in chosen.model_dump_json() and 'https://' not in chosen.model_dump_json()
    asyncio.run(scenario())


def test_frozen_candidate_never_switches_on_reorder_or_media_failure():
    from backend.infox.favorites import collect_favorites, choose_favorite, PlanError
    async def scenario():
        client = FavoritesClient([favorite(0), favorite(1)])
        result = await collect_favorites(client)
        selected = await choose_favorite(client, result)
        client.items.reverse()
        assert await choose_favorite(client, await collect_favorites(client), frozen=selected) == selected
        client.unavailable = ('item-0',)
        with pytest.raises(PlanError, match='selection_unavailable'):
            await choose_favorite(client, await collect_favorites(client), frozen=selected)
        assert client.probes[-1] == 'item-0'
    asyncio.run(scenario())


@pytest.mark.parametrize('mode', ['empty_page', 'wrong_skip', 'changed_total', 'page_limit'])
def test_incomplete_or_unstable_pages_are_explicitly_rejected(mode):
    from backend.infox.favorites import collect_favorites, PlanError
    from backend.infox.plan_models import SelectionPolicy
    class Broken(FavoritesClient):
        async def search_favorites(self, **request):
            result = await super().search_favorites(**request)
            if request['skip']:
                if mode == 'empty_page': result['items'] = []
                if mode == 'wrong_skip': result['skip'] = 0
                if mode == 'changed_total': result['total'] += 1
            return result
    with pytest.raises(PlanError):
        asyncio.run(collect_favorites(Broken([favorite(i) for i in range(53)]),
            policy=SelectionPolicy(max_pages=1 if mode == 'page_limit' else 20)))


def test_unvalidated_uservideo_and_unusable_results_do_not_fallback():
    from backend.infox.favorites import collect_favorites, choose_favorite, PlanError
    async def scenario():
        client = FavoritesClient([favorite(duration=0), favorite(1, mime_type='image/jpeg')])
        result = await collect_favorites(client)
        with pytest.raises(PlanError, match='no_usable_candidate'):
            await choose_favorite(client, result)
        with pytest.raises(PlanError, match='unsupported_uservideo'):
            await collect_favorites(client, source='uservideo')
    asyncio.run(scenario())


def test_media_probe_is_bounded_and_never_forwards_credentials_or_redirects():
    import httpx
    from backend.infox.client import InfoXClient
    from backend.infox.config import InfoXCredentials
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers.get('range') == 'bytes=0-31'
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        assert 'x-org-device-token' not in request.headers
        return httpx.Response(206, headers={'content-type': 'video/mp4'}, content=b'\0\0\0\x20ftypisom'+b'\0'*20)
    async def scenario():
        async with InfoXClient(InfoXCredentials(access_token='PRIVATE',device_token='DEVICE'),
                               media_transport=httpx.MockTransport(handler)) as client:
            raw = favorite(segment_url_presigned='https://kr.object.iwinv.kr/assets-videos/clip-0.mp4?signature=PRIVATE')
            assert await client.favorite_segment_available(raw)
            for url in ('https://localhost/assets-videos/clip-0.mp4',
                        'https://kr.object.iwinv.kr/assets-videos/other.mp4',
                        'https://user:secret@kr.object.iwinv.kr/assets-videos/clip-0.mp4'):
                assert not await client.favorite_segment_available({**raw, 'segment_url_presigned': url})
    asyncio.run(scenario())
    assert len(seen) == 1


@pytest.mark.parametrize('status,mime,body', [(302,'video/mp4',b'ftyp'),(403,'video/mp4',b'ftyp'),
    (200,'text/html',b'<html>'),(206,'video/mp4',b'not-an-mp4')])
def test_media_probe_rejects_redirect_errors_and_incompatible_content(status,mime,body):
    import httpx
    from backend.infox.client import InfoXClient
    from backend.infox.config import InfoXCredentials
    async def scenario():
        async with InfoXClient(InfoXCredentials(),media_transport=httpx.MockTransport(
                lambda r:httpx.Response(status,headers={'content-type':mime,'location':'https://other.invalid'},content=body))) as client:
            assert not await client.favorite_segment_available(favorite(
                segment_url_presigned='https://kr.object.iwinv.kr/assets-videos/clip-0.mp4?signature=PRIVATE'))
    asyncio.run(scenario())


def test_subframe_candidate_does_not_block_a_later_usable_segment():
    from backend.infox.favorites import collect_favorites,choose_favorite
    async def scenario():
        client=FavoritesClient([favorite(duration=.01),favorite(1)])
        selected=await choose_favorite(client,await collect_favorites(client))
        assert selected.id=='item-1'
    asyncio.run(scenario())
