"""Account policy regression tests: legacy migration, role authority and locking."""
from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
from threading import Barrier
from types import SimpleNamespace

from argon2 import PasswordHasher
import pytest

from app.accounts import Accounts, AccountError


@pytest.fixture
def accounts(tmp_path):
    return Accounts(tmp_path)


def rejected(code, operation):
    with pytest.raises(AccountError) as caught:
        operation()
    assert caught.value.status_code == code


def test_six_digit_passwords_for_all_lifecycle_operations(accounts):
    admin = accounts.init_admin('admin', '123456')
    user = accounts.create_user('alice', '654321', admin['id'])
    for item in (admin, user):
        assert item['must_change_password'] is False
    assert accounts.login('admin', '123456', '127.0.0.1')['user'] == admin
    tokens = [accounts.login('alice', '654321', '127.0.0.1')['token'] for _ in range(2)]
    accounts.reset_password(user['id'], '456789', admin['id'])
    assert not accounts.get_user(user['id'])['must_change_password']
    for token in tokens:
        rejected(401, lambda: accounts.authenticate(token))
    token = accounts.login('alice', '456789', '127.0.0.1')['token']
    accounts.change_password(user['id'], '456789', '987654')
    rejected(401, lambda: accounts.authenticate(token))
    assert not accounts.login('alice', '987654', '127.0.0.1')['user']['must_change_password']
    audit = json.dumps(accounts.list_audit())
    for password in ('123456', '654321', '456789', '987654'):
        assert password not in audit


@pytest.mark.parametrize('password', ['12345', 'a' * 1025])
def test_core_password_boundaries_preserve_existing_account(accounts, password):
    rejected(422, lambda: accounts.init_admin('admin', password))
    admin = accounts.init_admin('admin', 'initial-password')
    rejected(422, lambda: accounts.create_user('alice', password, admin['id']))
    rejected(422, lambda: accounts.reset_password(admin['id'], password, admin['id']))
    rejected(422, lambda: accounts.change_password(admin['id'], 'initial-password', password))
    assert accounts.login('admin', 'initial-password', '127.0.0.1')
    assert len(accounts.list_users()) == 1


def test_1024_character_password_still_supported(accounts):
    admin = accounts.init_admin('admin', '9' * 1024)
    assert accounts.login('admin', '9' * 1024, '127.0.0.1')['user'] == admin


def test_old_database_migration_preserves_hashes_and_overrides_old_default(tmp_path):
    private = tmp_path / 'private'
    private.mkdir()
    encoded = PasswordHasher().hash('existing-password')
    user_id = 'a' * 32
    with sqlite3.connect(private / 'accounts.db') as conn:
        conn.execute('''CREATE TABLE users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
            role TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 1,
            max_concurrent INTEGER NOT NULL DEFAULT 1, max_queued INTEGER NOT NULL DEFAULT 10,
            legacy_owner INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL)''')
        conn.execute("INSERT INTO users(id,username,password_hash,role,legacy_owner,created_at) VALUES(?,?,?,'admin',1,123)",
                     (user_id, 'admin', encoded))
    accounts = Accounts(tmp_path)
    admin = accounts.init_admin('ignored', 'ignored-password')
    assert admin['id'] == user_id and not admin['must_change_password']
    assert admin['legacy_owner'] and admin['created_at'] == 123
    user = accounts.create_user('alice', 'another-password', admin['id'])
    assert not user['must_change_password']
    token = accounts.login('admin', 'existing-password', '127.0.0.1')['token']
    with sqlite3.connect(accounts.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM users WHERE must_change_password!=0').fetchone()[0] == 0
        assert conn.execute('SELECT password_hash FROM users WHERE id=?', (user_id,)).fetchone()[0] == encoded
        # An already-created Accounts instance must also serialize stale flags as false.
        conn.execute('UPDATE users SET must_change_password=1')
    assert not accounts.get_user(user_id)['must_change_password']
    assert all(not user['must_change_password'] for user in accounts.list_users())
    assert not accounts.authenticate(token)['user']['must_change_password']
    Accounts(tmp_path)
    with sqlite3.connect(accounts.db_path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM users WHERE must_change_password!=0').fetchone()[0] == 0
        assert conn.execute('SELECT password_hash FROM users WHERE id=?', (user_id,)).fetchone()[0] == encoded


def test_create_admin_and_change_roles_preserve_ownership_revoke_sessions_and_audit(accounts):
    admin = accounts.init_admin('admin', 'initial-password')
    second = accounts.create_user('second', 'second-password', admin['id'], role='admin')
    ordinary = accounts.create_user('alice', 'alice-password', admin['id'])
    assert second['role'] == 'admin' and ordinary['role'] == 'user'
    assert not second['legacy_owner'] and not ordinary['legacy_owner']
    with sqlite3.connect(accounts.db_path) as conn:
        hashes = dict(conn.execute('SELECT id,password_hash FROM users'))
    for user, password, role in [(ordinary, 'alice-password', 'admin'), (admin, 'initial-password', 'user')]:
        tokens = [accounts.login(user['username'], password, '127.0.0.1')['token'] for _ in range(2)]
        changed = accounts.update_user(user['id'], second['id'], role=role)
        assert changed['role'] == role and changed['legacy_owner'] == user['legacy_owner']
        for token in tokens:
            rejected(401, lambda: accounts.authenticate(token))
        assert accounts.login(user['username'], password, '127.0.0.1')['user']['role'] == role
    with sqlite3.connect(accounts.db_path) as conn:
        assert dict(conn.execute('SELECT id,password_hash FROM users')) == hashes
    events = accounts.list_audit()
    for user_id, role in [(ordinary['id'], 'admin'), (admin['id'], 'user')]:
        assert any(event['action'] == 'user.update' and event['target'] == user_id
                   and event['actor_id'] == second['id'] and event['details']['role'] == role
                   for event in events if event['action'] == 'user.update')
    assert any(event['action'] == 'user.create' and event['target'] == second['id']
               and event['details']['role'] == 'admin' for event in events)
    assert not any(password in json.dumps(events) for password in hashes.values())
    rejected(403, lambda: accounts.create_user('blocked', 'valid-password', admin['id'], role='admin'))
    rejected(403, lambda: accounts.update_user(admin['id'], admin['id'], role='admin'))


@pytest.mark.parametrize('role', ['owner', 'ADMIN', '', True, 1, ['admin']])
def test_invalid_core_roles_are_atomic(accounts, role):
    admin = accounts.init_admin('admin', 'initial-password')
    rejected(422, lambda: accounts.create_user('alice', 'valid-password', admin['id'], role=role))
    rejected(422, lambda: accounts.update_user(admin['id'], admin['id'], role=role, max_queued=99))
    assert accounts.list_users() == [admin]


@pytest.mark.parametrize('changes', [{'role': 'user'}, {'enabled': False}, {'role': 'user', 'enabled': False}])
def test_last_enabled_admin_cannot_be_removed_even_if_disabled_admin_exists(accounts, changes):
    admin = accounts.init_admin('admin', 'initial-password')
    other = accounts.create_user('second', 'second-password', admin['id'], role='admin')
    accounts.update_user(other['id'], admin['id'], enabled=False)
    token = accounts.login('admin', 'initial-password', '127.0.0.1')['token']
    rejected(409, lambda: accounts.update_user(admin['id'], admin['id'], max_queued=99, **changes))
    assert accounts.get_user(admin['id']) == admin
    assert accounts.authenticate(token)['user'] == admin


@pytest.mark.parametrize('operations', [({'role': 'user'}, {'role': 'user'}),
                                        ({'role': 'user'}, {'enabled': False})])
def test_concurrent_admin_demotion_or_disable_keeps_one_admin(accounts, operations):
    admin = accounts.init_admin('admin', 'initial-password')
    other = accounts.create_user('second', 'second-password', admin['id'], role='admin')
    barrier = Barrier(2)
    def change(item):
        user, changes = item
        barrier.wait(timeout=10)
        try:
            accounts.update_user(user['id'], user['id'], **changes)
            return 200
        except AccountError as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = list(pool.map(change, zip((admin, other), operations)))
    assert sorted(codes) == [200, 409]
    assert sum(user['role'] == 'admin' and user['enabled'] for user in accounts.list_users()) == 1


def test_same_role_patch_keeps_existing_sessions(accounts):
    admin = accounts.init_admin('admin', 'initial-password')
    token = accounts.login('admin', 'initial-password', '127.0.0.1')['token']
    accounts.update_user(admin['id'], admin['id'], role='admin', max_concurrent=2)
    assert accounts.authenticate(token)['user']['max_concurrent'] == 2


def test_bootstrap_cli_accepts_six_digits_and_describes_current_policy(tmp_path, monkeypatch, capsys):
    from app import manage_users
    prompts = []
    def enter_password(prompt):
        prompts.append(prompt)
        return '123456'
    monkeypatch.setattr(manage_users, 'settings', SimpleNamespace(storage_dir=tmp_path))
    monkeypatch.setattr(manage_users, 'getpass', enter_password)
    monkeypatch.setattr('sys.argv', ['manage_users', 'init-admin', '--username', 'admin'])
    manage_users.main()
    assert Accounts(tmp_path).login('admin', '123456', '127.0.0.1')['user']['must_change_password'] is False
    assert '6' in prompts[0] and '强制' not in prompts[0]
    assert '123456' not in capsys.readouterr().out
