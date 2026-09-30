from dataclasses import replace
from pathlib import Path
import subprocess

import imageio_ffmpeg
import pytest
from app.generation_settings import GenerationConfig
from app.model_catalog import SD20, SD25
from tests.test_model_catalog import client, enable
from tests.test_production_worker import setup
from tests.test_tenant_preview import preview, asset, post, terminal


def test_ratio_saved_restored_and_rejected_if_invalid(client):
    enable(client)
    draft=client.post('/api/production/drafts',json={}).json()
    for ratio in ('16:9','9:16','1:1','4:3','3:4','21:9','adaptive'):
        response=client.put('/api/production/drafts/'+draft['id'],json={
            'revision':draft['revision'],'model':{'model':SD25,'ratio':ratio}})
        assert response.status_code==200,response.text
        draft=response.json();assert draft['model']['ratio']==ratio
    assert client.put('/api/production/drafts/'+draft['id'],json={
        'revision':draft['revision'],'model':{'ratio':'17:3'}}).status_code==422


def test_old_settings_default_to_adaptive():
    assert GenerationConfig().ratio=='adaptive'


@pytest.mark.parametrize('ratio', ['16:9','9:16','1:1','4:3','3:4','21:9'])
def test_real_framing_preserves_picture_duration_audio_and_original(tmp_path,ratio):
    from app.video_framing import reframe_video
    from app.person_video import probe
    import cv2
    source=tmp_path/'source.mp4';target=tmp_path/'framed.mp4'
    binary=imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([binary,'-v','error','-y','-f','lavfi','-i','color=red:s=360x640:r=24:d=2',
                    '-f','lavfi','-i','sine=frequency=440:duration=2','-c:v','libx264','-c:a','aac',str(source)],check=True,capture_output=True)
    original=source.read_bytes();result=reframe_video(source,target,ratio)
    info=probe(result);w,h=map(int,ratio.split(':'))
    assert abs(info['width']/info['height']-w/h)<.001
    assert abs(info['duration']-2)<.1
    assert b'Audio:' in subprocess.run([binary,'-i',str(result)],capture_output=True).stderr
    capture=cv2.VideoCapture(str(result));ok,frame=capture.read();capture.release()
    assert ok and frame[int(info['height']/2),int(info['width']/2),2]>200
    if ratio!='9:16':assert frame[0,0].max()<15
    assert source.read_bytes()==original


@pytest.mark.parametrize('model,expected',[(SD20,'16:9'),(SD25,'adaptive')])
def test_provider_uses_native_ratio_only_when_supported(tmp_path,monkeypatch,model,expected):
    from app.video_provider import VideoProvider
    from app import person_video
    video=tmp_path/'video.mp4';video.write_bytes(b'video')
    face=tmp_path/'face.png';face.write_bytes(b'image');clothes=tmp_path/'clothes.png';clothes.write_bytes(b'image')
    config=GenerationConfig(mode='http',api_key='fixture',model=model,ratio='16:9')
    provider=VideoProvider(config);calls=[]
    monkeypatch.setattr(person_video,'validate_file',lambda *a,**k:{'duration':5})
    def request(method,path,**kwargs):
        if method=='POST':calls.append(kwargs['json']);return {'id':'fixture-task'}
        return {'status':'succeeded','content':{'video_url':'https://example.test/result.mp4'}}
    monkeypatch.setattr(provider,'_request',request)
    monkeypatch.setattr(provider,'_download',lambda *a:None)
    provider.generate(video,[face],[clothes],'test',tmp_path/'out.mp4',video_url='https://example.test/video.mp4')
    assert calls[0]['ratio']==expected


def test_worker_frames_cached_redaction_without_cross_ratio_reuse(setup,monkeypatch):
    from app import production_worker as worker, person_video
    cfg,store,draft,private=setup
    private['generation'].update(model=SD25,duration=-1)
    monkeypatch.setattr(person_video,'validate_file',lambda *a,**k:{'duration':5})
    calls=[]
    def frame(source,target,ratio):
        calls.append((source.read_bytes(),ratio));target.write_bytes(source.read_bytes()+ratio.encode());return target
    monkeypatch.setattr(worker,'reframe_video',frame)
    for ratio in ('16:9','1:1','adaptive'):
        private['generation']['ratio']=ratio
        run=store.create_run(draft['id'],draft['revision'],ratio,private)
        worker.execute_run(cfg,store,store.claim_next())
        assert store.get_run(run['id'])['status']=='succeeded'
        assert (cfg.storage_dir/'outputs'/(run['id']+'.mp4')).read_bytes()==b'redacted'+(ratio.encode() if ratio!='adaptive' else b'')
    assert calls==[(b'redacted','16:9'),(b'redacted','1:1')]


def test_preview_uses_requested_ratio_but_keeps_shared_cache_unframed(preview,monkeypatch):
    from app import video_framing
    client,tenants,_,_,redactions,*_=preview
    asset(tenants['1'])
    calls=[]
    def frame(source,target,ratio):
        calls.append((source.read_bytes(),ratio))
        if ratio=='adaptive':return source
        target.write_bytes(source.read_bytes()+ratio.encode());return target
    monkeypatch.setattr(video_framing,'reframe_video',frame)
    for ratio in ('16:9','1:1','adaptive'):
        submitted=post(client,ratio=ratio)
        assert submitted.status_code==200,submitted.text
        done=terminal(client,submitted.json()['id'])
        assert done['status']=='defaced'
    assert len(redactions)==1
    assert calls==[(b'video-redacted','16:9'),(b'video-redacted','1:1'),(b'video-redacted','adaptive')]
    assert post(client,ratio='invalid').status_code==422
