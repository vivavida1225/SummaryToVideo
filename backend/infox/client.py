"""InfoX adapter with retryable reads and explicitly one-shot generation writes.

Fixed origin, verified TLS, no redirects, no generic write method. Keep this
boundary separate from the local X-App-Token and future generation operations.
"""

import asyncio
import re
import ssl
from datetime import date
from typing import AsyncIterator

import httpx
import truststore

from .config import InfoXCredentials
from .errors import InfoXError, check_status
from .sse import SSEEvent, parse_events


ORIGIN = 'https://infox2.infomaxai.com'
RETRYABLE = {'network_error', 'timeout', 'rate_limited', 'server_error'}


def _header_value(value: str) -> bool:
    return bool(value) and all(32 < ord(char) < 127 for char in value)


def _identifier(value: int | str) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise InfoXError('configuration_error')
    text = str(value)
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', text) or text == '0' or text.startswith('-'):
        raise InfoXError('configuration_error')
    return text


class InfoXClient:
    def __init__(self, credentials: InfoXCredentials, *, transport: httpx.AsyncBaseTransport | None = None,
                 stream_auth: str = 'cookie', timeout: float = 30, stream_read_timeout: float = 45,
                 retry_delay: float = 0.25):
        if stream_auth not in ('cookie', 'bearer') or min(timeout, stream_read_timeout) <= 0 or retry_delay < 0:
            raise InfoXError('configuration_error')
        self._credentials = credentials
        self._token = credentials.access_token
        if self._token and not _header_value(self._token):
            raise InfoXError('configuration_error')
        if credentials.device_token and not _header_value(credentials.device_token):
            raise InfoXError('configuration_error')
        self._auth_lock = asyncio.Lock()
        self._stream_auth = stream_auth
        self._stream_timeout = httpx.Timeout(timeout, read=stream_read_timeout)
        self._retry_delay = retry_delay
        self._http = httpx.AsyncClient(base_url=ORIGIN, timeout=timeout, follow_redirects=False,
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), transport=transport)

    async def __aenter__(self) -> 'InfoXClient':
        await self._http.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self._http.__aexit__(*args)
        self._token = ''
        self._http.cookies.clear()

    def _headers(self, *, bearer: bool = True) -> dict[str, str]:
        headers = {'Accept': 'application/json'}
        if bearer and self._token:
            headers['Authorization'] = f'Bearer {self._token}'
        if self._credentials.device_token:
            headers['X-Org-Device-Token'] = self._credentials.device_token
        return headers

    async def _json_once(self, method: str, path: str, *, body: dict | None = None,
                         bearer: bool = True) -> dict:
        try:
            response = await self._http.request(method, path, json=body, headers=self._headers(bearer=bearer))
        except httpx.TimeoutException:
            raise InfoXError('timeout') from None
        except httpx.HTTPError:
            raise InfoXError('network_error') from None
        check_status(response.status_code)
        try:
            data = response.json()
        except (ValueError, UnicodeError, RecursionError):
            raise InfoXError('invalid_response') from None
        if not isinstance(data, dict):
            raise InfoXError('invalid_response')
        return data

    def _set_token(self, result: dict) -> None:
        token = result.get('access_token')
        if (not isinstance(token, str) or not _header_value(token)
                or str(result.get('token_type', '')).lower() != 'bearer'):
            raise InfoXError('invalid_response')
        self._token = token

    async def authenticate(self) -> None:
        """Reuse a configured token or login once; session secrets never leave this object."""
        async with self._auth_lock:
            if self._token:
                return
            c = self._credentials
            if c.email and c.password:
                result = await self._json_once('POST', '/infomaxai/auth/login',
                                               body={'email': c.email, 'password': c.password}, bearer=False)
            elif c.device_token:
                result = await self._json_once('POST', '/infomaxai/auth/org-email/device-refresh',
                                               body={'device_token': c.device_token}, bearer=False)
            else:
                raise InfoXError('configuration_error')
            self._set_token(result)

    async def _refresh_locked(self) -> None:
        try:
            result = await self._json_once('GET', '/infomaxai/auth/refresh-token')
        except InfoXError as error:
            if error.code not in ('auth_expired', 'permission_denied'):
                raise
            self._token = ''
            if not self._credentials.device_token:
                raise
            result = await self._json_once('POST', '/infomaxai/auth/org-email/device-refresh',
                body={'device_token': self._credentials.device_token}, bearer=False)
        self._set_token(result)

    async def refresh(self) -> None:
        await self.authenticate()
        async with self._auth_lock:
            await self._refresh_locked()

    async def _refresh_expired(self, rejected_token: str) -> None:
        async with self._auth_lock:
            if self._token == rejected_token:
                await self._refresh_locked()

    async def _read(self, method: str, path: str, *, body: dict | None = None) -> dict:
        # Only these allowlisted read methods call this, including the search POST.
        await self.authenticate()
        refreshed = False
        transient_failures = 0
        while True:
            rejected_token = self._token
            try:
                return await self._json_once(method, path, body=body)
            except InfoXError as error:
                if error.code == 'auth_expired' and not refreshed:
                    # Refresh errors propagate, rather than entering the read retry policy.
                    await self._refresh_expired(rejected_token)
                    refreshed = True
                    continue
                if error.code not in RETRYABLE or transient_failures == 2:
                    raise
                await asyncio.sleep(self._retry_delay * (2 ** transient_failures))
                transient_failures += 1

    async def video_state(self, video_id: int | str) -> dict:
        video_id = _identifier(video_id)
        result = await self._read('GET', f'/infomaxai/videos/videostate/{video_id}')
        if str(result.get('id')) != video_id or not isinstance(result.get('video'), dict):
            raise InfoXError('invalid_response')
        return result

    async def create_project(self, body: dict) -> str:
        # Caller must durably claim the intent first. Never retry, even on 401.
        await self.authenticate()
        result = await self._json_once('POST', '/infomaxai/videos/create_video_new', body=body)
        try:
            return _identifier(result.get('id'))
        except InfoXError:
            raise InfoXError('invalid_response') from None

    async def start_generation(self, video_id: str) -> str:
        # This GET has side effects and MUST NOT pass through _read().
        await self.authenticate()
        result = await self._json_once('GET', f'/infomaxai/worker/infox_generate_type_text/{_identifier(video_id)}')
        if result.get('message') != 'generate_type_text':
            raise InfoXError('invalid_response')
        try:
            return _identifier(result.get('taskid'))
        except InfoXError:
            raise InfoXError('invalid_response') from None

    async def render_logs(self, video_id: str) -> dict:
        return await self._read('GET', f'/infomaxai/videos/video_render_logs/{_identifier(video_id)}')

    async def search_favorites(self, *, search: str = '반도체', source: str = 'video',
                               skip: int = 0, limit: int = 50, date_from: str | None = None) -> dict:
        if (source not in ('video', 'uservideo') or not isinstance(search, str) or not search.strip()
                or type(skip) is not int or skip < 0 or type(limit) is not int or not 1 <= limit <= 50):
            raise InfoXError('configuration_error')
        body = {'skip': skip, 'limit': limit, 'search': search, 'include_politics': False,
                'sort_by': 'relevance', 'search_mode': 'relevance', 'favorites_only': True}
        if date_from is not None:
            try:
                body['date_from'] = date.fromisoformat(date_from).isoformat()
            except (ValueError, TypeError):
                raise InfoXError('configuration_error') from None
        result = await self._read('POST', f'/infomaxai/recommand/{source}', body=body)
        if (not isinstance(result.get('items'), list)
                or any(type(result.get(key)) is not int or result[key] < 0 for key in ('total', 'skip', 'limit'))
                or any(not isinstance(item, dict) or item.get('is_favorite') is not True for item in result['items'])):
            raise InfoXError('invalid_response')
        return result

    async def events(self, video_id: int | str, *, last_event_id: str = '',
                     max_event_bytes: int = 1024 * 1024) -> AsyncIterator[SSEEvent]:
        """Yield raw frames. No reconnect loop and no inference about generation completion.

        Cookie mode matches the observed browser EventSource. Bearer mode is an
        explicit deployment option. A caller must decide how to resume after EOF.
        """
        video_id = _identifier(video_id)
        if last_event_id and not _header_value(last_event_id):
            raise InfoXError('configuration_error')
        bearer = self._stream_auth == 'bearer'
        if bearer:
            await self.authenticate()
        for attempt in range(2):
            headers = self._headers() if bearer else {}
            headers['Accept'] = 'text/event-stream'
            if last_event_id:
                headers['Last-Event-ID'] = last_event_id
            rejected_token = self._token
            try:
                async with self._http.stream('GET', '/api/stream', params={'channel': f'infox:{video_id}'},
                    headers=headers, timeout=self._stream_timeout) as response:
                    if response.status_code == 401 and bearer and attempt == 0:
                        await self._refresh_expired(rejected_token)
                        continue
                    check_status(response.status_code)
                    if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'text/event-stream':
                        raise InfoXError('invalid_response')
                    async for event in parse_events(response.aiter_bytes(), last_event_id=last_event_id,
                                                     max_event_bytes=max_event_bytes):
                        yield event
                    return
            except httpx.TimeoutException:
                raise InfoXError('timeout') from None
            except httpx.HTTPError:
                raise InfoXError('network_error') from None
