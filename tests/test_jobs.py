import asyncio
import json

import pytest

from backend.jobs import JobManager, JobConflict
from backend.config import Settings
from backend.validation import validate_compressed


class Clipboard:
    def __init__(self, fail=False):
        self.writes = []
        self.fail = fail

    def write(self, text):
        if self.fail:
            raise OSError('clipboard busy')
        self.writes.append(text)


class Compressor:
    def __init__(self, response, clipboard):
        self.response, self.clipboard, self.sources = response, clipboard, []

    async def run(self, serialized, emit, save_invalid):
        self.sources.append(serialized)
        emit('requesting', attempt=1, key_number=1, message='request')
        if isinstance(self.response, Exception):
            raise self.response
        return validate_compressed(self.response, serialized)


def setup(tmp_path, response, clipboard=None):
    settings = Settings(tmp_path)
    (tmp_path / 'src').mkdir()
    clipboard = clipboard or Clipboard()
    compressor = Compressor(response, clipboard)
    manager = JobManager(settings, clipboard=clipboard, compressor_factory=lambda: compressor)
    return manager, clipboard, compressor


def test_automatic_save_copy_and_compression_order(tmp_path, tiny_html, compressed):
    async def scenario():
        manager, clipboard, compressor = setup(tmp_path, compressed)
        job = manager.start(html=tiny_html)
        await manager.wait(job['id'])
        job = manager.get(job['id'])
        assert job['state'] == 'completed'
        assert clipboard.writes == [job['serialized'], job['compressed']]
        assert compressor.sources == [job['serialized']]
        folder = tmp_path / job['output_dir']
        assert (folder / 'serialized.txt').read_text(encoding='utf-8') == job['serialized']
        assert (folder / 'compressed.txt').read_text(encoding='utf-8') == job['compressed']
        assert json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))['state'] == 'completed'
        restored = JobManager(manager.settings, clipboard=clipboard)
        assert restored.get(job['id'])['compressed'] == compressed
        assert restored.artifact(job['id'], 'compressed.txt') == compressed
        await restored.copy_result(job['id'], 'compressed')
        assert clipboard.writes[-1] == compressed
    asyncio.run(scenario())


def test_failure_preserves_serialized_and_retry_uses_snapshot(tmp_path, tiny_html, compressed):
    async def scenario():
        manager, clipboard, compressor = setup(tmp_path, ValueError('API failed'))
        (tmp_path / 'src/input.txt').write_text(tiny_html, encoding='utf-8')
        first = manager.start(file_path='input.txt')
        await manager.wait(first['id'])
        first = manager.get(first['id'])
        assert first['state'] == 'failed' and first['serialized']
        assert clipboard.writes == [first['serialized']]
        (tmp_path / 'src/input.txt').write_text('changed after first attempt', encoding='utf-8')
        compressor.response = compressed
        second = manager.retry(first['id'])
        await manager.wait(second['id'])
        second = manager.get(second['id'])
        assert second['state'] == 'completed'
        assert second['id'] != first['id'] and second['parent_id'] == first['id']
        assert compressor.sources == [first['serialized'], first['serialized']]
    asyncio.run(scenario())


def test_clipboard_failure_warns_but_still_produces_final_result(tmp_path, tiny_html, compressed):
    async def scenario():
        manager, _, _ = setup(tmp_path, compressed, Clipboard(fail=True))
        job = manager.start(html=tiny_html)
        await manager.wait(job['id'])
        result = manager.get(job['id'])
        assert result['state'] == 'completed' and result['compressed']
        assert any('클립보드' in s for s in result['warnings'])
    asyncio.run(scenario())


def test_duplicate_start_rejected(tmp_path, tiny_html, compressed):
    async def scenario():
        manager, _, _ = setup(tmp_path, compressed)
        job = manager.start(html=tiny_html)
        with pytest.raises(JobConflict):
            manager.start(html=tiny_html)
        await manager.wait(job['id'])
    asyncio.run(scenario())


def test_save_failure_stops_api_but_keeps_downloadable_text(tmp_path, tiny_html, compressed, monkeypatch):
    async def scenario():
        manager, _, compressor = setup(tmp_path, compressed)
        original = manager.store.write_text
        def fail(job_id, name, text):
            if name == 'serialized.txt':
                raise OSError('disk full')
            original(job_id, name, text)
        monkeypatch.setattr(manager.store, 'write_text', fail)
        job = manager.start(html=tiny_html)
        await manager.wait(job['id'])
        result = manager.get(job['id'])
        assert result['state'] == 'failed' and result['serialized']
        assert not compressor.sources
        assert manager.artifact(job['id'], 'serialized.txt') == result['serialized']
    asyncio.run(scenario())


def test_invalid_input_never_calls_api(tmp_path, compressed):
    async def scenario():
        manager, clipboard, compressor = setup(tmp_path, compressed)
        job = manager.start(html='not HTML')
        await manager.wait(job['id'])
        assert manager.get(job['id'])['state'] == 'failed'
        assert not compressor.sources and not clipboard.writes
    asyncio.run(scenario())


def test_unwritable_output_root_still_generates_downloadable_serialization(tmp_path, tiny_html, compressed):
    async def scenario():
        manager, _, compressor = setup(tmp_path, compressed)
        (tmp_path / 'outputs').write_text('blocks directory creation', encoding='utf-8')
        job = manager.start(html=tiny_html)
        await manager.wait(job['id'])
        result = manager.get(job['id'])
        assert result['state'] == 'failed'
        assert result['serialized'] and manager.artifact(job['id'], 'serialized.txt').startswith('<1>')
        assert not compressor.sources
    asyncio.run(scenario())
