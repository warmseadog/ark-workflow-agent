import pytest
from app import main, variation_settings
from app.production_store import ProductionStore
from tests.test_production_api import client, complete_draft
from tests.test_access_control import protected, accounts_clients


def body(draft, key='camera', inspiration='先拍袖口，再拉远'):
    return {'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':key,'variation':{'inspiration':inspiration}}


def test_inspiration_freezes_settings_and_does_not_edit_draft(client):
    variation_settings.save_config(main.settings,{'api_key':'private-camera-key'})
    draft=complete_draft(client)
    # Mock mode remains available only for local development, not a claim of AI output.
    first=client.post('/api/production/runs',json=body(draft))
    assert first.status_code==200,first.text
    run=ProductionStore(main.settings.storage_dir).get_run(first.json()['id'],private=True)
    assert run['private']['variation']['inspiration']=='先拍袖口，再拉远'
    assert run['private']['variation']['config']['api_key']=='private-camera-key'
    assert run['variation']['recipe']
    assert 'private-camera-key' not in first.text
    assert client.get('/api/production/drafts/'+draft['id']).json()['prompt']=='original'
    assert client.post('/api/production/runs',json=body(draft)).json()['id']==run['id']
    assert client.post('/api/production/runs',json=body(draft,inspiration='侧拍')).status_code==409
    plain=body(draft);plain.pop('variation')
    assert client.post('/api/production/runs',json=plain).status_code==409
    second=client.post('/api/production/runs',json=body(draft,'camera-2')).json()
    assert second['variation']['recipe']!=run['variation']['recipe']


def test_missing_planner_config_rejected_before_paid_preparation(client,monkeypatch):
    draft=complete_draft(client)
    monkeypatch.setattr('app.portrait_generation.prepare',lambda *a:(_ for _ in ()).throw(AssertionError('paid call')))
    assert client.post('/api/production/runs',json=body(draft)).status_code==422
    assert ProductionStore(main.settings.storage_dir).list_runs()==[]


def test_normal_user_can_submit_variation_but_not_configure(accounts_clients):
    from app import tenancy
    _,users,(admin,alice,bob)=accounts_clients
    assert admin.put('/api/variation-settings',json={'api_key':'admin-private-key','model':'custom-llm'}).status_code==200
    for c in (alice,bob):
        assert c.get('/api/variation-settings').status_code==403
        assert c.put('/api/variation-settings',json={'enabled':False}).status_code==403
    draft=complete_draft(alice)
    response=alice.post('/api/production/runs',json=body(draft))
    assert response.status_code==200,response.text
    saved=ProductionStore(tenancy.user_settings(main.settings,users[1]).storage_dir).get_run(response.json()['id'],private=True)
    assert saved['private']['variation']['inspiration']=='先拍袖口，再拉远'
    assert 'variation' not in response.json()
    assert 'admin-private-key' not in response.text
    for suffix in ('', '/resume', '/person-preparation/retry'):
        route='/api/production/runs/'+saved['id']+suffix
        assert (bob.post(route) if suffix else bob.get(route)).status_code==404
    assert 'admin-private-key' not in admin.get('/api/variation-settings').text
    assert 'id="variation-toggle"' not in admin.get('/').text
    html=alice.get('/').text
    assert 'id="variation-toggle"' not in html and 'id="variation-editor"' in html
    assert '配置拍法模型' not in html


def test_operational_admin_can_configure_model(accounts_clients):
    from app.accounts import Accounts
    from fastapi.testclient import TestClient
    accounts,users,clients=accounts_clients
    # Promote an existing ordinary account to the operational administrator role.
    accounts.update_user(users[1]['id'],users[0]['id'],role='admin')
    c=clients[1]
    login=c.post('/api/auth/login',headers={'Origin':'http://testserver'},json={'username':'alice','password':'changed-user-password'})
    assert login.status_code==200,login.text
    c.headers['X-CSRF-Token']=login.json()['csrf_token']
    assert c.put('/api/variation-settings',json={'model':'admin-selected-model'}).status_code==200
    assert 'id="variation-form"' in c.get('/admin/settings').text


def test_config_endpoint_roundtrip(client):
    res=client.put('/api/variation-settings',json={'api_key':'camera-key','template':'默认摄影方向','model':'my-vision-llm'})
    assert res.status_code==200,res.text
    assert res.json()['config']['model']=='my-vision-llm'
    assert 'camera-key' not in res.text
    assert client.put('/api/variation-settings',json={'template':''}).status_code==422


def test_pro_settings_wait_for_user_key(client):
    client.put('/api/variation-settings',json={'api_key':'previous-key'})
    values={'model':'doubao-seed-2-1-pro-260915','thinking_enabled':True,
        'reasoning_effort':'high','max_completion_tokens':32768,'timeout_seconds':300,'clear_api_key':True}
    response=client.put('/api/variation-settings',json=values)
    assert response.status_code==200,response.text
    config=client.get('/api/variation-settings').json()['config']
    assert not config['has_api_key'] and config['problem']
    assert all(config[k]==v for k,v in values.items() if k!='clear_api_key')
    for invalid in ({'thinking_enabled':'true'},{'reasoning_effort':'invalid'},{'reasoning_effort':[]},
                    {'max_completion_tokens':True},{'max_completion_tokens':0},{'timeout_seconds':601}):
        assert client.put('/api/variation-settings',json=invalid).status_code==422


def test_ordinary_owner_can_resume_variation_without_exposing_plan(accounts_clients):
    import json
    from app import tenancy
    accounts,users,(_,alice,_)=accounts_clients
    draft=complete_draft(alice)
    run=alice.post('/api/production/runs',json={k:v for k,v in body(draft).items() if k!='variation'}).json()
    store=ProductionStore(tenancy.user_settings(main.settings,users[1]).storage_dir)
    with store.connection() as db:
        db.execute('INSERT INTO production_variations VALUES (?,?,?)',(run['id'],'group',json.dumps({'recipe':'侧拍'})))
    # An invalid preparation retry still fails validation, not the admin-only gate.
    assert alice.post('/api/production/runs/'+run['id']+'/person-preparation/retry').status_code==409
    store.update_run(run['id'], status='needs_attention', stage='variation_planning')
    detail=alice.get('/api/production/runs/'+run['id']).json()
    assert 'variation' not in detail
    assert detail['can_resume']
    page=alice.get('/api/production/runs').json()
    item=next(item for item in page['items'] if item['id']==run['id'])
    assert 'variation' not in item
    assert item['can_resume']
    assert alice.post('/api/production/runs/'+run['id']+'/resume').status_code==200
    with store.connection() as db:
        db.execute('INSERT INTO production_person_preparations VALUES (?,?,?)',
            (run['id'],json.dumps({'retryable':True,'state':'failed'}),0))
    store.update_run(run['id'],status='failed',stage='person_preparing')
    assert alice.get('/api/production/runs/'+run['id']).json()['can_retry_preparation']
    assert alice.post('/api/production/runs/'+run['id']+'/person-preparation/retry').status_code==200


@pytest.mark.parametrize('inspiration',['','  ','\n\t　'])
def test_blank_inspiration_is_plain_even_without_planner_config(client,monkeypatch,inspiration):
    draft=complete_draft(client)
    def forbidden(*a,**kw):raise AssertionError('blank request must not enter variation preflight')
    monkeypatch.setattr('app.variation.preflight',forbidden)
    response=client.post('/api/production/runs',json=body(draft,inspiration=inspiration))
    assert response.status_code==200,response.text
    store=ProductionStore(main.settings.storage_dir)
    saved=store.get_run(response.json()['id'],private=True)
    assert 'variation' not in saved['private'] and not store.get_variation(saved['id'])
    plain=body(draft);plain.pop('variation')
    assert client.post('/api/production/runs',json=plain).json()['id']==saved['id']
    assert client.post('/api/production/runs',json=body(draft,inspiration='侧拍')).status_code==409


def test_legacy_blank_variation_idempotency_keeps_original_task(client):
    from app.variation import preflight
    variation_settings.save_config(main.settings,{'api_key':'legacy-private-key'})
    draft=complete_draft(client);store=ProductionStore(main.settings.storage_dir)
    old=store.create_run(draft['id'],draft['revision'],'legacy-blank',
        {'variation':preflight(main.settings,store,draft,{'inspiration':''})})
    response=client.post('/api/production/runs',json=body(draft,'legacy-blank',''))
    assert response.status_code==200,response.text
    assert response.json()['id']==old['id']
    assert store.get_run(old['id'],private=True)['private']['variation']['config']['api_key']=='legacy-private-key'


@pytest.mark.parametrize('inspiration',['', '侧拍'])
def test_only_nonblank_variation_conflicts_with_continuation(client,monkeypatch,inspiration):
    from tests.test_continuation_api import extension_draft
    from app import continuation_settings
    draft=extension_draft(client,monkeypatch)
    continuation_settings.save_config(main.settings,{'api_key':'continuation-key'})
    variation_settings.save_config(main.settings,{'api_key':'variation-key'})
    response=client.post('/api/production/runs',json=body(draft,inspiration=inspiration))
    if inspiration:
        assert response.status_code==422 and '换个拍法暂不同时续写' in response.text
    else:
        assert response.status_code==200,response.text
        private=ProductionStore(main.settings.storage_dir).get_run(response.json()['id'],private=True)['private']
        assert 'continuation' in private and 'variation' not in private
