from dataclasses import replace
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.config import settings
from app import scheduling_settings, production_worker
from app.accounts import Accounts
from app.production_store import ProductionStore
from app.tenancy import user_settings


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'true')
    monkeypatch.setenv('APP_VIDEO_WORKERS', '4')
    cfg = replace(settings, storage_dir=tmp_path, config_root=None, user_id='')
    app = FastAPI()
    @app.middleware('http')
    async def actor(request, call_next):
        request.state.user = {'role': request.headers.get('X-Test-Role', 'user')}
        return await call_next(request)
    app.include_router(scheduling_settings.get_router(lambda: cfg))
    return TestClient(app), cfg


def test_admin_reads_super_admin_writes_and_invalid_values_leave_durable_config(api):
    client, cfg = api
    assert client.get('/api/admin/scheduling').status_code == 403
    reader = {'X-Test-Role': 'admin'}
    writer = {'X-Test-Role': 'super_admin'}
    data = client.get('/api/admin/scheduling', headers=reader).json()
    assert data['config']['global_concurrency'] == 4 and data['can_edit'] is False
    assert client.put('/api/admin/scheduling', headers=reader, json={'global_concurrency': 2}).status_code == 403
    response = client.put('/api/admin/scheduling', headers=writer, json={'global_concurrency': 2})
    assert response.status_code == 200
    assert response.json()['config']['global_concurrency'] == 2
    for invalid in (0, 5, 2.5, True, '3'):
        assert client.put('/api/admin/scheduling', headers=writer, json={'global_concurrency': invalid}).status_code == 422
    assert scheduling_settings.load_config(cfg)['global_concurrency'] == 2
    with sqlite3.connect(cfg.storage_dir / 'private' / 'scheduling.db') as db:
        assert db.execute('SELECT before_value,after_value FROM audit').fetchall() == [(4, 2)]


def test_usage_and_actual_worker_capacity_are_global_and_honor_running_manager(api, monkeypatch):
    client, cfg = api
    accounts = Accounts(cfg.storage_dir)
    admin = accounts.init_admin('root', 'password-123')
    user = accounts.create_user('worker', 'password-123', admin['id'])
    tenant = user_settings(cfg, user)
    store = ProductionStore(tenant.storage_dir)
    draft = store.create_draft({})
    store.create_run(draft['id'], 1, 'first', {})
    store.create_run(draft['id'], 1, 'second', {})
    store.claim_next()
    manager = production_worker.QueueManager(cfg)
    monkeypatch.setitem(production_worker._managers, str(cfg.storage_dir.resolve()), manager)
    monkeypatch.setenv('APP_VIDEO_WORKERS', '50')
    result = client.get('/api/admin/scheduling', headers={'X-Test-Role': 'admin'}).json()
    assert result['config']['worker_capacity'] == 4
    assert result['usage'] == {'running': 1, 'queued': 1, 'available_slots': 3}
    assert scheduling_settings.load_config(tenant)['global_concurrency'] == 4
    assert client.put('/api/admin/scheduling', headers={'X-Test-Role': 'super_admin'}, json={'global_concurrency': 5}).status_code == 422


def test_dispatch_rechecks_stale_account_before_claiming(api, monkeypatch):
    _, cfg = api
    accounts = Accounts(cfg.storage_dir)
    admin = accounts.init_admin('root', 'password-123')
    user = accounts.create_user('worker', 'password-123', admin['id'])
    tenant = user_settings(cfg, user)
    store = ProductionStore(tenant.storage_dir)
    draft = store.create_draft({})
    run = store.create_run(draft['id'], 1, 'first', {})
    manager = production_worker.QueueManager(cfg)
    key = manager._tenant_key(tenant)
    monkeypatch.setattr(manager, '_ready_tenants', lambda: [(key, user, tenant, store)])
    accounts.delete_user(user['id'], admin['id'])
    assert manager.next_job() is None
    assert store.get_run(run['id'])['status'] == 'cancelled'
