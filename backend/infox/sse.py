"""SSE framing only. Task success/failure interpretation belongs to phase 3."""

import codecs
import json
import re
from dataclasses import dataclass, field
from typing import AsyncIterator

from .errors import InfoXError


@dataclass(frozen=True)
class SSEEvent:
    data: str = field(repr=False)
    event: str = field(default='message', repr=False)
    id: str = field(default='', repr=False)
    retry_ms: int | None = None

    def json(self):
        try:
            return json.loads(self.data)
        except (ValueError, RecursionError):
            raise InfoXError('invalid_event_json') from None


async def parse_events(chunks: AsyncIterator[bytes], *, last_event_id: str = '',
                       max_event_bytes: int = 1024 * 1024) -> AsyncIterator[SSEEvent]:
    if type(max_event_bytes) is not int or max_event_bytes < 1:
        raise InfoXError('configuration_error')
    decoder = codecs.getincrementaldecoder('utf-8-sig')('strict')
    pending = ''
    data: list[str] = []
    event_name, event_id = '', last_event_id
    retry_ms = None
    frame_bytes = 0
    skip_lf = False
    try:
        async for chunk in chunks:
            pending += decoder.decode(chunk)
            while pending:
                if skip_lf:
                    if pending.startswith('\n'):
                        pending = pending[1:]
                    skip_lf = False
                match = re.search(r'[\r\n]', pending)
                if match is None:
                    break
                pos = match.start()
                skip_lf = pending[pos] == '\r'
                line, pending = pending[:pos], pending[pos + 1:]
                # Normalize CR/LF/CRLF as one line ending, including split CRLF.
                frame_bytes += len(line.encode('utf-8')) + 1
                if frame_bytes > max_event_bytes:
                    raise InfoXError('invalid_response')
                if not line:
                    if data:
                        yield SSEEvent(data='\n'.join(data), event=event_name or 'message',
                                       id=event_id, retry_ms=retry_ms)
                    data, event_name, retry_ms, frame_bytes = [], '', None, 0
                    continue
                if line.startswith(':'):
                    continue
                name, _, value = line.partition(':')
                value = value[1:] if value.startswith(' ') else value
                if name == 'data':
                    data.append(value)
                elif name == 'event':
                    event_name = value
                elif name == 'id' and '\x00' not in value:
                    event_id = value
                elif name == 'retry' and re.fullmatch(r'[0-9]{1,10}', value):
                    retry_ms = int(value)
            if frame_bytes + len(pending.encode('utf-8')) > max_event_bytes:
                raise InfoXError('invalid_response')
        decoder.decode(b'', final=True)
    except UnicodeError:
        raise InfoXError('invalid_response') from None
