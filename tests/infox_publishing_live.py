"""Explicit user-designated saved target only. This command performs one publication.

Never run as part of pytest or browser smoke. Durable receipts live in run-root;
re-running this command can only restore a publication, never submit it again.
"""
import argparse
import asyncio
import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from backend.clipboard import WindowsClipboard
from backend.infox.client import InfoXClient
from backend.infox.config import InfoXCredentials
from backend.infox.publishing.manager import PublicationManager
from backend.infox.publishing.models import PublishInput
from backend.infox.publishing.store import PublicationStore
from backend.infox.publishing.tracking import history_logs, normalize_publish_event
from backend.infox.replacement import state_fingerprint
from backend.infox.run_models import RunRecord, Snapshot, Scene, ProductionSettings, Receipt, Tracking, now, text_hash
from backend.infox.save_models import AssetSave, remote_version
from backend.infox.store import RunStore
from backend.infox.tracking import verify_draft

KST = timezone(timedelta(hours=9))


def imported_saved_record(before, after, history, market_date):
    PublishInput(intent_id=str(uuid4()), market_date=market_date)
    video_id = before.get('id')
    logs = history_logs(history, video_id)
    if (before.get('can_edit') is not True or after.get('can_edit') is not True
            or before.get('id') != after.get('id') or before.get('updated_at') != after.get('updated_at')
            or state_fingerprint(before) != state_fingerprint(after) or len(logs) != 1
            or logs[0]['state'] != 'videosgenerationend'):
        raise ValueError('saved target not verified')
    version = remote_version(before.get('updated_at'))
    scenes = before['video']['scenes']
    voice = scenes[0]['voiceover']
    settings = ProductionSettings(voice_id=voice['voice_id'], voice_speed=voice['voice_speed'],
        voice_provider=voice['voice_provider'], auto_replace=False)
    texts = [scene['voiceover']['content'] for scene in scenes]
    timestamp = now()
    job_id = datetime.now(KST).strftime('%Y%m%d_%H%M%S_%f_') + uuid4().hex[:8]
    text = '\n'.join(texts)
    snapshot = Snapshot(job_id=job_id, revision=0, text=text, text_sha256=text_hash(text),
        scenes=tuple(Scene(line_number=i+1, scene_index=i, text=t) for i,t in enumerate(texts)),
        settings=settings, confirmed_at=timestamp, review_confirmed=True, generation_state='completed',
        validation_issues=(), warnings=('imported_user_designated_saved_target',))
    # These are observed existing project/draft results, not new creation
    # requests. No earlier local run is modified or retrospectively attributed.
    r = RunRecord(id=uuid4().hex, created_at=timestamp, updated_at=timestamp, snapshot=snapshot,
        video_id=video_id, task_id=logs[0]['task_id'], project=Receipt(state='accepted'),
        generation=Receipt(state='accepted'), stage='ready_for_editor',
        asset_save=AssetSave(state='unchanged_verified', verified_version=version,
            verified_fingerprint=state_fingerprint(after), checked_at=timestamp))
    check = verify_draft(r, after)
    if not check.ready:
        raise ValueError('saved target not ready')
    return r.model_copy(update={'tracking': Tracking(completion_seen=True, task_status='completed', draft=check)})


async def main(args):
    root=Path(args.run_root).resolve()
    run_store=RunStore(root); publications=PublicationStore(run_store)
    video_id=args.video_id
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',video_id): raise ValueError('invalid target')
    input=PublishInput(intent_id=str(uuid4()),market_date=args.market_date)
    counts=Counter(); events=[]
    class LiveClient(InfoXClient):
        async def _json_once(self, method, path, **kwargs):
            operation=next((operation for needle,operation in (
                ('/worker/process_rendering/','render'),('/worker/infox_generate_metainfo_youtube/','metadata_generate'),
                ('/sns_upload_info','metadata_update'),('/worker/infox_upload_youtube_video/','upload')) if needle in path),None)
            if operation: counts[operation]+=1
            elif method=='GET' and '/videos/videostate/' in path: counts['video_state']+=1
            elif method=='GET' and '/videos/video_render_logs/' in path: counts['history']+=1
            elif '/worker/render_charge_preview/' in path: counts['charge_preview']+=1
            elif '/auth/' not in path: raise ValueError('unexpected remote operation')
            return await super()._json_once(method,path,**kwargs)
        async def events(self, target, **kwargs):
            async for frame in super().events(target,**kwargs):
                body=frame.json()
                def capture(value, depth=0):
                    if not isinstance(value,dict) or depth>6: return
                    kw=value.get('additional_kwargs')
                    if isinstance(kw,dict) and isinstance(kw.get('taskid'),str):
                        event=normalize_publish_event(body,target,kw['taskid'])
                        if event and len(events)<512: events.append(event)
                    for key in ('data','message','messages'):
                        nested=value.get(key)
                        for item in nested if isinstance(nested,list) else [nested]: capture(item,depth+1)
                capture(body)
                yield frame
    factory=lambda:LiveClient(InfoXCredentials.load(root))
    # Search existing durable runs first; never replace a run's video_id.
    with run_store._transaction() as conn:
        existing=[run_store._decode(row) for row in conn.execute('SELECT record FROM runs')
                  if json.loads(row['record']).get('video_id')==video_id]
    if len(existing)>1: raise ValueError('ambiguous local target')
    if existing:
        run=existing[0]
    else:
        if not args.import_saved_target: raise ValueError('no associated local run')
        async with factory() as client:
            before=await client.video_state(video_id)
            history=await client.render_logs(video_id)
            after=await client.video_state(video_id)
        run=imported_saved_record(before,after,history,args.market_date)
        digest=hashlib.sha256(('user-designated-live:'+video_id).encode()).hexdigest()
        run,_=run_store.create(run,snapshot_key=digest,intent_id=uuid4().hex,digest=digest,new_run=False)
    clipboard_readback=False
    class VerifiedClipboard(WindowsClipboard):
        def write(self,value):
            nonlocal clipboard_readback
            counts['clipboard_write']+=1
            super().write(value)
            clipboard_readback=self.read().get('text')==value
            if not clipboard_readback: raise OSError('clipboard readback unconfirmed')
    manager=PublicationManager(publications,factory,VerifiedClipboard())
    try:
        await manager.recover()
        r=await manager.start(run.id,input)
        previous=None
        while manager.active(r.id):
            r=publications.get(r.id)
            status=(r.stage,r.progress)
            if status!=previous:
                print(json.dumps({'stage':r.stage,'progress':r.progress}),flush=True)
                previous=status
            await asyncio.sleep(1)
        r=publications.get(r.id)
        report={
            'verification_time':datetime.now(KST).isoformat(),
            'mode':'real_user_designated_first_publication','video_id':video_id,'run_id':run.id,'publication_id':r.id,
            'imported_saved_target':'imported_user_designated_saved_target' in run.snapshot.warnings,
            'market_date':r.snapshot.market_date,'title':r.snapshot.title,'description':r.snapshot.description,
            'stage':r.stage,'failed_stage':r.failed_stage,'error_code':r.error_code,
            'render_task_id':r.render_task_id,'metadata_task_id':r.metadata_task_id,'upload_task_id':r.upload_task_id,
            'operations_this_invocation':dict(counts),'receipts':r.receipts.model_dump(mode='json'),
            'intermediate_render_progress_received':any(e.task_id==r.render_task_id and e.progress is not None and 0<e.progress<100 for e in events),
            'render_progress_values':sorted({e.progress for e in events if e.task_id==r.render_task_id and e.progress is not None}),
            'infox_unlisted_verified':r.uploaded_verified,'share_url':r.share_url,'clipboard_state':r.clipboard_state,
            'clipboard_readback_verified':clipboard_readback,
            'youtube_direct_privacy_or_playback_verified':False,'live_failure_paths_tested':False,
            'remote_editor_context_writes':0,
        }
        destination=Path(args.report).resolve(); destination.parent.mkdir(parents=True,exist_ok=True)
        destination.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=True),flush=True)
    finally:
        await manager.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root',required=True)
    parser.add_argument('--video-id',required=True)
    parser.add_argument('--market-date',required=True)
    parser.add_argument('--report',required=True)
    parser.add_argument('--import-saved-target',action='store_true')
    try: asyncio.run(main(parser.parse_args()))
    except Exception as error:
        print(json.dumps({'error':'live_verification_stopped','code':getattr(error,'code','unconfirmed')}),flush=True)
        raise SystemExit(1) from None
