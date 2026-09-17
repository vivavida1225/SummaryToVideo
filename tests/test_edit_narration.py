import asyncio

import pytest

from backend.config import Settings
from backend.jobs import JobManager
from tests.test_jobs import setup
from tests.test_api import client, auth


def test_edit_is_persisted_and_used_by_copy_download_and_reload(tmp_path, tiny_html, compressed):
    async def scenario():
        manager, clipboard, compressor = setup(tmp_path, compressed)
        created = manager.start(html=tiny_html)
        await manager.wait(created['id'])
        edited = '  직접 수정한 😀 대본\r\n두 번째 줄\n'
        result = manager.update_compressed(created['id'], edited, revision=0)
        expected = '  직접 수정한 😀 대본\n두 번째 줄\n'
        assert result['compressed'] == expected
        assert result['body_char_count'] == len(expected.replace('\n', ''))
        assert result['revision'] == 1 and result['edited_at']
        assert result['state'] == 'completed'
        assert manager.artifact(created['id'], 'compressed.txt') == expected
        await manager.copy_result(created['id'], 'compressed')
        assert clipboard.writes == [expected]
        # Preserve the generated artifact; API download and reload use the atomic edit snapshot.
        assert (tmp_path / result['output_dir'] / 'compressed.txt').read_text(encoding='utf-8') == compressed
        reloaded = JobManager(Settings(tmp_path)).get(created['id'])
        assert reloaded['compressed'] == expected and reloaded['revision'] == 1
        assert len(compressor.sources) == 1  # Manual editing never invokes generation.
    asyncio.run(scenario())


def test_empty_edit_survives_reload_without_restoring_old_draft(tmp_path, tiny_html, compressed):
    async def scenario():
        manager, clipboard, _ = setup(tmp_path, compressed)
        created = manager.start(html=tiny_html)
        await manager.wait(created['id'])
        manager.jobs[created['id']]['state'] = 'failed'
        manager.store.write_text(created['id'], 'invalid_response_1.txt', 'old draft')
        result = manager.update_compressed(created['id'], '', revision=0)
        assert result['body_char_count'] == 0
        assert manager.artifact(created['id'], 'compressed.txt') == ''
        await manager.copy_result(created['id'], 'compressed')
        assert clipboard.writes == ['']
        assert JobManager(Settings(tmp_path)).get(created['id'])['compressed'] == ''
    asyncio.run(scenario())


def test_failed_persistence_does_not_acknowledge_or_replace_saved_text(tmp_path, tiny_html, compressed, monkeypatch):
    async def scenario():
        manager, _, _ = setup(tmp_path, compressed)
        created = manager.start(html=tiny_html)
        await manager.wait(created['id'])
        def fail_edit(*args, **kwargs):
            raise OSError('disk full')
        monkeypatch.setattr(manager.store, 'save_edit', fail_edit)
        with pytest.raises(OSError):
            manager.update_compressed(created['id'], 'unsaved', revision=0)
        assert manager.get(created['id'])['compressed'] == compressed
        assert JobManager(Settings(tmp_path)).get(created['id'])['compressed'] == compressed
    asyncio.run(scenario())


def test_edit_endpoint_auth_conflict_and_latest_artifact(tmp_path, tiny_html, compressed):
    from backend.compression import Compressor
    from tests.test_compression import Transport
    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('instructions', encoding='utf-8')
    with client(tmp_path) as c:
        manager = c.app.state.manager
        manager.compressor_factory = lambda **options: Compressor(prompt, [(1, 'fake')], transport=Transport([compressed]))
        headers = auth(c)
        job_id = c.post('/api/jobs', headers=headers, json={'html': tiny_html}).json()['id']
        c.portal.call(manager.wait, job_id)
        path = f'/api/jobs/{job_id}/compressed'
        assert c.post(path, json={'text': 'new', 'revision': 0}).status_code == 403
        response = c.post(path, headers=headers, json={'text': '새 대본', 'revision': 0})
        assert response.status_code == 200
        assert response.json()['revision'] == 1
        conflict = c.post(path, headers=headers, json={'text': 'stale', 'revision': 0})
        assert conflict.status_code == 409 and isinstance(conflict.json()['detail'], str)
        assert c.get(f'/api/jobs/{job_id}/artifacts/compressed.txt', headers=headers).text == '새 대본'
        assert c.post(path, headers=headers, json={'text': '', 'revision': 1}).status_code == 200
        assert c.get(f'/api/jobs/{job_id}/artifacts/compressed.txt', headers=headers).text == ''
        manager.jobs[job_id]['state'] = 'requesting'
        assert c.post(path, headers=headers, json={'text': 'running', 'revision': 2}).status_code == 409


@pytest.mark.parametrize('payload', [
    {'text': 123, 'revision': 0}, {'revision': 0}, {'text': 'draft'},
    {'text': 'draft', 'revision': -1}, {'text': 'draft', 'revision': True},
    {'text': 'draft', 'revision': '0'}, {'text': 'draft', 'revision': 0, 'state': 'completed'},
])
def test_edit_payload_errors_use_string_detail(tmp_path, payload):
    with client(tmp_path) as c:
        response = c.post('/api/jobs/missing/compressed', headers=auth(c), json=payload)
        assert response.status_code == 422
        assert isinstance(response.json()['detail'], str)


def test_oversized_unicode_edit_does_not_mutate_saved_text(tmp_path, tiny_html, compressed, monkeypatch):
    async def scenario():
        manager, _, _ = setup(tmp_path, compressed)
        created = manager.start(html=tiny_html)
        await manager.wait(created['id'])
        monkeypatch.setattr('backend.jobs.MAX_INPUT_BYTES', 5)
        with pytest.raises(ValueError):
            manager.update_compressed(created['id'], '가나다', revision=0)
        assert manager.get(created['id'])['compressed'] == compressed
    asyncio.run(scenario())


def test_edit_preserves_generation_diagnostics_and_updates_live_counts(tmp_path, tiny_html, compressed):
    async def scenario():
        manager, _, _ = setup(tmp_path, compressed)
        created = manager.start(html=tiny_html)
        await manager.wait(created['id'])
        job = manager.jobs[created['id']]
        job.update(state='needs_review', validation_issues=['original diagnostic'])
        edited = manager.update_compressed(created['id'], '가' * 551, revision=0)
        assert edited['state'] == 'needs_review'
        assert edited['validation_issues'] == ['original diagnostic']
        assert edited['body_char_count'] == 551 and edited['excess_char_count'] == 1
        reduced = manager.update_compressed(created['id'], '가' * 500, revision=1)
        assert reduced['body_char_count'] == 500 and reduced['excess_char_count'] == 0
        assert reduced['revision'] == 2
    asyncio.run(scenario())


def test_atomic_edit_replace_failure_preserves_text_and_revision(tmp_path, tiny_html, compressed, monkeypatch):
    from pathlib import Path
    async def scenario():
        manager, _, _ = setup(tmp_path, compressed)
        created = manager.start(html=tiny_html)
        await manager.wait(created['id'])
        manager.update_compressed(created['id'], 'first saved edit', revision=0)
        replace = Path.replace
        def failed_replace(path, target):
            if Path(target).name == 'edited_compressed.json':
                raise OSError('persistent disk failure')
            return replace(path, target)
        monkeypatch.setattr(Path, 'replace', failed_replace)
        with pytest.raises(OSError):
            manager.update_compressed(created['id'], 'not committed', revision=1)
        assert manager.get(created['id'])['compressed'] == 'first saved edit'
        recovered = JobManager(Settings(tmp_path)).get(created['id'])
        assert recovered['compressed'] == 'first saved edit' and recovered['revision'] == 1
        assert not list((tmp_path / recovered['output_dir']).glob('*.tmp'))
    asyncio.run(scenario())
