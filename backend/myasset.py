"""Fetch and validate the fixed myasset market-news source."""

import asyncio
from datetime import date

import httpx
from bs4 import BeautifulSoup

from .config import MAX_INPUT_BYTES
from .serializer import SerializationError, serialize_html


SOURCE_URL = 'https://www.myasset.com/myasset/research/aiNews/marketConditionPopup.cmd'
FETCH_DEADLINE_SECONDS = 30
STRUCTURE_ERROR = '원문 페이지 구조를 확인할 수 없습니다. HTML을 직접 붙여넣거나 잠시 후 다시 시도하세요.'


class MyassetError(ValueError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def check_size(size: int) -> None:
    if size > MAX_INPUT_BYTES:
        raise MyassetError(413, 'myasset 원문은 5 MiB 이하만 지원합니다.')


def extract_myasset_html(document: bytes) -> str:
    check_size(len(document))
    try:
        soup = BeautifulSoup(document.decode('utf-8-sig'), 'html.parser')
    except UnicodeError:
        raise MyassetError(502, STRUCTURE_ERROR) from None
    containers = soup.select('div.contWrap')
    if len(containers) != 1:
        raise MyassetError(502, STRUCTURE_ERROR)
    container = containers[0]
    news = container.select('.embedded-content')
    if not news:
        raise MyassetError(404, '선택한 날짜와 gubun의 게시글 본문을 찾을 수 없습니다. 설정을 확인하거나 게시 후 다시 시도하세요.')
    if len(news) != 1:
        raise MyassetError(502, STRUCTURE_ERROR)
    fragment = container.decode_contents()
    check_size(len(fragment.encode('utf-8')))
    try:
        serialize_html(fragment)
    except SerializationError:
        raise MyassetError(502, STRUCTURE_ERROR) from None
    return fragment


async def fetch_myasset_source(base_date: date, gubun: int) -> dict:
    params = {'id': 1, 'base_date': base_date.isoformat(), 'gubun': gubun, 'cond': 'list'}
    try:
        async with asyncio.timeout(FETCH_DEADLINE_SECONDS):
            async with httpx.AsyncClient(timeout=httpx.Timeout(20, connect=5), follow_redirects=False) as client:
                async with client.stream('GET', SOURCE_URL, params=params) as response:
                    if response.status_code != 200:
                        raise MyassetError(502, 'myasset 원문을 불러오지 못했습니다. 잠시 후 다시 시도하세요.')
                    content_type = response.headers.get('content-type', '').split(';', 1)[0].strip().lower()
                    if content_type not in ('text/html', 'application/xhtml+xml'):
                        raise MyassetError(502, STRUCTURE_ERROR)
                    document = bytearray()
                    async for chunk in response.aiter_bytes():
                        check_size(len(document) + len(chunk))
                        document.extend(chunk)
                    # Parsing runs off the event loop so other local API calls remain responsive.
                    html = await asyncio.to_thread(extract_myasset_html, bytes(document))
                    return {'html': html, 'source_url': str(response.url),
                            'base_date': base_date.isoformat(), 'gubun': gubun}
    except (TimeoutError, httpx.TimeoutException):
        raise MyassetError(504, 'myasset 응답 시간이 초과되었습니다. 다시 시도하세요.') from None
    except httpx.RequestError:
        raise MyassetError(502, 'myasset 원문을 불러오지 못했습니다. 잠시 후 다시 시도하세요.') from None
