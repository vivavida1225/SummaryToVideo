"""Explicit live integration check; excluded from pytest discovery.

Uses the configured API and saves results without changing the clipboard.
Run from project root: .venv/Scripts/python.exe -m tests.live_smoke
"""

import asyncio
import json
import sys
from pathlib import Path

from backend.config import Settings
from backend.jobs import JobManager


async def main():
    manager = JobManager(Settings())
    job = manager.start(file_path='0910/0910_html.txt')
    previous = None
    while True:
        current = manager.get(job['id'])
        marker = (current['state'], current['attempt'], current['key_number'])
        if marker != previous:
            print(json.dumps({'state': marker[0], 'attempt': marker[1], 'key_number': marker[2]}, ensure_ascii=False), flush=True)
            previous = marker
        if current['state'] in ('completed', 'failed', 'needs_review'):
            break
        await asyncio.sleep(1)
    Path('.runtime').mkdir(exist_ok=True)
    Path('.runtime/live-smoke.json').write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key: current[key] for key in ('id', 'state', 'attempt', 'error', 'warnings', 'output_dir')}, ensure_ascii=False), flush=True)
    await manager.close()
    return 0 if current['state'] == 'completed' else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(asyncio.run(main()))
