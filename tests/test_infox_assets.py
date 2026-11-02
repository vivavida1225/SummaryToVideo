import copy

import pytest


def image(filename='assigned.jpg', **meta):
    return {'bucket': 'images', 'filename': filename, **meta}


def video(segment='clip-1.mp4', **meta):
    return {'bucket': 'videos', 'filename': 'original.mp4', 'segment': segment,
            'videoId': 'source-1', 'frameIndex': 1, 'timestamp': 6, **meta}


def layer(media, *, auto=True, kind='ImageLayer', children=None):
    props = {'media': media}
    if auto is not None: props['aigeneratable'] = {'auto_select': auto}
    return {'type': {'resolvedName': kind}, 'props': props, 'child': children or []}


def state(media=None, candidates=None):
    return {'id': 'video-1', 'updated_at': '2026-11-02T01:00:00Z', 'video': {'scenes': [
        {'id': 'scene-1', 'rootShape': {'layers': {
            'ROOT': {'child': ['assigned']}, 'assigned': layer(media or {'image_files': [image()]})}},
         'rerank_result': candidates if candidates is not None else [image(title='반도체 설비')],
         'voiceover': {'content': '삼성 SK 삼전닉스'}}]}}


def audit(s, **kwargs):
    from backend.infox.assets import audit_assets
    template = kwargs.pop('template_id_override', 1786)
    return audit_assets(s, template_id=template, run_id='a' * 32, video_id='video-1', **kwargs)


def test_bundle_matches_exact_file_tags_and_descriptions_without_parent_title():
    s = state({'title': '삼성 오래된 템플릿', 'image_files': [image('one.jpg'), image('two.jpg'), image('three.jpg')]}, [
        image('one.jpg', tags=['ＳＫ 하이닉스']), image('two.jpg', description='삼성전자 공장'),
        image('three.jpg', title='sketch of machinery'), image('unused.jpg', title='삼전닉스')])
    before = copy.deepcopy(s)
    report = audit(s)
    assert report.state == 'completed'
    assert [item.verdict for item in report.items] == ['detected', 'detected', 'not_detected']
    assert [item.item_index for item in report.items] == [0, 1, 2]
    assert any(hit.field == 'tags[0]' and hit.term == 'SK하이닉스' for hit in report.items[0].hits)
    assert report.items[0].metadata_sources == ('video.scenes[0].rerank_result[0]',)
    assert s == before


@pytest.mark.parametrize('text,detected', [('sketch SKY disk A_SK2', False), ('sk 공장', True),
    ('SK의 실적', True), ('Ｓ Ｋ 하이닉스', True), ('  삼성 전자 ', True), ('삼전닉스', True),
    ('삼전 하닉', False), ('stock sketch', False)])
def test_alias_normalization_keeps_sk_ascii_word_boundaries(text, detected):
    item = audit(state(candidates=[image(description=text)])).items[0]
    assert (item.verdict == 'detected') is detected


def test_only_connected_automatic_layers_and_exact_video_segments_are_inspected():
    s = state(video(title='반도체 설비'))
    layers = s['video']['scenes'][0]['rootShape']['layers']
    layers['assigned'] = layer(video(), kind='VideoLayer')
    layers['detached'] = layer(image(title='삼성'))
    layers['manual'] = layer(image(title='삼성'), auto=False)
    layers['ROOT']['child'].append('manual')
    s['video']['scenes'][0]['rerank_result'] = [video('clip-2.mp4', title='삼성'), video(title='중립 장비')]
    report = audit(s)
    assert report.state == 'completed' and len(report.items) == 1
    assert report.items[0].verdict == 'not_detected'
    assert report.items[0].reference.segment == 'clip-1.mp4'


def test_missing_metadata_is_nonblocking_while_unknown_graph_still_blocks():
    report = audit(state(candidates=[]))
    assert report.items[0].verdict == 'unknown' and report.state == 'completed'
    for mutation in ('missing_child', 'cycle', 'no_marker', 'no_root', 'unknown_type', 'bad_bundle'):
        s = state()
        layers = s['video']['scenes'][0]['rootShape']['layers']
        if mutation == 'missing_child': layers['ROOT']['child'].append('missing')
        if mutation == 'cycle': layers['assigned']['child'] = ['ROOT']
        if mutation == 'no_marker': layers['assigned']['props'].pop('aigeneratable')
        if mutation == 'no_root': layers.pop('ROOT')
        if mutation == 'unknown_type': layers['assigned']['type']['resolvedName'] = 'UnseenMediaLayer'
        if mutation == 'bad_bundle': layers['assigned']['props']['media']['image_files'] = 'broken'
        assert audit(s).state == 'needs_attention', mutation
    assert audit(state(), template_id_override=99).state == 'needs_attention'


def test_search_metadata_only_matches_file_objects_not_queries_or_other_scenes():
    s = state(candidates=[])
    scene = s['video']['scenes'][0]
    scene['search_metadata'] = {'query': '삼성', 'unused': image('other.jpg', title='삼성'),
                                'result': image(tags=['하이닉스'])}
    report = audit(s)
    assert report.items[0].verdict == 'detected'
    assert report.items[0].metadata_sources == ('video.scenes[0].search_metadata.result',)
    scene['search_metadata'] = {'query': '삼성'}
    s['video']['scenes'].append({'id': 'scene-2', 'rootShape': {'layers': {'ROOT': {'child': []}}},
                               'rerank_result': [image(title='삼성')]})
    assert audit(s).items[0].verdict == 'unknown'


def test_remote_favorite_flag_never_grants_exemption_and_urls_are_not_saved():
    s = state({'image_files': [image(is_favorite=True, url_presigned='https://invalid/?token=SECRET')]},
              [image(description='삼성 SECRET https://invalid/?token=SECRET')])
    report = audit(s)
    assert report.items[0].verdict == 'detected'
    assert 'SECRET' not in report.model_dump_json() and 'https://' not in report.model_dump_json()


def test_verified_favorite_history_binds_exact_placement_and_segment():
    from backend.infox.asset_models import FavoriteReplacement
    s = state(video(title='삼성'))
    s['video']['scenes'][0]['rootShape']['layers']['assigned'] = layer(video(title='삼성'), kind='VideoLayer')
    item = audit(s).items[0]
    proof = FavoriteReplacement(run_id='a'*32, video_id='video-1', scene_index=0, scene_id='scene-1',
        layer_id='assigned', item_index=None, reference=item.reference, source='video', search='반도체',
        favorites_only=True, saved_verified=True, verified_at='2026-11-02T01:00:00Z')
    assert audit(s, verified_replacements=(proof,)).items[0].verdict == 'favorite_exempt'
    for field, value in (('layer_id', 'other'), ('scene_id', 'other'), ('run_id', 'b'*32), ('item_index', 1)):
        assert audit(s, verified_replacements=(proof.model_copy(update={field: value}),)).items[0].verdict == 'detected'
    changed = copy.deepcopy(s)
    changed['video']['scenes'][0]['rootShape']['layers']['assigned']['props']['media']['segment'] = 'other.mp4'
    assert audit(changed, verified_replacements=(proof,)).items[0].verdict != 'favorite_exempt'


def test_incomplete_report_cannot_be_labeled_completed():
    from pydantic import ValidationError
    from backend.infox.asset_models import AssetAudit
    value = state(candidates=[])
    value['video']['scenes'][0]['rootShape']['layers']['ROOT']['child'].append('missing')
    incomplete = audit(value).model_dump()
    incomplete['state'] = 'completed'
    with pytest.raises(ValidationError):
        AssetAudit.model_validate(incomplete)


def test_unknown_connected_leaf_cannot_hide_beside_a_supported_asset():
    s = state()
    layers = s['video']['scenes'][0]['rootShape']['layers']
    layers['ROOT']['child'].append('unknown')
    layers['unknown'] = {'type': {'resolvedName': 'UnseenMediaLayer'}, 'props': {}, 'child': []}
    assert audit(s).state == 'needs_attention'


def test_video_layer_with_residual_image_bundle_is_unresolved():
    s = state()
    s['video']['scenes'][0]['rootShape']['layers']['assigned'] = layer(
        video(title='삼성', image_files=[image(title='neutral')]), kind='VideoLayer')
    report = audit(s)
    assert report.state == 'needs_attention'
    assert report.items[0].verdict == 'unknown' and report.items[0].reference.kind == 'video'


def test_joined_bundle_parent_cannot_supply_its_stale_title():
    s = state(candidates=[image(title='삼성', image_files=[image(title='neutral')])])
    report = audit(s)
    assert report.state == 'completed' and report.items[0].verdict == 'not_detected'
    assert report.items[0].metadata_sources == ('video.scenes[0].rerank_result[0].image_files[0]',)


@pytest.mark.parametrize('deep', [False, True])
def test_unsupported_metadata_remains_unknown_but_does_not_block(deep):
    tags = {'unexpected': ['삼성']}
    if deep:
        tags = '삼성'
        for _ in range(10):
            tags = [tags]
    report = audit(state(candidates=[image(title='neutral', tags=tags)]))
    assert report.state == 'completed' and report.items[0].verdict == 'unknown'
    assert report.issues and not report.blocking_issues


def test_blank_optional_fields_are_absent_metadata_and_video_frame_conflicts_do_not_join():
    assert audit(state(candidates=[image(title='neutral', description=' ', text_body='', tags=[])])).state == 'completed'
    s = state(candidates=[video(title='삼성', frameIndex=2)])
    s['video']['scenes'][0]['rootShape']['layers']['assigned'] = layer(video(), kind='VideoLayer')
    assert audit(s).items[0].verdict == 'unknown'


def test_verified_favorite_exemption_does_not_require_usable_metadata():
    from backend.infox.asset_models import AssetReference, FavoriteReplacement
    proof = FavoriteReplacement(run_id='a'*32, video_id='video-1', scene_index=0, scene_id='scene-1',
        layer_id='assigned', item_index=0, reference=AssetReference(kind='image', bucket='images', filename='assigned.jpg'), source='video', search='반도체',
        favorites_only=True, saved_verified=True, verified_at='2026-11-02T01:00:00Z')
    s = state(candidates=[image(title='neutral', tags={'unexpected': ['삼성']})])
    report = audit(s, verified_replacements=(proof,))
    assert report.state == 'completed' and report.items[0].verdict == 'favorite_exempt'


def test_observed_shape_layer_is_nonmedia_but_unknown_shape_media_is_unresolved():
    s = state()
    layers = s['video']['scenes'][0]['rootShape']['layers']
    layers['ROOT']['child'].append('shape')
    layers['shape'] = {'type': {'resolvedName': 'ShapeLayer'}, 'props': {'shape': 'rectangle'}, 'child': []}
    assert audit(s).state == 'completed'
    layers['shape']['props']['media'] = image(title='삼성')
    assert audit(s).state == 'needs_attention'


def test_inspection_budget_includes_bundle_items_and_own_metadata():
    from backend.infox.asset_models import AssetPolicy
    s = state({'image_files': [image(title='neutral') for _ in range(100)]}, candidates=[])
    report = audit(s, policy=AssetPolicy(max_nodes=12))
    assert report.state == 'needs_attention'
    assert len(report.items) < 12
    assert any(issue.code == 'inspection_limit' for issue in report.issues)
