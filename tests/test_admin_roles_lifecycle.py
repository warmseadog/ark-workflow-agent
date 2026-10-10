import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.accounts import Accounts, AccountError
from app.production_store import ProductionStore


def rejected(code, fn):
    with pytest.raises(AccountError) as caught:
        fn()
    assert caught.value.status_code == code


def test_bootstrap_super_and_admin_scope(tmp_path):
    store = Accounts(tmp_path)
    owner = store.init_admin('owner', '123456')
    assert owner['role'] == 'super_admin'
    admin = store.create_user('manager', '123456', owner['id'], role='admin')
    user = store.create_user('person', '123456', admin['id'])
    rejected(403, lambda: store.create_user('other-admin', '123456', admin['id'], role='admin'))
    rejected(403, lambda: store.update_user(user['id'], admin['id'], role='super_admin'))
    rejected(403, lambda: store.update_user(owner['id'], admin['id'], max_queued=20))
    rejected(403, lambda: store.reset_password(admin['id'], '654321', admin['id']))
    assert store.update_user(user['id'], admin['id'], max_queued=12)['max_queued'] == 12


@pytest.mark.parametrize('name', ['x', 'x' * 21])
def test_new_name_limits_but_legacy_names_can_login(tmp_path, name):
    store = Accounts(tmp_path)
    owner = store.init_admin('owner', '123456')
    rejected(422, lambda: store.create_user(name, '123456', owner['id']))
    rejected(422, lambda: store.register(name, '123456', 'local'))
    user = store.create_user('legacy', '123456', owner['id'])
    with sqlite3.connect(store.db_path) as db:
        db.execute('UPDATE users SET username=? WHERE id=?', (name, user['id']))
    assert store.login(name, '123456', 'local')['user']['id'] == user['id']


def test_old_check_constraint_migration_preserves_hash_and_session(tmp_path):
    from argon2 import PasswordHasher
    import hashlib
    import time
    private = tmp_path / 'private'
    private.mkdir()
    encoded = PasswordHasher().hash('123456')
    token = 'a' * 43
    owner_id = 'b' * 32
    with sqlite3.connect(private / 'accounts.db') as db:
        db.executescript("""CREATE TABLE users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','user')), enabled INTEGER NOT NULL DEFAULT 1,
            must_change_password INTEGER NOT NULL DEFAULT 0, max_concurrent INTEGER NOT NULL DEFAULT 1,
            max_queued INTEGER NOT NULL DEFAULT 10, legacy_owner INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL);
            CREATE TABLE sessions(token_hash TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id),
            csrf_token TEXT,created_at REAL,expires_at REAL);""")
        db.execute("INSERT INTO users(id,username,password_hash,role,legacy_owner,created_at) VALUES(?,?,?,'admin',1,0)", (owner_id, 'owner', encoded))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), owner_id, 'csrf', time.time(), time.time()+3600))
    migrated = Accounts(tmp_path)
    assert migrated.authenticate(token)['user']['role'] == 'super_admin'
    assert migrated.get_user(owner_id)['legacy_owner']
    with sqlite3.connect(migrated.db_path) as db:
        assert db.execute('SELECT password_hash FROM users').fetchone()[0] == encoded
        assert not db.execute('PRAGMA foreign_key_check').fetchall()


def add_run(store, ident, status='queued', provider=None):
    with store.connection() as db:
        db.execute('''INSERT INTO production_runs(id,draft_id,revision,idempotency_key,snapshot,private,status,stage,message,created_at,updated_at)
            VALUES(?,?,1,?,'{}','{}',?,'queued','',?,?)''', (ident, ident, ident, status, '2026-10-05T00:00:00+00:00', '2026-10-05T00:00:00+00:00'))
        if provider:
            db.execute('UPDATE production_runs SET provider_task_id=? WHERE id=?', (provider, ident))


def test_delete_revokes_session_cancels_queue_preserves_history_restore_super_only(tmp_path):
    store = Accounts(tmp_path)
    owner = store.init_admin('owner', '123456')
    admin = store.create_user('manager', '123456', owner['id'], role='admin')
    user = store.create_user('person', '123456', owner['id'])
    token = store.login('person', '123456', 'local')['token']
    production = ProductionStore(tmp_path / 'users' / user['id'])
    add_run(production, 'queue')
    add_run(production, 'done', 'succeeded')
    deleted = store.delete_user(user['id'], admin['id'])
    assert deleted['deleted_at'] and not deleted['enabled']
    rejected(401, lambda: store.authenticate(token))
    rejected(401, lambda: store.login('person', '123456', 'local'))
    assert production.get_run('queue')['status'] == 'cancelled'
    assert production.get_run('done')['status'] == 'succeeded'
    rejected(409, lambda: store.update_user(user['id'], owner['id'], enabled=True))
    rejected(403, lambda: store.restore_user(user['id'], admin['id']))
    assert store.restore_user(user['id'], owner['id'])['enabled']
    assert store.login('person', '123456', 'local')['user']['id'] == user['id']


@pytest.mark.parametrize('status,provider', [('running', None), ('queued', 'remote-task')])
def test_delete_rejects_inflight_work_without_disabling(tmp_path, status, provider):
    store = Accounts(tmp_path)
    owner = store.init_admin('owner', '123456')
    user = store.create_user('person', '123456', owner['id'])
    production = ProductionStore(tmp_path / 'users' / user['id'])
    add_run(production, 'work', status, provider)
    rejected(409, lambda: store.delete_user(user['id'], owner['id']))
    assert store.get_user(user['id'])['enabled']


def test_last_super_survives_concurrent_demotion(tmp_path):
    store = Accounts(tmp_path)
    first = store.init_admin('owner', '123456')
    second = store.create_user('second', '123456', first['id'], role='super_admin')
    barrier = Barrier(2)
    def demote(user):
        barrier.wait()
        try:
            store.update_user(user['id'], user['id'], role='admin')
            return 200
        except AccountError as exc:
            return exc.status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(demote, [first, second])) == [200, 409]
    remaining = [u for u in store.list_users() if u['role'] == 'super_admin']
    rejected(409, lambda: store.delete_user(remaining[0]['id'], remaining[0]['id']))
