"""InfoX adapter with retryable reads and explicitly one-shot generation writes.

Fixed origin, verified TLS, no redirects, no generic write method. Keep this
boundary separate from the local X-App-Token and future generation operations.
"""

import asyncio
import re
import ssl
from datetime import date, datetime
from typing import AsyncIterator
from urllib.parse import unquote, urlsplit
from urllib.parse import urlencode

import httpx
import truststore

from .config import InfoXCredentials
from .errors import InfoXError, check_status
from .sse import SSEEvent, parse_events
from .save_models import remote_version


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
                 media_transport: httpx.AsyncBaseTransport | None = None,
                 stream_auth: str = 'cookie', timeout: float = 30, stream_read_timeout: float = 45,
                 retry_delay: float = 0.25, connect_timeout: float = 15):
        if stream_auth not in ('cookie', 'bearer') or min(timeout, stream_read_timeout) <= 0 or retry_delay < 0:
            raise InfoXError('configuration_error')
        self._credentials = credentials
        self._media_transport = media_transport
        self._token = credentials.access_token
        if self._token and not _header_value(self._token):
            raise InfoXError('configuration_error')
        if credentials.device_token and not _header_value(credentials.device_token):
            raise InfoXError('configuration_error')
        self._auth_lock = asyncio.Lock()
        self._stream_auth = stream_auth
        self._stream_timeout = httpx.Timeout(timeout, read=stream_read_timeout)
        self._retry_delay = retry_delay
        self._http = httpx.AsyncClient(base_url=ORIGIN, timeout=httpx.Timeout(timeout, connect=connect_timeout), follow_redirects=False,
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
                         bearer: bool = True, expected_status: int | None = None) -> dict:
        try:
            response = await self._http.request(method, path, json=body, headers=self._headers(bearer=bearer))
        except httpx.TimeoutException:
            raise InfoXError('timeout') from None
        except httpx.HTTPError:
            raise InfoXError('network_error') from None
        check_status(response.status_code)
        if expected_status is not None and response.status_code != expected_status:
            raise InfoXError('invalid_response')
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

    async def get_presigned_download_url(self, bucket: str, key: str, download_filename: str) -> str:
        """Read endpoint returning a JSON string, unlike the ordinary object APIs."""
        await self.authenticate()
        path = '/infomaxai/auth/get_presigned_download_url?' + urlencode(
            {'bucket':bucket, 'key':key, 'download_filename':download_filename})
        refreshed = False
        failures = 0
        while True:
            rejected = self._token
            try:
                try:
                    response = await self._http.get(path, headers=self._headers())
                except httpx.TimeoutException:
                    raise InfoXError('timeout') from None
                except httpx.HTTPError:
                    raise InfoXError('network_error') from None
                check_status(response.status_code)
                try:
                    url = response.json()
                    self._validate_download_url(url, bucket, key)
                except (ValueError, TypeError):
                    raise InfoXError('invalid_response') from None
                return url
            except InfoXError as error:
                if error.code == 'auth_expired' and not refreshed:
                    await self._refresh_expired(rejected)
                    refreshed = True
                    continue
                if error.code not in RETRYABLE or failures == 2:
                    raise
                await asyncio.sleep(self._retry_delay * 2 ** failures)
                failures += 1

    @staticmethod
    def _validate_download_url(url, bucket, key):
        if not isinstance(url, str) or any(ord(c) <= 32 for c in url):
            raise InfoXError('invalid_response')
        try:
            parsed = urlsplit(url)
            expected = '/' + bucket + '/' + key
            if (parsed.scheme != 'https' or parsed.hostname != 'kr.object.iwinv.kr'
                    or parsed.port not in (None, 443) or parsed.username or parsed.password or parsed.fragment
                    or unquote(parsed.path) != expected or any(p in ('.', '..') for p in expected.split('/'))):
                raise ValueError('invalid media url')
        except (ValueError, TypeError):
            raise InfoXError('invalid_response') from None

    async def download_rendered_video(self, url, bucket, key, folder, filename) -> dict:
        """Stream the exact MP4 with an independent credential-free HTTP client."""
        from .downloads.files import async_writer, file_io
        self._validate_download_url(url, bucket, key)
        try:
            async with asyncio.timeout(1200):
                async with httpx.AsyncClient(verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
                        transport=self._media_transport, timeout=httpx.Timeout(30, connect=15),
                        follow_redirects=False) as media:
                    async with media.stream('GET', url, headers={'Accept-Encoding':'identity'}) as response:
                        check_status(response.status_code)
                        if response.status_code != 200 or response.headers.get('content-type','').split(';')[0] not in (
                                'video/mp4','application/octet-stream'):
                            raise InfoXError('invalid_media')
                        async with async_writer(folder, filename) as writer:
                            prefix = b''
                            async for chunk in response.aiter_bytes(chunk_size=65536):
                                if len(prefix) < 12:
                                    prefix = (prefix + chunk)[:12]
                                await file_io(writer.write, chunk)
                            length = response.headers.get('content-length')
                            if prefix[4:8] != b'ftyp' or (length is not None and (
                                    not length.isdigit() or writer.size != int(length))):
                                raise InfoXError('invalid_media')
                            return await file_io(writer.finish)
        except (TimeoutError, httpx.TimeoutException):
            raise InfoXError('timeout') from None
        except httpx.HTTPError:
            raise InfoXError('network_error') from None

    async def render_charge_preview(self, video_id: str) -> dict:
        return await self._read('GET', f'/infomaxai/worker/render_charge_preview/{_identifier(video_id)}')

    async def _publish_once(self, path: str, message: str) -> str:
        await self.authenticate()
        result = await self._json_once('GET', path, expected_status=200)
        if result.get('message') != message:
            raise InfoXError('invalid_response')
        try:
            return _identifier(result.get('taskid'))
        except InfoXError:
            raise InfoXError('invalid_response') from None

    async def start_render(self, video_id: str) -> str:
        return await self._publish_once(f'/infomaxai/worker/process_rendering/{_identifier(video_id)}', 'render')

    async def generate_youtube_metadata(self, video_id: str, render_task_id: str) -> str:
        return await self._publish_once('/infomaxai/worker/infox_generate_metainfo_youtube/'
            f'{_identifier(video_id)}/{_identifier(render_task_id)}', 'generate_metainfo_youtube')

    async def update_youtube_metadata(self, video_id: str, metadata_task_id: str, *,
                                     title: str, description: str, keywords: list[str]) -> dict:
        await self.authenticate()
        result = await self._json_once('PATCH', f'/infomaxai/videos/video_render_logs/{_identifier(video_id)}/sns_upload_info',
            body={'task_id': _identifier(metadata_task_id), 'title': title, 'description': description, 'keywords': keywords},
            expected_status=200)
        # The exact success body was not retained; require HTTP acknowledgement
        # and a matching history readback before any upload (manager contract).
        if (result.get('error') or result.get('success') is False or not result
                or ('taskid' in result and result['taskid'] != metadata_task_id)):
            raise InfoXError('invalid_response')
        return {}  # Never return/persist raw remote responses.

    async def upload_youtube(self, video_id: str, metadata_task_id: str) -> str:
        video_id = _identifier(video_id)
        query = urlencode({'redirect_url': f'{ORIGIN}/studio/library/videos/{video_id}?chatboxView=script',
                           'privacy_status': 'unlisted'})
        return await self._publish_once('/infomaxai/worker/infox_upload_youtube_video/'
            f'{video_id}/{_identifier(metadata_task_id)}?{query}', 'upload_youtube_video')

    async def save_video_context(self, video_id: str, body: dict) -> str:
        """One attempt only. Caller MUST commit the save intent before entering."""
        try:
            if (set(body) != {'video', 'contextData', 'updatedAt'} or not isinstance(body['video'], dict)
                    or (body['contextData'] is not None and not isinstance(body['contextData'], dict))):
                raise ValueError('invalid body')
            remote_version(body['updatedAt'])
        except (TypeError, ValueError, KeyError):
            raise InfoXError('configuration_error') from None
        await self.authenticate()
        result = await self._json_once('POST', f'/infomaxai/context/update_video_context/{_identifier(video_id)}', body=body,
                                       expected_status=200)
        try:
            version = remote_version(result.get('updated_at'))
            if (result.get('status') != 'Video updated'
                    or datetime.fromisoformat(version) <= datetime.fromisoformat(body['updatedAt'])):
                raise ValueError('invalid acknowledgement')
            return version
        except (TypeError, ValueError):
            raise InfoXError('invalid_response') from None

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

    async def favorite_segment_available(self, item: dict) -> bool:
        """Read at most 32 decoded bytes from the exact selected clip, without credentials.

        The current favorites response uses this single object-storage host.
        Never follow redirects or send the API bearer/device/cookies there.
        """
        try:
            url = item['segment_url_presigned']
            parsed = urlsplit(url)
            expected = '/' + item['bucket'] + '/' + item['segment']
            if (parsed.scheme != 'https' or parsed.hostname != 'kr.object.iwinv.kr'
                    or parsed.port not in (None, 443) or parsed.username or parsed.password
                    or parsed.fragment or unquote(parsed.path) != expected
                    or any(part in ('.', '..') for part in unquote(parsed.path).split('/'))):
                return False
            async with httpx.AsyncClient(timeout=10, follow_redirects=False,
                    verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), transport=self._media_transport) as media:
                async with media.stream('GET', url, headers={'Range': 'bytes=0-31',
                        'Accept': 'video/mp4', 'Accept-Encoding': 'identity'}) as response:
                    if (response.status_code not in (200, 206) or
                            response.headers.get('content-type', '').split(';')[0].lower() not in ('video/mp4', 'application/octet-stream')
                            or response.headers.get('content-encoding', 'identity') != 'identity'):
                        return False
                    async for chunk in response.aiter_bytes(chunk_size=32):
                        return len(chunk) >= 8 and chunk[4:8] == b'ftyp'
        except (KeyError, TypeError, ValueError, httpx.HTTPError):
            return False
        return False

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
