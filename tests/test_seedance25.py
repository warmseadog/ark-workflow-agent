from pathlib import Path

import pytest
from PIL import Image
from app import person_video
from app.generation_settings import GenerationConfig
from app.model_catalog import SD25
from app.video_provider import VideoProvider, ProviderError


def test_model_aware_video_duration_and_library_upload_limit(tmp_path, monkeypatch):
    source=tmp_path/'source.mp4'; source.write_bytes(b'video')
    person=tmp_path/'person.mp4'; person.write_bytes(b'person')
    monkeypatch.setattr(person_video,'probe',lambda path: {
        'duration':20 if Path(path).name=='source.mp4' else 10,
        'fps':24,'width':1280,'height':720})
    assert person_video.validate_file(source)['duration']==20
    assert person_video.validate_pair(source,person,max_seconds=30)==30
    with pytest.raises(ValueError): person_video.validate_pair(source,person)
    with pytest.raises(ValueError): person_video.validate_pair(source,person,max_seconds=29)


def provider_fixture(tmp_path, monkeypatch):
    video=tmp_path/'input.mp4'; video.write_bytes(b'video')
    image=tmp_path/'reference.png'; Image.new('RGB',(400,400),'red').save(image)
    monkeypatch.setattr(person_video,'probe',lambda path:{'duration':20,'fps':24,'width':1280,'height':720})
    provider=VideoProvider(GenerationConfig(model=SD25,mode='http',api_key='fixture'))
    bodies=[]
    monkeypatch.setattr(provider,'_request',lambda method,path,**kw:bodies.append(kw['json']) or {'id':'fixture-task'})
    monkeypatch.setattr(provider,'_poll',lambda *args:{'task_id':args[0]})
    return provider,video,image,bodies


def test_edit_request_locks_duration_ratio_and_target_video(tmp_path,monkeypatch):
    provider,video,image,bodies=provider_fixture(tmp_path,monkeypatch)
    provider.generate(video,[image],[image],'替换人物和衣服',tmp_path/'output.mp4',video_url='https://fixture.invalid/video')
    body=bodies[0]
    assert body['model']==SD25 and body['duration']==-1 and body['ratio']=='adaptive'
    assert body['content'][-1]['role']=='reference_video'
    assert '编辑 @Video1' in body['content'][0]['text']


def test_25_accepts_30_images_rejects_31_without_submitting(tmp_path,monkeypatch):
    provider,video,image,bodies=provider_fixture(tmp_path,monkeypatch)
    provider.generate(video,[image]*15,[image]*15,'换装',tmp_path/'output.mp4',video_url='https://fixture.invalid/video')
    assert len([c for c in bodies[0]['content'] if c['type']=='image_url'])==30
    with pytest.raises(ProviderError):
        provider.generate(video,[image]*16,[image]*15,'换装',tmp_path/'output.mp4',video_url='https://fixture.invalid/video')
    assert len(bodies)==1


def test_overlong_25_source_rejected_before_provider_call(tmp_path,monkeypatch):
    provider,video,image,bodies=provider_fixture(tmp_path,monkeypatch)
    monkeypatch.setattr(person_video,'probe',lambda path:{'duration':31,'fps':24,'width':1280,'height':720})
    with pytest.raises((ValueError,ProviderError)):
        provider.generate(video,[image],[image],'换装',tmp_path/'output.mp4',video_url='https://fixture.invalid/video')
    assert not bodies
