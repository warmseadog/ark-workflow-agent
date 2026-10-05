from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from jinja2 import DictLoader, Environment
from starlette.templating import Jinja2Templates


PASSWORD = 'Initial-password-42!'
NEW_PASSWORD = 'Changed-password-43!'


@pytest.fixture
def auth_app(tmp_path, monkeypatch):
    from app.accounts import AccountError, Accounts
    from app.authentication import get_router
    monkeypatch.setenv('APP_AUTH_ENABLED', 'true')
    monkeypatch.setenv('APP_COOKIE_SECURE', 'false')
    monkeypatch.delenv('APP_PUBLIC_ORIGIN', raising=False)
    accounts = Accounts(tmp_path)
    admin = accounts.init_admin('admin', PASSWORD)
    templates = Jinja2Templates(env=Environment(loader=DictLoader({
        'login.html': '<h1>Login</h1>', 'password.html': '<h1>Password</h1>',
    })))
    app = FastAPI()

    @app.middleware('http')
    async def session_state(request: Request, call_next):
        # Reproduce the state contract, deliberately without permission gates:
        # handlers themselves must reject non-admin requests.
        request.state.user = None
        request.state.session = None
        if token := request.cookies.get('ark_session'):
            try:
                request.state.session = accounts.authenticate(token)
                request.state.user = request.state.session['user']
            except AccountError:
                pass
        return await call_next(request)

    app.include_router(get_router(lambda: SimpleNamespace(storage_dir=tmp_path), templates))
    with TestClient(app) as client:
        yield client, accounts, admin


def login(client, username='admin', password=PASSWORD, **kwargs):
    return client.post('/api/auth/login', json={'username': username, 'password': password},
                       headers={'Origin': 'http://testserver'}, **kwargs)


def test_three_role_listing_and_delete_restore_http(auth_app):
    client, accounts, owner = auth_app
    manager = accounts.create_user('manager', PASSWORD, owner['id'], role='admin')
    user = accounts.create_user('person', PASSWORD, owner['id'])
    login(client, 'manager')
    assert {u['id'] for u in client.get('/api/admin/users').json()['items']} == {user['id']}
    assert client.delete('/api/admin/users/' + owner['id']).status_code == 403
    assert client.delete('/api/admin/users/' + user['id']).status_code == 200
    assert client.post('/api/admin/users/' + user['id'] + '/restore').status_code == 403
    login(client)
    response = client.post('/api/admin/users/' + user['id'] + '/restore')
    assert response.status_code == 200 and response.json()['user']['enabled']


def test_login_me_cookie_and_html_contract(auth_app):
    client, accounts, admin = auth_app
    assert client.get('/login').text == '<h1>Login</h1>'
    assert client.get('/account/password').text == '<h1>Password</h1>'
    assert client.get('/api/auth/me').status_code == 401
    response = login(client)
    assert response.status_code == 200
    assert set(response.json()) == {'user', 'csrf_token'}
    assert response.json()['user'] == admin
    cookie = response.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=lax' in cookie and 'max-age=43200' in cookie
    assert 'path=/' in cookie and 'secure' not in cookie
    assert client.cookies['ark_session'] not in response.text
    assert client.get('/api/auth/me').json() == {
        'user': admin, 'csrf_token': response.json()['csrf_token'], 'auth_enabled': True,
    }


@pytest.mark.parametrize('origin', [None, 'null', 'https://evil.example',
                                      'http://testserver.evil.example', 'http://testserver/path',
                                      'http://@testserver', 'http://testserver?', 'http://testserver#'])
def test_login_requires_strict_same_origin(auth_app, origin):
    client, _, _ = auth_app
    headers = {'Origin': origin} if origin is not None else {}
    response = client.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD}, headers=headers)
    assert response.status_code == 403
    assert any('\u4e00' <= char <= '\u9fff' for char in response.json()['detail'])
    assert 'ark_session' not in client.cookies


def test_public_origin_and_secure_cookie_default(auth_app, monkeypatch):
    client, _, _ = auth_app
    monkeypatch.setenv('APP_PUBLIC_ORIGIN', 'https://studio.example')
    monkeypatch.delenv('APP_COOKIE_SECURE')
    assert login(client).status_code == 403
    response = client.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD},
                           headers={'Origin': 'https://studio.example'})
    assert response.status_code == 200 and '; Secure' in response.headers['set-cookie']


def test_password_change_and_logout_clear_cookie_and_revoke(auth_app):
    client, accounts, _ = auth_app
    login(client)
    original = client.cookies['ark_session']
    response = client.post('/api/auth/password', json={'current_password': PASSWORD, 'new_password': NEW_PASSWORD})
    assert response.status_code == 200 and response.json() == {'needs_login': True}
    assert 'ark_session' not in client.cookies
    assert client.get('/api/auth/me').status_code == 401
    assert login(client, password=PASSWORD).status_code == 401
    assert login(client, password=NEW_PASSWORD).status_code == 200
    assert client.cookies['ark_session'] != original
    response = client.post('/api/auth/logout')
    assert response.status_code == 204 and not response.content
    assert 'ark_session' not in client.cookies
    assert client.post('/api/auth/logout').status_code == 204


def test_admin_endpoints_enforce_permissions_and_wrap_results(auth_app):
    client, accounts, admin = auth_app
    assert client.get('/api/admin/users').status_code == 401
    login(client)
    assert client.get('/api/admin/users').json() == {'items': [admin]}
    response = client.post('/api/admin/users', json={'username': 'alice', 'password': PASSWORD,
                                                    'max_concurrent': 2, 'max_queued': 3})
    assert response.status_code == 201
    user = response.json()['user']
    user_id = user['id']
    response = client.patch(f'/api/admin/users/{user_id}', json={'max_concurrent': 4, 'enabled': False})
    assert response.status_code == 200 and not response.json()['user']['enabled']
    assert client.patch(f'/api/admin/users/{admin["id"]}', json={'enabled': False}).status_code == 409
    assert client.patch(f'/api/admin/users/{user_id}', json={'enabled': True}).status_code == 200
    response = client.post(f'/api/admin/users/{user_id}/reset-password', json={'password': NEW_PASSWORD})
    assert response.status_code == 200 and not response.json()['user']['must_change_password']
    assert client.get('/api/admin/audit?limit=2').json()['items']
    assert login(client, 'alice', NEW_PASSWORD).status_code == 200
    for method, path, payload in [
        ('GET', '/api/admin/users', None), ('GET', '/api/admin/audit', None),
        ('POST', '/api/admin/users', {'username': 'bob', 'password': PASSWORD}),
        ('PATCH', f'/api/admin/users/{user_id}', {'enabled': False}),
        ('POST', f'/api/admin/users/{admin["id"]}/reset-password', {'password': PASSWORD}),
    ]:
        assert client.request(method, path, json=payload).status_code == 403


def test_disabled_auth_me_contract_and_closed_admin_endpoints(auth_app, monkeypatch):
    client, _, _ = auth_app
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    assert client.get('/api/auth/me').json() == {'user': None, 'csrf_token': '', 'auth_enabled': False}
    assert login(client).status_code == 403
    assert client.get('/api/admin/users').status_code == 403


def test_invalid_payload_does_not_echo_password_or_accept_invalid_role(auth_app):
    client, _, _ = auth_app
    login(client)
    response = client.post('/api/admin/users', json={'username': 'alice', 'password': PASSWORD, 'role': 'owner'})
    assert response.status_code == 422 and PASSWORD not in response.text
    response = client.post('/api/auth/password', json={'current_password': PASSWORD, 'new_password': 123})
    assert response.status_code == 422 and PASSWORD not in response.text
    assert client.patch('/api/admin/users/unknown', json={'enabled': 'false'}).status_code == 422


def test_login_rate_limit_ignores_spoofed_forwarded_ip(auth_app):
    client, _, _ = auth_app
    for attempt in range(5):
        response = client.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong'},
                               headers={'Origin': 'http://testserver', 'X-Forwarded-For': f'10.0.0.{attempt}'})
        assert response.status_code == 401
    assert login(client).status_code == 429


def test_login_replaces_and_revokes_previous_browser_session_without_redirect(auth_app):
    from app.accounts import AccountError
    client, accounts, _ = auth_app
    assert login(client).status_code == 200
    first_token = client.cookies['ark_session']
    response = client.post('/api/auth/login?next=https://evil.example',
                           json={'username': 'admin', 'password': PASSWORD},
                           headers={'Origin': 'http://testserver'})
    assert response.status_code == 200 and 'location' not in response.headers
    assert response.headers['cache-control'] == 'no-store'
    assert client.cookies['ark_session'] != first_token
    with pytest.raises(AccountError):
        accounts.authenticate(first_token)


def test_origin_port_zero_is_not_same_origin(auth_app):
    client, _, _ = auth_app
    response = client.post('/api/auth/login', json={'username': 'admin', 'password': PASSWORD},
                           headers={'Origin': 'http://testserver:0'})
    assert response.status_code == 403


def test_api_admin_creation_role_patch_and_six_digit_passwords(auth_app):
    client, accounts, admin = auth_app
    login(client)
    response = client.post('/api/admin/users', json={'username': 'second', 'password': '123456', 'role': 'admin'})
    assert response.status_code == 201, response.text
    user = response.json()['user']
    assert user['role'] == 'admin' and not user['legacy_owner']
    assert not user['must_change_password']
    path = f'/api/admin/users/{user["id"]}'
    # A quota-only PATCH must not silently reset an administrator's role.
    assert client.patch(path, json={'max_queued': 3}).json()['user']['role'] == 'admin'
    token = accounts.login('second', '123456', '127.0.0.1')['token']
    assert client.patch(path, json={'role': 'user'}).json()['user']['role'] == 'user'
    from app.accounts import AccountError
    with pytest.raises(AccountError) as caught:
        accounts.authenticate(token)
    assert caught.value.status_code == 401
    assert client.patch(f'/api/admin/users/{admin["id"]}', json={'role': 'user'}).status_code == 409
    response = client.post(path + '/reset-password', json={'password': '654321'})
    assert response.status_code == 200 and not response.json()['user']['must_change_password']
    assert login(client, 'second', '654321').status_code == 200
    assert client.patch(path, json={'role': 'admin'}).status_code == 403
    assert client.post('/api/admin/users', json={'username': 'third', 'password': '123456', 'role': 'admin'}).status_code == 403
    response = client.post('/api/auth/password', json={'current_password': '654321', 'new_password': '456789'})
    assert response.status_code == 200 and response.json() == {'needs_login': True}
    assert 'ark_session' not in client.cookies
    assert login(client, 'second', '456789').status_code == 200
    assert not client.get('/api/auth/me').json()['user']['must_change_password']


@pytest.mark.parametrize('role', ['owner', 'ADMIN', '', None, True, 1, ['admin']])
def test_api_roles_reject_invalid_values_for_create_and_patch(auth_app, role):
    client, accounts, admin = auth_app
    login(client)
    response = client.post('/api/admin/users', json={'username': 'invalid', 'password': PASSWORD, 'role': role})
    assert response.status_code == 422 and PASSWORD not in response.text
    assert client.patch(f'/api/admin/users/{admin["id"]}', json={'role': role}).status_code == 422
    assert accounts.get_user(admin['id'])['role'] == 'super_admin'
    assert len(accounts.list_users()) == 1


@pytest.mark.parametrize('password', ['12345', 'a' * 1025])
def test_api_password_boundaries_are_atomic(auth_app, password):
    client, accounts, admin = auth_app
    login(client)
    assert client.post('/api/admin/users', json={'username': 'bad', 'password': password}).status_code == 422
    assert client.post(f'/api/admin/users/{admin["id"]}/reset-password', json={'password': password}).status_code == 422
    assert client.post('/api/auth/password', json={'current_password': PASSWORD, 'new_password': password}).status_code == 422
    assert client.get('/api/auth/me').status_code == 200
    assert len(accounts.list_users()) == 1
