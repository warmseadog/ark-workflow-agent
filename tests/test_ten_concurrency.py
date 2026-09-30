import threading

from app import production_worker as worker
from tests.test_tenant_scheduler import tenants, queued, finish, close


def test_ten_global_slots_and_account_limit(tenants, monkeypatch):
    monkeypatch.delenv('APP_VIDEO_WORKERS', raising=False)
    tenants.rows[0][0]['max_concurrent']=10
    tenants.rows[1][0]['enabled']=False
    queued(tenants.a,11)
    manager=worker.QueueManager(tenants.base)
    jobs=[manager.next_job() for _ in range(10)]
    assert all(jobs)
    assert manager.next_job() is None
    finish(manager,jobs[0])
    assert manager.next_job() is not None


def test_worker_pool_reaches_ten_without_duplicate_claims(tenants,monkeypatch):
    monkeypatch.delenv('APP_VIDEO_WORKERS',raising=False)
    tenants.rows[0][0]['max_concurrent']=10;tenants.rows[1][0]['enabled']=False
    queued(tenants.a,11)
    release=threading.Event();entered=threading.Event();lock=threading.Lock();seen=[]
    def execute(settings,store,run):
        with lock:
            seen.append(run['id'])
            if len(seen)==10:entered.set()
        assert release.wait(10)
        store.update_run(run['id'],status='succeeded')
    monkeypatch.setattr(worker,'execute_run',execute)
    manager=worker.QueueManager(tenants.base)
    try:
        assert manager.start()
        assert entered.wait(6)
        assert len(seen)==len(set(seen))==10
        assert len([t for t in manager.threads if t.name=='production-worker'])==10
    finally:
        release.set();close(manager)
