import json
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_restore_hold_blocks_reads_writes_and_provider_callbacks(tmp_path):
    from app import recovery_guard
    api = FastAPI()
    seen = []
    @api.api_route('/api/action', methods=['GET', 'POST'])
    def action():
        seen.append(1)
        return {'ok': True}
    recovery_guard.install(api, lambda: tmp_path)
    client = TestClient(api)
    assert client.get('/api/action').status_code == 200
    (tmp_path/'.restore-hold.json').write_text('{broken', encoding='utf-8')
    assert client.get('/api/action').status_code == 503
    assert client.post('/api/action').status_code == 503
    assert client.get('/auth/portrait/return/'+'a'*64).status_code == 503
    assert len(seen) == 1
    assert 'broken' not in client.get('/api/action').text


def test_tenant_checks_server_root_and_release_requires_matching_snapshot(tmp_path):
    from app import recovery_guard
    from app.config import settings
    cfg = replace(settings, storage_dir=tmp_path/'users'/'alice', config_root=tmp_path)
    (tmp_path/'.restore-hold.json').write_text(json.dumps({'snapshot_id':'snapshot-1'}))
    assert recovery_guard.is_restore_held(cfg)
    with pytest.raises(ValueError):
        recovery_guard.release_hold(tmp_path, snapshot_id='wrong', operator='ops', note='reconciled')
    assert recovery_guard.is_restore_held(cfg)
    recovery_guard.release_hold(tmp_path, snapshot_id='snapshot-1', operator='ops', note='All uncertain tasks kept isolated after provider reconciliation.')
    assert not recovery_guard.is_restore_held(cfg)
    record = json.loads(next((tmp_path/'private'/'recovery-releases').glob('*.json')).read_text())
    assert record['snapshot_id'] == 'snapshot-1'
    assert record['operator'] == 'ops'


def test_release_never_requeues_quarantined_records(tmp_path):
    from app import recovery_guard
    from app.production_store import ProductionStore
    store = ProductionStore(tmp_path)
    draft = store.create_draft({})
    run = store.create_run(draft['id'], 1, 'held', {})
    store.update_run(run['id'], status='restore_held')
    (tmp_path/'.restore-hold.json').write_text(json.dumps({'snapshot_id':'snapshot-2'}))
    recovery_guard.release_hold(tmp_path, snapshot_id='snapshot-2', operator='ops', note='Old run remains held')
    assert store.get_run(run['id'])['status'] == 'restore_held'


def test_main_lifespan_does_not_start_any_worker_while_held(tmp_path, monkeypatch):
    from app import main, production_worker
    from app.config import settings
    monkeypatch.setattr(main, 'settings', replace(settings, storage_dir=tmp_path))
    (tmp_path/'.restore-hold.json').write_text('{}')
    monkeypatch.setattr(production_worker, 'wake', lambda *args: pytest.fail('restored data started a worker'))
    with TestClient(main.app) as client:
        assert client.get('/healthz').status_code == 503
