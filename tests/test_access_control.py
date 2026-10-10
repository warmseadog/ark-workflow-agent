from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from app import main
from app.config import settings


@pytest.fixture
def protected(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED','true')
    monkeypatch.setenv('APP_COOKIE_SECURE','false')
    monkeypatch.delenv('APP_PUBLIC_ORIGIN',raising=False)
    monkeypatch.setattr(main,'settings',replace(settings,storage_dir=tmp_path))
    return TestClient(main.app)


def test_auth_enabled_with_empty_database_fails_closed(protected):
    assert protected.get('/api/production/drafts').status_code == 401
    assert protected.get('/api/jobs').status_code == 401
    assert protected.get('/',follow_redirects=False).status_code == 303
    assert protected.get('/videos',follow_redirects=False).status_code == 303
    assert protected.get('/api/production/videos').status_code == 401
    assert protected.get('/healthz').status_code == 200


@pytest.mark.parametrize('value',[' true ','tru','enabled'])
def test_auth_flag_whitespace_or_typo_never_opens_private_api(protected,monkeypatch,value):
    monkeypatch.setenv('APP_AUTH_ENABLED',value)
    assert protected.get('/api/production/drafts').status_code==401


def test_public_callback_does_not_make_other_endpoints_public(protected):
    assert protected.get('/auth/portrait/done').status_code == 200
    assert protected.post('/auth/portrait/done').status_code in (401,405)
    assert protected.get('/api/portrait/config').status_code == 401
    assert protected.get('/api/admin/users').status_code == 401


@pytest.fixture
def accounts_clients(protected,monkeypatch):
    from app.accounts import Accounts
    from app import production_worker
    monkeypatch.setattr(production_worker,'wake',lambda settings:None)
    accounts=Accounts(main.settings.storage_dir)
    admin=accounts.init_admin('admin','temporary-admin-password')
    accounts.change_password(admin['id'],'temporary-admin-password','changed-admin-password')
    users=[admin]
    for name in ('alice','bob'):
        user=accounts.create_user(name,'temporary-user-password',admin['id'])
        accounts.change_password(user['id'],'temporary-user-password','changed-user-password')
        users.append(user)
    clients=[]
    for user in users:
        client=TestClient(main.app)
        response=client.post('/api/auth/login',headers={'Origin':'http://testserver'},json={
            'username':user['username'],'password':'changed-admin-password' if user['id']==admin['id'] else 'changed-user-password'})
        assert response.status_code == 200,response.text
        client.headers['X-CSRF-Token']=response.json()['csrf_token']
        clients.append(client)
    return accounts,users,clients


def test_two_accounts_cannot_read_or_mutate_each_others_objects(accounts_clients):
    from tests.test_production_api import complete_draft
    accounts,users,(_,a,b)=accounts_clients
    draft=complete_draft(a)
    run=a.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'same-key'}).json()
    assert 'id' in run,run
    assert b.get('/api/production/drafts').json()['items']==[]
    assert b.get('/api/production/runs?page=1').json()['items']==[]
    assert b.get('/api/production/videos').json()['items']==[]
    for path in ['/api/production/drafts/'+draft['id'], '/api/production/runs/'+run['id'],
                 '/api/production/runs/'+run['id']+'/playback', '/api/production/runs/'+run['id']+'/poster', draft['assets'][0]['url']]:
        assert b.get(path).status_code == 404,path
    for suffix in ('cancel','resume','copy','person-preparation/retry'):
        assert b.post('/api/production/runs/'+run['id']+'/'+suffix).status_code==404
    assert b.delete('/api/production/runs/'+run['id']).status_code==404
    assert b.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'prompt':'hacked'}).status_code==404
    second=complete_draft(b)
    other=b.post('/api/production/runs',json={'draft_id':second['id'],'revision':second['revision'],'idempotency_key':'same-key'})
    assert other.status_code==200,other.text
    assert other.json()['id']!=run['id']
    assert a.get(draft['assets'][0]['url']).headers['cache-control']=='no-store'
    assert a.post('/api/prompt-templates',json={'name':'alice only','content':'test'}).status_code==403
    assert b.get('/api/prompt-templates').status_code==403


def test_completed_video_library_and_posters_stay_with_owner(accounts_clients):
    from app import tenancy
    from app.production_store import ProductionStore
    _, users, (_, alice, bob) = accounts_clients
    config = tenancy.user_settings(main.settings, users[1])
    store = ProductionStore(config.storage_dir)
    draft = store.create_draft({'name': '仅限本人观看'})
    run = store.create_run(draft['id'], 1, 'library-isolation', {})
    store.update_run(run['id'], status='succeeded')
    output = config.storage_dir / 'outputs' / (run['id'] + '.mp4')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b'invalid-video-for-placeholder')
    assert alice.get('/api/production/videos').json()['items'][0]['id'] == run['id']
    poster = '/api/production/runs/' + run['id'] + '/poster'
    assert alice.get(poster).status_code == 200
    assert bob.get('/api/production/videos').json()['total'] == 0
    assert bob.get(poster).status_code == 404


def test_normal_account_cannot_access_global_admin_or_legacy(accounts_clients):
    _,_,(_,a,_)=accounts_clients
    for path in ('/admin/settings','/admin/users','/api/admin/users','/api/admin/overview','/api/jobs','/api/model-settings','/api/storage-settings','/api/portrait/assets'):
        assert a.get(path).status_code==403,path
    for path in ('/api/portrait/people/sync','/api/portrait/people/resolve','/api/portrait/import'):
        assert a.post(path,json={}).status_code==403,path
    assert a.put('/api/redaction-settings',json={}).status_code==403
    assert a.get('/api/redaction-service').status_code==403
    assert a.put('/api/redaction-service',json={'mode':'http','endpoint':'https://mask.example'}).status_code==403
    assert a.get('/api/production/model-options').status_code==200
    draft=a.post('/api/production/drafts',json={}).json()
    changed={**draft['mask'],'mask_scale':2.8}
    assert a.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'mask':changed}).status_code==403


def test_csrf_disable_reset_without_forced_password_change(accounts_clients):
    accounts,users,(admin,a,b)=accounts_clients
    assert a.post('/api/production/drafts',headers={'X-CSRF-Token':'wrong'},json={}).status_code==403
    assert a.post('/api/production/drafts',headers={'Origin':'https://evil.example'},json={}).status_code==403
    accounts.update_user(users[1]['id'],users[0]['id'],enabled=False)
    assert a.get('/api/production/drafts').status_code==401
    accounts.reset_password(users[2]['id'],'replacement-password',users[0]['id'])
    assert b.get('/api/production/drafts').status_code==401
    response=b.post('/api/auth/login',headers={'Origin':'http://testserver'},json={'username':'bob','password':'replacement-password'})
    assert response.status_code==200
    assert b.get('/api/production/drafts').status_code==200
    assert b.get('/api/auth/me').json()['user']['must_change_password'] is False
    b.headers['X-CSRF-Token']=response.json()['csrf_token']
    assert b.post('/api/production/drafts',json={}).status_code==200


def test_initial_login_needs_no_password_change_and_still_enforces_csrf(protected):
    from app.accounts import Accounts
    accounts=Accounts(main.settings.storage_dir)
    admin=accounts.init_admin('admin','123456')
    accounts.create_user('alice','654321',admin['id'])
    response=protected.post('/api/auth/login',headers={'Origin':'http://testserver'},json={'username':'alice','password':'654321'})
    assert response.status_code==200,response.text
    assert response.json()['user']['must_change_password'] is False
    assert protected.get('/api/production/drafts').status_code==200
    assert protected.get('/',follow_redirects=False).status_code==200
    assert protected.post('/api/production/drafts',json={}).status_code==403
    protected.headers['X-CSRF-Token']=response.json()['csrf_token']
    assert protected.post('/api/production/drafts',json={}).status_code==200


def test_middleware_ignores_legacy_flag_even_from_stale_session(accounts_clients,monkeypatch):
    from app.accounts import Accounts
    _,_,(_,a,_)=accounts_clients
    original=Accounts.authenticate
    def legacy_session(self,token):
        session=original(self,token)
        session['user']['must_change_password']=True
        return session
    # Exercise middleware independently of the migration/public serializer.
    monkeypatch.setattr(Accounts,'authenticate',legacy_session)
    assert a.get('/api/production/drafts').status_code==200
    assert a.get('/',follow_redirects=False).status_code==200
    assert a.post('/api/production/drafts',json={}).status_code==200


def test_role_change_requires_new_login_and_enforces_new_permissions(accounts_clients):
    accounts,users,(admin,a,_)=accounts_clients
    path='/api/admin/users/'+users[1]['id']
    assert admin.patch(path,json={'role':'admin'}).status_code==200
    assert a.get('/api/auth/me').status_code==401
    response=a.post('/api/auth/login',headers={'Origin':'http://testserver'},json={'username':'alice','password':'changed-user-password'})
    assert response.status_code==200
    a.headers['X-CSRF-Token']=response.json()['csrf_token']
    assert a.get('/api/admin/users').status_code==200
    assert a.get('/api/jobs').status_code==403  # Promotion never grants legacy data ownership.
    assert admin.patch(path,json={'role':'user'}).status_code==200
    assert a.get('/api/admin/users').status_code==401
    response=a.post('/api/auth/login',headers={'Origin':'http://testserver'},json={'username':'alice','password':'changed-user-password'})
    assert response.status_code==200
    assert a.get('/api/admin/users').status_code==403


def test_admin_task_overview_requires_explicit_admin_and_scopes_filter(accounts_clients):
    from tests.test_production_api import complete_draft
    _,users,(admin,a,b)=accounts_clients
    draft=complete_draft(a)
    a.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'test'})
    assert a.get('/api/admin/tasks').status_code==403
    result=admin.get('/api/admin/tasks',params={'user_id':users[1]['id']})
    assert result.status_code==200,result.text
    data=result.json()
    assert data['stats']['submitted']==1
    assert len(data['items'])==1 and data['items'][0]['user_id']==users[1]['id']
    assert admin.get('/api/admin/tasks',params={'user_id':users[2]['id']}).json()['stats']['submitted']==0


def test_parallel_requests_keep_their_authenticated_tenant(accounts_clients):
    from concurrent.futures import ThreadPoolExecutor
    _,_,(_,a,b)=accounts_clients
    def create(item):
        client,label=item
        response=client.post('/api/production/drafts',json={'name':label})
        assert response.status_code==200,response.text
    pairs=[(a,'alice-'+str(i)) if i%2 else (b,'bob-'+str(i)) for i in range(20)]
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(create,pairs))
    assert len(a.get('/api/production/drafts').json()['items'])==10
    assert all(x['name'].startswith('alice-') for x in a.get('/api/production/drafts').json()['items'])
    assert all(x['name'].startswith('bob-') for x in b.get('/api/production/drafts').json()['items'])


def test_person_thumbnail_and_callback_capabilities_are_tenant_scoped(accounts_clients):
    import json
    import time
    from app import portrait_service,tenancy
    from app.portrait_library import PortraitLibrary
    from app.portrait_sessions import Sessions
    _,users,(_,a,b)=accounts_clients
    portrait_service.save_config(main.settings,{'access_key':'test-ak','secret_key':'test-sk'})
    alice=tenancy.user_settings(main.settings,users[1])
    # Virtual people are tenant-private; real people may be shared by policy.
    library=PortraitLibrary(alice)
    person=library.add_person('group-alice','Alice person',person_type='AIGC')
    from io import BytesIO
    from PIL import Image
    picture=BytesIO()
    Image.new('RGB',(400,500),'blue').save(picture,format='JPEG')
    uploaded=a.post('/api/production/assets',data={'kind':'face'},files={'file':('alice.jpg',picture.getvalue(),'image/jpeg')})
    assert uploaded.status_code==200,uploaded.text
    photo=library.enqueue(person['id'],uploaded.json()['id'])
    library.update(photo['id'],status='active',remote_id='asset-alice',checked=time.time())
    library.thumbnail(library.get_photo(photo['id'],private=True))
    assert len(a.get('/api/portrait/people').json()['items'])==1
    assert b.get('/api/portrait/people').json()['items']==[]
    thumbnail=a.get('/api/portrait/people/'+person['id']+'/thumbnail')
    assert thumbnail.status_code==200 and thumbnail.headers['content-type'].startswith('image/')
    Image.open(BytesIO(thumbnail.content)).verify()
    for suffix in ('thumbnail','photos','reference'):
        assert b.get('/api/portrait/people/'+person['id']+'/'+suffix).status_code==404
    assert b.delete('/api/portrait/people/'+person['id']).status_code==404
    sessions=Sessions(alice)
    account=portrait_service.fingerprint(portrait_service.load_config(alice))
    data={'status':'pending','expires_at':time.time()+600,'token':'private-provider-token','url':'https://example.com/official-auth','callback_seen':False}
    with sessions.db() as db:
        db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',('alice-session','alice-request',account,'f'*64,json.dumps(data)))
    assert b.get('/api/portrait/sessions/alice-session').status_code==404
    anonymous=TestClient(main.app)
    assert anonymous.get('/auth/portrait/scan/'+'f'*64,follow_redirects=False).status_code==302
    assert anonymous.get('/auth/portrait/return/'+'f'*64,params={'bytedToken':'wrong','resultCode':'10000'}).status_code==400
    assert not sessions.get('alice-session')['callback_seen']
