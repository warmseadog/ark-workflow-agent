from dataclasses import replace
from pathlib import Path
from io import BytesIO
import hashlib
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from app import main, portrait_service, production_worker, portrait_library
from app.production_store import ProductionStore

@pytest.fixture
def setup(tmp_path, monkeypatch):
    settings=replace(main.settings,storage_dir=tmp_path,seedance_mode='mock')
    monkeypatch.setattr(main,'settings',settings)
    monkeypatch.setattr(production_worker,'wake',lambda *_:None)
    portrait_service.save_config(settings,{'access_key':'test-ak','secret_key':'test-sk'})
    return TestClient(main.app)

def video_bytes(tmp_path, seconds=3, size=(640,640), fps=24):
    path=tmp_path/'fixture.mp4'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),fps,size)
    assert writer.isOpened()
    for _ in range(round(seconds*fps)):writer.write(np.full((size[1],size[0],3),100,dtype=np.uint8))
    writer.release()
    return path.read_bytes()

def upload(client,data,kind='person_video'):
    return client.post('/api/production/assets',data={'kind':kind},files={'file':('person.mp4',data,'video/mp4')})

def test_video_upload_rejects_invalid_and_accepts_real_video(setup,tmp_path):
    assert upload(setup,b'not video').status_code==422
    assert upload(setup,video_bytes(tmp_path,1)).status_code==422
    assert upload(setup,video_bytes(tmp_path,3,(200,200))).status_code==422
    result=upload(setup,video_bytes(tmp_path))
    assert result.status_code==200,result.text
    assert result.json()['kind']=='person_video'

def test_video_client_sends_video_type_and_rejects_image(setup,monkeypatch):
    config=portrait_service.load_config(main.settings)
    client=portrait_service.ArkPortraitClient(config,asset_type='Video')
    calls=[]
    def request(action,payload):
        calls.append((action,payload))
        if action=='CreateAsset':return {'Id':'asset-video'}
        if action=='GetAssetGroup':return {'Id':'group-person','GroupType':'LivenessFace','ProjectName':config.project_name}
        return {'Id':'asset-video','GroupId':'group-person','AssetType':'Image','Status':'Active','ProjectName':config.project_name}
    monkeypatch.setattr(client,'_request',request)
    assert client.create_asset('group-person','https://test.invalid/video','clip')=='asset-video'
    assert calls[0][1]['AssetType']=='Video'
    with pytest.raises(portrait_service.PortraitError):client.get_asset('asset-video')

def test_video_ingestion_reuse_and_fresh_authorization(setup,tmp_path,monkeypatch):
    lib=portrait_library.PortraitLibrary(main.settings)
    person=lib.add_person('group-person','测试人物')
    asset=upload(setup,video_bytes(tmp_path)).json()
    assert 'id' in asset,asset
    job=lib.enqueue(person['id'],asset['id'])
    assert lib.enqueue(person['id'],asset['id'])['id']==job['id']
    assert setup.post('/api/portrait/photos/'+job['id']+'/use').status_code==422
    calls=[];state={'status':'Active','group':'group-person'}
    def request(self,action,payload):
        calls.append((action,payload))
        if action=='CreateAsset':return {'Id':'asset-video'}
        if action=='GetAssetGroup':return {'Id':payload['Id'],'GroupType':'LivenessFace','ProjectName':'default'}
        if action=='GetAsset':return {'Id':payload['Id'],'GroupId':state['group'],'AssetType':'Video','Status':state['status'],'ProjectName':'default','Name':'clip'}
        raise AssertionError(action)
    monkeypatch.setattr(portrait_service.ArkPortraitClient,'_request',request)
    monkeypatch.setattr(portrait_library,'upload_photo',lambda *_:'https://test.invalid/clip')
    lib.process_one();lib.update(job['id'],next_check=0);lib.process_one()
    assert lib.get_photo(job['id'])['status']=='active'
    listed=lib.photos_for_person(person['id']);assert listed[0]['kind']=='person_video'
    result=setup.post('/api/portrait/photos/'+job['id']+'/use')
    assert result.status_code==200,result.text
    assert result.json()['portrait']['remote_asset_id']=='asset-video'
    assert lib.person(person['id'])['video_count']==1
    assert lib.person(person['id'])['photo_count']==0
    state['group']='group-other'
    assert setup.post('/api/portrait/photos/'+job['id']+'/use').status_code==422
    assert sum(a=='CreateAsset' for a,_ in calls)==1

def test_combined_duration_validation(tmp_path):
    from app.person_video import validate_pair
    a=tmp_path/'a.mp4';a.write_bytes(video_bytes(tmp_path,8))
    b=tmp_path/'b.mp4';b.write_bytes(video_bytes(tmp_path,8))
    with pytest.raises(ValueError,match='15'):validate_pair(a,b)
    b.write_bytes(video_bytes(tmp_path,3));assert validate_pair(a,b)==11


def make_draft(client,tmp_path,source_seconds=7):
    from PIL import Image
    lib=portrait_library.PortraitLibrary(main.settings)
    person=lib.add_person('group-person','测试人物')
    source=upload(client,video_bytes(tmp_path,source_seconds),'video').json()
    person_asset=upload(client,video_bytes(tmp_path,3)).json()
    pic=BytesIO();Image.new('RGB',(400,400),'red').save(pic,format='PNG')
    clothes=client.post('/api/production/assets',data={'kind':'clothing'},files={'file':('c.png',pic.getvalue(),'image/png')}).json()
    face=client.post('/api/production/assets',data={'kind':'face'},files={'file':('p.png',pic.getvalue(),'image/png')}).json()
    draft=client.post('/api/production/drafts',json={}).json()
    result=client.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'person_id':person['id'],
        'source_asset_id':source['id'],'person_video_asset_id':person_asset['id'],'person_reference_mode':'video',
        'face_asset_ids':[face['id']],'clothing_asset_ids':[clothes['id']],
        'model':{'model':'doubao-seedance-2-0-260128'}})
    assert result.status_code==200,result.text
    return result.json(),person_asset,source

def test_draft_restore_run_uses_video_only_and_does_not_mask_identity(setup,tmp_path,monkeypatch):
    draft,person_asset,source=make_draft(setup,tmp_path)
    copied=setup.post('/api/production/drafts',json={'copy_from':draft['id']}).json()
    assert copied['person_reference_mode']=='video' and copied['person_video_asset_id']==person_asset['id']
    assert person_asset['id'] in {a['id'] for a in copied['assets']}
    r=setup.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'video-first'})
    assert r.status_code==200,r.text
    store=ProductionStore(main.settings.storage_dir)
    run=store.get_run(r.json()['id'],private=True)
    uploads=run['private']['portrait']['uploads']
    assert set(uploads)=={person_asset['id']}
    assert len(store.get_run(run['id'])['snapshot']['face_asset_ids'])==1
    touched=[]
    def mask(src,dst,*args):
        touched.append(src);dst.write_bytes(src.read_bytes())
    monkeypatch.setattr(production_worker,'run_deface',mask)
    production_worker.execute_run(main.settings,store,run)
    assert store.get_run(run['id'])['status']=='queued' and not touched
    lib=portrait_library.PortraitLibrary(main.settings)
    lib.update(next(iter(uploads.values())),status='active',remote_id='asset-video')
    def request(self,action,payload):
        if action=='GetAssetGroup':return {'Id':'group-person','GroupType':'LivenessFace','ProjectName':'default'}
        assert action=='GetAsset'
        assert self.asset_type=='Video'
        return {'Id':'asset-video','GroupId':'group-person','AssetType':'Video','ProjectName':'default','Status':'Active'}
    monkeypatch.setattr(portrait_service.ArkPortraitClient,'_request',request)
    calls=[]
    def generate(self,video,faces,clothes,prompt,output,**kwargs):
        assert faces==[] and len(clothes)==1
        assert kwargs['person_video_uri']=='asset://asset-video'
        assert kwargs['person_video'].read_bytes()==video_bytes(tmp_path,3)
        assert 'image_asset_uris' not in kwargs
        calls.append(kwargs);output.write_bytes(b'fake generation')
    monkeypatch.setattr(production_worker.VideoProvider,'generate',generate)
    production_worker.execute_run(main.settings,store,store.get_run(run['id'],private=True))
    assert store.get_run(run['id'])['status']=='succeeded'
    assert len(calls)==1 and touched==[Path(store.get_asset(source['id'],private=True)['path'])]
    assert calls[0]['person_video'] not in touched

def test_video_run_rejects_overlong_combination_before_enqueuing(setup,tmp_path):
    draft,_,_=make_draft(setup,tmp_path,13)
    result=setup.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'long'})
    assert result.status_code==422 and '15' in result.text
    with ProductionStore(main.settings.storage_dir).connection() as db:
        assert db.execute('SELECT count(*) FROM portrait_photos').fetchone()[0]==0
        assert db.execute('SELECT count(*) FROM production_runs').fetchone()[0]==0

def test_provider_sends_two_video_references_and_correct_image_roles(tmp_path,monkeypatch):
    from app.generation_settings import GenerationConfig
    from app.video_provider import VideoProvider,ProviderError
    from PIL import Image
    source=tmp_path/'source.mp4';source.write_bytes(video_bytes(tmp_path,7))
    person=tmp_path/'person.mp4';person.write_bytes(video_bytes(tmp_path,3))
    clothes=tmp_path/'c.png';Image.new('RGB',(400,400),'red').save(clothes)
    provider=VideoProvider(GenerationConfig(mode='http',api_key='test',public_base_url='https://test.invalid'))
    bodies=[]
    def request(*args,**kwargs):bodies.append(kwargs['json']);return {'id':'task-test'}
    monkeypatch.setattr(provider,'_request',request)
    monkeypatch.setattr(provider,'_poll',lambda *args:{'task_id':args[0]})
    result=provider.generate(source,[],[clothes],'应用@Image1人物参考图，应用@Image2衣服参考图',tmp_path/'out.mp4',video_url='https://test.invalid/source',person_video=person,person_video_uri='asset://asset-video')
    assert result['task_id']=='task-test'
    content=bodies[0]['content'];assert [x['type'] for x in content]==['text','image_url','video_url','video_url']
    assert content[2]['video_url']['url']=='https://test.invalid/source'
    assert content[3]=={'type':'video_url','video_url':{'url':'asset://asset-video'},'role':'reference_video'}
    text=content[0]['text']
    assert '@Video2人物参考视频' in text and '@Image1衣服参考图' in text and '@Image1人物参考图' not in text
    assert provider._content_roles[3]=='人物参考视频'
    with pytest.raises(ProviderError):provider.generate(source,[],[clothes],'',tmp_path/'out.mp4',person_video=person,person_video_uri='https://test.invalid/raw-face.mp4')
