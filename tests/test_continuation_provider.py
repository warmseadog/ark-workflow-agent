from dataclasses import replace
import pytest
from app.video_provider import VideoProvider, ProviderError
from app.generation_settings import GenerationConfig


def test_extension_uses_explicit_extend_and_own_prompt(tmp_path,monkeypatch):
    video=tmp_path/'base.mp4';video.write_bytes(b'base')
    config=GenerationConfig(mode='http',model='doubao-seedance-2-5-260628',api_key='key',duration=12)
    calls=[]
    monkeypatch.setattr('app.person_video.validate_file',lambda *a,**kw:{'duration':8})
    monkeypatch.setattr(VideoProvider,'_request',lambda self,m,p,**kw:calls.append(kw['json']) or {'id':'extend-1'})
    monkeypatch.setattr(VideoProvider,'_poll',lambda self,ident,*a:{'task_id':ident})
    ids=[]
    VideoProvider(config).extend(video,'人物自然转身，沿走廊走近镜头。',tmp_path/'out.mp4',video_url='https://assets/base.mp4',on_submitted=ids.append)
    body=calls[0]
    assert body['omni_reference_task_type']=='extend'
    assert body['duration']==12 and body['ratio']=='adaptive'
    assert '向后延长' in body['content'][0]['text']
    assert '不进行延长或新增镜头' not in body['content'][0]['text']
    assert ids==['extend-1']


def test_extension_resume_never_resubmits(tmp_path,monkeypatch):
    monkeypatch.setattr(VideoProvider,'_request',lambda *a,**kw:pytest.fail('must not submit'))
    monkeypatch.setattr(VideoProvider,'_poll',lambda self,ident,*a:{'id':ident})
    result=VideoProvider(GenerationConfig()).extend(tmp_path/'missing','','out',resume_task_id='existing')
    assert result=={'id':'existing'}


def test_terminal_status_is_distinct_from_network_failure(tmp_path,monkeypatch):
    monkeypatch.setattr(VideoProvider,'_request',lambda *a,**kw:{'status':'failed','error':{'message':'reject'}})
    with pytest.raises(ProviderError) as error:VideoProvider(GenerationConfig())._poll('id',tmp_path/'out',None)
    assert error.value.terminal_failure is True
