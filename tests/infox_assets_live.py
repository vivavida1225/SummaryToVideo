"""Audit only the existing phase 3 verified run; never generate or modify a video."""

import asyncio
import json
from datetime import datetime

from backend.config import ROOT
from backend.infox.client import InfoXClient
from backend.infox.config import InfoXCredentials
from backend.infox.manager import RunManager
from backend.infox.store import RunStore


async def main(root=ROOT):
    marker = root / '.runtime/infox-phase3-live.json'
    if not marker.exists():
        raise ValueError('existing verified run required')
    store = RunStore(root)
    original = store.get(json.loads(marker.read_text(encoding='utf-8'))['run_id'])
    assert original.tracking.completion_seen and original.tracking.draft.ready
    calls = []
    class ReadOnlyClient(InfoXClient):
        async def _json_once(self, method, path, **kwargs):
            if path == f'/infomaxai/videos/videostate/{original.video_id}' and method == 'GET':
                calls.append('video_state')
            elif path in ('/infomaxai/auth/login', '/infomaxai/auth/org-email/device-refresh', '/infomaxai/auth/refresh-token'):
                calls.append('authentication')
            else:
                raise AssertionError('unexpected remote operation')
            return await super()._json_once(method, path, **kwargs)
    manager = RunManager(store, lambda: ReadOnlyClient(InfoXCredentials.load(root)))
    try:
        await manager.audit(original.id)
        await manager.wait(original.id)
        final = store.get(original.id)
        async with ReadOnlyClient(InfoXCredentials.load(root)) as client:
            after = await client.video_state(original.video_id)
        audit = final.asset_audit
        report = {'verified_at_kst': datetime.now().astimezone().isoformat(), 'existing_phase3_run_reused': True,
            'stage': final.stage, 'audit_state': audit.state, 'counts': audit.counts(),
            'item_count': len(audit.items), 'automatic_layer_count': len({(x.scene_index,x.layer_id) for x in audit.items}),
            'same_snapshot': final.snapshot == original.snapshot,
            'same_video_and_task': final.video_id == original.video_id and final.task_id == original.task_id,
            'same_receipts': final.project == original.project and final.generation == original.generation,
            'server_version_unchanged': bool(audit.source_version and
                datetime.fromisoformat(after['updated_at']) == datetime.fromisoformat(audit.source_version)),
            'remote_video_writes': 0, 'operations': calls,
            'issues': [issue.code for issue in audit.issues], 'error_code': audit.error_code,
            'items': [{'scene_index': x.scene_index, 'item_index': x.item_index, 'kind': x.reference.kind,
                       'verdict': x.verdict, 'reason': x.reason,
                       'hits': [{'term': hit.term, 'field': hit.field} for hit in x.hits],
                       'matched_metadata_sources': len(x.metadata_sources)} for x in audit.items]}
        (root / 'docs/infox/phase4-live-verification.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True))
    finally:
        await manager.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception:
        print('{"error":"read_only_audit_interrupted_check_existing_run"}')
        raise SystemExit(1) from None
