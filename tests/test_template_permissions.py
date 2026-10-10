"""Shared templates follow the current role, personal templates follow the owner."""
import pytest

from app import main, local_preferences
from tests.test_access_control import protected, accounts_clients


def login(client, username, password):
    response = client.post('/api/auth/login', headers={'Origin': 'http://testserver'},
                           json={'username': username, 'password': password})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf_token']


@pytest.mark.parametrize('method', ['PUT', 'DELETE'])
@pytest.mark.parametrize('prefix', ['', 'system:'])
def test_demoted_legacy_owner_cannot_write_existing_shared_template(accounts_clients, method, prefix):
    accounts, users, (legacy, alice, bob) = accounts_clients
    original = local_preferences.save_template(main.settings, 'Existing shared', 'original')
    accounts.update_user(users[1]['id'], users[0]['id'], role='super_admin')
    accounts.update_user(users[0]['id'], users[1]['id'], role='user')
    assert legacy.get('/api/auth/me').status_code == 401
    login(legacy, 'admin', 'changed-admin-password')
    response = legacy.request(method, '/api/prompt-templates/'+prefix+original['id'],
                              json={'name': 'Changed', 'content': 'tampered'})
    assert response.status_code == 403, response.text
    assert bob.get('/api/prompt-templates').status_code == 403
    assert next(x for x in local_preferences.list_templates(main.settings) if x['id']==original['id'])['content']=='original'


def test_legacy_owner_personal_templates_remain_private_across_role_changes(accounts_clients):
    accounts, users, (legacy, alice, bob) = accounts_clients
    created = legacy.post('/api/prompt-templates', json={'name': 'Private', 'content': 'only me'})
    assert created.status_code == 200
    ident = created.json()['id']
    assert bob.get('/api/prompt-templates').status_code == 403
    accounts.update_user(users[1]['id'], users[0]['id'], role='super_admin')
    accounts.update_user(users[0]['id'], users[1]['id'], role='user')
    login(legacy, 'admin', 'changed-admin-password')
    assert legacy.put('/api/prompt-templates/'+ident, json={'name': 'Private', 'content': 'updated'}).status_code == 403
    copy = legacy.post('/api/prompt-templates', json={'name': 'Copy', 'content': 'still private'})
    assert copy.status_code == 403
    assert bob.get('/api/prompt-templates').status_code == 403
    assert legacy.delete('/api/prompt-templates/'+ident).status_code == 403
    accounts.update_user(users[0]['id'], users[1]['id'], role='admin')
    login(legacy, 'admin', 'changed-admin-password')
    assert any(x['id']==ident and x['content']=='only me' for x in legacy.get('/api/prompt-templates').json()['items'])
    assert accounts.get_user(users[0]['id'])['legacy_owner'] is True


def test_any_current_admin_can_manage_shared_templates_without_exposing_personal_templates(accounts_clients):
    accounts, users, (legacy, alice, bob) = accounts_clients
    endpoint = '/api/admin/prompt-templates'
    assert bob.get(endpoint).status_code == 403
    accounts.update_user(users[1]['id'], users[0]['id'], role='admin')
    login(alice, 'alice', 'changed-user-password')
    personal = alice.post('/api/prompt-templates', json={'name': 'Private', 'content': 'alice only'}).json()
    created = alice.post(endpoint, json={'name': 'Shared', 'content': 'everyone'})
    assert created.status_code == 200, created.text
    ident = created.json()['id']
    assert all(x['id'] != personal['id'] for x in alice.get(endpoint).json()['items'])
    for client in (legacy, alice):
        item = next(x for x in client.get('/api/prompt-templates').json()['items'] if x['id']=='system:'+ident)
        assert item['read_only'] and item['scope']=='shared'
    assert alice.put(endpoint+'/'+ident, json={'name': 'Updated', 'content': 'new shared'}).status_code == 200
    for method in ('POST', 'PUT', 'DELETE'):
        url = endpoint if method == 'POST' else endpoint+'/'+ident
        assert bob.request(method, url, json={'name': 'Bad', 'content': 'bad'}).status_code == 403
    accounts.update_user(users[1]['id'], users[0]['id'], role='user')
    assert alice.delete(endpoint+'/'+ident).status_code == 401
    login(alice, 'alice', 'changed-user-password')
    assert alice.delete(endpoint+'/'+ident).status_code == 403
    assert legacy.delete(endpoint+'/'+ident).status_code == 200


def test_shared_template_service_rechecks_role_and_enabled_state(accounts_clients):
    from app.tenancy import user_settings
    accounts, users, _ = accounts_clients
    caller = user_settings(main.settings, users[0])
    accounts.update_user(users[1]['id'], users[0]['id'], role='super_admin')
    accounts.update_user(users[0]['id'], users[1]['id'], role='user')
    with pytest.raises(PermissionError):
        local_preferences.save_shared_template(caller, 'Denied', 'denied')
    accounts.update_user(users[0]['id'], users[1]['id'], role='admin', enabled=False)
    with pytest.raises(PermissionError):
        local_preferences.save_shared_template(caller, 'Denied', 'denied')
