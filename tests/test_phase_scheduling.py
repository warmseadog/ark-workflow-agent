from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app import production_store, production_worker
from app.production_store import ProductionStore


@pytest.fixture
def phases(tmp_path, monkeypatch):
    seconds = [0]
    monkeypatch.setattr(production_store, 'now', lambda: (datetime(2026, 10, 5, tzinfo=timezone.utc) + timedelta(seconds=seconds[0])).isoformat())
    store = ProductionStore(tmp_path)
    draft = store.create_draft({})
    run = store.create_run(draft['id'], 1, 'timed', {})
    return seconds, store, run['id']


def test_explicit_phases_accumulate_without_progress_or_retry_double_count(phases):
    clock, store, ident = phases
    assert 'phases' in store.get_run(ident)['timing']
    clock[0] = 5
    store.claim_next()
    clock[0] = 7
    store.set_phase(ident, 'waiting')
    clock[0] = 10
    store.set_phase(ident, 'masking')
    clock[0] = 14
    store.set_phase(ident, 'upload')
    clock[0] = 16
    store.set_phase(ident, 'model')
    clock[0] = 20
    store.set_phase(ident, 'model')
    store.update_run(ident, status='needs_attention', provider_task_id='remote')
    clock[0] = 30
    store.resume_run(ident)
    clock[0] = 32
    store.claim_next()
    store.set_phase(ident, 'model')
    clock[0] = 38
    store.update_run(ident, status='succeeded')
    result = ProductionStore(store.storage).get_run(ident)['timing']['phases']
    assert {key: value['seconds'] for key, value in result.items()} == {
        'waiting': 10, 'masking': 4, 'upload': 2, 'model': 10, 'other': 2, 'paused': 10}
    clock[0] = 90
    assert store.get_run(ident)['timing']['phases'] == result


def test_cached_mask_and_recovery_do_not_invent_interrupted_or_historical_seconds(phases):
    clock, store, ident = phases
    assert 'phases' in store.get_run(ident)['timing']
    clock[0] = 5
    store.claim_next()
    store.set_phase(ident, 'masking', cached=True)
    store.set_phase(ident, 'model')
    clock[0] = 500
    ProductionStore(store.storage).recover()
    result = store.get_run(ident)['timing']['phases']
    assert result['masking'] == {'seconds': 0, 'status': 'complete', 'cached': True}
    assert result['model']['seconds'] is None
    assert result['model']['status'] == 'unknown'
    assert result['waiting']['seconds'] == 5
    with store.connection() as db:
        db.execute('DELETE FROM production_run_phases WHERE run_id=?', (ident,))
    assert all(item['seconds'] is None for item in store.get_run(ident)['timing']['phases'].values())


def test_hot_reload_cap_drains_existing_jobs_and_stays_persisted(tmp_path, monkeypatch):
    from app.config import Settings
    from app import scheduling_settings
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    monkeypatch.setenv('APP_VIDEO_WORKERS', '3')
    monkeypatch.setenv('STORAGE_DIR', str(tmp_path))
    settings = replace(Settings.from_env(), storage_dir=tmp_path)
    manager = production_worker.QueueManager(settings)
    assert scheduling_settings.load_config(settings)['global_concurrency'] == 3
    store = manager.store
    draft = store.create_draft({})
    for i in range(4):
        store.create_run(draft['id'], 1, str(i), {})
    first = manager.next_job()
    second = manager.next_job()
    assert first and second
    scheduling_settings.save_config(settings, 1)
    assert manager.next_job() is None
    manager.release(first[0])
    assert manager.next_job() is None
    manager.release(second[0])
    assert manager.next_job()
    assert scheduling_settings.load_config(settings)['global_concurrency'] == 1
    for invalid in (0, 4, 1.5, True, '2'):
        with pytest.raises(ValueError):
            scheduling_settings.save_config(settings, invalid)
