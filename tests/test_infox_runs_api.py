from uuid import uuid4

from backend.storage import ResultStore
from tests.test_api import auth, client


JOB_ID = '20261102_120000_000000_1234abcd'


def seed(root, *, state='completed'):
    store = ResultStore(root)
    store.metadata(dict(id=JOB_ID, state=state, revision=0, warnings=[], validation_issues=[]))
    store.write_text(JOB_ID, 'compressed.txt', '최초 생성본')
    store.save_edit(JOB_ID, '최신 편집 😀\n둘째 줄', revision=2, edited_at='2026-11-02T01:00:00Z')


def payload(**changes):
    return {'intent_id': uuid4().hex, 'revision': 2, **changes}


def test_prepare_api_security_snapshot_and_restart(tmp_path):
    seed(tmp_path)
    path = f'/api/jobs/{JOB_ID}/infox-runs'
    body = payload()
    with client(tmp_path) as c:
        assert c.post(path, json=body).status_code == 403
        headers = auth(c)
        assert c.post(path, headers={**headers, 'Origin': 'https://evil.invalid'}, json=body).status_code == 403
        response = c.post(path, headers=headers, json=body)
        assert response.status_code == 201
        record = response.json()
        assert record['snapshot']['text'] == '최신 편집 😀\n둘째 줄'
        assert record['stage'] == 'prepared' and record['project']['state'] == 'not_sent'
        assert c.post(path, headers=headers, json=body).json()['id'] == record['id']
        assert c.get(f'/api/infox-runs/{record["id"]}').status_code == 403
        assert c.get(path, headers=headers).json()['run']['id'] == record['id']
    with client(tmp_path) as c:
        headers = auth(c)
        assert c.get(f'/api/infox-runs/{record["id"]}', headers=headers).json() == record
        assert c.post(path, headers=headers, json=body).status_code == 200
        assert c.get(path, headers=headers).json()['run']['snapshot']['revision'] == 2


def test_conflict_review_and_validation_errors_are_safe(tmp_path):
    seed(tmp_path, state='needs_review')
    path = f'/api/jobs/{JOB_ID}/infox-runs'
    with client(tmp_path) as c:
        headers = auth(c)
        assert c.post(path, headers=headers, json=payload(revision=0)).status_code == 409
        response = c.post(path, headers=headers, json=payload())
        assert response.status_code == 409 and response.json()['code'] == 'review_required'
        assert c.post(path, headers=headers, json=payload(review_confirmed=True)).status_code == 201
        response = c.post(path, headers=headers, json=payload(settings={'access_token': 'PRIVATE-TOKEN'}))
        assert response.status_code == 422 and 'PRIVATE-TOKEN' not in response.text
        assert 'detail' in response.json()
        assert c.get('/api/infox-runs/not-an-id', headers=headers).status_code == 404


def test_atomic_failure_rolls_back_record_and_intent(tmp_path):
    seed(tmp_path)
    with client(tmp_path) as c:
        store = c.app.state.infox_runs.store
        with store._transaction() as conn:
            conn.execute("CREATE TRIGGER fail_intent BEFORE INSERT ON intents BEGIN SELECT RAISE(ABORT, 'PRIVATE-DB-ERROR'); END")
        response = c.post(f'/api/jobs/{JOB_ID}/infox-runs', headers=auth(c), json=payload())
        assert response.status_code == 503 and 'PRIVATE-DB-ERROR' not in response.text
        assert store.latest(JOB_ID) is None
        with store._transaction() as conn:
            assert conn.execute('SELECT COUNT(*) FROM intents').fetchone()[0] == 0


def test_invalid_settings_do_not_acknowledge_run_or_leak_values(tmp_path):
    seed(tmp_path)
    with client(tmp_path) as c:
        response = c.post(f'/api/jobs/{JOB_ID}/infox-runs', headers=auth(c),
                          json=payload(settings={'voice_speed': 1.0, 'password': 'PRIVATE'}))
        assert response.status_code == 422
        assert 'PRIVATE' not in response.text
        assert c.app.state.infox_runs.latest(JOB_ID) is None
