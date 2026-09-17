from fastapi.testclient import TestClient
import pytest

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


def test_myasset_source_requires_auth_and_defaults_to_30(tmp_path, monkeypatch):
    calls = []
    async def fetch(base_date, gubun):
        calls.append((base_date.isoformat(), gubun))
        return dict(html='<p>news</p>', source_url='https://www.myasset.com/',
                    base_date=base_date.isoformat(), gubun=gubun)
    monkeypatch.setattr('backend.app.fetch_myasset_source', fetch, raising=False)
    with client(tmp_path) as c:
        assert c.post('/api/sources/myasset', json={'base_date': '2026-09-17'}).status_code == 403
        headers = auth(c)
        response = c.post('/api/sources/myasset', headers=headers, json={'base_date': '2026-09-17'})
        assert response.status_code == 200
        assert response.json()['gubun'] == 30
        response = c.post('/api/sources/myasset', headers=headers, json={'base_date': '2026-09-17', 'gubun': 1})
        assert response.json()['gubun'] == 1
        assert calls == [('2026-09-17', 30), ('2026-09-17', 1)]
        assert not c.app.state.manager.jobs


@pytest.mark.parametrize('payload', [
    {}, {'base_date': '2026-02-30'}, {'base_date': '20260917'}, {'base_date': 1789603200},
    {'base_date': '2026-9-17'}, {'base_date': '2026-09-17T00:00:00'},
    *[{'base_date': '2026-09-17', 'gubun': value} for value in [-1, True, 1.5, '1', None]],
    {'base_date': '2026-09-17', 'url': 'https://example.com'},
])
def test_myasset_rejects_invalid_settings_before_fetch(tmp_path, monkeypatch, payload):
    async def unexpected(*args):
        pytest.fail('Invalid input reached external fetch')
    monkeypatch.setattr('backend.app.fetch_myasset_source', unexpected, raising=False)
    with client(tmp_path) as c:
        response = c.post('/api/sources/myasset', headers=auth(c), json=payload)
        assert response.status_code == 422
        assert isinstance(response.json()['detail'], str)
        assert 'gubun' in response.json()['detail']


@pytest.mark.parametrize('status', [404, 413, 502, 504])
def test_myasset_errors_preserve_string_detail_without_starting_job(tmp_path, monkeypatch, status):
    from backend.myasset import MyassetError
    async def fetch(*args):
        raise MyassetError(status, '원문을 가져올 수 없습니다.')
    monkeypatch.setattr('backend.app.fetch_myasset_source', fetch)
    with client(tmp_path) as c:
        response = c.post('/api/sources/myasset', headers=auth(c), json={'base_date': '2026-09-17'})
        assert response.status_code == status
        assert response.json() == {'detail': '원문을 가져올 수 없습니다.'}
        assert not c.app.state.manager.jobs


def test_myasset_html_reaches_existing_job_pipeline(tmp_path, tiny_html, compressed, monkeypatch):
    import httpx
    from backend import myasset
    from backend.gemini import GeminiTransport
    from backend.serializer import serialize_html
    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200,
        headers={'Content-Type': 'text/html'}, text='<div class="contWrap">' + tiny_html + '</div>'))
    monkeypatch.setattr(myasset.httpx, 'AsyncClient', lambda **kwargs: original_client(transport=transport, **kwargs))
    sources = []
    async def generate(self, **kwargs):
        sources.append(kwargs['source'])
        return compressed
    monkeypatch.setattr(GeminiTransport, 'generate', generate)
    (tmp_path / '.env').write_text('GEMINI_API_KEY_1=fake', encoding='utf-8')
    (tmp_path / 'prompts').mkdir()
    (tmp_path / 'prompts/compress_to_1min.txt').write_text('instructions', encoding='utf-8')
    with client(tmp_path) as c:
        headers = auth(c)
        imported = c.post('/api/sources/myasset', headers=headers, json={'base_date': '2026-09-17', 'gubun': 1})
        assert imported.status_code == 200
        assert not c.app.state.manager.jobs
        created = c.post('/api/jobs', headers=headers, json={'html': imported.json()['html']})
        assert created.status_code == 202
        c.portal.call(c.app.state.manager.wait, created.json()['id'])
        job = c.get('/api/jobs/' + created.json()['id'], headers=headers).json()
        assert sources == [serialize_html(tiny_html)]
        assert job['state'] == 'completed'
        assert job['compressed'] == compressed


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


def test_delimited_response_is_saved_and_only_copied_on_request(tmp_path, tiny_html, compressed):
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
        assert copied == []
        assert c.post(f'/api/jobs/{job_id}/copy', headers=headers, json={'stage': 'serialized'}).status_code == 200
        assert copied == [job['serialized']]
        assert c.post(f'/api/jobs/{job_id}/copy', headers=headers, json={'stage': 'compressed'}).status_code == 200
        assert copied == [job['serialized'], compressed]
        folder = tmp_path / job['output_dir']
        assert (folder / 'raw_response_1.txt').read_text(encoding='utf-8') == raw
        assert (folder / 'compressed.txt').read_text(encoding='utf-8') == compressed
        manager.jobs.pop(job_id)
        assert c.get(f'/api/jobs/{job_id}', headers=headers).json()['compressed'] == compressed
        assert c.get(f'/api/jobs/{job_id}/artifacts/compressed.txt', headers=headers).text == compressed
