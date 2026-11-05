import hashlib
import json
import re
from urllib.parse import parse_qs, urlsplit

from ..tracking import task_event
from .models import PublishEvent, StageVerification


def _initialization_flags(body, video_id, task_id):
    """Inspect the same envelopes/scope as task_event, without changing draft tracking."""
    if not isinstance(body, dict) or task_event(body, video_id, task_id) is None:
        return []
    kwargs = body.get('additional_kwargs')
    if isinstance(kwargs, dict) and kwargs.get('taskid') == task_id:
        return ['task_status' not in kwargs and type(kwargs.get('progress')) in (int, float)
                and kwargs['progress'] == 0 and not kwargs.get('error')]
    flags = []
    for key in ('messages', 'message', 'data'):
        nested = body.get(key)
        for item in nested if isinstance(nested, list) else [nested]:
            flags.extend(_initialization_flags(item, video_id, task_id))
    return flags


def normalize_publish_event(body, video_id, task_id):
    event = task_event(body, video_id, task_id)
    if event is None:
        return None
    status = event.status
    if status == 'unrecognized':
        flags = _initialization_flags(body, video_id, task_id)
        if flags and all(flags):
            status = 'initializing'
    return PublishEvent(task_id=task_id, status=status, progress=event.progress,
                        error_present=event.status == 'failed')


def parse_youtube_share_url(value):
    if not isinstance(value, str) or any(ord(c) <= 32 for c in value):
        raise ValueError('invalid share url')
    u = urlsplit(value)
    if (u.scheme != 'https' or u.hostname not in ('youtube.com', 'www.youtube.com', 'youtu.be')
            or u.port not in (None, 443) or u.username or u.password or u.fragment):
        raise ValueError('invalid share url')
    if u.hostname == 'youtu.be':
        video_id = u.path[1:] if u.path.startswith('/') else ''
    else:
        values = parse_qs(u.query).get('v', [])
        video_id = values[0] if u.path == '/watch' and len(values) == 1 else ''
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', video_id):
        raise ValueError('invalid video id')
    return video_id, f'https://youtu.be/{video_id}'


def history_logs(history, video_id):
    if (not isinstance(history, dict) or not isinstance(history.get('logs'), list)
            or ('video_id' in history and str(history['video_id']) != video_id)):
        raise ValueError('invalid history')
    logs = history['logs']
    ids = []
    for log in logs:
        if (not isinstance(log, dict) or not isinstance(log.get('task_id'), str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', log['task_id'])
                or ('video_id' in log and str(log['video_id']) != video_id)
                or not isinstance(log.get('state'), str)):
            raise ValueError('invalid history record')
        ids.append(log['task_id'])
    if len(set(ids)) != len(ids):
        raise ValueError('duplicate history record')
    return logs


def file_fingerprint(log):
    fields = [log.get('bucket'), log.get('file_name')]
    if not all(isinstance(x, str) and x and len(x) <= 1024 and '://' not in x and '?' not in x for x in fields):
        return None
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode()).hexdigest()


def _keywords(info):
    words = info.get('keywords')
    if (not isinstance(words, list) or len(words) > 200
            or any(not isinstance(w, str) or len(w) > 500 for w in words)):
        raise ValueError('invalid keywords')
    return words


def verify_publish_stage(record, history):
    try:
        logs = history_logs(history, record.video_id)
        id = (record.render_task_id if record.stage == 'rendering' else record.metadata_task_id
              if record.stage in ('generating_metadata', 'saving_metadata') else record.upload_task_id)
        log = next((row for row in logs if row['task_id'] == id), None)
        if log is None:
            return StageVerification(status='waiting')
        state = log['state']
        # Observed task-scoped transitions; pending never establishes completion,
        # even when result fields are already populated.
        pending = {
            'rendering': {'start', 'workerstart'},
            'generating_metadata': {'generate_metainfo_youtube_start', 'info'},
            'uploading': {'upload_youtube_start'},
        }
        if state in pending.get(record.stage, set()):
            return StageVerification(status='waiting')
        # Only observed terminal states are accepted. Unknown in-progress/failure
        # history values cannot establish success or a definite remote failure.
        wanted = 'sns' if record.stage == 'uploading' else 'end'
        if state != wanted:
            return StageVerification(status='unknown', error_code='history_unrecognized')
        if record.stage == 'rendering':
            fingerprint = file_fingerprint(log)
            return StageVerification(status='completed' if fingerprint else 'waiting',
                evidence={'render_file_fingerprint': fingerprint} if fingerprint else {})
        if record.stage in ('generating_metadata', 'saving_metadata'):
            fingerprint = file_fingerprint(log)
            if not fingerprint or not isinstance(log.get('sns_upload_info'), dict):
                return StageVerification(status='waiting')
            if fingerprint != record.render_file_fingerprint:
                return StageVerification(status='unknown', error_code='metadata_mismatch')
            info = log['sns_upload_info']
            words = _keywords(info)
            if record.stage == 'saving_metadata' and (info.get('title') != record.snapshot.title
                    or info.get('description') != record.snapshot.description or words != list(record.keywords)):
                return StageVerification(status='waiting')
            return StageVerification(status='completed', evidence={'keywords': words})
        if record.stage == 'uploading':
            metadata = next((row for row in logs if row['task_id'] == record.metadata_task_id), None)
            info = metadata.get('sns_upload_info') if metadata else None
            if (not isinstance(info, dict) or info.get('privacyStatus') != 'unlisted'
                    or file_fingerprint(metadata) != record.render_file_fingerprint
                    or info.get('title') != record.snapshot.title
                    or info.get('description') != record.snapshot.description
                    or _keywords(info) != list(record.keywords)):
                return StageVerification(status='unknown', error_code='metadata_mismatch')
            if not log.get('sns_url'):
                return StageVerification(status='waiting')
            video_id, url = parse_youtube_share_url(log['sns_url'])
            return StageVerification(status='completed', evidence={'youtube_video_id': video_id, 'share_url': url,
                                                                    'uploaded_verified': True})
        raise ValueError('invalid stage')
    except (ValueError, TypeError, KeyError, AttributeError):
        return StageVerification(status='unknown', error_code='history_unrecognized')
