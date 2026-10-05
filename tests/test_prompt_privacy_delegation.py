from dataclasses import replace
import pytest
from fastapi.testclient import TestClient
from app import main, tenancy, production_worker
from app.accounts import Accounts
from app.production_store import ProductionStore


def test_local_preview_projection_does_not_require_an_account_database(tmp_path, monkeypatch):
    from app.prompt_visibility import project
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    settings = replace(main.settings, storage_dir=tmp_path, config_root=None, user_id='')
    scoped = tenancy.user_settings(settings, {'id':'1' * 32})
    value = {'defaced_url':'/api/previews/'+'2' * 32+'/file'}
    assert project(value, scoped) == value
    assert not (tmp_path/'private/accounts.db').exists()


@pytest.fixture
def users(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'true')
    monkeypatch.setenv('APP_COOKIE_SECURE', 'false')
    monkeypatch.delenv('APP_PUBLIC_ORIGIN', raising=False)
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path, config_root=None, user_id='', seedance_mode='mock'))
    monkeypatch.setattr(production_worker, 'wake', lambda *_: None)
    accounts = Accounts(tmp_path)
    owner = accounts.init_admin('owner', 'test-password')
    alice = accounts.create_user('alice', 'test-password', owner['id'])
    operator = accounts.create_user('operator', 'test-password', owner['id'], role='admin')
    clients = []
    for user in (owner, alice, operator):
        client = TestClient(main.app)
        response = client.post('/api/auth/login', headers={'Origin':'http://testserver'},
                               json={'username':user['username'], 'password':'test-password'})
        assert response.status_code == 200, response.text
        client.headers['X-CSRF-Token'] = response.json()['csrf_token']
        clients.append(client)
    return accounts, (owner, alice, operator), clients


def test_ordinary_prompt_endpoints_and_rendering_are_private(users):
    _, _, (owner, alice, _) = users
    assert owner.get('/api/prompt-templates').status_code == 200
    assert alice.get('/api/prompt-templates').status_code == 403
    assert alice.post('/api/prompt-templates', json={'name':'mine','content':'changed'}).status_code == 403
    assert alice.post('/api/production/prompt-preview', json={}).status_code == 403
    page = alice.get('/').text
    assert 'id="generation-prompt"' not in page
    assert 'id="final-generation-prompt"' not in page
    protected = '/api/admin/prompt-templates/editor-rules.js'
    assert protected not in page
    assert alice.get(protected).status_code == 403
    assert TestClient(main.app).get(protected).status_code == 401
    assert owner.get(protected).status_code == 200
    assert '锁定脸型' in owner.get(protected).text
    assert '锁定脸型' not in alice.get('/static/production.js').text
    assert '参考包型、颜色、材质及背带' not in alice.get('/static/production.js').text


def test_ordinary_draft_run_and_copy_hide_but_preserve_prompts(users):
    _, (_, target, _), (_, alice, _) = users
    store = ProductionStore(tenancy.user_settings(main.settings, target).storage_dir)
    draft = store.create_draft({'prompt':'SYSTEM-SECRET', 'prompt_template_id':'secret-template',
                                'prompt_rule_version':'exclusive-v2'})
    run = store.create_run(draft['id'], draft['revision'], 'hidden-run', {})
    store.update_continuation(run['id'], plan={'continuation_prompt':'CONTINUATION-SECRET'})
    for endpoint in ('/drafts', '/drafts/'+draft['id'], '/runs', '/runs/'+run['id']):
        response = alice.get('/api/production'+endpoint)
        assert response.status_code == 200, response.text
        assert 'SYSTEM-SECRET' not in response.text and 'CONTINUATION-SECRET' not in response.text
    copied = alice.post('/api/production/runs/'+run['id']+'/copy').json()
    assert 'prompt' not in copied
    assert store.get_draft(copied['id'])['prompt'] == 'SYSTEM-SECRET'
    response = alice.put('/api/production/drafts/'+draft['id'], json={'revision':draft['revision'],'prompt':'tampered'})
    assert response.status_code == 403
    assert store.get_draft(draft['id'])['prompt'] == 'SYSTEM-SECRET'


def test_delegated_restore_preserves_owner_version_and_is_idempotent(users):
    _, (_, target, actor), (_, alice, operator) = users
    target_store = ProductionStore(tenancy.user_settings(main.settings, target).storage_dir)
    draft = target_store.create_draft({'prompt':'frozen original', 'prompt_rule_version':'legacy-v1','name':'Original'})
    run = target_store.create_run(draft['id'], draft['revision'], 'source', {})
    endpoint = '/api/admin/task-records/'+target['id']+'/'+run['id']+'/restore-draft'
    assert alice.post(endpoint,json={'idempotency_key':'restore'}).status_code == 403
    first = operator.post(endpoint,json={'idempotency_key':'restore'})
    assert first.status_code == 200, first.text
    restored = first.json()
    assert restored['id'] != draft['id']
    assert restored['delegated_user']['id'] == target['id']
    assert restored['prompt'] == 'frozen original' and restored['prompt_rule_version'] == 'legacy-v1'
    assert operator.post(endpoint,json={'idempotency_key':'restore'}).json()['id'] == restored['id']
    assert target_store.get_draft(draft['id'])['name'] == 'Original'
    assert target_store.get_draft(restored['id'])['delegation']['actor_id'] == actor['id']
    prefix = '/api/admin/delegated/'+target['id']+'/production'
    assert operator.get(prefix+'/drafts/'+restored['id']).json()['prompt'] == 'frozen original'
    assert alice.get(prefix+'/drafts/'+restored['id']).status_code == 403
    changed = operator.put(prefix+'/drafts/'+restored['id'], json={'revision':restored['revision'],'name':'Rework'})
    assert changed.status_code == 200, changed.text
    assert target_store.get_draft(restored['id'])['name'] == 'Rework'


def test_delegated_submission_uses_owner_quota_fresh_run_and_actor_audit(users):
    from tests.test_production_api import complete_draft
    accounts, (_, target, actor), (_, alice, operator) = users
    original = complete_draft(alice)
    store = ProductionStore(tenancy.user_settings(main.settings,target).storage_dir)
    source = store.create_run(original['id'], original['revision'], 'first-attempt', {})
    store.update_run(source['id'], status='succeeded', provider_task_id='old-provider-id')
    restored = operator.post('/api/admin/task-records/'+target['id']+'/'+source['id']+'/restore-draft',
                             json={'idempotency_key':'second-attempt'}).json()
    response = operator.post('/api/admin/delegated/'+target['id']+'/production/runs',
                              json={'draft_id':restored['id'],'revision':restored['revision'],'idempotency_key':'new-run'})
    assert response.status_code == 200, response.text
    new = store.get_run(response.json()['id'], private=True)
    assert new['id'] != source['id'] and new['provider_task_id'] is None
    assert new['private']['delegation']['actor_id'] == actor['id']
    assert new['private']['delegation']['owner_id'] == target['id']
    assert new['snapshot']['prompt'] == store.get_draft(original['id'])['prompt']
    accounts.update_user(target['id'], users[1][0]['id'], max_queued=0)
    blocked = operator.post('/api/admin/delegated/'+target['id']+'/production/runs',
        json={'draft_id':restored['id'],'revision':restored['revision'],'idempotency_key':'blocked'})
    assert blocked.status_code in (409,429)


def test_admin_cannot_delegate_super_or_peer_and_user_cannot_forge_provenance(users):
    _, (owner_user, _, operator_user), (owner, alice, operator) = users
    for user in (owner_user, operator_user):
        if user['id'] == operator_user['id']:
            continue
        response = operator.get('/api/admin/delegated/'+user['id']+'/production/drafts')
        assert response.status_code == 403
    draft = alice.post('/api/production/drafts', json={}).json()
    assert alice.put('/api/production/drafts/'+draft['id'], json={'revision':draft['revision'],
        'delegation':{'actor_id':owner_user['id']}}).status_code == 422
