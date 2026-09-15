from fastapi.testclient import TestClient

from backend.app import create_app
from backend.config import Settings


class Clipboard:
    def __init__(self):
        self.reads = 0
    def read(self):
        self.reads += 1
        return {'text': '<div>sample</div>', 'format': 'text'}
    def write(self, _):
        pass


def client(tmp_path):
    return TestClient(create_app(Settings(tmp_path), clipboard=Clipboard()), base_url='http://127.0.0.1:8765')


def auth(c):
    return {'X-App-Token': c.get('/api/session').json()['token']}


def test_session_catalog_and_job_model_validation(tmp_path):
    with client(tmp_path) as c:
        session = c.get('/api/session').json()
        assert session['model'] == 'gemini-3.8-flash'
        assert [m['id'] for m in session['models']] == [
            'gemini-3.8-flash', 'gemini-3.7-flash', 'gemini-3.6-flash', 'gemini-3.5-flash-lite']
        headers = auth(c)
        rejected = c.post('/api/jobs', headers=headers, json={'html': 'invalid', 'model': 'unknown'})
        assert rejected.status_code == 422 and '모델' in rejected.json()['detail']
        chosen = c.post('/api/jobs', headers=headers, json={'html': 'invalid', 'model': 'gemini-3.7-flash'})
        assert chosen.status_code == 202
        assert chosen.json()['requested_model'] == 'gemini-3.7-flash'
        c.portal.call(c.app.state.manager.wait, chosen.json()['id'])
        default = c.post('/api/jobs', headers=headers, json={'html': 'invalid'})
        assert default.json()['requested_model'] == 'gemini-3.8-flash'


def test_retry_model_and_original_source_reach_transport(tmp_path, tiny_html, compressed, monkeypatch):
    from backend.gemini import GeminiTransport, ProviderError
    calls = []

    async def generate(self, **kwargs):
        calls.append(kwargs)
        if len(calls) <= 3:
            raise ProviderError('quota', retryable=True)
        return compressed

    monkeypatch.setattr(GeminiTransport, 'generate', generate)
    (tmp_path / '.env').write_text('GEMINI_API_KEY_1=fake', encoding='utf-8')
    (tmp_path / 'prompts').mkdir()
    (tmp_path / 'prompts/compress_to_1min.txt').write_text('instructions', encoding='utf-8')
    with client(tmp_path) as c:
        headers = auth(c)
        first = c.post('/api/jobs', headers=headers, json={'html': tiny_html, 'model': 'gemini-3.7-flash'}).json()
        manager = c.app.state.manager
        c.portal.call(manager.wait, first['id'])
        assert manager.get(first['id'])['state'] == 'failed'
        rejected = c.post(f"/api/jobs/{first['id']}/retry", headers=headers, json={'model': 'unknown'})
        assert rejected.status_code == 422 and '모델' in rejected.json()['detail']
        retry = c.post(f"/api/jobs/{first['id']}/retry", headers=headers, json={'model': 'gemini-3.6-flash'}).json()
        c.portal.call(manager.wait, retry['id'])
        assert calls[-1]['model'] == 'gemini-3.6-flash'
        assert calls[-1]['source'] == calls[0]['source']
        assert manager.get(retry['id'])['state'] == 'completed'
        legacy_retry = c.post(f"/api/jobs/{first['id']}/retry", headers=headers).json()
        c.portal.call(manager.wait, legacy_retry['id'])
        assert calls[-1]['model'] == 'gemini-3.7-flash'


def test_clipboard_is_not_read_on_session_and_requires_token(tmp_path):
    with client(tmp_path) as c:
        headers = auth(c)
        assert c.app.state.manager.clipboard.reads == 0
        assert c.post('/api/clipboard/read').status_code == 403
        assert c.post('/api/clipboard/read', headers=headers).json()['text'] == '<div>sample</div>'


def test_cross_origin_and_rebinding_hosts_cannot_read_session(tmp_path):
    with client(tmp_path) as c:
        assert c.get('/api/session', headers={'Origin': 'https://evil.example'}).status_code == 403
        assert c.get('/api/session', headers={'Host': 'evil.example:8765'}).status_code == 403
        assert c.get('/api/session', headers={'Sec-Fetch-Site': 'cross-site'}).status_code == 403
        assert 'token' not in c.get('/api/health').json()


def test_file_listing_excludes_secrets_and_paths_are_confined(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/한글.txt').write_text('hi', encoding='utf-8')
    (tmp_path / '.env').write_text('GEMINI_API_KEY_1=secret-value', encoding='utf-8')
    with client(tmp_path) as c:
        headers = auth(c)
        assert c.get('/api/files', headers=headers).json() == {'files': [{'path': '한글.txt', 'size': 2}]}
        assert c.post('/api/jobs', headers=headers, json={'file_path': '../.env'}).status_code == 422
        assert c.post('/api/jobs', headers=headers, json={'html': 'x', 'file_path': '한글.txt'}).status_code == 422
        assert 'secret-value' not in c.get('/api/session').text
        assert c.get('/.env').status_code == 404


def test_job_polling_failure_and_download_restrictions(tmp_path):
    with client(tmp_path) as c:
        headers = auth(c)
        response = c.post('/api/jobs', headers=headers, json={'html': 'invalid'})
        assert response.status_code == 202
        job_id = response.json()['id']
        job = c.get('/api/jobs/' + job_id, headers=headers).json()
        assert job['state'] == 'failed' and job['error']
        assert c.get(f'/api/jobs/{job_id}/artifacts/metadata.json', headers=headers).status_code == 422
        assert c.get('/api/jobs/invalid-id', headers=headers).status_code == 404


def test_request_size_is_bounded(tmp_path):
    with client(tmp_path) as c:
        assert c.post('/api/jobs', headers={**auth(c), 'Content-Length': '99999999'}, content='{}').status_code == 413


def test_validation_errors_use_string_detail_contract(tmp_path):
    with client(tmp_path) as c:
        response = c.post('/api/jobs', headers=auth(c), json={})
        assert response.status_code == 422
        assert isinstance(response.json()['detail'], str)


def test_failed_narration_poll_copy_and_download(tmp_path, tiny_html):
    from backend.compression import Compressor
    from tests.test_compression import Transport

    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('instructions', encoding='utf-8')
    draft = 'invalid final narration\nsecond line'
    with client(tmp_path) as c:
        manager = c.app.state.manager
        manager.compressor_factory = lambda **options: Compressor(prompt, [(1, 'fake')],
            transport=Transport(['invalid first draft', draft]))
        copied = []
        manager.clipboard.write = copied.append
        headers = auth(c)
        job_id = c.post('/api/jobs', headers=headers, json={'html': tiny_html}).json()['id']
        c.portal.call(manager.wait, job_id)
        job = c.get(f'/api/jobs/{job_id}', headers=headers).json()
        assert job['state'] == 'failed' and job['compressed'] == draft
        assert c.post(f'/api/jobs/{job_id}/copy', headers=headers, json={'stage': 'compressed'}).status_code == 200
        assert copied[-1] == draft
        response = c.get(f'/api/jobs/{job_id}/artifacts/compressed.txt', headers=headers)
        assert response.status_code == 200 and response.text == draft


def test_delimited_response_is_saved_copied_and_downloaded_as_five_lines(tmp_path, tiny_html, compressed):
    from backend.compression import Compressor
    from tests.test_compression import Transport
    from pathlib import Path

    prompt = tmp_path / 'prompt.txt'
    prompt.write_text('instructions', encoding='utf-8')
    raw = compressed.replace('\n', '<SCENE_BREAK>')
    with client(tmp_path) as c:
        manager = c.app.state.manager
        manager.compressor_factory = lambda **options: Compressor(prompt, [(1, 'fake')], transport=Transport([raw]))
        copied = []
        manager.clipboard.write = copied.append
        headers = auth(c)
        job_id = c.post('/api/jobs', headers=headers, json={'html': tiny_html}).json()['id']
        c.portal.call(manager.wait, job_id)
        job = c.get(f'/api/jobs/{job_id}', headers=headers).json()
        assert job['state'] == 'completed' and job['compressed'] == compressed
        assert copied[-1] == compressed
        folder = tmp_path / job['output_dir']
        assert (folder / 'raw_response_1.txt').read_text(encoding='utf-8') == raw
        assert (folder / 'compressed.txt').read_text(encoding='utf-8') == compressed
        manager.jobs.pop(job_id)
        assert c.get(f'/api/jobs/{job_id}', headers=headers).json()['compressed'] == compressed
        assert c.get(f'/api/jobs/{job_id}/artifacts/compressed.txt', headers=headers).text == compressed
