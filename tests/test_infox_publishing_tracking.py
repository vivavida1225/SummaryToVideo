import json
from pathlib import Path

import pytest

from tests.test_infox_publishing_store import setup_publication


def history():
    return json.loads((Path(__file__).parent/'fixtures/infox_publishing/observed.json').read_text(encoding='utf-8'))['history']


def record(root, stage='rendering'):
    from backend.infox.publishing.tracking import file_fingerprint
    store, r = setup_publication(root)
    return r.model_copy(update={'stage':stage,'render_task_id':'mw1r3pcy-10q4c','metadata_task_id':'mw1r7mul-1ly57','upload_task_id':'mw1r8mz7-dsk95',
        'render_file_fingerprint':file_fingerprint(history()['logs'][2]),
        'keywords':tuple(history()['logs'][1]['sns_upload_info']['keywords'])})


def test_100_percent_without_result_is_not_completion(tmp_path):
    from backend.infox.publishing.tracking import normalize_publish_event, verify_publish_stage
    r = record(tmp_path)
    event = normalize_publish_event({'additional_kwargs':{'taskid':r.render_task_id,'task_status':'completed','progress':100}},r.video_id,r.render_task_id)
    assert event.progress == 100
    assert verify_publish_stage(r, {'logs':[{'task_id':r.render_task_id,'state':'end'}]}).status == 'waiting'


@pytest.mark.parametrize('progress', [None, float('nan'), -1, 101, True, '42'])
def test_wrong_task_and_stale_progress_are_ignored(progress):
    from backend.infox.publishing.tracking import normalize_publish_event
    body = {'channel':'infox:video-1','additional_kwargs':{'taskid':'task-1','task_status':'processing','progress':progress}}
    assert normalize_publish_event(body,'video-2','task-1') is None
    assert normalize_publish_event(body,'video-1','other-task') is None
    assert normalize_publish_event(body,'video-1','task-1').progress is None


def test_failure_stops_stage():
    from backend.infox.publishing.tracking import normalize_publish_event
    batch = {'messages':[{'additional_kwargs':{'taskid':'task','task_status':'failed','error':'secret'}},
                         {'additional_kwargs':{'taskid':'task','task_status':'completed','progress':100}}]}
    event = normalize_publish_event(batch,'video','task')
    assert event.status == 'failed' and event.error_present
    assert 'secret' not in event.model_dump_json()


def test_metadata_matches_render_file(tmp_path):
    from backend.infox.publishing.tracking import verify_publish_stage
    r = record(tmp_path)
    result = verify_publish_stage(r, history())
    assert result.status == 'completed'
    r = r.model_copy(update={'stage':'generating_metadata', 'render_file_fingerprint':result.evidence['render_file_fingerprint']})
    assert verify_publish_stage(r, history()).status == 'completed'
    bad = history(); bad['logs'][1]['file_name'] = 'different.mp4'
    assert verify_publish_stage(r, bad).status == 'unknown'
    bad = history(); bad['logs'].append(dict(bad['logs'][1]))
    assert verify_publish_stage(r, bad).status == 'unknown'


def test_unlisted_upload_requires_matching_task_and_url(tmp_path):
    from backend.infox.publishing.tracking import verify_publish_stage
    r = record(tmp_path, 'uploading')
    result = verify_publish_stage(r, history())
    assert result.status == 'completed'
    assert result.evidence['share_url'] == 'https://youtu.be/M5Xzx8AsefY'
    bad = history(); bad['logs'][1]['sns_upload_info']['privacyStatus']='public'
    assert verify_publish_stage(r,bad).status == 'unknown'
    bad = history(); bad['logs'][0]['task_id']='other'
    assert verify_publish_stage(r,bad).status == 'waiting'


def test_upload_rechecks_frozen_metadata_and_render_reference(tmp_path):
    from backend.infox.publishing.tracking import verify_publish_stage
    from backend.infox.publishing.tracking import file_fingerprint
    r = record(tmp_path, 'uploading')
    r = r.model_copy(update={'render_file_fingerprint':file_fingerprint(history()['logs'][2]),
        'keywords':tuple(history()['logs'][1]['sns_upload_info']['keywords'])})
    assert verify_publish_stage(r,history()).status=='completed'
    bad=history(); bad['logs'][1]['sns_upload_info']['title']='other'
    assert verify_publish_stage(r,bad).status=='unknown'
    bad=history(); bad['logs'][1]['file_name']='other.mp4'
    assert verify_publish_stage(r,bad).status=='unknown'


@pytest.mark.parametrize('url', ['https://evil.invalid/watch?v=M5Xzx8AsefY','http://youtu.be/M5Xzx8AsefY',
    'https://user:secret@youtu.be/M5Xzx8AsefY','https://youtube.com.evil.invalid/watch?v=M5Xzx8AsefY',
    'https://youtu.be/short','https://youtu.be:444/M5Xzx8AsefY','https://www.youtube.com/watch?v=M5Xzx8AsefY&v=OtherId0000'])
def test_invalid_share_urls(url):
    from backend.infox.publishing.tracking import parse_youtube_share_url
    with pytest.raises(ValueError): parse_youtube_share_url(url)


def test_observed_upload_start_is_waiting_and_never_completion(tmp_path):
    from backend.infox.publishing.tracking import verify_publish_stage
    r=record(tmp_path,'uploading')
    h=history(); h['logs'][0].update(state='upload_youtube_start',sns_url=None)
    assert verify_publish_stage(r,h).status=='waiting'
    h['logs'][0]['sns_url']='https://youtu.be/M5Xzx8AsefY'
    assert verify_publish_stage(r,h).status=='waiting'
