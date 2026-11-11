"""Pure local media conversion and independent full-state preservation checks.

No HTTP/save calls. Full proposed JSON, including refreshed URLs, stays in memory.
Current editor timing contract: module 12764 hY/Qg, 30fps, explicit slot widths.
"""

import copy
import hashlib
import json
import math
from urllib.parse import unquote, urlsplit

from .assets import _reference
from .favorites import candidate_from_item
from .plan_models import PlanError, PreservationChecks, ReplacementChoice


LAYOUT_KEYS = frozenset(('boxSize','position','rotate','rotation','objectFit','objectPosition','crop',
    'filterOptions','mosaicRegions','animation','animationEffects','borderOptions','smartContainTint',
    'transparency','slideType','imageMinFrames','showTitle'))
FILE_KEYS = frozenset(('id','indx','tags','title','title_ko','bucket','filename','filename_resized','segment',
    'videoId','frameIndex','timestamp','duration','originalDuration','video_duration','durationInFrames',
    'startFrameVideo','endFrameVideo','muted','volume','playbackRate','image_files','media_type','mime_type',
    'thumbnail','preview_presigned','segment_url_presigned','url_presigned','url','external_url',
    'external_thumbnail_url','resolution_width','resolution_height','file_size','provider','provider_name',
    'source','source_table','score','frame_id','match_engine','score_origin','decision_prob','video_created_at',
    'video_title_raw','created_at','total','is_favorite','notuse','userdefine','captions','text_body','description',
    'frame_stt','img_description_ai','frame_visual_caption','bm25_score','semantic_score','series_key',
    'source_kind','segment_pick','original_filename','crowdpic_code'))


def state_fingerprint(state):
    def stable(value):
        if isinstance(value, dict):
            return {key: stable(item) for key,item in value.items()
                    if key not in ('preview_presigned','segment_url_presigned','url_presigned')}
        if isinstance(value, list):
            return [stable(item) for item in value]
        return value
    try:
        return hashlib.sha256(json.dumps(stable(state), ensure_ascii=False, sort_keys=True,
                                         separators=(',',':'), allow_nan=False).encode()).hexdigest()
    except (TypeError, ValueError):
        raise PlanError('source_changed') from None


def _positive(value):
    return type(value) in (int,float) and math.isfinite(value) and value > 0


def _scene_frames(state, scene):
    if state.get('video',{}).get('frameRate') != 30:
        raise PlanError('unsupported_timing')
    if type(scene.get('durationInFrames')) is int and scene['durationInFrames'] > 0:
        return scene['durationInFrames']
    # The editor fallback also incorporates padding/offset/tail. Don't infer it
    # from duration alone when the server has not provided an explicit budget.
    raise PlanError('unsupported_timing')


def _width(item):
    value=item.get('durationInFrames')
    if value is not None and (type(value) is not int or value <= 0):
        raise PlanError('unsupported_timing')
    return value


def _video_frames(item):
    if not _positive(item.get('duration')):
        raise PlanError('unsupported_timing')
    start=item.get('startFrameVideo',0)
    end=item.get('endFrameVideo',math.floor(item['duration']*30))
    rate=item.get('playbackRate',1)
    if (type(start) is not int or type(end) is not int or start < 0 or end <= start
            or not _positive(rate) or not .25 <= rate <= 4):
        raise PlanError('unsupported_timing')
    return max(1, math.ceil((end-start)/(rate if item.get('muted') is not False else 1)))


def slot_durations(kind, media, budget):
    files=media.get('image_files',[])
    if kind=='VideoLayer' or not files:
        return [budget]
    if not isinstance(files,list) or not all(isinstance(x,dict) for x in files):
        raise PlanError('unsupported_media')
    widths=[_width(item) for item in files]
    if kind=='ImageLayer':
        if any(x.get('media_type')=='video' for x in files):
            raise PlanError('unsupported_media')
        if not any(x is not None for x in widths):
            return [max(1,math.ceil(budget/len(files)))]*len(files)
        fixed=sum(x for x in widths if x is not None)
        unset=widths.count(None)
        fill=max(1,math.ceil(max(0,budget-fixed)/unset)) if unset else max(1,math.ceil(budget/len(files)))
        result=[x if x is not None else fill for x in widths]
        if sum(result)<budget: result[-1]+=budget-sum(result)
        return result
    if kind!='MediaLayer':
        raise PlanError('unsupported_media')
    kinds=['video' if x.get('media_type')=='video' else 'image-defined' if width is not None else 'image-unset'
           for x,width in zip(files,widths)]
    if all(k=='image-unset' for k in kinds):
        return [max(1,math.ceil(budget/len(files)))]*len(files)
    requested=[width if width is not None else _video_frames(item) if k=='video' else 0
               for item,width,k in zip(files,widths,kinds)]
    minimum=media.get('imageMinFrames',90)
    if type(minimum) is not int or minimum < 1:
        raise PlanError('unsupported_timing')
    used=sum(requested); unset=kinds.count('image-unset')
    if used+unset*minimum<=budget:
        if unset:
            fill=max(1,math.ceil(max(0,budget-used)/unset))
            return [fill if k=='image-unset' else n for k,n in zip(kinds,requested)]
        requested[-1]+=budget-used
        return requested
    explicit=[k=='image-defined' or width is not None for k,width in zip(kinds,widths)]
    remaining=max(0,budget-sum(n for n,fixed in zip(requested,explicit) if fixed))
    count=explicit.count(False)
    base=remaining//count if count else 0
    extra=remaining-base*count
    result=[]
    for value,fixed in zip(requested,explicit):
        if fixed: result.append(value)
        else:
            result.append(max(1,base+(1 if extra>0 else 0)))
            if extra>0: extra-=1
    return result


def _target_node(state, target):
    try:
        scene=state['video']['scenes'][target.scene_index]
        if not target.scene_id or scene['id']!=target.scene_id or target.verdict!='detected':
            raise PlanError('source_changed')
        layers=scene['rootShape']['layers']; stack=['ROOT']; seen=set()
        while stack:
            key=stack.pop()
            if not isinstance(key,str) or key in seen:
                raise PlanError('source_changed')
            seen.add(key)
            stack.extend(layers[key].get('child',[]))
        if target.layer_id not in seen:
            raise PlanError('source_changed')
        node=layers[target.layer_id]; kind=node['type']['resolvedName']; media=node['props']['media']
        if kind not in ('ImageLayer','VideoLayer','MediaLayer') or node['props']['aigeneratable']['auto_select'] is not True:
            raise PlanError('unsupported_media')
        files=media.get('image_files',[])
        if not isinstance(files,list) or (kind=='VideoLayer' and files):
            raise PlanError('unsupported_media')
        item=files[target.item_index] if target.item_index is not None else media
        item_kind='video' if kind=='VideoLayer' or item.get('media_type')=='video' else 'image'
        if _reference(item,item_kind)!=target.reference:
            raise PlanError('source_changed')
        return scene,node,media,item
    except (KeyError,IndexError,TypeError,AttributeError):
        raise PlanError('source_changed') from None


def make_choices(state, audit, candidate):
    if audit.state!='completed' or audit.blocking_issues:
        raise PlanError('audit_required')
    result=[]
    for target in audit.items:
        if target.verdict!='detected': continue
        scene,node,media,_=_target_node(state,target)
        widths=slot_durations(node['type']['resolvedName'],media,_scene_frames(state,scene))
        index=target.item_index or 0
        if math.floor(candidate.duration*30)<1:
            raise PlanError('no_usable_candidate')
        result.append(ReplacementChoice(target=target,candidate=candidate,slot_frames=widths[index],
            result_item_index=target.item_index if len(media.get('image_files',[]))>1 else None))
    return tuple(result)


def _layout(media):
    if set(media)-LAYOUT_KEYS-FILE_KEYS:
        raise PlanError('unsupported_media')
    return {key:copy.deepcopy(value) for key,value in media.items() if key in LAYOUT_KEYS}


def _ephemeral(candidate, raw):
    if raw is None: return {}
    if candidate_from_item(raw)!=candidate:
        raise PlanError('selection_unavailable')
    result={}
    for key,filename in (('segment_url_presigned',candidate.segment),('preview_presigned',candidate.thumbnail)):
        if not raw.get(key): continue
        try:
            parsed=urlsplit(raw[key])
            if (parsed.scheme!='https' or parsed.hostname!='kr.object.iwinv.kr' or parsed.port not in (None,443)
                or parsed.username or parsed.password or parsed.fragment
                or unquote(parsed.path)!='/'+candidate.bucket+'/'+filename):
                raise PlanError('selection_unavailable')
        except (TypeError,ValueError):
            raise PlanError('selection_unavailable') from None
        result[key]=raw[key]
    return result


def _new_media(choice, layout, raw=None):
    c=choice.candidate; frames=min(math.floor(c.duration*30),choice.slot_frames)
    if frames<1: raise PlanError('unsupported_timing')
    return {**layout,'id':c.id,'videoId':c.video_id,'bucket':c.bucket,'filename':c.segment,'segment':c.segment,
        'thumbnail':c.thumbnail,'filename_resized':c.thumbnail,'frameIndex':c.frame_index,'timestamp':c.timestamp,
        'title':c.title,'mime_type':c.mime_type,'media_type':'video','resolution_width':c.width,'resolution_height':c.height,
        'duration':c.duration,'originalDuration':c.duration,'durationInFrames':choice.slot_frames,
        'startFrameVideo':0,'endFrameVideo':frames,'muted':True,'volume':0,'playbackRate':1,
        **_ephemeral(c,raw)}


def apply_replacements(state, choices, *, ephemeral=None):
    if any(choice.clips for choice in choices):
        from .sequences import apply_sequences
        return apply_sequences(state, choices, ephemeral=ephemeral)
    ephemeral=ephemeral or {}; result=copy.deepcopy(state); groups={}; seen=set()
    for choice in choices:
        key=(choice.target.scene_index,choice.target.layer_id,choice.target.item_index)
        if key in seen: raise PlanError('transform_invalid')
        seen.add(key); _target_node(state,choice.target)
        groups.setdefault(key[:2],[]).append(choice)
    for (si,lid),selected in groups.items():
        scene,node,media,_=_target_node(state,selected[0].target)
        widths=slot_durations(node['type']['resolvedName'],media,_scene_frames(state,scene))
        new_node=result['video']['scenes'][si]['rootShape']['layers'][lid]
        files=media.get('image_files',[])
        for choice in selected:
            if choice.slot_frames!=widths[choice.target.item_index or 0]:
                raise PlanError('source_changed')
        if len(files)>1:
            output=copy.deepcopy(files)
            for index,item in enumerate(output): item['durationInFrames']=widths[index]
            for choice in selected:
                index=choice.target.item_index
                if index is None or choice.result_item_index!=index: raise PlanError('transform_invalid')
                output[index]=_new_media(choice,_layout(files[index]),ephemeral.get(choice.candidate.key()))
            new_node['props']['media']={**_layout(media),'image_files':output}
            new_node['type']['resolvedName']='MediaLayer'
        else:
            choice=selected[0]
            if len(selected)!=1 or choice.result_item_index is not None: raise PlanError('transform_invalid')
            layout=_layout(media)
            if files: layout.update(_layout(files[0]))
            new_node['props']['media']={**_new_media(choice,layout,ephemeral.get(choice.candidate.key())),'image_files':[]}
            new_node['type']['resolvedName']='VideoLayer'
    if not validate_replacements(state,result,choices).ready:
        raise PlanError('transform_invalid')
    return result


def validate_replacements(before, after, choices):
    """Check scope/normal media/timeline/selected references independently of conversion."""
    if any(choice.clips for choice in choices):
        from .sequences import validate_sequences
        return validate_sequences(before, after, choices)
    scope,normal,layout,timing,refs,removed=True,True,True,True,True,True
    try:
        left,right=copy.deepcopy(before),copy.deepcopy(after)
        groups={}
        for choice in choices: groups.setdefault((choice.target.scene_index,choice.target.layer_id),[]).append(choice)
        for (si,lid),selected in groups.items():
            scene,node,media,_=_target_node(before,selected[0].target)
            new=after['video']['scenes'][si]['rootShape']['layers'][lid]; new_media=new['props']['media']
            files=media.get('image_files',[]); is_bundle=len(files)>1
            new_kind=new['type']['resolvedName']
            refs=refs and new_kind==('MediaLayer' if is_bundle else 'VideoLayer')
            if not is_bundle: refs=refs and new_media.get('image_files')==[]
            slots=slot_durations(node['type']['resolvedName'],media,_scene_frames(before,scene))
            timing=timing and slot_durations(new_kind,new_media,_scene_frames(after,after['video']['scenes'][si]))==slots
            expected_layout=_layout(media)
            if len(files)==1: expected_layout.update(_layout(files[0]))
            layout=layout and all(new_media.get(key)==value for key,value in expected_layout.items())
            if is_bundle:
                new_files=new_media.get('image_files',[])
                refs=refs and set(new_media)==set(expected_layout)|{'image_files'}
                normal=normal and len(new_files)==len(files)
                replaced={x.target.item_index for x in selected}
                for index,item in enumerate(files):
                    if index not in replaced:
                        normal=normal and new_files[index]=={**item,'durationInFrames':slots[index]}
            for choice in selected:
                target=choice.target; c=choice.candidate
                actual=new_media['image_files'][choice.result_item_index] if choice.result_item_index is not None else new_media
                expected={'id':c.id,'videoId':c.video_id,'bucket':c.bucket,'filename':c.segment,'segment':c.segment,
                    'frameIndex':c.frame_index,'timestamp':c.timestamp,'thumbnail':c.thumbnail,'filename_resized':c.thumbnail,
                    'title':c.title,'duration':c.duration,'originalDuration':c.duration,'mime_type':c.mime_type,
                    'resolution_width':c.width,'resolution_height':c.height,'media_type':'video','startFrameVideo':0,
                    'endFrameVideo':min(math.floor(c.duration*30),choice.slot_frames),'durationInFrames':choice.slot_frames,
                    'muted':True,'volume':0,'playbackRate':1}
                child_layout=_layout(files[target.item_index]) if is_bundle else expected_layout
                layout=layout and {key:actual[key] for key in actual if key in LAYOUT_KEYS}==child_layout
                allowed=set(expected)|set(child_layout)|{key for key in ('preview_presigned','segment_url_presigned') if key in actual}
                if not is_bundle: allowed.add('image_files')
                refs=refs and set(actual)==allowed
                refs=refs and all(type(actual.get(k)) is type(v) and actual[k]==v for k,v in expected.items())
                _ephemeral(c,{**{'id':c.id,'videoId':c.video_id,'bucket':c.bucket,'segment':c.segment,'thumbnail':c.thumbnail,
                    'frameIndex':c.frame_index,'timestamp':c.timestamp,'duration':c.duration,'mime_type':c.mime_type,
                    'resolution_width':c.width,'resolution_height':c.height,'title':c.title,'is_favorite':True},
                    **{key:actual[key] for key in ('preview_presigned','segment_url_presigned') if key in actual}})
                old=target.reference
                removed=removed and not (actual.get('bucket')==old.bucket and
                    (actual.get('segment')==old.segment if old.kind=='video' else actual.get('filename')==old.filename))
            for data in (left,right):
                masked=data['video']['scenes'][si]['rootShape']['layers'][lid]
                masked['props']['media']='<allowed-media-change>'
                masked['type']['resolvedName']='<allowed-type-change>'
        scope=left==right
    except (KeyError,IndexError,TypeError,ValueError,AttributeError,PlanError):
        return PreservationChecks()
    return PreservationChecks(scope_preserved=bool(scope),normal_assets_preserved=bool(normal),layout_preserved=bool(layout),
        timing_preserved=bool(timing),references_consistent=bool(refs),old_active_references_removed=bool(removed))
