"""Fifty global execution slots, with independently managed account quotas."""
import threading

import pytest

from app import production_worker as worker
from tests.test_tenant_scheduler import tenants, queued, finish, close


@pytest.mark.parametrize('configured', [None, '50', '100', 'invalid'])
def test_global_limit_dispatches_fifty_and_holds_the_fifty_first(tenants, monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv('APP_VIDEO_WORKERS', raising=False)
    else:
        monkeypatch.setenv('APP_VIDEO_WORKERS', configured)
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL', '100')
    tenants.rows[0][0]['max_concurrent'] = 60
    tenants.rows[1][0]['enabled'] = False
    queued(tenants.a, 51)
    manager = worker.QueueManager(tenants.base)
    jobs = [manager.next_job() for _ in range(50)]
    assert all(jobs)
    assert len({job[2]['id'] for job in jobs}) == 50
    assert manager.next_job() is None
    finish(manager, jobs[0])
    assert manager.next_job() is not None
    assert manager.next_job() is None


def test_account_cap_eight_and_admin_changes_remain_effective(tenants, monkeypatch):
    monkeypatch.setenv('APP_VIDEO_WORKERS', '50')
    tenants.rows[0][0]['max_concurrent'] = 8
    tenants.rows[1][0]['enabled'] = False
    queued(tenants.a, 15)
    manager = worker.QueueManager(tenants.base)
    jobs = [manager.next_job() for _ in range(8)]
    assert all(jobs)
    assert manager.next_job() is None
    tenants.rows[0][0]['max_concurrent'] = 10
    jobs.extend([manager.next_job(), manager.next_job()])
    assert all(jobs) and manager.next_job() is None
    tenants.rows[0][0]['max_concurrent'] = 4
    for job in jobs[:6]:
        finish(manager, job)
    assert manager.next_job() is None
    finish(manager, jobs[6])
    assert manager.next_job() is not None


def test_worker_pool_reaches_fifty_without_duplicate_claims(tenants, monkeypatch):
    monkeypatch.delenv('APP_VIDEO_WORKERS', raising=False)
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL', '100')
    tenants.rows[0][0]['max_concurrent'] = 60
    tenants.rows[1][0]['enabled'] = False
    queued(tenants.a, 51)
    release = threading.Event()
    entered = threading.Event()
    lock = threading.Lock()
    seen = []

    def execute(settings, store, run):
        with lock:
            seen.append(run['id'])
            if len(seen) == 50:
                entered.set()
        assert release.wait(25)
        store.update_run(run['id'], status='succeeded')

    monkeypatch.setattr(worker, 'execute_run', execute)
    manager = worker.QueueManager(tenants.base)
    try:
        assert manager.start()
        assert entered.wait(20)
        assert len(seen) == len(set(seen)) == 50
    finally:
        release.set()
        close(manager)
