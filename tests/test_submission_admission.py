"""Admission invariants against real SQLite and authenticated application routes."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone, timedelta
import threading
import time

import pytest
from app.production_store import ProductionStore
from app.config import settings


def test_global_waiting_limit_is_atomic_across_tenants(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL', '3')
    stores=[ProductionStore(tmp_path/'users'/str(i),queue_root=tmp_path) for i in range(6)]
    drafts=[s.create_draft({}) for s in stores]
    def submit(i):
        from app.queue_admission import CapacityError
        try:return stores[i].create_run(drafts[i]['id'],1,str(i),{})
        except CapacityError:return None
    with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(submit,range(6)))
    assert sum(r is not None for r in results)==3
    accepted=next(i for i,r in enumerate(results) if r)
    assert stores[accepted].create_run(drafts[accepted]['id'],1,str(accepted),{})['id']==results[accepted]['id']
    stores[accepted].cancel_run(results[accepted]['id'])
    rejected=next(i for i,r in enumerate(results) if r is None)
    assert submit(rejected)['status']=='queued'


def test_reservations_count_before_validation_and_release_on_error(tmp_path, monkeypatch):
    from app.queue_admission import reserve,CapacityError
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL','1')
    one=replace(settings,storage_dir=tmp_path/'users'/'a',config_root=tmp_path)
    two=replace(settings,storage_dir=tmp_path/'users'/'b',config_root=tmp_path)
    with pytest.raises(ValueError,match='invalid input'):
        with reserve(one,'first',max_queued=2):
            with pytest.raises(CapacityError):
                with reserve(two,'second',max_queued=2):pass
            raise ValueError('invalid input')
    with reserve(two,'second',max_queued=2):pass


def test_reservation_is_not_double_counted_at_commit(tmp_path,monkeypatch):
    from app.queue_admission import reserve
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL','1')
    cfg=replace(settings,storage_dir=tmp_path/'users'/'a',config_root=tmp_path)
    store=ProductionStore(cfg.storage_dir,queue_root=tmp_path);draft=store.create_draft({})
    with reserve(cfg,'one',max_queued=1):
        run=store.create_run(draft['id'],1,'one',{},max_queued=1)
    assert run['status']=='queued'
    assert store.claim_next()['id']==run['id']
    with reserve(cfg,'two',max_queued=1):
        assert store.create_run(draft['id'],1,'two',{},max_queued=1)['status']=='queued'


def test_queue_expiry_preserves_remote_tasks_and_releases_capacity(tmp_path,monkeypatch):
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL','3');monkeypatch.setenv('APP_QUEUE_TIMEOUT_SECONDS','600')
    store=ProductionStore(tmp_path,queue_root=tmp_path);draft=store.create_draft({})
    fresh=store.create_run(draft['id'],1,'old',{})
    remote=store.create_run(draft['id'],1,'remote',{})
    store.update_run(remote['id'],provider_task_id='paid-existing')
    continuation=store.create_run(draft['id'],1,'continued',{})
    store.update_continuation(continuation['id'],base_ready=True)
    old=(datetime.now(timezone.utc)-timedelta(seconds=601)).isoformat()
    with store.connection() as db:db.execute('UPDATE production_run_timing SET state_since=?',(old,))
    assert store.expire_queued()==1
    assert store.get_run(fresh['id'])['error_kind']=='queue_timeout'
    assert store.get_run(fresh['id'])['status']=='failed'
    assert store.get_run(remote['id'])['status']=='queued'
    assert store.get_run(continuation['id'])['status']=='queued'
    assert store.create_run(draft['id'],1,'replacement',{})['status']=='queued'


def test_resume_and_preparation_retry_cannot_bypass_global_limit(tmp_path,monkeypatch):
    from app.queue_admission import CapacityError
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL','1')
    store=ProductionStore(tmp_path,queue_root=tmp_path);draft=store.create_draft({})
    remote=store.create_run(draft['id'],1,'remote',{})
    store.update_run(remote['id'],status='needs_attention',provider_task_id='paid-existing')
    prep=store.create_run(draft['id'],1,'prep',{'person_preparation':{'account':'local','input_digest':'test'}})
    store.update_run(prep['id'],status='failed')
    store.update_preparation(prep['id'],retryable=True)
    store.create_run(draft['id'],1,'full',{})
    with pytest.raises(CapacityError):store.resume_run(remote['id'])
    with pytest.raises(CapacityError):store.retry_preparation(prep['id'])


def test_full_queue_rejects_before_expensive_validation(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app import main,production_router,production_worker
    monkeypatch.setenv('APP_AUTH_ENABLED','false');monkeypatch.setenv('APP_MAX_QUEUED_TOTAL','1')
    monkeypatch.setattr(main,'settings',replace(settings,storage_dir=tmp_path,config_root=None,seedance_mode='mock'))
    monkeypatch.setattr(production_worker,'wake',lambda *_:None)
    from tests.test_production_api import complete_draft
    client=TestClient(main.app);draft=complete_draft(client)
    body={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'one'}
    assert client.post('/api/production/runs',json=body).status_code==200
    def expensive(*a,**kw):pytest.fail('full queue must reject before resolving generation config')
    monkeypatch.setattr(production_router,'resolve_task_config',expensive)
    response=client.post('/api/production/runs',json={**body,'idempotency_key':'two'})
    assert response.status_code==429,response.text
    assert response.headers['retry-after']
    assert client.post('/api/production/runs',json=body).status_code==200


def test_dead_validation_reservation_is_reclaimed(tmp_path,monkeypatch):
    from app.queue_admission import reserve,transaction
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL','1')
    cfg=replace(settings,storage_dir=tmp_path,config_root=tmp_path)
    # Reproduce durable state after a process exits before finally cleanup:
    # a reservation exists, but no OS handle owns its lease.
    with transaction(tmp_path) as db:
        db.execute('INSERT INTO reservations VALUES (?,?,?)',('a'*32,str(tmp_path.resolve()),'crashed'))
    with reserve(cfg,'after-restart',max_queued=1):pass
    with transaction(tmp_path) as db:assert db.execute('SELECT COUNT(*) FROM reservations').fetchone()[0]==0


def test_ingress_gate_rejects_without_waiting_for_slow_validation(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from app import main,production_router,production_worker
    from tests.test_production_api import complete_draft
    monkeypatch.setenv('APP_AUTH_ENABLED','false');monkeypatch.setenv('APP_SUBMISSION_WORKERS','1')
    monkeypatch.setattr(main,'settings',replace(settings,storage_dir=tmp_path,config_root=None,seedance_mode='mock'))
    monkeypatch.setattr(production_worker,'wake',lambda *_:None)
    client=TestClient(main.app);draft=complete_draft(client)
    body={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'slow'}
    entered=threading.Event();release=threading.Event()
    original=production_router.resolve_task_config
    def slow(*args,**kwargs):
        entered.set();assert release.wait(5)
        return original(*args,**kwargs)
    monkeypatch.setattr(production_router,'resolve_task_config',slow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(client.post,'/api/production/runs',json=body)
        try:
            assert entered.wait(3)
            t=time.monotonic()
            rejected=client.post('/api/production/runs',json={**body,'idempotency_key':'next'})
            assert rejected.status_code==429,rejected.text
            assert time.monotonic()-t<1
        finally:release.set()
        assert first.result().status_code==200


def test_worker_expiry_still_runs_when_video_slots_are_full(tmp_path,monkeypatch):
    from app import production_worker as worker
    monkeypatch.setenv('APP_AUTH_ENABLED','false');monkeypatch.setenv('APP_QUEUE_TIMEOUT_SECONDS','1')
    cfg=replace(settings,storage_dir=tmp_path,config_root=None)
    store=ProductionStore(tmp_path);draft=store.create_draft({});run=store.create_run(draft['id'],1,'old',{})
    with store.connection() as db:
        db.execute('UPDATE production_run_timing SET state_since=?',((datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat(),))
    manager=worker.QueueManager(cfg)
    manager._active[str(tmp_path.resolve())]=manager.worker_count
    assert manager.next_job() is None
    manager._next_auxiliary_settings('playback')
    assert store.get_run(run['id'])['error_kind']=='queue_timeout'


def test_tenant_schema_initialization_does_not_break_other_admissions(tmp_path,monkeypatch):
    import sqlite3
    from contextlib import closing
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL','1')
    starting=tmp_path/'users'/'starting';starting.mkdir(parents=True)
    with closing(sqlite3.connect(starting/'production.db')) as db:
        db.execute('CREATE TABLE production_assets (id TEXT)')
    store=ProductionStore(tmp_path/'users'/'ready',queue_root=tmp_path)
    draft=store.create_draft({})
    assert store.create_run(draft['id'],1,'one',{})['status']=='queued'


def test_ingress_rejection_does_not_need_default_threadpool(tmp_path,monkeypatch):
    import asyncio
    import anyio
    import httpx
    from fastapi import FastAPI,APIRouter
    from app.queue_admission import route_class
    monkeypatch.setenv('APP_SUBMISSION_WORKERS','1')
    cfg=replace(settings,storage_dir=tmp_path,config_root=None)
    app=FastAPI();router=APIRouter(route_class=route_class(lambda:cfg))
    entered=threading.Event();release=threading.Event()
    @router.post('/runs')
    def slow(payload:dict):
        entered.set();assert release.wait(5)
        return {'ok':True}
    app.include_router(router)
    async def scenario():
        limiter=anyio.to_thread.current_default_thread_limiter()
        original=limiter.total_tokens;limiter.total_tokens=1
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
                first=asyncio.create_task(client.post('/runs',json={'idempotency_key':'one'}))
                try:
                    for _ in range(100):
                        if entered.is_set():break
                        await asyncio.sleep(.01)
                    assert entered.is_set()
                    result=await asyncio.wait_for(client.post('/runs',json={'idempotency_key':'two'}),1)
                    assert result.status_code==429
                finally:
                    release.set()
                    assert (await first).status_code==200
        finally:limiter.total_tokens=original
    asyncio.run(scenario())


def test_admission_is_atomic_between_processes(tmp_path,monkeypatch):
    import subprocess
    import sys
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL','3')
    script='''
import sys,time
from pathlib import Path
from app.production_store import ProductionStore
from app.queue_admission import CapacityError
root=Path(sys.argv[1]);ident=sys.argv[2]
store=ProductionStore(root/'users'/ident,queue_root=root)
draft=store.create_draft({})
(root/('ready-'+ident)).touch()
deadline=time.monotonic()+20
while not (root/'go').exists():
    assert time.monotonic()<deadline
    time.sleep(.01)
try:
    store.create_run(draft['id'],1,ident,{})
    print('accepted')
except CapacityError:print('rejected')
'''
    processes=[subprocess.Popen([sys.executable,'-c',script,str(tmp_path),str(i)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for i in range(6)]
    try:
        deadline=time.monotonic()+20
        while len(list(tmp_path.glob('ready-*')))<6:
            assert time.monotonic()<deadline
            time.sleep(.02)
        (tmp_path/'go').touch()
        outputs=[]
        for proc in processes:
            out,err=proc.communicate(timeout=20)
            assert proc.returncode==0,err
            outputs.append(out.strip())
        assert outputs.count('accepted')==3,outputs
        assert outputs.count('rejected')==3,outputs
    finally:
        for proc in processes:
            if proc.poll() is None:proc.kill()
            proc.wait()


def test_process_death_releases_live_reservation(tmp_path,monkeypatch):
    import subprocess
    import sys
    from app.queue_admission import reserve,CapacityError
    monkeypatch.setenv('APP_MAX_QUEUED_TOTAL','1')
    script='''
import sys,time
from pathlib import Path
from types import SimpleNamespace
from app.queue_admission import reserve
root=Path(sys.argv[1]);cfg=SimpleNamespace(storage_dir=root,config_root=root)
with reserve(cfg,'child'):
    (root/'ready').touch()
    time.sleep(30)
'''
    proc=subprocess.Popen([sys.executable,'-c',script,str(tmp_path)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    cfg=replace(settings,storage_dir=tmp_path,config_root=tmp_path)
    try:
        deadline=time.monotonic()+20
        while not (tmp_path/'ready').exists():
            assert proc.poll() is None,proc.communicate()
            assert time.monotonic()<deadline
            time.sleep(.02)
        with pytest.raises(CapacityError):
            with reserve(cfg,'parent'):pass
        proc.kill();proc.wait(5)
        with reserve(cfg,'after-crash'):pass
    finally:
        if proc.poll() is None:proc.kill()
        proc.wait()
def test_offline_benchmark_isolates_workflow_storage(tmp_path):
    """Starting the local benchmark must not recover a real workflow database."""
    import subprocess
    import sys
    from pathlib import Path
    script = '''
import os,sys
from pathlib import Path
from tests.bench_queue_admission import offline
root=Path(sys.argv[1]).resolve()
os.environ['WORKFLOW_DB']=str(root.parent/'outside.db')
os.environ['WORKFLOW_STORAGE']=str(root.parent/'outside-files')
offline(root)
assert Path(os.environ['WORKFLOW_DB']).resolve().is_relative_to(root)
assert Path(os.environ['WORKFLOW_STORAGE']).resolve().is_relative_to(root)
'''
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path/'benchmark')],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
