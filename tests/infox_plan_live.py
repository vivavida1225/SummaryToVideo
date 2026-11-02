"""Read-only phase 5 check on the existing run and a controlled local copy.

The existing run has zero detected assets and auto_replace=false. Never invent
production findings or create a video to exercise replacement. Only the local
copy receives a synthetic metadata hit; it is never stored or sent to InfoX.
"""

import asyncio
import copy
import json
from datetime import datetime

from backend.config import ROOT
from backend.infox.assets import audit_assets
from backend.infox.client import InfoXClient
from backend.infox.config import InfoXCredentials
from backend.infox.favorites import collect_favorites, choose_favorite
from backend.infox.manager import RunManager
from backend.infox.replacement import make_choices, apply_replacements, validate_replacements
from backend.infox.store import RunStore


async def main(root=ROOT):
    store=RunStore(root)
    original=store.get(json.loads((root/'.runtime/infox-phase3-live.json').read_text(encoding='utf-8'))['run_id'])
    calls=[]
    class ReadOnlyClient(InfoXClient):
        async def _json_once(self,method,path,**kwargs):
            if method=='GET' and path==f'/infomaxai/videos/videostate/{original.video_id}': calls.append('video_state')
            elif method=='POST' and path=='/infomaxai/recommand/video': calls.append('favorites_search')
            elif path in ('/infomaxai/auth/login','/infomaxai/auth/refresh-token','/infomaxai/auth/org-email/device-refresh'):
                calls.append('authentication')
            else: raise AssertionError('remote video write forbidden')
            return await super()._json_once(method,path,**kwargs)
        async def favorite_segment_available(self,raw):
            calls.append('bounded_segment_read')
            return await super().favorite_segment_available(raw)
    manager=RunManager(store,lambda:ReadOnlyClient(InfoXCredentials.load(root)))
    try:
        await manager.plan(original.id);await manager.wait(original.id)
        final=store.get(original.id)
        async with ReadOnlyClient(InfoXCredentials.load(root)) as client:
            before=await client.video_state(original.video_id)
            results=await collect_favorites(client)
            chosen=await choose_favorite(client,results)
            rechecked=await choose_favorite(client,await collect_favorites(client),frozen=chosen)
            controlled=copy.deepcopy(before)
            injected=False
            for scene in controlled['video']['scenes']:
                for node in scene.get('rootShape',{}).get('layers',{}).values():
                    props=node.get('props',{})
                    if props.get('aigeneratable',{}).get('auto_select') is True:
                        files=props.get('media',{}).get('image_files',[])
                        if files and not injected:
                            files[0]['description']='삼성전자 연동 검증용 가정';injected=True
            assert injected
            synthetic_audit=audit_assets(controlled,template_id=original.snapshot.settings.template_id,
                                        run_id='0'*32,video_id=original.video_id)
            choices=make_choices(controlled,synthetic_audit,chosen)
            proposed=apply_replacements(controlled,choices,ephemeral=results.raw)
            checks=validate_replacements(controlled,proposed,choices)
            assert checks.ready
            after=await client.video_state(original.video_id)
        report={'verified_at_kst':datetime.now().astimezone().isoformat(),'existing_phase3_run_reused':True,
            'production_plan_state':final.replacement_plan.state,'production_detected_count':final.asset_audit.counts()['detected'],
            'production_selections':len(final.replacement_plan.selections),'auto_replace':final.snapshot.settings.auto_replace,
            'favorites_total':results.evidence.total,'favorites_pages':results.evidence.pages,
            'general_video_schema_verified':True,'uservideo_nonempty_schema_verified':False,
            'selected_segment_accessible':True,'same_frozen_candidate_rechecked':chosen==rechecked,
            'controlled_local_copy_only':True,'synthetic_metadata_hit_added':True,'controlled_target_count':len(choices),
            'controlled_checks':checks.model_dump(),'same_snapshot':original.snapshot==final.snapshot,
            'same_video_and_task':original.video_id==final.video_id and original.task_id==final.task_id,
            'same_receipts':original.project==final.project and original.generation==final.generation,
            'server_version_unchanged':before['updated_at']==after['updated_at'],
            'saved_favorite_history_unchanged':original.favorite_replacements==final.favorite_replacements,
            'remote_video_writes':0,'operations':calls}
        (root/'docs/infox/phase5-live-verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(report,ensure_ascii=True))
    finally:
        await manager.close()


if __name__=='__main__':
    try: asyncio.run(main())
    except Exception:
        print('{"error":"read_only_plan_verification_incomplete_check_existing_run"}')
        raise SystemExit(1) from None
