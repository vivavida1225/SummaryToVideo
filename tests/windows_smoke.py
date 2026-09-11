"""Explicit launcher lifecycle test. Does not call Gemini or open a browser."""

import json
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]


def launch(*arguments):
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(ROOT / 'scripts/launch.ps1'), '-NoBrowser', '-NoDialog', *arguments],
            cwd=ROOT, stdout=stdout, stderr=stderr, timeout=180)
        if result.returncode:
            stdout.seek(0)
            stderr.seek(0)
            raise AssertionError(stdout.read().decode('utf-8', errors='replace') + stderr.read().decode('utf-8', errors='replace'))


def main():
    runtime = ROOT / '.runtime/server.json'
    if runtime.exists():
        raise SystemExit('Stop the current app with stop.cmd before this lifecycle test.')
    launch()
    first = json.loads(runtime.read_text(encoding='utf-8-sig'))
    health = httpx.get(f"http://127.0.0.1:{first['port']}/api/health", trust_env=False).json()
    assert health['instance_id'] == first['instance_id']
    launch()
    assert json.loads(runtime.read_text(encoding='utf-8-sig'))['instance_id'] == first['instance_id']
    launch('-Stop')
    assert not runtime.exists()
    with socket.socket() as blocker:
        blocker.bind(('127.0.0.1', 8765))
        blocker.listen()
        launch()
        second = json.loads(runtime.read_text(encoding='utf-8-sig'))
        assert 8766 <= second['port'] <= 8775
        launch('-Stop')
    assert not runtime.exists()
    print('launcher startup / duplicate reuse / graceful stop / occupied port: PASS')


if __name__ == '__main__':
    main()
