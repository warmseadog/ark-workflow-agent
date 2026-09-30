"""The everyday task list supports explicit, read-only administrator scopes."""
import pytest
from app import main
from app.production_store import ProductionStore
from app.tenancy import user_settings
from tests.test_access_control import protected, accounts_clients


@pytest.fixture(autouse=True)
def isolate_legacy_jobs(monkeypatch):
    from app.jobs import store
    monkeypatch.setattr(store, 'list', lambda: [])


def seed(user, count=1):
    settings = user_settings(main.settings, user)
    store = ProductionStore(settings.storage_dir)
    result = []
    for i in range(count):
        draft = store.create_draft({'name': user['username']+str(i), 'model': {'duration': 8}})
        run = store.create_run(draft['id'], draft['revision'], str(i), {'secret': 'must-not-leak'})
        store.update_run(run['id'], status='succeeded')
        result.append(run)
    return settings, store, result


def test_admin_records_all_filter_pagination_and_deleted(accounts_clients):
    accounts, users, (admin, alice, bob) = accounts_clients
    seed(users[0])
    _, store, runs = seed(users[1], 12)
    seed(users[2])
    store.delete_run(runs[0]['id'])
    accounts.update_user(users[2]['id'], users[0]['id'], enabled=False)
    first = admin.get('/api/admin/task-records').json()
    assert first['total'] == 13 and first['pages'] == 2 and len(first['items']) == 10
    second = admin.get('/api/admin/task-records?page=2').json()
    assert len(second['items']) == 3
    assert len({(x['user_id'], x['id']) for x in first['items']+second['items']}) == 13
    assert {x['username'] for x in first['users']} == {'admin', 'alice', 'bob'}
    assert next(x for x in first['users'] if x['username']=='bob')['enabled'] is False
    filtered = admin.get('/api/admin/task-records', params={'user_id':users[1]['id'], 'page':99}).json()
    assert filtered['page'] == 2 and filtered['total'] == 11
    assert all(x['user_id']==users[1]['id'] and x['read_only'] for x in filtered['items'])
    mine = admin.get('/api/admin/task-records', params={'user_id':users[0]['id']}).json()
    assert mine['items'][0]['read_only'] is False
    assert admin.get('/api/admin/task-records?user_id=missing').status_code == 404
    assert admin.get('/api/admin/task-records?page=100000000000000000000').status_code == 422
    assert alice.get('/api/admin/task-records').status_code == 403
    assert alice.get('/api/production/runs?page=1').json()['total'] == 11


def test_admin_record_detail_media_are_scoped_and_read_only(accounts_clients):
    _, users, (admin, alice, bob) = accounts_clients
    settings, store, runs = seed(users[1])
    run = runs[0]
    output = settings.storage_dir/'outputs'/(run['id']+'.mp4')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b'0123456789')
    base = '/api/admin/task-records/'+users[1]['id']+'/'+run['id']
    detail = admin.get(base)
    assert detail.status_code == 200
    assert detail.json()['username'] == 'alice' and detail.json()['read_only'] is True
    assert 'must-not-leak' not in detail.text and str(settings.storage_dir) not in detail.text
    assert detail.json()['download_url'] == base+'/download'
    assert admin.get(base+'/playback').json()['original_url'] == base+'/download'
    assert admin.get(base+'/download', headers={'Range':'bytes=2-5'}).content == b'2345'
    assert admin.head(base+'/download').status_code == 200
    for client in (alice, bob):
        for suffix in ('', '/download', '/playback', '/playback/original', '/defaced'):
            assert client.get(base+suffix).status_code == 403
    assert bob.get('/api/production/runs/'+run['id']).status_code == 404
    assert admin.get(base.replace(users[1]['id'],users[2]['id'])+'/download').status_code == 404
    for suffix, method in (('', 'DELETE'), ('/name','PUT'), ('/copy','POST')):
        assert admin.request(method,base+suffix,json={}).status_code in (404,405)
    store.delete_run(run['id'])
    assert admin.get(base).status_code == admin.get(base+'/download').status_code == 404


def test_admin_assets_must_belong_to_the_selected_run(accounts_clients):
    from PIL import Image
    _, users, (admin,alice,_) = accounts_clients
    settings = user_settings(main.settings,users[1])
    store = ProductionStore(settings.storage_dir)
    root = settings.storage_dir/'assets';root.mkdir(parents=True,exist_ok=True)
    for ident in ('included','unrelated'):
        path = root/(ident+'.png')
        Image.new('RGB',(64,64),'blue').save(path)
        store.add_asset(ident,ident+'.png','face',path,path.stat().st_size,'image/png',ident)
    draft = store.create_draft({'face_asset_ids':['included']})
    run = store.create_run(draft['id'],draft['revision'],'assets',{})
    base = '/api/admin/task-records/'+users[1]['id']+'/'+run['id']
    asset = admin.get(base).json()['snapshot']['assets'][0]
    assert asset['url'] == base+'/assets/included/file'
    assert admin.get(asset['url']).content == (root/'included.png').read_bytes()
    assert admin.get(asset['thumbnail_url']).status_code == 200
    assert admin.get(base+'/assets/unrelated/file').status_code == 404
    assert alice.get(asset['url']).status_code == 403


def test_legacy_owner_records_and_missing_outputs(accounts_clients,monkeypatch):
    from app.jobs import Job,store as jobs
    _,users,(admin,_,_) = accounts_clients
    job = Job(id='old-task',status='succeeded',output_name='old-video.mp4',defaced_name='blurred.mp4')
    missing = Job(id='missing-output',status='succeeded')
    monkeypatch.setattr(jobs,'list',lambda:[job,missing])
    monkeypatch.setattr(jobs,'get',lambda ident:job if ident==job.id else missing if ident==missing.id else None)
    root = main.settings.storage_dir
    (root/'outputs').mkdir(exist_ok=True);(root/'outputs'/'old-video.mp4').write_bytes(b'old video')
    (root/'work'/job.id).mkdir(parents=True);(root/'work'/job.id/'blurred.mp4').write_bytes(b'old blur')
    base = '/api/admin/task-records/'+users[0]['id']+'/legacy-'+job.id
    records = admin.get('/api/admin/task-records').json()
    assert records['total'] == 2
    detail = admin.get(base).json()
    assert detail['legacy'] is True and detail['defaced_url']==base+'/defaced'
    assert admin.get(base+'/download').content==b'old video'
    assert admin.get(base+'/defaced').content==b'old blur'
    assert admin.get(base+'/playback').json()['original_url']==base+'/download'
    assert admin.get(base.replace(users[0]['id'],users[1]['id'])).status_code==404
