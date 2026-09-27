from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from app import main, production_worker
from app.generation_settings import GenerationConfig
from app.video_provider import VideoProvider, ProviderError
from tests.test_production_api import asset, complete_draft

KINDS = ['bag','hat','watch','shoes','necklace','glasses']

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main,'settings',replace(main.settings,storage_dir=tmp_path,seedance_mode='mock'))
    monkeypatch.setattr(production_worker,'wake',lambda _:None)
    return TestClient(main.app)

def test_accessories_persist_validate_and_count(client):
    d=complete_draft(client)
    refs={k:asset(client,k,k+'.png')['id'] for k in KINDS+['scene','hairstyle']}
    values={k+'_asset_ids':[v] for k,v in refs.items()}
    values.update({k+'_enabled':True for k in refs})
    r=client.put('/api/production/drafts/'+d['id'],json={'revision':d['revision'],**values})
    assert r.status_code==200,r.text
    d=r.json()
    body={'draft_id':d['id'],'revision':d['revision'],'idempotency_key':'overflow'}
    assert client.post('/api/production/runs',json=body).status_code==422
    d=client.put('/api/production/drafts/'+d['id'],json={'revision':d['revision'],'hat_enabled':False}).json()
    run=client.post('/api/production/runs',json={**body,'revision':d['revision'],'idempotency_key':'allowed'}).json()
    assert run['snapshot']['hat_asset_ids']==[refs['hat']] and run['snapshot']['hat_enabled'] is False
    copied=client.post('/api/production/runs/'+run['id']+'/copy').json()
    assert copied['glasses_asset_ids']==[refs['glasses']]
    for bad in ({'bag_asset_ids':[refs['hat']]},{'bag_enabled':'yes'},{'bag_asset_ids':[refs['bag'],refs['bag']]}):
        assert client.put('/api/production/drafts/'+copied['id'],json={'revision':copied['revision'],**bad}).status_code==422

@pytest.fixture
def paths(tmp_path):
    values=[]
    for name in ['video.mp4','face.png','clothes.png','hair.png','glasses.png']:
        p=tmp_path/name;p.write_bytes(name.encode());values.append(p)
    return values

def test_provider_maps_accessories_and_text_only_scene(paths,tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(VideoProvider,'_request',lambda self,*a,**kw:calls.append(kw) or {'id':'task'})
    monkeypatch.setattr(VideoProvider,'_poll',lambda *a:{})
    config=GenerationConfig(mode='http',api_key='key',public_base_url='https://example.test')
    VideoProvider(config).generate(paths[0],[paths[1]],[paths[2]],'用户自定义',tmp_path/'out.mp4',video_url='https://example.test/video',
        hairstyles=[paths[3]],accessories={'glasses':[paths[4]]},scene_description='夜晚的海边')
    content=calls[0]['json']['content']
    assert len(content)==6
    text=content[0]['text']
    assert '@Image4（图片4）为眼镜参考图' in text and '镜框' in text
    assert '夜晚的海边' in text and '保留原视频场景' not in text and '用户自定义' in text

def test_material_error_names_all_actual_roles(paths,tmp_path,monkeypatch):
    from tests.test_video_provider import response
    monkeypatch.setattr('app.video_provider.requests.request',lambda *a,**kw:response({'error':{'message':"content[3] content[4] may contain real person",'code':'InputImageRejected'}},400))
    with pytest.raises(ProviderError) as err:
        VideoProvider(GenerationConfig(mode='http',api_key='key')).generate(paths[0],[paths[1]],[paths[2]],'',tmp_path/'out.mp4',
            video_url='https://example.test/video',hairstyles=[paths[3]],accessories={'glasses':[paths[4]]})
    message=str(err.value)
    assert '发型参考图' in message and '眼镜参考图' in message and 'InputImageRejected' in message
    assert '第 1 张人物' not in message


def test_worker_uses_enabled_snapshot_references_and_scene_text(client,monkeypatch):
    from app.production_store import ProductionStore
    d=complete_draft(client)
    bag=asset(client,'bag','bag.png'); hat=asset(client,'hat','hat.png')
    d=client.put('/api/production/drafts/'+d['id'],json={'revision':d['revision'],
        'bag_asset_ids':[bag['id']],'bag_enabled':True,'hat_asset_ids':[hat['id']],'hat_enabled':False,
        'scene_enabled':True,'scene_description':'日落海边'}).json()
    run=client.post('/api/production/runs',json={'draft_id':d['id'],'revision':d['revision'],'idempotency_key':'worker-accessory'}).json()
    # Editor changes cannot mutate the already-submitted run.
    client.put('/api/production/drafts/'+d['id'],json={'revision':d['revision'],'bag_enabled':False,'hat_enabled':True})
    monkeypatch.setattr(production_worker,'run_deface',lambda src,dst,*a:dst.write_bytes(b'redacted'))
    calls=[]
    monkeypatch.setattr(VideoProvider,'generate',lambda self,*a,**kw:calls.append(kw))
    store=ProductionStore(main.settings.storage_dir)
    production_worker.execute_run(main.settings,store,store.claim_next())
    assert list(calls[0]['accessories'])==['bag']
    assert calls[0]['scene_description']=='日落海边' and calls[0]['scenes']==[]
    assert store.get_run(run['id'])['status']=='succeeded'


def test_resume_reconstructs_error_roles_from_snapshot_without_source_files(client,monkeypatch):
    from app.production_store import ProductionStore
    from tests.test_video_provider import response
    d=complete_draft(client);g=asset(client,'glasses','glasses.png')
    d=client.put('/api/production/drafts/'+d['id'],json={'revision':d['revision'],'glasses_asset_ids':[g['id']],'glasses_enabled':True}).json()
    run=client.post('/api/production/runs',json={'draft_id':d['id'],'revision':d['revision'],'idempotency_key':'resume-role'}).json()
    store=ProductionStore(main.settings.storage_dir)
    store.update_run(run['id'],provider_task_id='existing-task')
    for a in d['assets']:
        from pathlib import Path
        Path(store.get_asset(a['id'],private=True)['path']).unlink()
    monkeypatch.setattr('app.video_provider.requests.request',lambda method,*a,**kw:response({'status':'failed','error':{'message':'content[3] may contain real person'}}) if method=='GET' else pytest.fail('must not submit'))
    production_worker.execute_run(main.settings,store,store.claim_next())
    result=store.get_run(run['id'])
    assert result['status']=='failed' and '眼镜参考图' in result['error']
