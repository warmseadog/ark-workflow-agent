from dataclasses import replace
import time
import pytest
from fastapi.testclient import TestClient
from app import main, portrait_service

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path))
    monkeypatch.setenv('PORTRAIT_PUBLIC_BASE_URL', 'https://118.196.7.195:8443')
    monkeypatch.setattr('app.production_worker.wake', lambda _: None)
    portrait_service.save_config(main.settings, {'access_key':'test-ak','secret_key':'test-sk'})
    state={'creates':0, 'checks':0, 'group':None}
    def create(self, callback):
        state['creates']+=1; state['callback']=callback
        return {'token':'private-token','url':'https://ark.volcengine.com/verify?secret=private'}
    def result(self, token):
        state['checks']+=1; assert token=='private-token'; return state['group']
    monkeypatch.setattr(portrait_service.ArkPortraitClient,'create_session',create,raising=False)
    monkeypatch.setattr(portrait_service.ArkPortraitClient,'validation_result',result,raising=False)
    with TestClient(main.app) as client: yield client,state

def start(client, key='a'*32):
    response=client.post('/api/portrait/sessions',json={'request_id':key})
    assert response.status_code==200,response.text
    return response.json()

def test_creation_is_idempotent_and_keeps_tokens_private(setup):
    client,state=setup; one=start(client); two=start(client)
    assert one['id']==two['id'] and state['creates']==1
    assert one['status']=='pending'
    assert 'private-token' not in str(one) and 'private' not in str(one)
    qr=client.get(one['qr_url']); assert qr.status_code==200 and '<svg' in qr.text
    assert qr.headers['cache-control']=='no-store'
    assert client.get('/api/portrait/config').json()['mode']=='automatic'

def test_callback_cannot_claim_success_without_official_verification(setup):
    client,state=setup; one=start(client); url='/api/portrait/sessions/'+one['id']
    assert client.get(url).json()['status']=='pending' and state['checks']==0
    callback=state['callback'].replace('https://118.196.7.195:8443','')
    assert client.get(callback+'?bytedToken=wrong&resultCode=10000').status_code==400
    assert client.get(url).json()['status']=='pending' and state['checks']==0
    response=client.get(callback+'?bytedToken=private-token&resultCode=10000',follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/auth/portrait/done'
    assert client.get(url).json()['status']=='pending' and state['checks']==1
    state['group']='group-authorized'
    from app import portrait_sessions
    monkey_time=time.time()+10
    old=portrait_sessions.time.time; portrait_sessions.time.time=lambda:monkey_time
    try: result=client.get(url).json()
    finally: portrait_sessions.time.time=old
    assert result['status']=='verified' and result['group_id']=='group-authorized'
    assert 'private-token' not in str(result)

def test_expiration_and_account_change_fail_closed(setup,monkeypatch):
    client,state=setup; one=start(client)
    from app import portrait_sessions
    future=time.time()+1900; monkeypatch.setattr(portrait_sessions.time,'time',lambda:future)
    assert client.get('/api/portrait/sessions/'+one['id']).json()['status']=='expired'
    assert client.get(one['qr_url']).status_code==410

def test_changed_credentials_invalidate_existing_session(setup):
    client,state=setup; one=start(client)
    portrait_service.save_config(main.settings,{'access_key':'new-ak','secret_key':'new-sk'})
    response=client.get('/api/portrait/sessions/'+one['id'])
    assert response.status_code==409

def test_unknown_callback_scan_and_missing_public_config(setup,monkeypatch):
    client,state=setup
    assert client.get('/auth/portrait/return/'+'0'*64+'?bytedToken=anything&resultCode=10000').status_code==400
    assert client.get('/auth/portrait/scan/'+'0'*64).status_code==404
    monkeypatch.delenv('PORTRAIT_PUBLIC_BASE_URL')
    assert client.post('/api/portrait/sessions',json={'request_id':'b'*32}).status_code==409
    assert state['creates']==0

def test_official_client_payload_and_response(monkeypatch):
    api=portrait_service.ArkPortraitClient(portrait_service.PortraitConfig(access_key='a',secret_key='b'))
    calls=[]
    def request(action,payload):
        calls.append((action,payload))
        return {'H5Link':'https://ark.volcengine.com/verify?x=1','BytedToken':'secret'} if action=='CreateVisualValidateSession' else {'GroupId':'group-ok'}
    monkeypatch.setattr(api,'_request',request)
    assert api.create_session('https://example.com/auth/return/state')['token']=='secret'
    assert api.validation_result('secret')=='group-ok'
    assert calls[0]==('CreateVisualValidateSession',{'CallbackURL':'https://example.com/auth/return/state','ProjectName':'default'})

def test_provider_h5_rejects_nonofficial_redirect(monkeypatch):
    api=portrait_service.ArkPortraitClient(portrait_service.PortraitConfig(access_key='a',secret_key='b'))
    monkeypatch.setattr(api,'_request',lambda *args:{'H5Link':'https://attacker.example/','BytedToken':'secret'})
    with pytest.raises(ValueError): api.create_session('https://example.com/callback')


def test_explicit_regenerate_can_release_pending_slot(setup):
    client,state=setup
    for i in range(12):
        one=start(client,format(i,'032x'))
        assert client.delete('/api/portrait/sessions/'+one['id']).status_code==200
        assert client.get(one['qr_url']).status_code==410
    assert state['creates']==12

def test_unicode_callback_does_not_crash_or_mark_verified(setup):
    client,state=setup; one=start(client)
    callback=state['callback'].replace('https://118.196.7.195:8443','')
    assert client.get(callback,params={'bytedToken':'伪造凭据','resultCode':'10000'}).status_code==400
    assert client.get('/api/portrait/sessions/'+one['id']).json()['status']=='pending'
