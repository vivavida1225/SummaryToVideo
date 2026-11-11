"""Frame-exact multi-clip replacement. Legacy plans retain their original converter."""

import copy
import math
import random

from .plan_models import PlanError, PreservationChecks, ReplacementChoice, ReplacementClip
from .replacement import (_target_node, _scene_frames, slot_durations, _layout, _new_media,
                          _ephemeral)


def choice_clips(choice):
    return choice.clips or (ReplacementClip(candidate=choice.candidate, slot_frames=choice.slot_frames,
                                            result_item_index=choice.result_item_index),)


def make_sequence_choices(state, audit, candidates, *, rng=None):
    if audit.state != 'completed' or audit.blocking_issues:
        raise PlanError('audit_required')
    rng = rng or random.SystemRandom()
    unique = {(c.bucket,c.segment):c for c in reversed(candidates)}
    pool = tuple(unique.values())
    result = []
    offsets = {}
    for target in audit.items:
        if target.verdict != 'detected':
            continue
        scene,node,media,_ = _target_node(state,target)
        widths = slot_durations(node['type']['resolvedName'],media,_scene_frames(state,scene))
        frames = widths[target.item_index or 0]
        count = (frames + 179) // 180
        base, extra = divmod(frames,count)
        eligible = [c for c in pool if math.floor(c.duration * 30) >= base + bool(extra)]
        if not eligible:
            raise PlanError('no_usable_candidate')
        rng.shuffle(eligible)
        selected = eligible[:count]
        key = (target.scene_index,target.layer_id)
        index = (target.item_index or 0) + offsets.get(key,0)
        bundled = len(media.get('image_files',[])) > 1 or count > 1
        clips = tuple(ReplacementClip(candidate=selected[i % len(selected)],
            slot_frames=base + (1 if i < extra else 0),
            result_item_index=index+i if bundled else None) for i in range(count))
        result.append(ReplacementChoice(target=target,candidate=clips[0].candidate,slot_frames=frames,
            result_item_index=clips[0].result_item_index,clips=clips,available_candidates=len(eligible)))
        offsets[key] = offsets.get(key,0) + count - 1
    return tuple(result)


def _groups(state, choices):
    groups, seen = {}, set()
    for choice in choices:
        target = choice.target
        key = (target.scene_index,target.layer_id,target.item_index)
        if key in seen or not choice.clips:
            raise PlanError('transform_invalid')
        seen.add(key)
        _target_node(state,target)
        clips = choice.clips
        if (choice.candidate != clips[0].candidate or choice.result_item_index != clips[0].result_item_index
                or len(clips) != (choice.slot_frames + 179) // 180
                or sum(c.slot_frames for c in clips) != choice.slot_frames
                or max(c.slot_frames for c in clips)-min(c.slot_frames for c in clips) > 1
                or any(c.slot_frames > math.floor(c.candidate.duration*30) for c in clips)):
            raise PlanError('transform_invalid')
        groups.setdefault(key[:2],[]).append(choice)
    return groups


def apply_sequences(state, choices, *, ephemeral=None):
    ephemeral = ephemeral or {}
    result = copy.deepcopy(state)
    for (si,lid), selected in _groups(state,choices).items():
        scene,node,media,_ = _target_node(state,selected[0].target)
        files = media.get('image_files',[])
        widths = slot_durations(node['type']['resolvedName'],media,_scene_frames(state,scene))
        by_index = {c.target.item_index or 0:c for c in selected}
        output = []
        bundled = len(files) > 1 or any(len(c.clips) > 1 for c in selected)
        for index,item in enumerate(files or [media]):
            choice = by_index.get(index)
            if choice is None:
                output.append({**copy.deepcopy(item),'durationInFrames':widths[index]})
                continue
            if choice.slot_frames != widths[index]:
                raise PlanError('source_changed')
            layout = _layout(item) if len(files) > 1 else {**_layout(media),**(_layout(item) if files else {})}
            for clip in choice.clips:
                if clip.result_item_index != (len(output) if bundled else None):
                    raise PlanError('transform_invalid')
                output.append(_new_media(clip,layout,ephemeral.get(clip.candidate.key())))
        new = result['video']['scenes'][si]['rootShape']['layers'][lid]
        if bundled:
            new['type']['resolvedName'] = 'MediaLayer'
            new['props']['media'] = {**_layout(media),'image_files':output}
        else:
            new['type']['resolvedName'] = 'VideoLayer'
            new['props']['media'] = {**output[0],'image_files':[]}
    if not validate_sequences(state,result,choices).ready:
        raise PlanError('transform_invalid')
    return result


def validate_sequences(before, after, choices):
    """Independently check output positions, every file field, and untouched state."""
    try:
        left,right = copy.deepcopy(before),copy.deepcopy(after)
        for (si,lid), selected in _groups(before,choices).items():
            scene,node,media,_ = _target_node(before,selected[0].target)
            new_scene = after['video']['scenes'][si]
            new = new_scene['rootShape']['layers'][lid]
            new_media = new['props']['media']
            files = media.get('image_files',[])
            widths = slot_durations(node['type']['resolvedName'],media,_scene_frames(before,scene))
            bundled = len(files) > 1 or any(len(c.clips) > 1 for c in selected)
            kind = 'MediaLayer' if bundled else 'VideoLayer'
            if new['type']['resolvedName'] != kind:
                raise PlanError('transform_invalid')
            output = new_media['image_files'] if bundled else [new_media]
            if not bundled and new_media.get('image_files') != []:
                raise PlanError('transform_invalid')
            if bundled and new_media != {**_layout(media),'image_files':output}:
                raise PlanError('transform_invalid')
            by_index = {c.target.item_index or 0:c for c in selected}
            cursor, expected_widths = 0, []
            for index,item in enumerate(files or [media]):
                choice = by_index.get(index)
                if choice is None:
                    if output[cursor] != {**item,'durationInFrames':widths[index]}:
                        raise PlanError('transform_invalid')
                    cursor += 1
                    expected_widths.append(widths[index])
                    continue
                if choice.slot_frames != widths[index]:
                    raise PlanError('source_changed')
                layout = _layout(item) if len(files) > 1 else {**_layout(media),**(_layout(item) if files else {})}
                for clip in choice.clips:
                    c = clip.candidate
                    if clip.result_item_index != (cursor if bundled else None):
                        raise PlanError('transform_invalid')
                    actual = output[cursor]
                    expected = {'id':c.id,'videoId':c.video_id,'bucket':c.bucket,'filename':c.segment,'segment':c.segment,
                        'thumbnail':c.thumbnail,'filename_resized':c.thumbnail,'frameIndex':c.frame_index,'timestamp':c.timestamp,
                        'title':c.title,'mime_type':c.mime_type,'media_type':'video','resolution_width':c.width,
                        'resolution_height':c.height,'duration':c.duration,'originalDuration':c.duration,
                        'durationInFrames':clip.slot_frames,'startFrameVideo':0,'endFrameVideo':clip.slot_frames,
                        'muted':True,'volume':0,'playbackRate':1,**layout}
                    if not bundled:
                        expected['image_files'] = []
                    expected.update(_ephemeral(c,{**{'id':c.id,'videoId':c.video_id,'bucket':c.bucket,'segment':c.segment,
                        'thumbnail':c.thumbnail,'frameIndex':c.frame_index,'timestamp':c.timestamp,'duration':c.duration,
                        'mime_type':c.mime_type,'resolution_width':c.width,'resolution_height':c.height,
                        'title':c.title,'is_favorite':True},**{k:actual[k] for k in ('preview_presigned','segment_url_presigned') if k in actual}}))
                    if (set(actual) != set(expected) or any(type(actual[k]) is not type(v) or actual[k] != v
                                                           for k,v in expected.items())):
                        raise PlanError('transform_invalid')
                    old = choice.target.reference
                    if c.bucket == old.bucket and c.segment == (old.segment if old.kind == 'video' else old.filename):
                        raise PlanError('transform_invalid')
                    cursor += 1
                    expected_widths.append(clip.slot_frames)
            if cursor != len(output) or slot_durations(kind,new_media,_scene_frames(after,new_scene)) != expected_widths:
                raise PlanError('transform_invalid')
            for value in (left,right):
                masked = value['video']['scenes'][si]['rootShape']['layers'][lid]
                masked['props']['media'] = '<allowed-media-change>'
                masked['type']['resolvedName'] = '<allowed-type-change>'
        if left != right:
            raise PlanError('transform_invalid')
    except (KeyError,IndexError,TypeError,ValueError,AttributeError,PlanError):
        return PreservationChecks()
    return PreservationChecks(**{k:True for k in PreservationChecks.model_fields})
