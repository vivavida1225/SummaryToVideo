import time
from uuid import uuid4

from tests.test_api import auth, client
from tests.test_infox_editor import verified
from tests.infox_publishing_fakes import PublishClient, PublishClipboard


def test_api_requires_local_session_and_origin_and_rejects_extra_fields(tmp_path):
    _,run=verified(tmp_path)
    with client(tmp_path) as c:
        headers=auth(c)
        path=f'/api/infox-runs/{run.id}/publications'
        body={'intent_id':str(uuid4()),'market_date':'2026-11-04'}
        assert c.post(path,json=body).status_code==403
        assert c.post(path,headers={**headers,'Origin':'https://evil.invalid'},json=body).status_code==403
        result=c.post(path,headers=headers,json={**body,'video_id':'secret','privacy':'public'})
        assert result.status_code==422 and 'secret' not in result.text
        assert c.get(path,headers=headers).json()=={'publication':None}


def test_reload_get_is_read_only_and_duplicate_post_returns_existing_publication(tmp_path):
    _,run=verified(tmp_path)
    with client(tmp_path) as c:
        fake=PublishClient()
        c.app.state.infox_publications.client_factory=lambda:fake
        c.app.state.infox_publications.clipboard=PublishClipboard()
        headers=auth(c); path=f'/api/infox-runs/{run.id}/publications'
        preview=c.get(path+'/preview',headers=headers)
        assert preview.status_code==200 and preview.json()=={'available':True,'charged':True,
            'can_start':True,'blocked_reason':None,'latest_publication_id':None,'has_remote_render':False}
        body={'intent_id':str(uuid4()),'market_date':'2026-11-04'}
        first=c.post(path,headers=headers,json=body)
        assert first.status_code==202, first.text
        id=first.json()['id']
        again=c.post(path,headers=headers,json=body)
        assert again.json()['id']==id
        for _ in range(100):
            result=c.get(f'/api/infox-publications/{id}',headers=headers)
            assert result.status_code==200
            if result.json()['stage'] in ('completed','failed','needs_attention','clipboard_failed'): break
            time.sleep(.01)
        assert result.json()['stage']=='completed', result.text
        before=list(fake.calls)
        for _ in range(3):
            assert c.get(path,headers=headers).json()['publication']['id']==id
        assert fake.calls==before==['render','metadata_generate','metadata_update','upload']
        bad=c.post(f'/api/infox-publications/{id}/copy-link',headers=headers,json={'url':'https://evil.invalid/?token=secret'})
        assert bad.status_code==422 and 'secret' not in bad.text
        assert c.post(path,headers=headers,json={**body,'market_date':'2026-11-05'}).status_code==409


def test_restart_keeps_receipts_and_no_writes(tmp_path):
    from tests.test_infox_publishing_store import setup_publication
    store,r=setup_publication(tmp_path)
    r=store.transition(r.id,r.version,{'stage':'starting_render'})
    store.claim_operation(r.id,'render',r.version)
    with client(tmp_path) as c:
        headers=auth(c)
        result=c.get(f'/api/infox-publications/{r.id}',headers=headers).json()
        assert result['stage']=='needs_attention'
        assert result['receipts']['render']['state']=='intent_recorded'
        assert result['error_code']=='server_restarted'


def test_rerender_api_is_guarded_and_history_keeps_both_attempts(tmp_path):
    from tests.test_infox_publishing_store import setup_publication
    store,first=setup_publication(tmp_path)
    first=store.transition(first.id,first.version,{'stage':'failed'})
    with client(tmp_path) as c:
        fake=PublishClient(suffix='-2')
        c.app.state.infox_publications.client_factory=lambda:fake
        c.app.state.infox_publications.clipboard=PublishClipboard()
        headers=auth(c);path=f'/api/infox-runs/{first.run_id}/publications'
        body={'intent_id':str(uuid4()),'market_date':'2026-11-04','previous_publication_id':first.id}
        assert c.post(path+'/rerender',json=body).status_code==403
        assert c.post(path+'/rerender',headers=headers,json={**body,'video_id':'wrong'}).status_code==422
        response=c.post(path+'/rerender',headers=headers,json=body)
        assert response.status_code==202,response.text
        second=response.json()
        assert second['attempt_number']==2
        for _ in range(100):
            result=c.get(f"/api/infox-publications/{second['id']}",headers=headers).json()
            if result['stage'] in ('completed','failed','needs_attention'): break
            time.sleep(.01)
        assert result['stage']=='completed',result
        assert c.post(path+'/rerender',headers=headers,json=body).json()['id']==second['id']
        assert c.post(path+'/rerender',headers=headers,json={**body,'intent_id':str(uuid4())}).status_code==409
        history=c.get(path+'/history',headers=headers).json()['publications']
        assert [r['id'] for r in history]==[second['id'],first.id]
        assert c.get(path,headers=headers).json()['publication']['id']==second['id']
        assert fake.calls==['render','metadata_generate','metadata_update','upload']
