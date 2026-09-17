import asyncio
from datetime import date

import httpx
import pytest

from backend import myasset
from backend.serializer import serialize_html


def page(html):
    return ('<nav>outside</nav><div class="contWrap">' + html + '</div><footer>outside</footer>').encode()


def test_extract_preserves_news_and_excludes_outer_page(tiny_html):
    fragment = myasset.extract_myasset_html(page(tiny_html))
    assert 'outside' not in fragment
    assert 'contWrap' not in fragment
    assert serialize_html(fragment) == serialize_html(tiny_html)


@pytest.mark.parametrize('document,status', [
    (b'<div class="contWrap"><a>promotion only</a></div>', 404),
    (b'<div class="contWrap"></div>', 404),
    (b'<div>missing container</div>', 502),
    (b'<div class="contWrap"></div><div class="contWrap"></div>', 502),
    (b'<div class="contWrap"><div class="embedded-content"><header>incomplete</header></div></div>', 502),
    (b'\xff', 502),
])
def test_invalid_or_unpublished_pages(document, status):
    with pytest.raises(myasset.MyassetError) as error:
        myasset.extract_myasset_html(document)
    assert error.value.status_code == status


def test_duplicate_news_is_rejected(tiny_html):
    with pytest.raises(myasset.MyassetError) as error:
        myasset.extract_myasset_html(page(tiny_html + tiny_html))
    assert error.value.status_code == 502


def test_duplicate_even_when_second_news_has_no_header(tiny_html):
    with pytest.raises(myasset.MyassetError) as error:
        myasset.extract_myasset_html(page(tiny_html + '<div class="embedded-content">other news</div>'))
    assert error.value.status_code == 502


def test_overall_deadline_is_enforced(monkeypatch):
    async def handler(request):
        await asyncio.sleep(1)
        pytest.fail('Fetch should have timed out')
    monkeypatch.setattr(myasset, 'FETCH_DEADLINE_SECONDS', 0.01)
    mock_http(monkeypatch, handler)
    with pytest.raises(myasset.MyassetError) as error:
        asyncio.run(myasset.fetch_myasset_source(date(2026, 9, 17), 1))
    assert error.value.status_code == 504


def mock_http(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(myasset.httpx, 'AsyncClient',
                        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_fetch_uses_fixed_destination_and_selected_parameters(monkeypatch, tiny_html):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={'Content-Type': 'text/html; charset=UTF-8'}, content=page(tiny_html))
    mock_http(monkeypatch, handler)
    result = asyncio.run(myasset.fetch_myasset_source(date(2026, 9, 17), 1))
    assert str(seen[0].url) == ('https://www.myasset.com/myasset/research/aiNews/marketConditionPopup.cmd'
                                '?id=1&base_date=2026-09-17&gubun=1&cond=list')
    assert result['source_url'] == str(seen[0].url)
    assert result['base_date'] == '2026-09-17' and result['gubun'] == 1
    assert '코스피' in serialize_html(result['html'])


@pytest.mark.parametrize('status,content_type', [(302, 'text/html'), (500, 'text/html'), (200, 'application/json')])
def test_fetch_rejects_status_redirect_and_non_html(monkeypatch, status, content_type):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={'Content-Type': content_type, 'Location': 'https://example.com'}, content=b'{}')
    mock_http(monkeypatch, handler)
    with pytest.raises(myasset.MyassetError) as error:
        asyncio.run(myasset.fetch_myasset_source(date(2026, 9, 17), 30))
    assert error.value.status_code == 502
    assert len(calls) == 1


@pytest.mark.parametrize('exception,status', [(httpx.ConnectError, 502), (httpx.ReadTimeout, 504)])
def test_fetch_maps_network_failures(monkeypatch, exception, status):
    def handler(request):
        raise exception('network failure', request=request)
    mock_http(monkeypatch, handler)
    with pytest.raises(myasset.MyassetError) as error:
        asyncio.run(myasset.fetch_myasset_source(date(2026, 9, 17), 30))
    assert error.value.status_code == status


def test_stream_stops_at_size_limit(monkeypatch):
    consumed = []
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for i in range(10):
                consumed.append(i)
                yield b'x' * 6
    monkeypatch.setattr(myasset, 'MAX_INPUT_BYTES', 10)
    mock_http(monkeypatch, lambda request: httpx.Response(200, headers={'Content-Type': 'text/html'}, stream=Stream()))
    with pytest.raises(myasset.MyassetError) as error:
        asyncio.run(myasset.fetch_myasset_source(date(2026, 9, 17), 30))
    assert error.value.status_code == 413
    assert consumed == [0, 1]


def test_extraction_checks_size_after_html_serialization(monkeypatch, tiny_html):
    # An ampersand becomes &amp; when BeautifulSoup serializes the fragment.
    raw = page(tiny_html + '<p>' + '& ' * 1000 + '</p>')
    monkeypatch.setattr(myasset, 'MAX_INPUT_BYTES', len(raw) + 1)
    with pytest.raises(myasset.MyassetError) as error:
        myasset.extract_myasset_html(raw)
    assert error.value.status_code == 413
