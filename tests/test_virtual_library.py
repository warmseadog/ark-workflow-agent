from dataclasses import replace
import pytest
from app import main, portrait_service as service
from app.portrait_library import PortraitLibrary

@pytest.fixture
def settings(tmp_path):
    settings=replace(main.settings,storage_dir=tmp_path)
    service.save_config(settings,{'access_key':'ak','secret_key':'sk'})
    return settings

def test_virtual_group_type_is_explicit_and_real_default_stays(settings,monkeypatch):
    cfg=service.load_config(settings)
    api=service.ArkPortraitClient(cfg,person_type='AIGC')
    calls=[]
    def request(action,payload):
        calls.append((action,payload))
        return {'Id':'group-v','GroupType':'AIGC','ProjectName':'default'}
    monkeypatch.setattr(api,'_request',request)
    assert api.get_group('group-v')['GroupType']=='AIGC'
    real=service.ArkPortraitClient(cfg)
    monkeypatch.setattr(real,'_request',request)
    with pytest.raises(ValueError): real.get_group('group-v')
    assert api.create_group('virtual')['Id']=='group-v'
    assert calls[-2][0]=='CreateAssetGroup'
    assert calls[-2][1]['GroupType']=='AIGC'

def test_virtual_people_are_not_certified_and_persist_type(settings):
    lib=PortraitLibrary(settings)
    virtual=lib.add_person('group-v','Virtual',person_type='AIGC')
    real=lib.add_person('group-r','Real')
    assert virtual['person_type']=='AIGC' and virtual['verified'] is False
    assert real['person_type']=='LivenessFace' and real['verified'] is True
    assert PortraitLibrary(settings).person(virtual['id'])['person_type']=='AIGC'
    with pytest.raises(ValueError): lib.add_person('group-v',person_type='LivenessFace')

def test_virtual_creation_timeout_is_durable_no_duplicate(settings,monkeypatch):
    calls=[]
    def create(self,name):
        calls.append(name)
        raise service.PortraitError('timeout')
    monkeypatch.setattr(service.ArkPortraitClient,'create_group',create)
    lib=PortraitLibrary(settings)
    first=lib.create_virtual('Alice','request-123')
    second=PortraitLibrary(settings).create_virtual('Alice','request-123')
    assert first['status']==second['status']=='uncertain'
    assert len(calls)==1

def test_virtual_asset_verification_rejects_real_group(settings,monkeypatch):
    api=service.ArkPortraitClient(service.load_config(settings),person_type='AIGC')
    def request(action,payload):
        if action=='GetAsset':return {'Id':'asset-v','GroupId':'group-v','AssetType':'Image','Status':'Active','ProjectName':'default'}
        return {'Id':'group-v','GroupType':'LivenessFace','ProjectName':'default'}
    monkeypatch.setattr(api,'_request',request)
    with pytest.raises(ValueError): api.get_asset('asset-v')


def test_virtual_upload_and_generation_require_active_official_aigc(settings,monkeypatch):
    from PIL import Image
    import hashlib
    from app import portrait_library as module
    from app.portrait_generation import prepare,verify,PortraitPending
    from app.generation_settings import GenerationConfig
    lib=PortraitLibrary(settings); person=lib.add_person('group-v','Virtual','AIGC')
    root=settings.storage_dir/'assets';root.mkdir(exist_ok=True)
    path=root/'face.png';Image.new('RGB',(400,400),'red').save(path)
    lib.store.add_asset('face-local','face.png','face',path,path.stat().st_size,'image/png',hashlib.sha256(path.read_bytes()).hexdigest())
    types=[]; state={'status':'Processing','creates':0}
    class API:
        def __init__(self,config,person_type='LivenessFace'): types.append(person_type)
        def get_group(self,ident):return {'Id':ident,'GroupType':'AIGC','ProjectName':'default'}
        def create_asset(self,*args):state['creates']+=1;return 'asset-v'
        def asset_state(self,*args):return {'status':state['status']}
        def get_asset(self,ident):return {'group_id':'group-v','remote_asset_id':ident,'person_type':'AIGC'}
    monkeypatch.setattr(service,'ArkPortraitClient',API)
    monkeypatch.setattr(module,'upload_photo',lambda *args:'https://private.invalid/photo')
    snapshot=prepare(settings,lib.store,{'person_id':person['id'],'face_asset_ids':['face-local']},GenerationConfig(protocol='ark',base_url='https://ark.cn-beijing.volces.com/api/v3'))
    assert snapshot['person_type']=='AIGC'
    with pytest.raises(PortraitPending): verify(snapshot,lib.store)
    lib.process_one();job_id=snapshot['uploads']['face-local'];lib.update(job_id,next_check=0)
    lib.process_one()
    with pytest.raises(PortraitPending): verify(snapshot,lib.store)
    state['status']='Active';lib.update(job_id,next_check=0);lib.process_one()
    assert verify(snapshot,lib.store)=={str(path.resolve()):'asset://asset-v'}
    assert set(types)=={'AIGC'} and state['creates']==1
    service.save_config(settings,{'access_key':'other','secret_key':'other'})
    with pytest.raises(ValueError): verify(snapshot,lib.store)


def test_virtual_api_create_sync_status_and_account_isolation(settings,monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main,'settings',settings)
    class API:
        def __init__(self,config,person_type='LivenessFace'):assert person_type=='AIGC'
        def create_group(self,name):return {'Id':'group-v','Name':name,'GroupType':'AIGC','ProjectName':'default'}
        def list_groups(self):return [{'Id':'group-v','Name':'V','GroupType':'AIGC','ProjectName':'default'}]
    monkeypatch.setattr(service,'ArkPortraitClient',API)
    client=TestClient(main.app)
    result=client.post('/api/portrait/people',json={'name':'V','person_type':'AIGC','request_id':'request-virtual'})
    assert result.status_code==200 and result.json()['status']=='ready'
    result=client.post('/api/portrait/people/sync',json={'person_type':'AIGC'})
    person=result.json()['items'][0]
    assert person['person_type']=='AIGC' and not person['verified']
    assert client.post('/api/portrait/virtual/test',json={}).json()['ok']
    status=client.get('/api/portrait/virtual/status');assert status.status_code==200
    assert 'secret_key' not in status.text and 'access_key' not in status.text
    assert client.post('/api/portrait/people',json={'name':'R','person_type':'LivenessFace','request_id':'request-real'}).status_code==422
    service.save_config(settings,{'access_key':'other','secret_key':'other'})
    assert client.get('/api/portrait/people').json()['items']==[]
    assert client.get('/api/portrait/virtual/status').json()['requests']==[]


def test_uncertain_name_cannot_resubmit_after_reload(settings,monkeypatch):
    calls=[]
    def create(*args):calls.append(1);raise service.PortraitError('timeout')
    monkeypatch.setattr(service.ArkPortraitClient,'create_group',create)
    lib=PortraitLibrary(settings)
    assert lib.create_virtual('Name','request-one')['status']=='uncertain'
    assert PortraitLibrary(settings).create_virtual('Name','request-two')['status']=='uncertain'
    assert len(calls)==1


def test_virtual_import_verifies_upstream_and_never_accepts_pending(settings,monkeypatch):
    from fastapi.testclient import TestClient
    from PIL import Image
    from app.portrait_generation import prepare,verify
    from app.generation_settings import GenerationConfig
    monkeypatch.setattr(main,'settings',settings)
    state={'status':'Active','type':'AIGC','project':'default'}
    def request(self,action,payload):
        if action=='GetAsset':return {'Id':'asset-v','GroupId':'group-v','AssetType':'Image',
            'Status':state['status'],'ProjectName':state['project'],'URL':'https://ark-asset.cn-beijing.volcengine.com/v.png'}
        return {'Id':'group-v','GroupType':state['type'],'ProjectName':'default'}
    monkeypatch.setattr(service.ArkPortraitClient,'_request',request)
    monkeypatch.setattr(service,'download_image',lambda remote,path:Image.new('RGB',(400,400)).save(path,format='PNG'))
    client=TestClient(main.app)
    payload={'remote_asset_id':'asset-v','person_type':'AIGC'}
    asset=client.post('/api/portrait/import',json=payload).json()
    assert asset['person_type']=='AIGC'
    lib=PortraitLibrary(settings)
    person=lib.people()[0];assert person['person_type']=='AIGC'
    assert person['photo_count']==1
    snapshot=prepare(settings,lib.store,{'face_asset_ids':[asset['id']]},GenerationConfig(protocol='ark',base_url='https://ark.cn-beijing.volces.com/api/v3'))
    assert list(verify(snapshot,lib.store).values())==['asset://asset-v']
    for key,value in [('status','Processing'),('status','Failed'),('type','LivenessFace'),('project','other')]:
        state.update(status='Active',type='AIGC',project='default');state[key]=value
        assert client.post('/api/portrait/import',json=payload).status_code==422
        with pytest.raises(ValueError):verify(snapshot,lib.store)
    state.update(status='Active',type='AIGC',project='default')
    service.save_config(settings,{'access_key':'other','secret_key':'other'})
    with pytest.raises(ValueError):verify(snapshot,lib.store)


def local_photo(lib):
    from PIL import Image
    import hashlib
    root=lib.settings.storage_dir/'assets';root.mkdir(exist_ok=True)
    path=root/'reconcile.png';Image.new('RGB',(400,400),'blue').save(path)
    lib.store.add_asset('reconcile','p.png','face',path,path.stat().st_size,'image/png',hashlib.sha256(path.read_bytes()).hexdigest())
    return 'reconcile',path

@pytest.mark.parametrize('status',['queued','failed','uncertain'])
def test_verified_import_reconciles_existing_photo(settings,status):
    lib=PortraitLibrary(settings);person=lib.add_person('group-v','V','AIGC')
    asset_id,path=local_photo(lib);job=lib.enqueue(person['id'],asset_id)
    lib.update(job['id'],status=status,remote_id='asset-old',message='old')
    lib.record_verified_import(person['id'],asset_id,'asset-verified')
    actual=lib.get_photo(job['id'],private=True)
    assert actual['status']=='active' and actual['remote_id']=='asset-verified'
    assert actual['checked']>0 and actual['next_check']==0
    assert lib.enqueue(person['id'],asset_id)['id']==job['id']
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError):lib.record_verified_import(person['id'],asset_id,'asset-other')


def test_import_waits_for_inflight_worker_then_reconciles(settings,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app import portrait_library as module
    lib=PortraitLibrary(settings);person=lib.add_person('group-v','V','AIGC')
    asset_id,_=local_photo(lib);job=lib.enqueue(person['id'],asset_id)
    entered,release=Event(),Event()
    class API:
        def __init__(self,*args,**kwargs):pass
        def get_group(self,*args):return {}
        def create_asset(self,*args):
            entered.set(); assert release.wait(3);return 'asset-worker'
    monkeypatch.setattr(service,'ArkPortraitClient',API)
    monkeypatch.setattr(module,'upload_photo',lambda *args:'https://private.invalid/photo')
    with ThreadPoolExecutor(max_workers=2) as pool:
        worker=pool.submit(lib.process_one);assert entered.wait(3)
        imported=pool.submit(lib.record_verified_import,person['id'],asset_id,'asset-import')
        try:
            # An importer must not modify this record while the submitting worker owns it.
            from concurrent.futures import TimeoutError
            with pytest.raises(TimeoutError): imported.result(timeout=.1)
        finally: release.set()
        worker.result(timeout=3);imported.result(timeout=3)
    actual=lib.get_photo(job['id'],private=True)
    assert actual['status']=='active' and actual['remote_id']=='asset-import'
    lib.process_one()
    assert lib.get_photo(job['id'])['status']=='active'


def test_mock_success_not_counted_as_generated(settings):
    import json
    lib=PortraitLibrary(settings);person=lib.add_person('group-v','V','AIGC')
    with lib.store.connection() as db:
        for mode in ['mock','ark']:
            db.execute("INSERT INTO production_runs (id,draft_id,revision,idempotency_key,snapshot,private,status,stage,message,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (mode,'draft',1,mode,json.dumps({'person_id':person['id'],'model':{'mode':mode}}),'{}','succeeded','complete','','now','now'))
    assert lib.person(person['id'])['generated_count']==1


@pytest.mark.parametrize('person_type', ['AIGC','LivenessFace'])
def test_remove_virtual_person_is_durable_reversible_and_preserves_photos(settings, monkeypatch, person_type):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(service.ArkPortraitClient, 'list_groups', lambda self: [
        {'Id': 'group-v', 'Name': 'Cloud name', 'GroupType': 'AIGC', 'ProjectName': 'default'}])
    lib = PortraitLibrary(settings)
    person = lib.add_person('group-v', 'Keep my name', person_type)
    asset_id, path = local_photo(lib)
    job = lib.enqueue(person['id'], asset_id)
    lib.update(job['id'], status='active', remote_id='asset-existing')
    client = TestClient(main.app)
    url = '/api/portrait/people/' + person['id']
    assert client.get(url + '/reference').json() == {'remote_asset_id': 'asset-existing'}
    assert client.delete(url).status_code == 200
    assert client.get('/api/portrait/people').json()['items'] == []
    assert client.get('/api/portrait/virtual/status').json()['people_count'] == 0
    assert client.post('/api/portrait/people/sync', json={'person_type':person_type}).json()['items'] == []
    assert PortraitLibrary(settings).people() == []
    assert path.is_file() and lib.get_photo(job['id'])['status'] == 'active'
    removed = client.get('/api/portrait/people?removed=true').json()['items']
    assert [p['id'] for p in removed] == [person['id']]
    assert client.post(url + '/restore', json={}).status_code == 200
    restored = client.get('/api/portrait/people').json()['items']
    assert len(restored) == 1 and restored[0]['name'] == 'Keep my name'
    assert restored[0]['photo_count'] == 1


def test_virtual_management_rejects_other_accounts_real_people_and_pending_reference(settings, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, 'settings', settings)
    lib = PortraitLibrary(settings)
    person = lib.add_person('group-v', 'Virtual', 'AIGC')
    real = lib.add_person('group-r', 'Real')
    client = TestClient(main.app)
    url = '/api/portrait/people/' + person['id']
    assert client.get(url + '/reference').status_code == 422
    assert client.get('/api/portrait/people/' + real['id'] + '/reference').status_code == 422
    service.save_config(settings, {'access_key':'other', 'secret_key':'other'})
    assert client.delete(url).status_code == 404
    assert client.post(url + '/restore', json={}).status_code == 404
    assert client.get(url + '/reference').status_code == 404
    assert client.get('/api/portrait/people?removed=true').json()['items'] == []


@pytest.mark.parametrize('person_type', ['AIGC', 'LivenessFace'])
def test_person_photo_gallery_is_local_scoped_and_includes_processing(settings, monkeypatch, person_type):
    from fastapi.testclient import TestClient
    from PIL import Image
    import hashlib
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(service.ArkPortraitClient, '_request', lambda *args: pytest.fail('Gallery must not query cloud'))
    lib = PortraitLibrary(settings)
    person = lib.add_person('group-gallery', 'Gallery', person_type)
    other = lib.add_person('group-other', 'Other', person_type)
    expected = []
    for index, state in enumerate(['active', 'processing', 'failed']):
        path = settings.storage_dir / 'assets' / ('gallery-' + str(index) + '.png')
        path.parent.mkdir(exist_ok=True)
        Image.new('RGB', (400,400), ['red','blue','green'][index]).save(path)
        asset = lib.store.add_asset('gallery'+str(index), path.name, 'face', path, path.stat().st_size, 'image/png', hashlib.sha256(path.read_bytes()).hexdigest())
        job = lib.enqueue(person['id'], asset['id'])
        lib.update(job['id'], status=state, remote_id='asset-gallery'+str(index))
        expected.append(job['id'])
    other_job = lib.enqueue(other['id'], 'gallery0')
    client = TestClient(main.app)
    url = '/api/portrait/people/' + person['id'] + '/photos'
    response = client.get(url)
    assert response.status_code == 200
    items = response.json()['items']
    assert {p['id'] for p in items} == set(expected)
    assert other_job['id'] not in {p['id'] for p in items}
    assert {p['status'] for p in items} == {'active','processing','failed'}
    for photo in items:
        assert photo['url'].startswith('/api/production/assets/')
        assert set(photo) == {'id','asset_id','name','kind','url','status','message','remote_asset_id'}
        assert photo['remote_asset_id'] == ('asset-gallery0' if photo['status']=='active' else None)
    service.save_config(settings, {'access_key':'different', 'secret_key':'different'})
    assert client.get(url).status_code == 404
