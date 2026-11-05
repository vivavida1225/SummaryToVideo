"""Pure inspection of connected auto-selected media. No remote reads or writes.

Only exact file/segment metadata is evidence. Raw text and URLs stay in memory;
reports contain safe references, configured terms and structural source paths.
"""

import math
import re
import unicodedata
from datetime import datetime, timezone

from .asset_models import AssetAudit, AssetHit, AssetItem, AssetIssue, AssetPolicy, AssetReference, blocking_asset_issues


FIELDS = ('title', 'title_ko', 'video_title_raw', 'tags', 'description', 'text_body',
          'img_description_ai', 'frame_visual_caption', 'frame_stt')


def _stable(value):
    if (isinstance(value, str) and value.strip() and len(value) <= 512 and
        not any(ord(c) < 32 for c in value) and not any(c in value for c in ('?', '#', '://'))):
        return value
    return None


def _id(value):
    return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value) else None


def _number(value, *, integer=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        return None
    return int(value) if integer and int(value) == value else None if integer else float(value)


def _reference(media, kind):
    return AssetReference(kind=kind, bucket=_stable(media.get('bucket')), filename=_stable(media.get('filename')),
        segment=_stable(media.get('segment')) if kind == 'video' else None,
        video_id=_stable(media.get('videoId')) if kind == 'video' else None,
        frame_index=_number(media.get('frameIndex'), integer=True) if kind == 'video' else None,
        timestamp=_number(media.get('timestamp')) if kind == 'video' else None,
        start_frame=_number(media.get('startFrameVideo'), integer=True) if kind == 'video' else None,
        end_frame=_number(media.get('endFrameVideo'), integer=True) if kind == 'video' else None)


def _same_file(ref, candidate):
    # Bundles describe their children; a leftover parent title is never evidence.
    if candidate.get('image_files'):
        return False
    if not ref.bucket or not ref.filename:
        return False
    if candidate.get('bucket') != ref.bucket or candidate.get('filename') != ref.filename:
        return False
    if ref.kind == 'video':
        if not (ref.segment and candidate.get('segment') == ref.segment and
                (not ref.video_id or candidate.get('videoId') == ref.video_id)):
            return False
        for field, value in (('frameIndex', ref.frame_index), ('timestamp', ref.timestamp),
                             ('startFrameVideo', ref.start_frame), ('endFrameVideo', ref.end_frame)):
            if value is not None and field in candidate and candidate[field] is not None and candidate[field] != value:
                return False
    return True


def _texts(value, path, unreadable, budget, depth=0):
    budget[0] -= 1
    if depth > 8 or budget[0] < 0:
        unreadable.append(path)
        return
    if isinstance(value, str):
        if value.strip():
            yield path, value
    elif isinstance(value, list):
        for i, item in enumerate(value):
            if budget[0] < 0:
                break
            yield from _texts(item, f'{path}[{i}]', unreadable, budget, depth + 1)
    elif isinstance(value, dict):
        if value and (not any(key in value for key in ('text', 'name', 'label', 'description')) or
                any(key not in ('text', 'name', 'label', 'description', 'id', 'score', 'lang', 'language') for key in value)):
            unreadable.append(path)
        for key in ('text', 'name', 'label', 'description'):
            if key in value:
                yield from _texts(value[key], f'{path}.{key}', unreadable, budget, depth + 1)
    elif value is not None:
        unreadable.append(path)


def _patterns(policy):
    for term in policy.aliases:
        chars = ''.join(unicodedata.normalize('NFKC', term).casefold().split())
        pattern = r'\s*'.join(re.escape(char) for char in chars)
        if chars.startswith('sk'):
            pattern = r'(?<![a-z0-9])' + pattern
        if chars == 'sk':
            pattern += r'(?![a-z0-9])'
        yield term, re.compile(pattern)


def audit_assets(state: dict, *, template_id: int, run_id: str, video_id: str,
                 verified_replacements: tuple = (), policy: AssetPolicy | None = None) -> AssetAudit:
    policy = policy or AssetPolicy()
    items, issues = [], []
    patterns = tuple(_patterns(policy))
    budget = [policy.max_nodes]

    def issue(code, scene=None, layer=None):
        item = AssetIssue(code=code, scene_index=scene, layer_id=layer)
        if item not in issues:
            issues.append(item)

    def metadata_nodes(value, path, depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 20:
            issue('inspection_limit')
            return
        if isinstance(value, dict):
            yield path, value
            for key, child in value.items():
                if budget[0] < 0:
                    break
                if isinstance(child, (dict, list)):
                    safe_key = key if isinstance(key, str) and re.fullmatch(r'[A-Za-z0-9_]{1,64}', key) else '*'
                    yield from metadata_nodes(child, f'{path}.{safe_key}', depth + 1)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if budget[0] < 0:
                    break
                if isinstance(child, (dict, list)):
                    yield from metadata_nodes(child, f'{path}[{index}]', depth + 1)

    version = None
    try:
        parsed = datetime.fromisoformat(state['updated_at'])
        if parsed.tzinfo is not None:
            version = parsed.isoformat()
    except (KeyError, TypeError, ValueError):
        pass
    if version is None:
        issue('version_missing')
    scenes = state.get('video', {}).get('scenes') if isinstance(state.get('video'), dict) else None
    if template_id not in policy.supported_templates:
        issue('unsupported_template')
    elif str(state.get('id')) != video_id:
        issue('invalid_video')
    elif not isinstance(scenes, list) or not scenes:
        issue('invalid_scenes')
    else:
        for si, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                issue('invalid_graph', si)
                continue
            scene_id = _id(scene.get('id'))
            if not scene_id:
                issue('scene_id_missing', si)
            shape = scene.get('rootShape')
            layers = shape.get('layers') if isinstance(shape, dict) else None
            if not isinstance(layers, dict) or 'ROOT' not in layers:
                issue('invalid_graph', si)
                continue
            candidates = None
            seen, stack = set(), ['ROOT']
            while stack:
                lid = stack.pop()
                budget[0] -= 1
                if budget[0] < 0:
                    issue('inspection_limit', si)
                    break
                if not _id(lid):
                    issue('invalid_graph', si)
                    continue
                if lid in seen:
                    issue('cycle_or_shared_layer', si, lid)
                    continue
                seen.add(lid)
                node = layers.get(lid)
                if not isinstance(node, dict):
                    issue('missing_layer', si, lid)
                    continue
                children = node.get('child', [])
                if not isinstance(children, list):
                    issue('invalid_graph', si, lid)
                    continue
                if len(children) > budget[0]:
                    issue('inspection_limit', si, lid)
                    continue
                stack.extend(reversed(children))
                if lid == 'ROOT':
                    continue
                props = node.get('props')
                kind = node.get('type', {}).get('resolvedName') if isinstance(node.get('type'), dict) else None
                if kind in ('TextLayer3', 'TextLayer4'):
                    continue
                # Observed vector decoration in template 1786, without a media reference.
                if kind == 'ShapeLayer' and isinstance(props, dict) and 'shape' in props and not props.get('media') and not props.get('aigeneratable'):
                    continue
                if not isinstance(props, dict):
                    issue('unsupported_layer', si, lid)
                    continue
                if kind not in ('ImageLayer', 'VideoLayer', 'MediaLayer'):
                    issue('unsupported_layer', si, lid)
                    continue
                marker = props.get('aigeneratable')
                auto = marker.get('auto_select') if isinstance(marker, dict) else None
                if auto is False:
                    continue
                if auto is not True:
                    issue('invalid_marker', si, lid)
                    continue
                media = props.get('media')
                if not isinstance(media, dict):
                    issue('invalid_media', si, lid)
                    continue
                files = media.get('image_files', [])
                if not isinstance(files, list):
                    issue('invalid_bundle', si, lid)
                    continue
                if kind == 'MediaLayer' and not files:
                    issue('invalid_bundle', si, lid)
                    continue
                if kind == 'VideoLayer' and files:
                    issue('invalid_bundle', si, lid)
                    items.append(AssetItem(scene_index=si, scene_id=scene_id, layer_id=lid, item_index=None,
                        reference=_reference(media, 'video'), verdict='unknown', reason='ambiguous_media'))
                    continue
                assigned = enumerate(files) if files else ((None, media),)
                for index, obj in assigned:
                    budget[0] -= 1
                    if budget[0] < 0:
                        issue('inspection_limit', si, lid)
                        break
                    if not isinstance(obj, dict):
                        issue('invalid_media', si, lid)
                        continue
                    if files and (obj.get('media_type') not in (None, 'image', 'video') or
                                  (kind == 'ImageLayer' and obj.get('media_type') == 'video')):
                        issue('invalid_bundle', si, lid)
                        continue
                    ref = _reference(obj, 'video' if kind == 'VideoLayer' or obj.get('media_type') == 'video' else 'image')
                    valid_ref = bool(ref.bucket and ref.filename and (ref.kind == 'image' or ref.segment))
                    exempt = valid_ref and scene_id and any(
                        proof.run_id == run_id and proof.video_id == video_id and proof.scene_index == si
                        and proof.scene_id == scene_id and proof.layer_id == lid and proof.item_index == index
                        and proof.reference == ref and proof.saved_verified is True and proof.favorites_only is True
                        for proof in verified_replacements)
                    if exempt:
                        items.append(AssetItem(scene_index=si, scene_id=scene_id, layer_id=lid, item_index=index,
                            reference=ref, verdict='favorite_exempt', reason='verified_favorite_replacement'))
                        continue
                    if candidates is None:
                        candidates = []
                        for key in ('rerank_result', 'search_metadata'):
                            candidates.extend(metadata_nodes(scene.get(key), f'video.scenes[{si}].{key}'))
                    sources = [(f'video.scenes[{si}].rootShape.layers.{lid}.props.media' +
                        (f'.image_files[{index}]' if index is not None else ''), obj)]
                    sources += [(path, candidate) for path, candidate in candidates if _same_file(ref, candidate)]
                    metadata_sources, hits, unreadable = [], [], []
                    for path, source in sources:
                        fields = [entry for field in FIELDS for entry in _texts(source.get(field), field, unreadable, budget)]
                        if fields:
                            metadata_sources.append(path)
                        for field, text in fields:
                            normalized = unicodedata.normalize('NFKC', text).casefold()
                            for term, pattern in patterns:
                                if pattern.search(normalized):
                                    hit = AssetHit(field=field, term=term, source=path)
                                    if hit not in hits:
                                        hits.append(hit)
                    if unreadable:
                        issue('inspection_limit' if budget[0] < 0 else 'metadata_unreadable', si, lid)
                    if not valid_ref:
                        verdict, reason = 'unknown', 'reference_incomplete'
                    elif hits:
                        verdict, reason = 'detected', 'company_detected'
                    elif unreadable:
                        verdict, reason = 'unknown', 'metadata_unreadable'
                    elif metadata_sources:
                        verdict, reason = 'not_detected', 'metadata_inspected'
                    else:
                        verdict, reason = 'unknown', 'metadata_missing'
                    items.append(AssetItem(scene_index=si, scene_id=scene_id, layer_id=lid, item_index=index,
                        reference=ref, verdict=verdict, reason=reason, hits=tuple(hits),
                        metadata_sources=tuple(dict.fromkeys(metadata_sources))))
    if not items:
        issue('no_inspectable_assets')
    partial = bool(blocking_asset_issues(issues))
    return AssetAudit(state='needs_attention' if partial else 'completed',
        audited_at=datetime.now(timezone.utc).isoformat(), source_version=version, policy=policy,
        items=tuple(items), issues=tuple(issues), error_code='inspection_incomplete' if partial else None)
