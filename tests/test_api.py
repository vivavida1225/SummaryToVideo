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
