"""Existing-run phase 6 read-only verification. Never fabricate production targets.

Only an actual not_needed run is permitted. No create/start/save endpoints are
allowlisted. Sanitized evidence distinguishes implementation from live saving.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

from backend.config import ROOT
from backend.infox.client import InfoXClient
from backend.infox.config import InfoXCredentials
from backend.infox.manager import RunManager
from backend.infox.replacement import state_fingerprint
from backend.infox.saving import save_body
from backend.infox.store import RunStore
from backend.infox.tracking import verify_draft
from backend.infox.assets import audit_assets


async def main(root=ROOT):
    store = RunStore(root)
    marker = json.loads((root / '.runtime/infox-phase3-live.json').read_text(encoding='utf-8'))
    original = store.get(marker['run_id'])
    assert original.replacement_plan.state == 'not_needed' and not original.replacement_plan.selections
    assert original.asset_audit.counts()['detected'] == 0 and not original.snapshot.settings.auto_replace
    calls = []
    class ReadOnlyClient(InfoXClient):
        async def _json_once(self, method, path, **kwargs):
            if method == 'GET' and path == f'/infomaxai/videos/videostate/{original.video_id}':
                calls.append('video_state')
            elif path in ('/infomaxai/auth/login', '/infomaxai/auth/refresh-token', '/infomaxai/auth/org-email/device-refresh'):
                calls.append('authentication')
            else:
                raise AssertionError('remote video write forbidden')
            return await super()._json_once(method, path, **kwargs)
    manager = RunManager(store, lambda: ReadOnlyClient(InfoXCredentials.load(root)))
    try:
        async with ReadOnlyClient(InfoXCredentials.load(root)) as client:
            before = await client.video_state(original.video_id)
            body = save_body(before)  # Mapping only; never send this body.
        assert verify_draft(original, before).ready
        assert state_fingerprint(before) == original.replacement_plan.source_fingerprint
        fresh = audit_assets(before, template_id=original.snapshot.settings.template_id,
                             run_id=original.id, video_id=original.video_id)
        assert fresh.state == 'completed' and fresh.counts()['detected'] == 0
        await manager.save(original.id); await manager.wait(original.id)
        final = store.get(original.id)
        async with ReadOnlyClient(InfoXCredentials.load(root)) as client:
            after = await client.video_state(original.video_id)
        assert final.asset_save.state == 'unchanged_verified'
        assert state_fingerprint(before) == state_fingerprint(after)
        assert state_fingerprint(after) == final.asset_save.verified_fingerprint
        assert verify_draft(final, after).ready
        assert original.snapshot == final.snapshot and original.project == final.project and original.generation == final.generation
        assert original.favorite_replacements == final.favorite_replacements
        report = {
            'verified_at_kst': datetime.now(timezone(timedelta(hours=9))).isoformat(),
            'existing_phase3_run_reused': True,
            'stage': final.stage, 'save_state': final.asset_save.state, 'plan_state': final.replacement_plan.state,
            'production_detected_count': 0, 'production_selections': 0, 'auto_replace': False,
            'context_mapping': {'video': 'videostate.video', 'contextData': 'videostate.context', 'updatedAt': 'videostate.updated_at'},
            'actual_context_is_object': isinstance(before['context'], dict),
            'actual_contextData_key_absent': 'contextData' not in before,
            'mapping_preserves_full_context': body['contextData'] == before['context'],
            'mapping_preserves_exact_version': body['updatedAt'] == before['updated_at'],
            'same_snapshot': original.snapshot == final.snapshot,
            'same_video_and_task': original.video_id == final.video_id and original.task_id == final.task_id,
            'same_receipts': original.project == final.project and original.generation == final.generation,
            'server_version_unchanged': before['updated_at'] == after['updated_at'],
            'full_remote_state_unchanged_except_signed_urls': state_fingerprint(before) == state_fingerprint(after),
            'current_remote_matches_unchanged_verification': state_fingerprint(after) == final.asset_save.verified_fingerprint,
            'draft_still_ready': verify_draft(final, after).ready,
            'saved_favorite_history_unchanged': original.favorite_replacements == final.favorite_replacements,
            'remote_video_writes': 0, 'synthetic_metadata_hit_added': False,
            'actual_save_response_verified': False, 'actual_conflict_response_verified': False,
            'actual_replacement_readback_verified': False, 'actual_replacement_editor_playback_verified': False,
            'reason': 'Existing authorized run has no replacement targets; no new validation project, generation, or synthetic production hit is permitted.',
            'editor_source_contract': {
                'load': 'getVideoState -> SET_CONTEXT_DATA(payload: response.context)',
                'save_hook': 'videoState.context -> saveVideoAndContextData(videoId, video, contextData, updatedAt)',
                'success': 'spec HAR observations: HTTP 200, status Video updated, updated_at',
                'conflict': 'editor save coordinator handles HTTP 409; actual conflict body remains unverified',
            },
            'operations': calls,
        }
        (root / 'docs/infox/phase6-live-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True))
    finally:
        await manager.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception:
        print('{"error":"existing_run_read_only_save_verification_incomplete"}')
        raise SystemExit(1) from None
