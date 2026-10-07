"""Production failures: rounded plans, extreme images and short-pixel videos."""
import copy
import hashlib
from io import BytesIO
from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from app.variation_llm import validate_plan
from tests.test_variation import example
from tests.test_production_api import client, complete_draft


def video(path, size=(576,576), frames=60, fps=30):
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),fps,size)
    assert writer.isOpened()
    for _ in range(frames):writer.write(np.full((size[1],size[0],3),120,dtype=np.uint8))
    writer.release()
    return path


@pytest.mark.parametrize('mode',['strict','user_priority'])
def test_plan_rounding_is_normalized_without_mutating_response(mode):
    plan=example();plan['shots'][0]['end']=13.97
    before=copy.deepcopy(plan)
    result=validate_plan(plan,13.966666666666667,prompt_mode=mode)
    assert result['shots'][0]['end']==13.966666666666667
    assert plan==before
    assert validate_plan(result,13.966666666666667,prompt_mode=mode)==result


@pytest.mark.parametrize('delta',[.005,-.005,.003])
def test_small_shot_boundary_gaps_and_overlaps_are_aligned(delta):
    plan=example();first=plan['shots'][0];first['end']=4
    plan['shots'].append({**first,'start':4+delta,'end':8})
    assert validate_plan(plan,8)['shots'][1]['start']==4


@pytest.mark.parametrize('delta',[.0051,-.0051,1,-1])
def test_real_timing_errors_have_specific_message(delta):
    plan=example();plan['shots'][0]['end']+=delta
    with pytest.raises(ValueError,match='时间'):validate_plan(plan,8)


def test_padding_keeps_entire_image_and_original(tmp_path):
    from app import reference_adaptation as reference_media
    p=tmp_path/'outfit.png';Image.new('RGB',(1763,4590),(91,32,17)).save(p)
    original=p.read_bytes()
    output,notice=reference_media.adapt_image(p,tmp_path/'adapted')
    with Image.open(output) as im:
        assert .4<=im.width/im.height<=2.5
        assert im.height==4590
        assert im.crop(((im.width-1763)//2,0,(im.width-1763)//2+1763,4590)).tobytes()==Image.open(p).tobytes()
    assert p.read_bytes()==original and output!=p and notice
    assert reference_media.adapt_image(p,tmp_path/'adapted')[0]==output


def test_compliant_image_is_not_reencoded(tmp_path):
    from app import reference_adaptation as reference_media
    p=tmp_path/'ok.png';Image.new('RGB',(600,800)).save(p)
    assert reference_media.adapt_image(p,tmp_path/'adapted')==(p,None)


def test_image_orientation_and_wide_padding(tmp_path):
    from app.reference_adaptation import adapt_image
    p=tmp_path/'rotated.jpg';im=Image.new('RGB',(300,900),'red')
    exif=Image.Exif();exif[274]=6;im.save(p,exif=exif)
    before=p.read_bytes();out,notice=adapt_image(p,tmp_path/'adapted')
    with Image.open(out) as result:
        assert result.size==(900,360) and result.getexif().get(274,1)==1
    assert p.read_bytes()==before and notice['before']==[900,300]


def test_low_pixel_video_gets_valid_copy_and_cache(tmp_path):
    from app import reference_adaptation as reference_media
    from app.person_video import probe
    p=video(tmp_path/'masked.mp4');before=p.read_bytes()
    out,notice=reference_media.adapt_video(p,tmp_path/'adapted',max_seconds=15)
    info=probe(out)
    assert (info['width'],info['height'])==(640,640)
    assert abs(info['duration']-2)<.05 and info['fps']==30
    assert p.read_bytes()==before and notice
    stamp=out.stat().st_mtime_ns
    assert reference_media.adapt_video(p,tmp_path/'adapted',max_seconds=15)[0]==out
    assert out.stat().st_mtime_ns==stamp


def test_conversion_failure_never_returns_original_low_pixel_file(tmp_path,monkeypatch):
    from app import reference_adaptation as reference_media
    import subprocess
    p=video(tmp_path/'masked.mp4')
    monkeypatch.setattr(reference_media.subprocess,'run',lambda *a,**k: (_ for _ in ()).throw(subprocess.TimeoutExpired('ffmpeg',1)))
    with pytest.raises(ValueError,match='适配失败'):reference_media.adapt_video(p,tmp_path/'adapted',max_seconds=15)
    assert not list((tmp_path/'adapted').glob('*.mp4'))


def test_invalid_image_is_definite_local_failure_before_submission(tmp_path,monkeypatch):
    from app.video_provider import VideoProvider,ProviderError
    from app.generation_settings import GenerationConfig
    source=video(tmp_path/'source.mp4')
    bad=tmp_path/'bad.png';bad.write_bytes(b'not an image')
    calls=[]
    monkeypatch.setattr(VideoProvider,'_request',lambda *a,**k:calls.append(k))
    with pytest.raises(ProviderError) as error:
        VideoProvider(GenerationConfig(mode='http',api_key='test')).generate(
            source,[bad],[],'',tmp_path/'out.mp4',video_url='https://unit.example/video')
    assert error.value.error_kind=='material_rejected'
    assert not error.value.submission_uncertain and calls==[]


def test_sd20_image_person_rejects_long_source_before_queue(client):
    from app import main
    from app.production_store import ProductionStore
    draft=complete_draft(client)
    store=ProductionStore(main.settings.storage_dir)
    path=Path(store.get_asset(draft['source_asset_id'],private=True)['path'])
    video(path,(64,64),673)
    response=client.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'too-long'})
    assert response.status_code==422,response.text
    assert '15' in response.text
    assert store.run_by_key('too-long') is None


def test_pipeline_submits_adapted_video_and_padded_image(tmp_path,monkeypatch):
    import base64,json
    from dataclasses import replace,asdict
    from app.config import settings
    from app.production_store import ProductionStore
    from app.generation_settings import GenerationConfig
    from app.storage_settings import StorageConfig
    from app import production_worker as worker
    from app.reference_media import get_video
    from app.person_video import probe
    cfg=replace(settings,storage_dir=tmp_path);store=ProductionStore(tmp_path)
    for ident,kind in [('source','video'),('face','face'),('clothes','clothing')]:
        path=tmp_path/(ident+('.mp4' if kind=='video' else '.png'))
        if kind=='video':video(path)
        else:Image.new('RGB',(100,400) if kind=='clothing' else (400,400)).save(path)
        store.add_asset(ident,path.name,kind,path,path.stat().st_size,'video/mp4' if kind=='video' else 'image/png',hashlib.sha256(path.read_bytes()).hexdigest())
    draft=store.create_draft({'source_asset_id':'source','face_asset_ids':['face'],'clothing_asset_ids':['clothes'],'prompt':'original'})
    private={'generation':asdict(GenerationConfig(mode='http',api_key='test',public_base_url='https://unit.example')),'storage':asdict(StorageConfig())}
    monkeypatch.setattr(worker,'run_deface',lambda src,dst,*args:dst.write_bytes(src.read_bytes()))
    sent=[]
    def request(self,method,url,**kwargs):
        body=kwargs['json'];sent.append(body)
        video_url=body['content'][-1]['video_url']['url']
        submitted=get_video(video_url.rsplit('/',1)[-1],tmp_path)
        assert probe(submitted)['width']==640 and 'model-input' in str(submitted)
        image_url=body['content'][2]['image_url']['url']
        with Image.open(BytesIO(base64.b64decode(image_url.split(',')[1]))) as im:assert im.size==(160,400)
        return {'id':'one-task'}
    monkeypatch.setattr(worker.VideoProvider,'_request',request)
    monkeypatch.setattr(worker.VideoProvider,'_poll',lambda *a,**k:{})
    run=store.create_run(draft['id'],1,'adapt',private)
    worker.execute_run(cfg,store,store.claim_next())
    result=store.get_run(run['id'])
    assert result['status']=='succeeded',result.get('error')
    assert len(sent)==1 and {n['kind'] for n in result['input_adaptations']}=={'image','video'}
    assert probe(tmp_path/'source.mp4')['width']==576
    assert probe(tmp_path/'work'/run['id']/'defaced.mp4')['width']==576


def test_plan_diagnostics_capture_original_and_normalized_without_credentials(monkeypatch):
    import json,requests
    from app.variation_llm import plan_variation
    from app.variation_settings import VariationConfig
    plan=example();plan['shots'][0]['end']=13.97;raw=json.dumps(plan)
    response=requests.Response();response.status_code=200
    response._content=json.dumps({'choices':[{'message':{'content':raw}}]}).encode()
    monkeypatch.setattr('app.variation_llm.requests.post',lambda *a,**kw:response)
    notes=[]
    plan_variation(VariationConfig(api_key='never-store-this'),context={'duration':13.966666666666667},frames=[],references=[],on_diagnostic=notes.append)
    assert notes[0]['raw_plan']==raw and notes[0]['normalized_plan']['shots'][0]['end']==13.966666666666667
    assert 'never-store-this' not in json.dumps(notes)


@pytest.mark.parametrize('raw,finish',[('{"summary":','stop'),('{"summary":"a","summary":"b"}','stop'),('{"summary":','length')])
def test_invalid_json_and_truncation_are_diagnosable(monkeypatch,raw,finish):
    import json,requests
    from app.variation_llm import plan_variation
    from app.variation_settings import VariationConfig
    response=requests.Response();response.status_code=200
    response._content=json.dumps({'choices':[{'finish_reason':finish,'message':{'content':raw}}]}).encode()
    monkeypatch.setattr('app.variation_llm.requests.post',lambda *a,**kw:response)
    notes=[]
    with pytest.raises(ValueError):plan_variation(VariationConfig(api_key='test'),context={'duration':8},frames=[],references=[],on_diagnostic=notes.append)
    assert len(notes)==1 and notes[0]['raw_plan']==raw and notes[0]['validation']=='failed'
