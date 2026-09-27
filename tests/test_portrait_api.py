from dataclasses import replace
from fastapi.testclient import TestClient
import pytest
from app import main

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path, seedance_mode='mock'))
    return TestClient(main.app)

def test_qr_is_official_only_local_only_and_does_not_authenticate(client):
    response=client.post('/api/portrait/qr',json={'url':'https://ark.volcengine.com/region:cn-beijing/mobile/authorization?pl=fixture'})
    assert response.status_code==200
    assert 'image/svg+xml' in response.headers['content-type']
    assert '<svg' in response.text
    assert response.headers['cache-control']=='no-store'
    for url in ['http://ark.volcengine.com/a','https://ark.volcengine.com.evil.test/a','https://user@ark.volcengine.com/a','https://127.0.0.1/a','javascript:alert(1)']:
        assert client.post('/api/portrait/qr',json={'url':url}).status_code==422
    assert client.post('/api/portrait/qr',json={'url':'https://ark.volcengine.com/a'},headers={'Origin':'https://evil.test'}).status_code==403
    assert client.get('/api/portrait/config').json()['has_credentials'] is False

def test_missing_configuration_reports_actionable_error(client):
    response=client.get('/api/portrait/assets')
    assert response.status_code==409
    assert client.get('/api/portrait/config').json()['mode']=='console_invitation'


@pytest.fixture
def official(client, monkeypatch):
    from app import portrait_service, production_worker
    from PIL import Image
    portrait_service.save_config(main.settings,{'access_key':'fixture-ak','secret_key':'fixture-sk'})
    state={'available':True,'downloads':0,'gets':0}
    remote={'remote_asset_id':'asset-realperson','name':'授权模特','group_id':'group-person','project':'default','status':'Active','asset_type':'Image','url':'https://ark-asset.cn-beijing.volcengine.com/secret.png?token=secret'}
    class Official:
        def __init__(self,config):pass
        def list_assets(self):return [remote.copy()]
        def get_asset(self,ident):
            state['gets']+=1
            if not state['available']:raise portrait_service.PortraitError('素材不可用')
            assert ident=='asset-realperson'
            return remote.copy()
    def download(asset,path):
        state['downloads']+=1
        Image.new('RGB',(12,12),(20,40,60)).save(path,format='PNG')
        return path
    monkeypatch.setattr(portrait_service,'ArkPortraitClient',Official)
    monkeypatch.setattr(portrait_service,'download_image',download)
    monkeypatch.setattr(production_worker,'wake',lambda settings:None)
    return state


def test_official_import_is_durable_deduplicated_and_not_a_local_photo_stamp(client,official):
    listing=client.get('/api/portrait/assets').json()
    assert 'token=secret' not in str(listing)
    response=client.post('/api/portrait/import',json={'remote_asset_id':'asset-realperson'})
    assert response.status_code==200,response.text
    asset=response.json()
    assert asset['portrait']['remote_asset_id']=='asset-realperson'
    assert 'fingerprint' not in str(asset)
    assert client.get(asset['url']).status_code==200
    duplicate=client.post('/api/portrait/import',json={'remote_asset_id':'asset-realperson'}).json()
    assert duplicate['id']==asset['id'] and official['downloads']==1
    bad=client.post('/api/portrait/import',json={'remote_asset_id':{'bad':1}})
    assert bad.status_code==422
    raw=client.post('/api/production/assets',data={'kind':'face'},files={'file':('another.png',b'arbitrary','image/png')}).json()
    assert 'portrait' not in raw
    assert client.post('/api/portrait/bind',json={'local_asset_id':raw['id'],'remote_asset_id':'asset-realperson'}).status_code==404


def test_authorized_submit_and_worker_revalidate_and_use_frozen_reference(client,official,monkeypatch):
    from app.production_store import ProductionStore
    from app import production_worker,portrait_service
    imported=client.post('/api/portrait/import',json={'remote_asset_id':'asset-realperson'}).json()
    video=client.post('/api/production/assets',data={'kind':'video'},files={'file':('ref.mp4',b'video','video/mp4')}).json()
    clothes=client.post('/api/production/assets',data={'kind':'clothing'},files={'file':('dress.png',b'dress','image/png')}).json()
    draft=client.post('/api/production/drafts',json={}).json()
    draft=client.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'source_asset_id':video['id'],'face_asset_ids':[imported['id']],'clothing_asset_ids':[clothes['id']]}).json()
    run=client.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'portrait-one'}).json()
    assert 'fixture-sk' not in str(run)
    store=ProductionStore(main.settings.storage_dir)
    assert store.get_run(run['id'],private=True)['private']['portrait']['config']['access_key']=='fixture-ak'
    calls=[]
    class Provider:
        def __init__(self,*args):pass
        def _safe(self,value):return str(value)
        def generate(self,video,faces,clothes,prompt,output,**kw):
            calls.append(kw['image_asset_uris'])
            output.write_bytes(b'complete')
    monkeypatch.setattr(production_worker,'VideoProvider',Provider)
    monkeypatch.setattr(production_worker,'run_deface',lambda src,dst,*args:dst.write_bytes(b'redacted'))
    production_worker.execute_run(main.settings,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='succeeded'
    assert list(calls[0].values())==['asset://asset-realperson']
    again=client.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'portrait-two'}).json()
    official['available']=False
    production_worker.execute_run(main.settings,store,store.claim_next())
    assert store.get_run(again['id'])['status']=='failed'
    assert len(calls)==1
    assert client.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'portrait-three'}).status_code==422
    official['available']=True
    portrait_service.save_config(main.settings,{'project_name':'changed'})
    result=client.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'portrait-four'})
    assert result.status_code==422 and '项目' in result.json()['detail']


def test_simultaneous_official_imports_share_one_local_asset(client,official,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app import portrait_service
    from app.production_store import ProductionStore
    original=portrait_service.download_image
    barrier=Barrier(2)
    def together(asset,path):
        barrier.wait(timeout=5)
        return original(asset,path)
    monkeypatch.setattr(portrait_service,'download_image',together)
    def upload():return client.post('/api/portrait/import',json={'remote_asset_id':'asset-realperson'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(upload) for _ in range(2)]
        failures=[future.exception() for future in futures if future.exception()]
        assert not failures, repr(failures)
        first,second=[future.result() for future in futures]
    assert first.status_code==second.status_code==200
    assert first.json()['id']==second.json()['id']
    store=ProductionStore(main.settings.storage_dir)
    with store.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM production_assets').fetchone()[0]==1
    assert len(list((main.settings.storage_dir/'assets').glob('*.png')))==1
