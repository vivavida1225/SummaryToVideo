"""Explicit single-run live verification; durable marker prevents another project.

Run manually: .venv/Scripts/python.exe -m tests.infox_generation_live
Uses backend .env. Never prints credentials, remote text, IDs or file URLs.
The dedicated integration narration is reviewed test material, not market news.
"""

import asyncio
import json
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from backend.config import ROOT, Settings
from backend.infox.client import InfoXClient
from backend.infox.config import InfoXCredentials
from backend.infox.manager import RunManager
from backend.infox.run_models import PrepareRunInput, ProductionSettings
from backend.infox.service import RunService
from backend.infox.store import RunStore
from backend.jobs import JobManager


TEXT = ('이 영상은 인포엑스 자동화의 제작 연결을 확인하기 위한 검증 영상입니다. 확정한 대본으로 시안을 만들고, 같은 작업의 완료 신호를 확인합니다.\n'
        '완료 신호를 받은 뒤에는 장면의 대본과 음성, 자막이 준비되었는지 다시 확인합니다. 기업 에셋 교체와 자동 화면 이동은 다음 단계에서 검증합니다.')


def event_example(body):
    """Allowlisted structural evidence only. IDs and free text never copied."""
    if isinstance(body, list):
        return [event_example(item) for item in body[:3]]
    if not isinstance(body, dict):
        return type(body).__name__
    result = {}
    for key in ('type', 'status', 'channel', 'id', 'message', 'messages', 'data', 'additional_kwargs',
                'taskid', 'task_status', 'progress', 'error', 'content', 'video_id', 'videoid'):
        if key not in body: continue
        value = body[key]
        if key in ('message', 'messages', 'data', 'additional_kwargs'):
            result[key] = event_example(value)
        elif key in ('id', 'taskid', 'video_id', 'videoid', 'channel'):
            result[key] = '<REDACTED_IDENTIFIER>'
        elif key == 'error': result[key] = bool(value)
        elif key == 'progress': result[key] = value if type(value) in (int, float) else type(value).__name__
        elif value in ('connection', 'connected', 'message', 'completed', 'running', 'failed', 'error', 'pending', 'started', 'processing'):
            result[key] = value
        else: result[key] = type(value).__name__
    return result


async def main(root: Path = ROOT):
    marker = root / '.runtime/infox-phase3-live.json'
    evidence = root / 'docs/infox/phase3-live-verification.json'
    marker.parent.mkdir(exist_ok=True)
    store = RunStore(root)
    jobs = JobManager(Settings(root))
    if marker.exists():
        run_id = json.loads(marker.read_text(encoding='utf-8'))['run_id']
        record = store.get(run_id)
        if evidence.exists() and record.stage == 'draft_ready':
            # Preserve the original observed event evidence on a read-only rerun.
            print(json.dumps({'stage': record.stage, 'existing_verified_run_reused': True}))
            await jobs.close()
            return
    else:
        job_id = datetime.now().strftime('%Y%m%d_%H%M%S_%f_') + uuid4().hex[:8]
        job = dict(id=job_id, parent_id=None, state='completed', source='phase3-integration-verification',
            revision=1, warnings=[], validation_issues=[], edited_at=datetime.now().isoformat())
        jobs.store.write_text(job_id, 'compressed.txt', '최초 검증본. 실제 전송에 사용하지 않습니다.')
        jobs.store.metadata(job)
        jobs.store.save_edit(job_id, TEXT, revision=1, edited_at=job['edited_at'])
        record, _ = RunService(jobs, store).prepare(job_id, PrepareRunInput(intent_id=uuid4().hex,
            revision=1, settings=ProductionSettings(title='InfoX 3단계 연동 검증 2026-11-02', auto_replace=False)))
        # Marker is written before external side effects. If interrupted, reuse only this run.
        with marker.open('x', encoding='utf-8') as stream:
            json.dump({'run_id': record.id, 'job_id': job_id}, stream)
    examples = []
    class ObservedClient(InfoXClient):
        async def events(self, *args, **kwargs):
            async for event in super().events(*args, **kwargs):
                if len(examples) < 80:
                    example = event_example(event.json())
                    if example not in examples: examples.append(example)
                yield event
    manager = RunManager(store, lambda: ObservedClient(InfoXCredentials.load(root)), tracking_timeout=600)
    try:
        await manager.start(record.id)
        await manager.wait(record.id)
        final = store.get(record.id)
        report = {'verified_at': datetime.now().isoformat(), 'stage': final.stage,
            'same_run': record.id == final.id, 'project_accepted': final.project.state == 'accepted',
            'generation_accepted': final.generation.state == 'accepted',
            'video_id_recorded': bool(final.video_id), 'task_id_recorded': bool(final.task_id),
            'snapshot_unchanged': final.snapshot == record.snapshot,
            'tracking': final.tracking.model_dump(mode='json'), 'observed_event_examples': examples,
            'live_failure_event_observed': final.tracking.task_status == 'failed'}
        (root / 'docs/infox/phase3-live-verification.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=True))
    finally:
        await manager.close()
        await jobs.close()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except Exception:
        print('{"error":"verification_interrupted_check_existing_durable_run"}')
        raise SystemExit(1) from None
