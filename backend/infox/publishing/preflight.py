"""Read-only policy shared by preview and execution; no requests or mutations."""
from datetime import datetime

from .tracking import file_fingerprint, parse_youtube_share_url, verify_publish_stage
from .models import TERMINAL


def history_block_reason(logs, draft_task_id):
    completed_times = []
    for log in logs:
        state = log['state']
        if state in ('start', 'workerstart', 'generate_metainfo_youtube_start', 'info', 'upload_youtube_start'):
            return 'publication_active'
        if state == 'videosgenerationend':
            if log['task_id'] != draft_task_id:
                return 'existing_publication'
        elif state == 'end':
            if not file_fingerprint(log):
                return 'previous_unconfirmed'
            if log.get('sns_upload_info') is None and not log.get('sns_url'):
                try:
                    completed_times.append(datetime.fromisoformat(log['updated_at']))
                except (KeyError, TypeError, ValueError):
                    pass  # Timestamps are only needed to justify an older cancellation.
        elif state == 'sns':
            try:
                parse_youtube_share_url(log.get('sns_url'))
            except (ValueError, TypeError):
                return 'previous_unconfirmed'
        elif state != 'cancel':
            return 'existing_publication'
    for log in logs:
        if log['state'] == 'cancel':
            try:
                cancelled = datetime.fromisoformat(log['updated_at'])
                if not completed_times or not any(t > cancelled for t in completed_times):
                    return 'previous_unconfirmed'
            except (KeyError, TypeError, ValueError):
                return 'previous_unconfirmed'
    return None


def previous_block_reason(records, history):
    logs = {row['task_id']: row for row in history['logs']}
    for record in records:
        if record.stage not in TERMINAL:
            return 'publication_active'
        render = logs.get(record.render_task_id)
        fingerprint = file_fingerprint(render) if render else None
        checking = record.model_copy(update={'render_file_fingerprint': fingerprint})
        for operation, stage, task_id in (
                ('render', 'rendering', record.render_task_id),
                ('metadata_generate', 'generating_metadata', record.metadata_task_id),
                ('metadata_update', 'saving_metadata', record.metadata_task_id),
                ('upload', 'uploading', record.upload_task_id)):
            receipt = getattr(record.receipts, operation)
            if receipt.state in ('not_sent', 'rejected'):
                continue
            if not task_id:
                return 'previous_unconfirmed'
            # An observed historical cancellation is already checked by history_block_reason.
            if operation == 'render' and logs.get(task_id, {}).get('state') == 'cancel':
                continue
            if verify_publish_stage(checking.model_copy(update={'stage': stage}), history).status != 'completed':
                return 'previous_unconfirmed'
    return None
