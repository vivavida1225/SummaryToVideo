import json
from pathlib import Path


def test_evidence_task_chain_matches():
    evidence = json.loads((Path(__file__).parent / 'fixtures/infox_publishing/observed.json').read_text(encoding='utf-8'))
    chain = evidence['chain']
    assert chain['render']['taskid'] == chain['metadata_generate']['input_task_id']
    assert chain['metadata_generate']['taskid'] == chain['metadata_update']['task_id']
    assert chain['metadata_update']['task_id'] == chain['upload']['input_task_id']
    assert chain['upload']['taskid'] == next(log['task_id'] for log in evidence['history']['logs'] if log['state'] == 'sns')
    render = next(log for log in evidence['history']['logs'] if log['task_id'] == chain['render']['taskid'])
    metadata = next(log for log in evidence['history']['logs'] if log['task_id'] == chain['metadata_generate']['taskid'])
    assert (render['file_name'], render['bucket']) == (metadata['file_name'], metadata['bucket'])
    assert metadata['sns_upload_info']['privacyStatus'] == 'unlisted'
