"""Manual review admission. Content diagnostics never grant or deny publication."""
from pydantic import Field

from .run_models import StrictModel, RunError, now


class ManualReviewInput(StrictModel):
    expected_version: int = Field(ge=0)


def review_block_reason(run, *, active=False):
    if run.requires_reconciliation:
        return 'request_unconfirmed'
    if not run.video_id or run.project.state != 'accepted' or run.generation.state != 'accepted':
        return 'editor_not_ready'
    if active or run.stage not in ('needs_attention', 'failed', 'draft_ready', 'assets_audited',
                                   'replacement_planned', 'ready_for_editor'):
        return 'production_active'
    if run.tracking.task_status in ('waiting', 'running'):
        return 'production_active'
    return None


def publication_ready(run):
    return run.review_mode == 'manual_review' and review_block_reason(run) is None


def publication_active(conn, video_id):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='publications'").fetchone():
        return conn.execute('SELECT 1 FROM publications WHERE video_id=? AND active=1', (video_id,)).fetchone() is not None
    return False


def review_capabilities(run, *, active=False):
    reason = review_block_reason(run, active=active)
    ready = reason is None
    return dict(can_enter_manual_review=ready, can_open_editor=ready and run.review_mode == 'manual_review',
                can_prepare_publication=ready and run.review_mode == 'manual_review',
                review_blocked_reason=reason)


def warning_codes(run):
    return tuple(dict.fromkeys(code for code in (run.tracking.error_code, run.asset_audit.error_code,
        run.replacement_plan.error_code, run.asset_save.error_code,
        *('draft_' + key for key, value in run.tracking.draft.model_dump().items() if not value),
        *('asset_issue:' + issue.code for issue in run.asset_audit.issues),
        *('asset_unknown:' + item.reason for item in run.asset_audit.items if item.verdict == 'unknown')) if code))


def enter_manual_review(store, run_id, expected_version):
    store._run_id(run_id)
    with store._transaction() as conn:
        run = store._decode(conn.execute('SELECT record FROM runs WHERE id=?', (run_id,)).fetchone())
        if run.review_mode == 'manual_review':
            return run  # Idempotent even when the original response was lost.
        if publication_active(conn, run.video_id):
            raise RunError('publication_active')
        if run.version != expected_version:
            raise RunError('record_conflict')
        reason = review_block_reason(run)
        if reason:
            raise RunError(reason)
        updated = run.model_copy(update={'review_mode': 'manual_review', 'manual_review_started_at': now(),
            'continuation': 'manual', 'version': run.version + 1, 'updated_at': now()})
        conn.execute('UPDATE runs SET record=? WHERE id=?', (updated.model_dump_json(), run_id))
    return updated
