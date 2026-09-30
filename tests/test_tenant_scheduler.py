"""Scheduler contracts with local SQLite stores and a dynamic account directory."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import importlib
from pathlib import Path
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

import app
from app.config import Settings, settings
from app import production_worker as worker
from app.portrait_library import PortraitLibrary
from app.production_store import ProductionStore


@dataclass(frozen=True)
class TenantSettings(Settings):
    config_root: Path | None = None
    user_id: str = ''


@pytest.fixture
def tenants(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_VIDEO_WORKERS', '2')  # Exercise fairness under a constrained global pool.
    # The account implementation is developed separately. Keep this contract
    # test runnable both before and after that module lands.
    try:
        tenancy = importlib.import_module('app.tenancy')
    except ModuleNotFoundError as exc:
        if exc.name != 'app.tenancy':
            raise
        tenancy = ModuleType('app.tenancy')
        monkeypatch.setitem(sys.modules, 'app.tenancy', tenancy)
        monkeypatch.setattr(app, 'tenancy', tenancy, raising=False)
    base = TenantSettings(**{**vars(settings), 'storage_dir': tmp_path,
                             'config_root': tmp_path, 'user_id': ''})
    rows = []

    def add(ident, **limits):
        cfg = replace(base, storage_dir=tmp_path/'users'/ident, user_id=ident)
        rows.append((dict(id=ident, enabled=True, max_queued=10, **limits), cfg))
        return cfg

    def root(cfg):
        return replace(cfg, storage_dir=cfg.config_root or cfg.storage_dir)

    def accounts(cfg, include_disabled=False):
        assert cfg.storage_dir == tmp_path
        return [(dict(user), config) for user, config in rows
                if include_disabled or user['enabled']]

    monkeypatch.setattr(tenancy, 'root_settings', root, raising=False)
    monkeypatch.setattr(tenancy, 'tenant_settings', accounts, raising=False)
    monkeypatch.setattr(tenancy, 'enabled', lambda: True, raising=False)
    monkeypatch.setenv('APP_AUTH_ENABLED', 'true')
    a, b = add('alice'), add('bob')
    return SimpleNamespace(base=base, a=a, b=b, rows=rows, add=add,
                           tenancy=tenancy)


def queued(cfg, count=1):
    store = ProductionStore(cfg.storage_dir)
    draft = store.create_draft({'name': cfg.user_id})
    runs = [store.create_run(draft['id'], 1, draft['id']+str(i), {})
            for i in range(count)]
    return store, runs


def finish(manager, job):
    cfg, store, run = job
    store.update_run(run['id'], status='succeeded')
    manager.release(cfg)


def close(manager):
    manager.stop.set()
    for thread in manager.threads:
        thread.join(5)
        assert not thread.is_alive()
    if manager.lockfile:
        manager.lockfile.close()


def test_round_robin_does_not_drain_busy_tenant(tenants):
    queued(tenants.a, 5)
    queued(tenants.b, 3)
    manager = worker.QueueManager(tenants.base)
    seen = []
    for _ in range(6):
        job = manager.next_job()
        seen.append(job[0].user_id)
        assert job[1].get_run(job[2]['id'])['status'] == 'running'
        finish(manager, job)
    assert seen == ['alice', 'bob', 'alice', 'bob', 'alice', 'bob']
    assert manager.store.storage == tenants.base.storage_dir


def test_default_user_cap_and_global_two_reservations(tenants):
    queued(tenants.a, 3)
    queued(tenants.b, 3)
    queued(tenants.add('carol'), 2)
    manager = worker.QueueManager(tenants.base)
    first, second = manager.next_job(), manager.next_job()
    assert [first[0].user_id, second[0].user_id] == ['alice', 'bob']
    assert manager.next_job() is None
    finish(manager, first)
    third = manager.next_job()
    assert third[0].user_id == 'carol'
    assert manager.next_job() is None
    finish(manager, second)
    finish(manager, third)


def test_busy_user_is_skipped_and_limit_changes_are_live(tenants):
    queued(tenants.a, 4)
    manager = worker.QueueManager(tenants.base)
    first = manager.next_job()
    assert manager.next_job() is None
    tenants.rows[0][0]['max_concurrent'] = 2
    second = manager.next_job()
    assert second[0].user_id == 'alice'
    assert manager.next_job() is None
    tenants.rows[0][0]['max_concurrent'] = 1
    finish(manager, first)
    assert manager.next_job() is None
    finish(manager, second)
    assert manager.next_job()[0].user_id == 'alice'


def test_parallel_claims_cannot_overbook_a_user(tenants):
    queued(tenants.a, 5)
    queued(tenants.b, 5)
    manager = worker.QueueManager(tenants.base)
    barrier = threading.Barrier(8)

    def claim(_):
        barrier.wait(5)
        return manager.next_job()

    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [job for job in pool.map(claim, range(8)) if job]
    assert sorted(job[0].user_id for job in jobs) == ['alice', 'bob']
    assert sum(run['status'] == 'running'
               for cfg in (tenants.a, tenants.b)
               for run in ProductionStore(cfg.storage_dir).list_runs()) == 2


def test_disabled_user_finishes_inflight_but_gets_no_new_work(tenants):
    a_store, _ = queued(tenants.a, 3)
    queued(tenants.b, 2)
    manager = worker.QueueManager(tenants.base)
    first = manager.next_job()
    tenants.rows[0][0]['enabled'] = False
    finish(manager, first)
    for _ in range(2):
        job = manager.next_job()
        assert job[0].user_id == 'bob'
        finish(manager, job)
    assert manager.next_job() is None
    assert sum(run['status'] == 'queued' for run in a_store.list_runs()) == 2


def test_dynamic_tenant_recovers_before_claim_without_recovering_active(tenants):
    store, _ = queued(tenants.a, 2)
    manager = worker.QueueManager(tenants.base)
    active = manager.next_job()
    carol = tenants.add('carol')
    new_store, runs = queued(carol)
    new_store.update_run(runs[0]['id'], status='running', provider_task_id='remote')
    job = manager.next_job()
    assert job[0] == carol
    assert job[2]['id'] == runs[0]['id']
    assert job[2]['provider_task_id'] == 'remote'
    finish(manager, job)
    assert manager.next_job() is None
    assert store.get_run(active[2]['id'])['status'] == 'running'


def test_start_recovers_disabled_stores_and_all_auxiliary_states(tenants, monkeypatch):
    stores = []
    for cfg in (tenants.a, tenants.b):
        store, runs = queued(cfg, 2)
        store.update_run(runs[0]['id'], status='running', stage='submitting')
        store.update_run(runs[1]['id'], status='running', provider_task_id='remote')
        PortraitLibrary(cfg)
        with store.connection() as db:
            db.execute("INSERT INTO production_playbacks VALUES ('play','processing','',NULL,'','')")
            for ident, state in [('upload', 'uploading'), ('submit', 'submitting')]:
                db.execute('INSERT INTO portrait_photos VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (ident, 'account', 'person', ident, ident, state, None, '', 0, 0, 0))
        stores.append((store, runs))
    tenants.rows[1][0]['enabled'] = False
    for method in ('loop', 'portrait_loop', 'playback_loop'):
        monkeypatch.setattr(worker.QueueManager, method, lambda self: None)
    manager = worker.QueueManager(tenants.a)
    follower = worker.QueueManager(tenants.b)
    try:
        assert manager.start()
        assert not follower.start()
        assert manager.settings.storage_dir == tenants.base.storage_dir
        assert [t.name for t in manager.threads].count('production-worker') == 2
        assert [t.name for t in manager.threads].count('portrait-worker') == 1
        assert [t.name for t in manager.threads].count('playback-worker') == 1
        for store, runs in stores:
            assert store.get_run(runs[0]['id'])['status'] == 'needs_attention'
            assert store.get_run(runs[1]['id'])['status'] == 'queued'
            with store.connection() as db:
                assert db.execute('SELECT status FROM production_playbacks').fetchone()[0] == 'queued'
                assert dict(db.execute('SELECT id,status FROM portrait_photos')) == {
                    'upload': 'queued', 'submit': 'uncertain'}
    finally:
        close(manager)
        close(follower)


@pytest.mark.parametrize('operation', ['recover', 'claim_next'])
def test_one_broken_tenant_does_not_block_the_other(tenants, monkeypatch, operation):
    queued(tenants.a)
    queued(tenants.b)
    original = getattr(ProductionStore, operation)

    def broken(store):
        if store.storage == tenants.a.storage_dir:
            raise OSError('tenant database unavailable')
        return original(store)

    monkeypatch.setattr(ProductionStore, operation, broken)
    manager = worker.QueueManager(tenants.base)
    job = manager.next_job()
    assert job[0].user_id == 'bob'
    finish(manager, job)
    monkeypatch.setattr(ProductionStore, operation, original)
    assert manager.next_job()[0].user_id == 'alice'


@pytest.mark.parametrize('outcome', ['raise', 'failed', 'queued', 'succeeded'])
def test_worker_releases_reservation_on_every_execution_outcome(tenants, monkeypatch, outcome):
    queued(tenants.a, 3)
    manager = worker.QueueManager(tenants.base)
    seen = []

    def execute(cfg, store, run):
        seen.append((cfg, store.storage))
        manager.stop.set()
        if outcome == 'raise':
            raise OSError('unexpected execution failure')
        store.update_run(run['id'], status=outcome)

    monkeypatch.setattr(worker, 'execute_run', execute)
    manager.loop()
    assert seen == [(tenants.a, tenants.a.storage_dir)]
    manager.stop.clear()
    assert manager.next_job()[0].user_id == 'alice'


@pytest.mark.parametrize('kind', ['portrait', 'playback'])
def test_auxiliary_loops_rotate_and_isolate_failures(tenants, monkeypatch, kind):
    from app import playback
    manager = worker.QueueManager(tenants.base)
    seen = []
    tenants.rows[1][0]['enabled'] = False
    tenants.add('carol')

    def process(cfg):
        seen.append(cfg.user_id)
        if len(seen) == 4:
            manager.stop.set()
        if cfg.user_id == 'alice':
            raise OSError('broken tenant')

    # Skip only polling delays; execute the actual loop and tenant recovery.
    monkeypatch.setattr(manager.stop, 'wait', lambda _: manager.stop.is_set())
    if kind == 'playback':
        monkeypatch.setattr(playback, 'process_one', process)
    else:
        monkeypatch.setattr(PortraitLibrary, 'process_one', lambda library: process(library.settings))
    getattr(manager, kind+'_loop')()
    assert seen == ['alice', 'carol', 'alice', 'carol']


def test_tenant_wakes_coalesce_and_shutdown_uses_root(tenants, monkeypatch):
    for method in ('loop', 'portrait_loop', 'playback_loop'):
        monkeypatch.setattr(worker.QueueManager, method, lambda self: None)
    key = str(tenants.base.storage_dir.resolve())
    try:
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(worker.wake, [tenants.a, tenants.b, tenants.base]*2))
        assert key in worker._managers
        manager = worker._managers[key]
        assert manager.settings.storage_dir == tenants.base.storage_dir
        assert len(manager.threads) == 4
        assert str(tenants.a.storage_dir.resolve()) not in worker._managers
        assert str(tenants.b.storage_dir.resolve()) not in worker._managers
        worker.shutdown(tenants.b)
        assert manager.stop.is_set()
        assert manager.lockfile.closed
        assert key not in worker._managers
        worker.wake(tenants.a)
        assert worker._managers[key] is not manager
    finally:
        for cfg in (tenants.base, tenants.a, tenants.b):
            worker.shutdown(cfg)


def test_legacy_mode_keeps_two_concurrent_jobs(tenants, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    monkeypatch.setattr(tenants.tenancy, 'enabled', lambda: False)
    monkeypatch.setattr(tenants.tenancy, 'tenant_settings',
                        lambda base, include_disabled=False: [
                            (dict(id='legacy', enabled=True, max_concurrent=2, max_queued=10), base)])
    queued(tenants.base, 3)
    manager = worker.QueueManager(tenants.base)
    first, second = manager.next_job(), manager.next_job()
    assert first[1] is manager.store and second[1] is manager.store
    assert manager.next_job() is None
    finish(manager, first)
    assert manager.next_job() is not None


def test_real_workers_overlap_two_tenants_without_recovering_inflight(tenants, monkeypatch):
    stores = [queued(cfg, 2)[0] for cfg in (tenants.a, tenants.b)]
    manager = worker.QueueManager(tenants.base)
    gate = threading.Lock()
    both_entered = threading.Event()
    unblock = threading.Event()
    all_done = threading.Event()
    active = set()
    seen = []
    errors = []

    def execute(cfg, store, run):
        with gate:
            if cfg.user_id in active or len(active) >= 2:
                errors.append('overbooked')
            if store.storage != cfg.storage_dir:
                errors.append('wrong store')
            active.add(cfg.user_id)
            seen.append(cfg.user_id)
            if len(active) == 2:
                both_entered.set()
        if not unblock.wait(5):
            errors.append('timed out')
        store.update_run(run['id'], status='succeeded')
        with gate:
            active.remove(cfg.user_id)
            if len(seen) == 4 and not active:
                all_done.set()

    monkeypatch.setattr(worker, 'execute_run', execute)
    # Exercise recovery and rotation concurrently with running video jobs,
    # without invoking external portrait services or ffmpeg.
    from app import playback
    monkeypatch.setattr(playback, 'process_one', lambda cfg: None)
    monkeypatch.setattr(PortraitLibrary, 'process_one', lambda library: None)
    try:
        assert manager.start()
        assert both_entered.wait(5)
        assert sorted(seen) == ['alice', 'bob']
        assert manager._next_auxiliary_settings('portrait') is not None
        assert manager._next_auxiliary_settings('playback') is not None
        for store in stores:
            assert sorted(run['status'] for run in store.list_runs()) == ['queued', 'running']
        unblock.set()
        assert all_done.wait(5)
        assert sorted(seen) == ['alice', 'alice', 'bob', 'bob']
        assert errors == []
        assert all(run['status'] == 'succeeded' for store in stores for run in store.list_runs())
    finally:
        unblock.set()
        close(manager)


def test_partial_recovery_must_finish_before_any_tenant_work(tenants, monkeypatch):
    store, runs = queued(tenants.a)
    store.update_run(runs[0]['id'], status='running', provider_task_id='remote')
    queued(tenants.b)
    original = PortraitLibrary.recover

    def broken(library):
        if library.settings.storage_dir == tenants.a.storage_dir:
            raise OSError('portrait recovery unavailable')
        original(library)

    monkeypatch.setattr(PortraitLibrary, 'recover', broken)
    manager = worker.QueueManager(tenants.base)
    job = manager.next_job()
    assert job[0].user_id == 'bob'
    assert manager._next_auxiliary_settings('portrait').user_id == 'bob'
    assert manager._next_auxiliary_settings('playback').user_id == 'bob'
    finish(manager, job)
    monkeypatch.setattr(PortraitLibrary, 'recover', original)
    job = manager.next_job()
    assert job[0].user_id == 'alice'
    assert job[2]['id'] == runs[0]['id']
    # Refreshing both auxiliary cursors must not reset an active video claim.
    for kind in ('portrait', 'playback'):
        manager._next_auxiliary_settings(kind)
    assert store.get_run(runs[0]['id'])['status'] == 'running'
