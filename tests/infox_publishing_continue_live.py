"""Operator-authorized, one-operation-at-a-time live checks, never an automatic resume.

The original stopped publication is immutable. A separate SQLite journal claims
each remaining operation before sending. Reinvocation cannot repeat any claim.
"""
import argparse
import asyncio
import json
import math
import re
import time
from contextlib import suppress
from pathlib import Path

from backend.clipboard import WindowsClipboard
from backend.infox.client import InfoXClient
from backend.infox.config import InfoXCredentials
from backend.infox.publishing.store import PublicationStore
from backend.infox.publishing.tracking import normalize_publish_event, parse_youtube_share_url, verify_publish_stage
from backend.infox.run_models import RunError, now
from backend.infox.store import RunStore

OPERATIONS=('metadata_generate','metadata_update','upload','copy')


class ManualVerificationStore:
    def __init__(self, publications):
        self.publications=publications

    def _edit(self,id,edit):
        with self.publications.run_store._transaction() as conn:
            self.publications._tables(conn)
            original=self.publications._decode(conn.execute('SELECT record FROM publications WHERE id=?',(id,)).fetchone())
            if (original.stage!='needs_attention' or original.failed_stage!='rendering'
                    or original.error_code!='stream_unrecognized' or not original.render_task_id
                    or original.receipts.render.state!='accepted'
                    or any(getattr(original.receipts,op).state!='not_sent' for op in OPERATIONS[:-1])):
                raise RunError('manual_verification_not_authorized')
            conn.execute('CREATE TABLE IF NOT EXISTS publication_live_verifications '
                         '(publication_id TEXT PRIMARY KEY REFERENCES publications(id), record TEXT NOT NULL)')
            row=conn.execute('SELECT record FROM publication_live_verifications WHERE publication_id=?',(id,)).fetchone()
            record=json.loads(row['record']) if row else {'publication_id':id,'render_task_id':original.render_task_id,
                'receipts':{op:{'state':'not_sent','verified':False} for op in OPERATIONS},
                'metadata_task_id':None,'upload_task_id':None,'keywords':[],'share_url':None,'youtube_video_id':None}
            edit(record)
            record['updated_at']=now()
            conn.execute('INSERT INTO publication_live_verifications VALUES (?,?) '
                         'ON CONFLICT(publication_id) DO UPDATE SET record=excluded.record',(id,json.dumps(record)))
            return record

    def get(self,id):
        return self._edit(id,lambda r:None)

    def claim(self,id,operation):
        if operation not in OPERATIONS: raise RunError('invalid_operation')
        def edit(r):
            receipt=r['receipts'][operation]
            index=OPERATIONS.index(operation)
            if receipt['state']!='not_sent' or index and not r['receipts'][OPERATIONS[index-1]]['verified']:
                raise RunError('request_already_recorded')
            receipt.update(state='intent_recorded',intent_at=now())
        return self._edit(id,edit)

    def finish(self,id,operation,outcome,evidence,*,verified=False):
        if outcome not in ('accepted','unknown') or operation not in OPERATIONS: raise RunError('invalid_operation')
        if set(evidence)-{'metadata_task_id','upload_task_id','keywords','share_url','youtube_video_id','clipboard_readback_verified'}:
            raise RunError('invalid_operation')
        for key in ('metadata_task_id','upload_task_id'):
            if key in evidence and not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',evidence[key]):
                raise RunError('invalid_external_id')
        def edit(r):
            receipt=r['receipts'][operation]
            if receipt['state']!='intent_recorded': raise RunError('request_already_recorded')
            receipt.update(state=outcome,verified=verified,acknowledged_at=now())
            r.update(evidence)
        return self._edit(id,edit)

    def verify(self,id,operation,evidence):
        def edit(r):
            if r['receipts'][operation]['state']!='accepted': raise RunError('invalid_stage')
            r['receipts'][operation]['verified']=True
            r.update(evidence)
        return self._edit(id,edit)


async def execute(args):
    publications=PublicationStore(RunStore(Path(args.run_root)))
    original=publications.get(args.publication_id)
    manual=ManualVerificationStore(publications)
    record=manual.get(original.id)
    events=[]; history_observations=[]; connected=asyncio.Event(); stream_error=[]; pump=None
    operation=args.operation
    async with InfoXClient(InfoXCredentials.load(Path(args.run_root))) as client:
        # This is a manual readback check, not a transition of the stopped run.
        history=await client.render_logs(original.video_id)
        rendering=original.model_copy(update={'stage':'rendering'})
        render=verify_publish_stage(rendering,history)
        if render.status!='completed': raise RunError('render_not_verified')
        current=original.model_copy(update={'render_file_fingerprint':render.evidence['render_file_fingerprint'],
            'metadata_task_id':record['metadata_task_id'],'upload_task_id':record['upload_task_id'],
            'keywords':tuple(record['keywords'])})
        state=await client.video_state(original.video_id)
        if str(state.get('id'))!=original.video_id or state.get('can_edit') is not True:
            raise RunError('preflight_unconfirmed')

        async def collect():
            try:
                async for frame in client.events(original.video_id):
                    body=frame.json()
                    if not isinstance(body,dict): raise ValueError('protocol error')
                    if body.get('type')=='connection' and body.get('status')=='connected' and body.get('channel')==f'infox:{original.video_id}':
                        connected.set(); continue
                    def visit(value,depth=0):
                        if not isinstance(value,dict) or depth>6:return
                        kw=value.get('additional_kwargs')
                        if isinstance(kw,dict) and isinstance(kw.get('taskid'),str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',kw['taskid']):
                            normalized=normalize_publish_event(body,original.video_id,kw['taskid'])
                            status=kw.get('task_status')
                            if normalized and len(events)<512:
                                events.append({**normalized.model_dump(),'raw_status_present':'task_status' in kw,
                                    'raw_status':status if isinstance(status,str) and re.fullmatch(r'[A-Za-z0-9_-]{0,32}',status) else None})
                        for key in ('messages','message','data'):
                            nested=value.get(key)
                            for item in nested if isinstance(nested,list) else [nested]:visit(item,depth+1)
                    visit(body)
            except asyncio.CancelledError: raise
            except Exception: stream_error.append('stream_unconfirmed')
            finally: connected.clear()

        try:
            if operation!='copy':
                pump=asyncio.create_task(collect())
                await asyncio.wait_for(connected.wait(),15)
                if stream_error or not connected.is_set(): raise RunError('stream_unconfirmed')
            if operation in ('metadata_update','upload'):
                verification=verify_publish_stage(current.model_copy(update={'stage':
                    'generating_metadata' if operation=='metadata_update' else 'saving_metadata'}),history)
                if verification.status!='completed': raise RunError('metadata_not_verified')
            manual.claim(original.id,operation)
            try:
                if operation=='metadata_generate':
                    id=await client.generate_youtube_metadata(original.video_id,original.render_task_id)
                    manual.finish(original.id,operation,'accepted',{'metadata_task_id':id})
                    current=current.model_copy(update={'stage':'generating_metadata','metadata_task_id':id})
                elif operation=='metadata_update':
                    await client.update_youtube_metadata(original.video_id,current.metadata_task_id,
                        title=current.snapshot.title,description=current.snapshot.description,keywords=list(current.keywords))
                    manual.finish(original.id,operation,'accepted',{})
                    current=current.model_copy(update={'stage':'saving_metadata'})
                elif operation=='upload':
                    id=await client.upload_youtube(original.video_id,current.metadata_task_id)
                    manual.finish(original.id,operation,'accepted',{'upload_task_id':id})
                    current=current.model_copy(update={'stage':'uploading','upload_task_id':id})
                else:
                    _,url=parse_youtube_share_url(record['share_url'])
                    clipboard=WindowsClipboard()
                    await asyncio.to_thread(clipboard.write,url)
                    matched=(await asyncio.to_thread(clipboard.read)).get('text')==url
                    if not matched: raise RunError('clipboard_unconfirmed')
                    manual.finish(original.id,operation,'accepted',{'clipboard_readback_verified':True},verified=True)
                    return
            except BaseException:
                with suppress(RunError): manual.finish(original.id,operation,'unknown',{})
                raise
            deadline=time.monotonic()+({'metadata_generate':300,'metadata_update':60,'upload':600}[operation])
            while time.monotonic()<deadline:
                relevant={current.render_task_id,current.metadata_task_id,current.upload_task_id}
                if any(e['task_id'] in relevant and e['status']=='failed' for e in events): raise RunError('remote_task_failed')
                history=await client.render_logs(original.video_id)
                await asyncio.sleep(.1)
                for row in history.get('logs',[]):
                    if row.get('task_id') in relevant and len(history_observations)<100:
                        state=row.get('state')
                        observation={'task_id':row['task_id'],'state':state if isinstance(state,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}',state) else None,
                            'file_present':bool(row.get('file_name')),'bucket_present':bool(row.get('bucket')),
                            'info_is_object':isinstance(row.get('sns_upload_info'),dict),'sns_url_present':bool(row.get('sns_url'))}
                        if observation not in history_observations: history_observations.append(observation)
                if any(e['task_id'] in relevant and e['status']=='failed' for e in events): raise RunError('remote_task_failed')
                result=verify_publish_stage(current,history)
                if result.status=='completed':
                    evidence={k:v for k,v in result.evidence.items() if k in ('keywords','share_url','youtube_video_id')}
                    manual.verify(original.id,operation,evidence)
                    break
                if result.status!='waiting': raise RunError(result.error_code or 'history_unconfirmed')
                await asyncio.sleep(2)
            else: raise RunError('stage_timeout')
        finally:
            if pump:
                pump.cancel(); await asyncio.gather(pump,return_exceptions=True)
            # Persist only allowlisted scalar task statuses/progress; never payloads.
            record=manual.get(original.id)
            record['observed_events']=events
            record['history_observations']=history_observations
            destination=Path(args.report)
            existing=json.loads(destination.read_text(encoding='utf-8'))
            prior=existing.get('authorized_manual_verification',{})
            logs=prior.get('operation_events',{})
            logs[operation]=events
            existing['authorized_manual_verification']={**record,'operation_events':logs,
                'automatic_publication_unchanged':publications.get(original.id)==original,
                'approved_scope':'Existing render; remaining operations once each; no render or automatic resume'}
            destination.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print(json.dumps({'operation':operation,'receipt':record['receipts'][operation],
                'events':events,'share_url':record.get('share_url')},ensure_ascii=True),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root',required=True)
    parser.add_argument('--publication-id',required=True)
    parser.add_argument('--operation',choices=OPERATIONS,required=True)
    parser.add_argument('--report',required=True)
    parser.add_argument('--operator-authorized',action='store_true',required=True)
    args=parser.parse_args()
    try: asyncio.run(execute(args))
    except Exception as error:
        print(json.dumps({'error':'manual_verification_stopped','code':getattr(error,'code','unconfirmed')}),flush=True)
        raise SystemExit(1) from None
