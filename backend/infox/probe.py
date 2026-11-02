"""Bounded, read-only connection check. Print structure/counts, never payloads."""

import argparse
import asyncio
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import dotenv_values

from ..config import ROOT
from .client import InfoXClient
from .config import InfoXCredentials
from .errors import InfoXError


@dataclass(frozen=True)
class ProbeOptions:
    video_id: str = field(repr=False)
    sse_timeout: float = 15
    date_from: str | None = None


def _state_shape(state: dict) -> dict:
    return {f'{key}_type': type(state.get(key)).__name__
            for key in ('id', 'video', 'script', 'can_edit', 'updated_at')}


async def probe(client: InfoXClient, options: ProbeOptions) -> dict:
    report = {'ok': False, 'verified_at_kst': datetime.now(timezone(timedelta(hours=9))).isoformat(),
              'checks': {}}
    checks = report['checks']
    step = 'configuration'
    try:
        if not math.isfinite(options.sse_timeout) or not 0 < options.sse_timeout <= 60:
            raise InfoXError('configuration_error')
        step = 'authenticate'
        await client.authenticate()
        checks[step] = 'ok'
        step = 'video_state'
        state = await client.video_state(options.video_id)
        checks[step] = _state_shape(state)
        step = 'refresh'
        await client.refresh()
        checks[step] = 'ok'
        step = 'video_state_after_refresh'
        state = await client.video_state(options.video_id)
        checks[step] = _state_shape(state)
        checks['favorites'] = {}
        for source in ('video', 'uservideo'):
            step = f'favorites_{source}'
            result = await client.search_favorites(source=source, date_from=options.date_from)
            checks['favorites'][source] = {'total': result['total'], 'received': len(result['items']),
                'skip': result['skip'], 'limit': result['limit'], 'favorites_only': True,
                'more_pages': result['skip'] + len(result['items']) < result['total']}
        step = 'sse'
        # Explicitly close the generator on the first body and on cancellation.
        stream = client.events(options.video_id)
        try:
            async with asyncio.timeout(options.sse_timeout):
                async for event in stream:
                    if not event.data.strip():
                        continue
                    body = event.json()
                    if not isinstance(body, dict):
                        raise InfoXError('invalid_event_json')
                    connection = body.get('type') == 'connection'
                    if connection and (body.get('channel') != f'infox:{options.video_id}'
                                       or body.get('status') != 'connected'):
                        raise InfoXError('invalid_response')
                    checks[step] = {'body_received': True, 'json_type': 'dict',
                        'connection_confirmed': connection,
                        'known_fields': [key for key in ('type', 'status', 'channel', 'id',
                            'additional_kwargs', 'data', 'message') if key in body]}
                    break
                else:
                    raise InfoXError('event_missing')
        finally:
            await stream.aclose()
        report['ok'] = True
    except InfoXError as error:
        report.update(failed_step=step, error={'code': error.code, 'status_code': error.status_code})
    except TimeoutError:
        report.update(failed_step=step, error={'code': 'event_timeout', 'status_code': None})
    return report


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description='InfoX phase 1 read-only connectivity check (no generation).')
    parser.add_argument('--video-id', help='Existing video ID; defaults to backend INFOX_VIDEO_ID.')
    parser.add_argument('--sse-timeout', type=float, default=15, help='Seconds, >0 and <=60.')
    parser.add_argument('--date-from', help='Optional search start date, YYYY-MM-DD; omitted by default.')
    parser.add_argument('--stream-auth', choices=('cookie', 'bearer'), default='cookie')
    args = parser.parse_args(argv)
    values = dotenv_values(root / '.env', encoding='utf-8-sig', interpolate=False)
    video_id = args.video_id or os.environ.get('INFOX_VIDEO_ID', values.get('INFOX_VIDEO_ID') or '').strip()
    if not video_id:
        print(json.dumps({'ok': False, 'failed_step': 'configuration',
                          'error': {'code': 'configuration_error', 'status_code': None}}))
        return 2

    async def execute():
        async with InfoXClient(InfoXCredentials.load(root), stream_auth=args.stream_auth) as client:
            return await probe(client, ProbeOptions(video_id=video_id, sse_timeout=args.sse_timeout,
                                                   date_from=args.date_from))

    try:
        report = asyncio.run(execute())
    except InfoXError as error:
        report = {'ok': False, 'failed_step': 'configuration',
                  'error': {'code': error.code, 'status_code': error.status_code}}
    except KeyboardInterrupt:
        return 130
    except Exception:
        # The CLI must not print an unfiltered dependency exception or traceback.
        report = {'ok': False, 'failed_step': 'internal',
                  'error': {'code': 'internal_error', 'status_code': None}}
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report['ok'] else 1
