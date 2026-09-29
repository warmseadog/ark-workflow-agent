import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest


PASSWORD = 'Initial-password-42!'
NEW_PASSWORD = 'Changed-password-43!'


@pytest.fixture
def accounts(tmp_path):
    from app.accounts import Accounts
    return Accounts(tmp_path)


def assert_error(code, operation):
    from app.accounts import AccountError
    with pytest.raises(AccountError) as caught:
        operation()
    assert caught.value.status_code == code
    assert caught.value.detail
    assert any('\u4e00' <= char <= '\u9fff' for char in caught.value.detail)


def test_bootstrap_is_normalized_private_and_never_overwrites(accounts, tmp_path):
    admin = accounts.init_admin('  ADMIN  ', PASSWORD)
    assert admin['username'] == 'admin'
    assert len(admin['id']) == 32 and int(admin['id'], 16)
    assert admin['role'] == 'admin' and admin['legacy_owner'] is True
    assert admin['enabled'] is True and admin['must_change_password'] is False
    assert admin['max_concurrent'] == 1 and admin['max_queued'] == 10
    assert admin['created_at']
    assert 'password_hash' not in admin
    accounts.init_admin('someone-else', NEW_PASSWORD)
    assert accounts.list_users() == [admin]
    assert accounts.get_user(admin['id']) == admin
    assert accounts.login('ADMIN', PASSWORD, '127.0.0.1')['user'] == admin
    with sqlite3.connect(tmp_path / 'private' / 'accounts.db') as conn:
        stored = conn.execute('SELECT password_hash FROM users').fetchone()[0]
    assert stored.startswith('$argon2id$') and PASSWORD not in stored


def test_concurrent_bootstrap_creates_exactly_one_legacy_admin(accounts, tmp_path):
    from app.accounts import Accounts
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda name: Accounts(tmp_path).init_admin(name, PASSWORD), ['one', 'two']))
    assert len(accounts.list_users()) == 1
    assert accounts.list_users()[0]['legacy_owner'] is True


def test_admin_creates_normalized_user_and_duplicate_rejected(accounts):
    admin = accounts.init_admin('admin', PASSWORD)
    user = accounts.create_user(' Ａlice ', PASSWORD, admin['id'], max_concurrent=2, max_queued=4)
    assert user['username'] == 'alice' and user['role'] == 'user'
    assert not user['must_change_password'] and not user['legacy_owner']
    assert (user['max_concurrent'], user['max_queued']) == (2, 4)
    assert_error(409, lambda: accounts.create_user('ALICE', PASSWORD, admin['id']))
    assert_error(403, lambda: accounts.create_user('bob', PASSWORD, user['id']))
    assert_error(404, lambda: accounts.get_user('missing'))


@pytest.mark.parametrize('changes', [
    {'username': '../bad'}, {'username': ''}, {'password': 'short'},
    {'max_concurrent': 0}, {'max_concurrent': True}, {'max_queued': -1},
    {'max_queued': '2'},
])
def test_invalid_user_input_cannot_create_account(accounts, changes):
    admin = accounts.init_admin('admin', PASSWORD)
    arguments = dict(username='alice', password=PASSWORD, actor_id=admin['id'])
    arguments.update(changes)
    assert_error(422, lambda: accounts.create_user(**arguments))
    assert len(accounts.list_users()) == 1


def test_sessions_are_unique_hashed_fixed_expiry_and_persistent(accounts, tmp_path, monkeypatch):
    from app import accounts as module
    accounts.init_admin('admin', PASSWORD)
    now = 1800000000.0
    monkeypatch.setattr(module.time, 'time', lambda: now)
    first = accounts.login('admin', PASSWORD, '127.0.0.1')
    second = accounts.login('admin', PASSWORD, '127.0.0.1')
    assert first['token'] != second['token'] and first['csrf_token'] != second['csrf_token']
    assert first['token'] != first['csrf_token']
    assert len(first['token']) >= 43
    session = module.Accounts(tmp_path).authenticate(first['token'])
    assert session['user'] == first['user'] and session['csrf_token'] == first['csrf_token']
    assert session['expires_at'] == now + 43200
    with sqlite3.connect(tmp_path / 'private' / 'accounts.db') as conn:
        dump = '\n'.join(conn.iterdump())
    assert first['token'] not in dump and PASSWORD not in dump
    now += 43199
    assert accounts.authenticate(first['token'])['expires_at'] == session['expires_at']
    now += 1
    assert_error(401, lambda: accounts.authenticate(first['token']))
    assert_error(401, lambda: accounts.authenticate('bad-token'))
    assert_error(401, lambda: accounts.authenticate(None))


def test_disabled_account_revokes_sessions_even_after_reenable(accounts):
    admin = accounts.init_admin('admin', PASSWORD)
    user = accounts.create_user('alice', PASSWORD, admin['id'])
    session = accounts.login('alice', PASSWORD, '127.0.0.1')
    accounts.update_user(user['id'], admin['id'], enabled=False)
    assert_error(401, lambda: accounts.authenticate(session['token']))
    assert_error(401, lambda: accounts.login('alice', PASSWORD, '127.0.0.1'))
    updated = accounts.update_user(user['id'], admin['id'], enabled=True, max_concurrent=3, max_queued=0)
    assert updated['max_concurrent'] == 3 and updated['max_queued'] == 0
    assert_error(401, lambda: accounts.authenticate(session['token']))


def test_final_admin_and_admin_authority_are_checked(accounts):
    admin = accounts.init_admin('admin', PASSWORD)
    user = accounts.create_user('alice', PASSWORD, admin['id'])
    assert_error(409, lambda: accounts.update_user(admin['id'], admin['id'], enabled=False))
    assert_error(403, lambda: accounts.update_user(user['id'], user['id'], max_concurrent=99))
    assert_error(403, lambda: accounts.reset_password(admin['id'], NEW_PASSWORD, user['id']))
    assert_error(422, lambda: accounts.update_user(user['id'], admin['id'], enabled='false'))
    assert accounts.get_user(admin['id'])['enabled']


def test_change_password_verifies_current_and_revokes_every_session(accounts):
    admin = accounts.init_admin('admin', PASSWORD)
    sessions = [accounts.login('admin', PASSWORD, '127.0.0.1') for _ in range(2)]
    assert_error(401, lambda: accounts.change_password(admin['id'], 'wrong', NEW_PASSWORD))
    assert accounts.authenticate(sessions[0]['token'])
    assert_error(422, lambda: accounts.change_password(admin['id'], PASSWORD, PASSWORD))
    accounts.change_password(admin['id'], PASSWORD, NEW_PASSWORD)
    assert not accounts.get_user(admin['id'])['must_change_password']
    for session in sessions:
        assert_error(401, lambda: accounts.authenticate(session['token']))
    assert_error(401, lambda: accounts.login('admin', PASSWORD, '127.0.0.1'))
    assert accounts.login('admin', NEW_PASSWORD, '127.0.0.1')


def test_reset_password_requires_no_change_and_logout_is_idempotent(accounts):
    admin = accounts.init_admin('admin', PASSWORD)
    user = accounts.create_user('alice', PASSWORD, admin['id'])
    accounts.change_password(user['id'], PASSWORD, NEW_PASSWORD)
    sessions = [accounts.login('alice', NEW_PASSWORD, '127.0.0.1') for _ in range(2)]
    accounts.reset_password(user['id'], PASSWORD, admin['id'])
    assert not accounts.get_user(user['id'])['must_change_password']
    for session in sessions:
        assert_error(401, lambda: accounts.authenticate(session['token']))
    session = accounts.login('alice', PASSWORD, '127.0.0.1')
    accounts.logout(session['token'])
    accounts.logout(session['token'])
    assert_error(401, lambda: accounts.authenticate(session['token']))


def test_invalid_password_reset_is_atomic_and_disabled_admin_has_no_authority(accounts, tmp_path):
    admin = accounts.init_admin('admin', PASSWORD)
    user = accounts.create_user('alice', PASSWORD, admin['id'])
    session = accounts.login('alice', PASSWORD, '127.0.0.1')
    assert_error(422, lambda: accounts.reset_password(user['id'], 'short', admin['id']))
    assert accounts.authenticate(session['token'])['user'] == user
    # Simulate a disabled admin changed by another process: authority must be
    # re-read from SQLite, not trusted from a cached public user.
    with sqlite3.connect(tmp_path / 'private' / 'accounts.db') as conn:
        conn.execute('UPDATE users SET enabled=0 WHERE id=?', (admin['id'],))
    assert_error(403, lambda: accounts.create_user('bob', PASSWORD, admin['id']))
    assert_error(403, lambda: accounts.update_user(user['id'], admin['id'], enabled=False))
    assert_error(403, lambda: accounts.reset_password(user['id'], NEW_PASSWORD, admin['id']))


def test_authenticate_rechecks_disabled_flag_even_if_session_still_exists(accounts, tmp_path):
    admin = accounts.init_admin('admin', PASSWORD)
    session = accounts.login('admin', PASSWORD, '127.0.0.1')
    with sqlite3.connect(tmp_path / 'private' / 'accounts.db') as conn:
        conn.execute('UPDATE users SET enabled=0 WHERE id=?', (admin['id'],))
    assert_error(401, lambda: accounts.authenticate(session['token']))


def test_two_admins_cannot_concurrently_disable_both(accounts, tmp_path):
    admin = accounts.init_admin('admin', PASSWORD)
    other = accounts.create_user('second', PASSWORD, admin['id'])
    # Role promotion is deliberately not part of the public create/update API.
    with sqlite3.connect(tmp_path / 'private' / 'accounts.db') as conn:
        conn.execute("UPDATE users SET role='admin' WHERE id=?", (other['id'],))

    def disable(user):
        from app.accounts import AccountError
        try:
            accounts.update_user(user['id'], user['id'], enabled=False)
            return 200
        except AccountError as exc:
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(disable, [admin, other]))
    assert sorted(results) == [200, 409]
    assert sum(user['enabled'] for user in accounts.list_users()) == 1


def test_username_rate_limit_normalizes_and_survives_new_instance(accounts, tmp_path, monkeypatch):
    from app import accounts as module
    accounts.init_admin('admin', PASSWORD)
    now = 1800000000.0
    monkeypatch.setattr(module.time, 'time', lambda: now)
    for attempt in range(5):
        assert_error(401, lambda: accounts.login(' ADMIN ', 'wrong', f'10.0.0.{attempt}'))
    assert_error(429, lambda: module.Accounts(tmp_path).login('admin', PASSWORD, '10.0.1.1'))
    now += 901
    assert accounts.login('admin', PASSWORD, '10.0.1.1')


def test_ip_rate_limit_blocks_username_rotation(accounts):
    for attempt in range(20):
        assert_error(401, lambda: accounts.login(f'unknown-{attempt}', 'wrong', '10.0.0.1'))
    assert_error(429, lambda: accounts.login('another', 'wrong', '10.0.0.1'))
    assert_error(401, lambda: accounts.login('another', 'wrong', '10.0.0.2'))


def test_audit_is_sanitized_bounded_and_contains_account_events(accounts):
    admin = accounts.init_admin('admin', PASSWORD)
    accounts.audit(admin['id'], 'example\naction', details={
        'password': PASSWORD, 'token': 'secret-token', 'nested': {'password': PASSWORD},
        'enabled': False, 'max_queued': 4,
    })
    items = accounts.list_audit(1)
    assert len(items) == 1 and items[0]['actor_id'] == admin['id']
    assert '\n' not in items[0]['action']
    assert items[0]['details']['enabled'] is False
    assert PASSWORD not in json.dumps(items) and 'secret-token' not in json.dumps(items)
    assert any(item['action'] == 'user.init_admin' for item in accounts.list_audit(100))
    assert_error(422, lambda: accounts.list_audit(-1))
