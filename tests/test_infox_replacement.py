import copy

import pytest

from backend.infox.assets import audit_assets
from backend.infox.favorites import candidate_from_item
from tests.test_infox_assets import state, image, layer, video
from tests.test_infox_favorites import favorite


def source():
    s = state({'title':'old parent', 'objectFit':'contain', 'image_files':[
        image('bad-0.jpg', crop={'x':5,'y':0,'width':95,'height':100}),
        image('good.jpg', custom_note='preserve me', animation={'type':'fade'}), image('bad-2.jpg')]},
        [image('bad-0.jpg',tags=['삼성']),image('good.jpg',title='중립'),image('bad-2.jpg',description='SK')])
    s['video'].update(frameRate=30,duration=12,masterShape={'untouched':True})
    scene=s['video']['scenes'][0]
    scene.update(duration=12,durationInFrames=360,offset_durationInFrames=0,
                 voiceover={'content':'대본', 'filename':'tts.wav','segment_captions':[{'text':'대본'}]})
    scene['rootShape']['layers']['assigned']['props'].update(position={'x':10,'y':20},boxSize={'width':640,'height':360},
        rotate=17,animationEffects={'enter':'fade'},borderOptions={'width':2})
    s['contextData']={'keep':['everything']}
    return s


def inspected(s):
    return audit_assets(s,template_id=1786,run_id='a'*32,video_id='video-1')


def test_mixed_bundle_preserves_normal_image_and_everything_outside_media():
    from backend.infox.replacement import make_choices, apply_replacements, validate_replacements
    s=source(); before=copy.deepcopy(s); c=candidate_from_item(favorite(7))
    choices=make_choices(s,inspected(s),c)
    assert len(choices)==2 and all(x.slot_frames==120 for x in choices)
    result=apply_replacements(s,choices,ephemeral={c.key():favorite(7)})
    node=result['video']['scenes'][0]['rootShape']['layers']['assigned']
    assert node['type']['resolvedName']=='MediaLayer'
    files=node['props']['media']['image_files']
    assert len(files)==3
    assert files[1]=={**before['video']['scenes'][0]['rootShape']['layers']['assigned']['props']['media']['image_files'][1],
                      'durationInFrames':120}
    for index in (0,2):
        assert files[index]['segment']==files[index]['filename']=='clip-7.mp4'
        assert files[index]['id']=='item-7' and files[index]['frameIndex']==7 and files[index]['timestamp']==42
        assert files[index]['startFrameVideo']==0 and files[index]['endFrameVideo']==120 and files[index]['muted'] is True
    assert validate_replacements(s,result,choices).ready
    assert s==before


@pytest.mark.parametrize('kind', ['ImageLayer','VideoLayer'])
def test_single_media_conversion_rebuilds_consistent_candidate_attributes(kind):
    from backend.infox.replacement import make_choices, apply_replacements, validate_replacements
    s=source(); scene=s['video']['scenes'][0]
    old=image(title='삼성') if kind=='ImageLayer' else video('old-clip.mp4',title='삼성')
    old.update(id='old-id',frameIndex=99,timestamp=999,preview_presigned='https://old.invalid/?token=OLD',
               segment_url_presigned='https://old.invalid/?token=OLD',source_table='old-table')
    scene['rootShape']['layers']['assigned']=layer(old,kind=kind)
    c=candidate_from_item(favorite(3)); choices=make_choices(s,inspected(s),c)
    result=apply_replacements(s,choices,ephemeral={c.key():favorite(3)})
    node=result['video']['scenes'][0]['rootShape']['layers']['assigned']; m=node['props']['media']
    assert node['type']['resolvedName']=='VideoLayer' and m['image_files']==[]
    assert m['id']=='item-3' and m['frameIndex']==3 and m['timestamp']==18
    assert m['filename']==m['segment']=='clip-3.mp4' and m['thumbnail']=='clip-3.jpeg'
    assert m['endFrameVideo']==180 and m['durationInFrames']==360
    assert 'OLD' not in str(m) and 'source_table' not in m
    assert validate_replacements(s,result,choices).ready


@pytest.mark.parametrize('tamper', ['voice','layout','healthy_file','timing','root','wrong_segment','wrong_id'])
def test_independent_full_data_checks_reject_out_of_scope_or_mixed_metadata_changes(tamper):
    from backend.infox.replacement import make_choices, apply_replacements, validate_replacements
    s=source(); choices=make_choices(s,inspected(s),candidate_from_item(favorite(4)))
    result=apply_replacements(s,choices); scene=result['video']['scenes'][0]
    node=scene['rootShape']['layers']['assigned']; files=node['props']['media']['image_files']
    if tamper=='voice': scene['voiceover']['content']='changed'
    if tamper=='layout': node['props']['position']['x']=999
    if tamper=='healthy_file': files[1]['filename']='wrong.jpg'
    if tamper=='timing': scene['duration']=99
    if tamper=='root': scene['rootShape']['layers']['ROOT']['child']=[]
    if tamper=='wrong_segment': files[0]['segment']='other.mp4'
    if tamper=='wrong_id': files[0]['id']='other-item'
    assert not validate_replacements(s,result,choices).ready


def test_implicit_ceil_timing_and_explicit_widths_match_current_editor_contract():
    from backend.infox.replacement import make_choices, apply_replacements, validate_replacements
    s=source(); s['video']['scenes'][0]['durationInFrames']=100
    c=candidate_from_item(favorite())
    choices=make_choices(s,inspected(s),c)
    assert all(x.slot_frames==34 for x in choices)
    assert validate_replacements(s,apply_replacements(s,choices),choices).ready
    files=s['video']['scenes'][0]['rootShape']['layers']['assigned']['props']['media']['image_files']
    files[0]['durationInFrames']=20
    assert [x.slot_frames for x in make_choices(s,inspected(s),c)]==[20,40]


def test_stale_choice_duplicate_targets_and_wrong_ephemeral_item_are_rejected():
    from backend.infox.replacement import make_choices, apply_replacements
    from backend.infox.plan_models import PlanError
    s=source(); c=candidate_from_item(favorite()); choices=make_choices(s,inspected(s),c)
    with pytest.raises(PlanError): apply_replacements(s,choices+choices)
    with pytest.raises(PlanError): apply_replacements(s,choices,ephemeral={c.key():favorite(9)})
    changed=copy.deepcopy(s)
    changed['video']['scenes'][0]['rootShape']['layers']['assigned']['props']['media']['image_files'][0]['filename']='changed.jpg'
    with pytest.raises(PlanError): apply_replacements(changed,choices)


def test_editor_media_layer_is_audited_per_actual_image_or_video_child():
    s=source(); scene=s['video']['scenes'][0]
    scene['rootShape']['layers']['assigned']['type']['resolvedName']='MediaLayer'
    files=scene['rootShape']['layers']['assigned']['props']['media']['image_files']
    files[2]=video('old.mp4',title='SK',duration=6.0,startFrameVideo=0,endFrameVideo=180,media_type='video')
    report=inspected(s)
    assert report.state=='completed'
    assert [item.reference.kind for item in report.items]==['image','image','video']


@pytest.mark.parametrize('tamper',['child_crop','stale_external','parent_ghost'])
def test_validator_checks_replaced_child_layout_and_stale_reference_fields(tamper):
    from backend.infox.replacement import make_choices,apply_replacements,validate_replacements
    s=source(); choices=make_choices(s,inspected(s),candidate_from_item(favorite(2)))
    result=apply_replacements(s,choices)
    media=result['video']['scenes'][0]['rootShape']['layers']['assigned']['props']['media']
    if tamper=='child_crop': media['image_files'][0]['crop']={'x':99,'y':99,'width':1,'height':1}
    if tamper=='stale_external': media['image_files'][0]['external_url']='https://old.invalid/?token=OLD'
    if tamper=='parent_ghost': media['filename']='bad-0.jpg'
    assert not validate_replacements(s,result,choices).ready


def test_missing_explicit_scene_frames_cannot_claim_editor_timing_preserved():
    from backend.infox.replacement import make_choices
    from backend.infox.plan_models import PlanError
    s=source();scene=s['video']['scenes'][0]
    scene.pop('durationInFrames');scene['duration']=12.01
    with pytest.raises(PlanError,match='unsupported_timing'):
        make_choices(s,inspected(s),candidate_from_item(favorite()))
