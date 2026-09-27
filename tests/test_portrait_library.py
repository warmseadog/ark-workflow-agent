from dataclasses import replace
from io import BytesIO
import json
import time
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from app import main, portrait_service, production_worker

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path, seedance_mode='mock'))
    portrait_service.save_config(main.settings, {'access_key':'test-ak','secret_key':'test-sk'})
    monkeypatch.setattr(production_worker,'wake',lambda settings:None)
    from app.portrait_sessions import Sessions
    sessions=Sessions(main.settings)
    account=portrait_service.fingerprint(portrait_service.load_config(main.settings))
    with sessions.db() as db:
        db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)', ('verified','request',account,'state',json.dumps({'status':'verified','group_id':'group-alice'})))
    return TestClient(main.app)

def picture(client, color='red', size=(400,400)):
    buf=BytesIO(); Image.new('RGB',size,color).save(buf,format='PNG')
    return client.post('/api/production/assets',data={'kind':'face'},files={'file':('person.png',buf.getvalue(),'image/png')}).json()

def test_verified_person_visible_without_photos_and_rename_is_durable(setup):
    r=setup.get('/api/portrait/people'); assert r.status_code==200,r.text
    people=r.json()['items']; assert len(people)==1
    person=people[0]; assert person['photo_count']==0 and person['verified']
    r=setup.put('/api/portrait/people/'+person['id'],json={'name':'小李'})
    assert r.status_code==200
    assert setup.get('/api/portrait/people').json()['items'][0]['name']=='小李'
    assert setup.put('/api/portrait/people/'+person['id'],json={'name':' '*10}).status_code==422
    assert 'test-sk' not in r.text and 'fingerprint' not in r.text
    portrait_service.save_config(main.settings, {'access_key':'other','secret_key':'other'})
    assert setup.get('/api/portrait/people').json()['items']==[]

def test_photo_dedup_is_per_person_and_validates_real_image(setup):
    from app.portrait_library import PortraitLibrary
    lib=PortraitLibrary(main.settings)
    person=setup.get('/api/portrait/people').json()['items'][0]
    other=lib.add_person('group-bob')
    first=picture(setup); second=picture(setup)
    def enqueue(person,asset):
        return setup.post('/api/portrait/photos',json={'person_id':person['id'],'asset_id':asset['id']})
    a=enqueue(person,first); assert a.status_code==200,a.text
    b=enqueue(person,second); assert b.json()['id']==a.json()['id']
    c=enqueue(other,first); assert c.json()['id']!=a.json()['id']
    assert enqueue(person,picture(setup,size=(30,30))).status_code==422
    assert setup.post('/api/portrait/photos',json={'person_id':'unknown','asset_id':first['id']}).status_code==404
    r=setup.get('/api/portrait/photos',params={'ids':a.json()['id']+','+c.json()['id']})
    assert len(r.json()['items'])==2
    assert 'test-sk' not in r.text and 'path' not in r.text and 'fingerprint' not in r.text

def test_photo_background_active_mismatch_and_uncertain_recovery(setup,monkeypatch):
    from app import portrait_library as module
    lib=module.PortraitLibrary(main.settings)
    person=setup.get('/api/portrait/people').json()['items'][0]
    state={'creates':0,'status':'Processing','uncertain':False}
    class API:
        def __init__(self,config):pass
        def get_group(self,gid):return {'Id':gid}
        def create_asset(self,gid,url,name):
            state['creates']+=1
            if state['uncertain']:raise portrait_service.PortraitError('网络异常')
            return 'asset-photo'
        def find_created_asset(self,gid,name):return 'asset-photo'
        def asset_state(self,aid,gid):return {'status':state['status'],'error_code':'FaceMismatch'}
    monkeypatch.setattr(portrait_service,'ArkPortraitClient',API)
    monkeypatch.setattr(module,'upload_photo',lambda *args:'https://test.invalid/private?signature=secret')
    photo=lib.enqueue(person['id'],picture(setup)['id'])
    lib.process_one(); assert lib.get_photo(photo['id'])['status']=='processing'
    state['status']='Active'; lib.update(photo['id'],next_check=0)
    lib.process_one(); assert lib.get_photo(photo['id'])['status']=='active'
    assert state['creates']==1
    bad=lib.enqueue(person['id'],picture(setup,'blue')['id'])
    state['status']='Failed'; lib.process_one(); lib.update(bad['id'],next_check=0); lib.process_one()
    assert '不一致' in lib.get_photo(bad['id'])['message']
    uncertain=lib.enqueue(person['id'],picture(setup,'green')['id'])
    state.update(uncertain=True,status='Active');lib.process_one()
    lib.update(uncertain['id'],next_check=0);lib.process_one()
    lib.update(uncertain['id'],next_check=0);lib.process_one()
    assert lib.get_photo(uncertain['id'])['status']=='active'
    assert state['creates']==3


def test_pending_photos_do_not_block_submit_or_other_runs_and_freeze_person(setup,monkeypatch):
    from app.portrait_library import PortraitLibrary
    from app.production_store import ProductionStore
    lib=PortraitLibrary(main.settings);store=ProductionStore(main.settings.storage_dir)
    person=setup.get('/api/portrait/people').json()['items'][0]
    image=picture(setup)
    video=setup.post('/api/production/assets',data={'kind':'video'},files={'file':('v.mp4',b'video','video/mp4')}).json()
    clothing=setup.post('/api/production/assets',data={'kind':'clothing'},files={'file':('c.png',b'clothing','image/png')}).json()
    draft=setup.post('/api/production/drafts',json={}).json()
    r=setup.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'person_id':person['id'],
        'source_asset_id':video['id'],'face_asset_ids':[image['id']],'clothing_asset_ids':[clothing['id']]})
    assert r.status_code==200,r.text
    draft=r.json()
    result=setup.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'pending-photo'})
    assert result.status_code==200,result.text
    run=result.json(); assert run['snapshot']['person_id']==person['id']
    called=[]
    monkeypatch.setattr(production_worker,'run_deface',lambda *args:called.append('deface'))
    production_worker.execute_run(main.settings,store,store.claim_next())
    assert not called
    current=store.get_run(run['id'])
    assert current['status']=='queued' and current['stage']=='authorizing'
    assert store.claim_next() is None
    other=lib.add_person('group-bob')
    setup.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'person_id':other['id']})
    assert store.get_run(run['id'])['snapshot']['person_id']==person['id']
    private=store.get_run(run['id'],private=True)['private']['portrait']
    jid=private['uploads'][image['id']]
    lib.update(jid,status='failed',message='照片与所选人物不一致')
    production_worker.execute_run(main.settings,store,store.get_run(run['id'],private=True))
    assert store.get_run(run['id'])['status']=='failed' and not called


def test_active_photo_uses_official_uri_and_rejects_changed_account(setup,monkeypatch):
    from app.portrait_library import PortraitLibrary
    from app.portrait_generation import prepare,verify
    from app.generation_settings import GenerationConfig
    lib=PortraitLibrary(main.settings)
    person=setup.get('/api/portrait/people').json()['items'][0]
    asset=picture(setup);job=lib.enqueue(person['id'],asset['id'])
    draft={'person_id':person['id'],'face_asset_ids':[asset['id']]}
    snapshot=prepare(main.settings,lib.store,draft,GenerationConfig(protocol='ark',base_url='https://ark.cn-beijing.volces.com/api/v3'))
    lib.update(job['id'],status='active',remote_id='asset-real')
    class API:
        def __init__(self,config):pass
        def get_asset(self,ident):return {'group_id':'group-alice','remote_asset_id':ident}
    monkeypatch.setattr(portrait_service,'ArkPortraitClient',API)
    assert list(verify(snapshot,lib.store).values())==['asset://asset-real']
    portrait_service.save_config(main.settings,{'access_key':'changed','secret_key':'changed'})
    with pytest.raises(ValueError,match='变更'):verify(snapshot,lib.store)


def test_restart_preserves_uncertainty_and_dedup_is_concurrent(setup):
    from concurrent.futures import ThreadPoolExecutor
    from app.portrait_library import PortraitLibrary
    lib=PortraitLibrary(main.settings)
    person=setup.get('/api/portrait/people').json()['items'][0]
    asset=picture(setup)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:lib.enqueue(person['id'],asset['id']),range(8)))
    assert len({x['id'] for x in results})==1
    ident=results[0]['id'];lib.update(ident,status='submitting');lib.recover()
    assert lib.get_photo(ident)['status']=='uncertain'
    lib.retry(ident);assert lib.get_photo(ident)['status']=='uncertain'
    lib.update(ident,status='uploading');lib.recover()
    assert lib.get_photo(ident)['status']=='queued'


def test_client_photo_contract_and_redacted_error(setup,monkeypatch):
    cfg=portrait_service.load_config(main.settings)
    api=portrait_service.ArkPortraitClient(cfg)
    calls=[]
    def request(action,payload):
        calls.append((action,payload))
        if action=='CreateAsset':return {'Id':'asset-created'}
        if action=='GetAsset':return {'Id':'asset-created','AssetType':'Image','Status':'Failed',
            'GroupId':'group-alice','ProjectName':'default','Error':{'Code':'FaceMismatch','Message':'private signed URL'}}
        return {'Items':[{'Id':'asset-created','Name':'portrait-name','GroupId':'group-alice','ProjectName':'default','AssetType':'Image'}]}
    monkeypatch.setattr(api,'_request',request)
    assert api.create_asset('group-alice','https://storage.invalid/private','portrait-name')=='asset-created'
    assert calls[-1][1]=={'GroupId':'group-alice','URL':'https://storage.invalid/private','Name':'portrait-name','AssetType':'Image','ProjectName':'default'}
    assert api.find_created_asset('group-alice','portrait-name')=='asset-created'
    state=api.asset_state('asset-created','group-alice')
    assert state=={'status':'Failed','error_code':'FaceMismatch'}
    with pytest.raises(ValueError):api.asset_state('asset-created','group-bob')


def test_account_switch_never_erases_uncertain_submission(setup,monkeypatch):
    from app.portrait_library import PortraitLibrary
    lib=PortraitLibrary(main.settings); person=setup.get('/api/portrait/people').json()['items'][0]
    job=lib.enqueue(person['id'],picture(setup)['id'])
    lib.update(job['id'],status='uncertain',next_check=0)
    portrait_service.save_config(main.settings,{'access_key':'other','secret_key':'other'})
    PortraitLibrary(main.settings).process_one()
    portrait_service.save_config(main.settings,{'access_key':'test-ak','secret_key':'test-sk'})
    restored=PortraitLibrary(main.settings)
    assert restored.get_photo(job['id'])['status']=='uncertain'
    assert restored.retry(job['id'])['status']=='uncertain'
