"""Self registration uses real SQLite, authentication and the permission boundary."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.templating import Jinja2Templates

from app import access_control, authentication
from app.accounts import AccountError, Accounts


PASSWORD = 'Register-password-42!'
ORIGIN = {'Origin': 'http://testserver'}


@dataclass
class Settings:
    storage_dir: Path
    config_root: Path | None = None
    user_id: str = ''


@pytest.fixture
def registration_app(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'true')
    monkeypatch.setenv('APP_COOKIE_SECURE', 'false')
    monkeypatch.delenv('APP_PUBLIC_ORIGIN', raising=False)
    accounts = Accounts(tmp_path)
    admin = accounts.init_admin('admin', PASSWORD)
    settings = Settings(tmp_path)
    app = FastAPI()
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / 'app/templates'))
    app.include_router(authentication.get_router(lambda: settings, templates))
    access_control.install(app, lambda: settings)
    with TestClient(app) as client:
        yield client, accounts, admin


def register(client, username='new-user', password=PASSWORD, **extra):
    return client.post('/api/auth/register', headers=ORIGIN,
                       json={'username': username, 'password': password, 'confirm_password': password, **extra})


def test_register_creates_ordinary_user_and_authenticated_session(registration_app):
    client, accounts, admin = registration_app
    response = register(client, '  Ａlice小舟  ')
    assert response.status_code == 201, response.text
    data = response.json()
    user = data['user']
    assert set(data) == {'user', 'csrf_token'}
    assert user['username'] == 'alice小舟'
    assert user['role'] == 'user' and user['enabled']
    assert not user['legacy_owner'] and not user['must_change_password']
    assert (user['max_concurrent'], user['max_queued']) == (8, 10)
    assert user['id'] != admin['id']
    assert response.headers['cache-control'] == 'no-store'
    cookie = response.headers['set-cookie'].lower()
    assert all(value in cookie for value in ('httponly', 'samesite=lax', 'max-age=43200', 'path=/'))
    assert PASSWORD not in response.text and 'password_hash' not in response.text
    assert client.cookies['ark_session'] not in response.text
    assert client.get('/api/auth/me').json()['user'] == user
    assert client.get('/api/admin/users').status_code == 403
    assert client.post('/api/auth/logout').status_code == 403
    assert client.post('/api/auth/logout', headers={'X-CSRF-Token': data['csrf_token']}).status_code == 204
    assert client.post('/api/auth/login', headers=ORIGIN,
                       json={'username': 'alice小舟', 'password': PASSWORD}).status_code == 200
    with accounts._connect() as conn:
        encoded = conn.execute('SELECT password_hash FROM users WHERE id=?', (user['id'],)).fetchone()[0]
    assert encoded.startswith('$argon2id$') and PASSWORD not in encoded
    audit = [row for row in accounts.list_audit() if row['action'] == 'user.register']
    assert len(audit) == 1 and audit[0]['actor_id'] == user['id']
    assert PASSWORD not in str(audit)


@pytest.mark.parametrize('extra', [{'role': 'admin'}, {'max_concurrent': 100}, {'max_queued': 100},
                                  {'legacy_owner': True}, {'enabled': True}])
def test_register_rejects_privileged_fields(registration_app, extra):
    client, accounts, _ = registration_app
    response = register(client, **extra)
    assert response.status_code == 422
    assert PASSWORD not in response.text
    assert len(accounts.list_users()) == 1


def test_admin_can_override_registration_quota_without_changing_existing_accounts(registration_app):
    client, accounts, admin = registration_app
    existing = accounts.create_user('existing-account', PASSWORD, admin['id'], max_concurrent=3)
    response = register(client)
    assert response.status_code == 201
    user = response.json()['user']
    assert user['max_concurrent'] == 8
    assert accounts.get_user(existing['id'])['max_concurrent'] == 3
    response = client.patch('/api/admin/users/' + user['id'], headers=ORIGIN,
                           json={'max_concurrent': 20})
    assert response.status_code == 403
    logged_in = client.post('/api/auth/login', headers=ORIGIN,
                           json={'username': 'admin', 'password': PASSWORD})
    assert logged_in.status_code == 200
    response = client.patch('/api/admin/users/' + user['id'],
                            headers={**ORIGIN, 'X-CSRF-Token': logged_in.json()['csrf_token']},
                            json={'max_concurrent': 20})
    assert response.status_code == 200
    assert response.json()['user']['max_concurrent'] == 20
    assert accounts.get_user(user['id'])['max_concurrent'] == 20
    assert accounts.get_user(existing['id'])['max_concurrent'] == 3


@pytest.mark.parametrize('fields', [
    {'confirm_password': 'different-secret'}, {'confirm_password': ''},
    {'password': '12345', 'confirm_password': '12345'},
    {'password': 'x' * 1025, 'confirm_password': 'x' * 1025},
    {'username': 'bad name'}, {'username': ' '}, {'username': 'a' * 65},
    {'password': 123456, 'confirm_password': 123456},
])
def test_register_validation_never_creates_user_or_echoes_password(registration_app, fields):
    client, accounts, _ = registration_app
    payload = {'username': 'new-user', 'password': PASSWORD, 'confirm_password': PASSWORD, **fields}
    response = client.post('/api/auth/register', headers=ORIGIN, json=payload)
    assert response.status_code == 422
    assert PASSWORD not in response.text and 'different-secret' not in response.text
    assert len(accounts.list_users()) == 1
    assert 'ark_session' not in client.cookies


def test_register_accepts_existing_six_digit_password_policy(registration_app):
    client, _, _ = registration_app
    assert register(client, password='123456').status_code == 201


def test_duplicate_name_preserves_password_and_current_session(registration_app):
    client, accounts, _ = registration_app
    assert register(client, 'Alice').status_code == 201
    token = client.cookies['ark_session']
    response = register(client, '  ＡLICE  ', password='different-password')
    assert response.status_code == 409
    assert '已存在' in response.json()['detail']
    assert len(accounts.list_users()) == 2
    assert client.cookies['ark_session'] == token
    assert accounts.authenticate(token)['user']['username'] == 'alice'
    assert accounts.login('alice', PASSWORD, 'test-ip')['user']['username'] == 'alice'


@pytest.mark.parametrize('origin', [None, 'null', 'https://evil.example', 'http://testserver/path',
                                  'http://testserver.evil.example', 'http://@testserver'])
def test_register_requires_same_origin(registration_app, origin):
    client, accounts, _ = registration_app
    response = client.post('/api/auth/register', headers={'Origin': origin} if origin else {},
                           json={'username': 'new-user', 'password': PASSWORD, 'confirm_password': PASSWORD})
    assert response.status_code == 403
    assert len(accounts.list_users()) == 1


def test_registration_rotates_cookie_with_secure_production_defaults(registration_app, monkeypatch):
    client, accounts, _ = registration_app
    assert register(client, 'first').status_code == 201
    previous = client.cookies['ark_session']
    monkeypatch.delenv('APP_COOKIE_SECURE')
    response = register(client, 'second')
    assert response.status_code == 201
    assert '; Secure' in response.headers['set-cookie']
    with pytest.raises(AccountError):
        accounts.authenticate(previous)


def test_registration_disabled_without_authentication(registration_app, monkeypatch):
    client, accounts, _ = registration_app
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    assert register(client).status_code == 403
    assert len(accounts.list_users()) == 1


def test_registration_cannot_capture_uninitialized_database(tmp_path):
    accounts = Accounts(tmp_path)
    with pytest.raises(AccountError) as error:
        accounts.register('visitor', PASSWORD, 'test-ip')
    assert error.value.status_code == 503
    assert accounts.list_users() == []
    assert accounts.init_admin('admin', PASSWORD)['legacy_owner']


def test_registration_limit_persists_across_instances_and_expires(registration_app, monkeypatch):
    client, accounts, _ = registration_app
    from app import accounts as module
    current = module.time.time()
    monkeypatch.setattr(module.time, 'time', lambda: current)
    for attempt in range(5):
        response = client.post('/api/auth/register',
                               headers={**ORIGIN, 'X-Forwarded-For': f'10.0.0.{attempt}'},
                               json={'username': f'person-{attempt}', 'password': PASSWORD, 'confirm_password': PASSWORD})
        assert response.status_code == 201
    assert register(client, 'too-many').status_code == 429
    other_worker = Accounts(accounts.root)
    with pytest.raises(AccountError) as error:
        other_worker.register('other-worker', PASSWORD, 'testclient')
    assert error.value.status_code == 429
    assert other_worker.register('other-ip', PASSWORD, 'another-ip')['user']['role'] == 'user'
    current += 901
    assert register(client, 'after-window').status_code == 201


def test_duplicate_attempts_are_rate_limited(registration_app):
    client, _, _ = registration_app
    for _ in range(5):
        assert register(client, 'admin').status_code == 409
    assert register(client, 'new-name').status_code == 429


def test_concurrent_registration_cannot_duplicate_username(registration_app):
    _, accounts, _ = registration_app

    def attempt(index):
        try:
            return Accounts(accounts.root).register('same-name', PASSWORD, f'ip-{index}')['user']
        except AccountError as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(attempt, range(3)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count(409) == 2
    assert len(accounts.list_users()) == 2


def test_register_page_is_public_but_other_auth_paths_stay_protected(registration_app):
    client, _, _ = registration_app
    response = client.get('/register', follow_redirects=False)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert client.get('/api/auth/me').status_code == 401
    assert client.get('/api/auth/register').status_code == 401
    assert client.post('/api/auth/register/extra').status_code == 401
