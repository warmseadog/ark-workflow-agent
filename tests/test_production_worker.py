from dataclasses import asdict, replace
from pathlib import Path
import hashlib
import pytest
from app.config import settings
from app.generation_settings import GenerationConfig
from app.storage_settings import StorageConfig
from app.production_store import ProductionStore, Conflict
from app import production_worker as worker
from app.video_provider import ProviderError


@pytest.fixture
def setup(tmp_path,monkeypatch):
    cfg=replace(settings,storage_dir=tmp_path)
    store=ProductionStore(tmp_path)
    for ident,kind in [('source','video'),('face','face'),('clothes','clothing')]:
        path=tmp_path/(ident+'.mp4' if kind=='video' else ident+'.png')
        path.write_bytes(ident.encode())
        store.add_asset(ident,path.name,kind,path,path.stat().st_size,'video/mp4' if kind=='video' else 'image/png',hashlib.sha256(path.read_bytes()).hexdigest())
    draft=store.create_draft({'source_asset_id':'source','face_asset_ids':['face'],'clothing_asset_ids':['clothes'],'prompt':'original'})
    private={'generation':asdict(GenerationConfig()),'storage':asdict(StorageConfig())}
    monkeypatch.setattr(worker,'run_deface',lambda src,dst,*args:dst.write_bytes(b'redacted'))
    return cfg,store,draft,private


def test_whole_pipeline_runs_without_browser_and_reuses_redaction(setup,monkeypatch):
    cfg,store,draft,private=setup
    calls=[]
    monkeypatch.setattr(worker,'run_deface',lambda src,dst,*args:calls.append(src) or dst.write_bytes(b'redacted'))
    for key in ['a','b']:
        run=store.create_run(draft['id'],1,key,private)
        worker.execute_run(cfg,store,store.claim_next())
        assert store.get_run(run['id'])['status']=='succeeded'
        assert (cfg.storage_dir/'outputs'/(run['id']+'.mp4')).read_bytes()==b'redacted'
    assert len(calls)==1


def test_provider_id_persisted_before_query_failure_and_resume_uses_same_id(setup,monkeypatch):
    cfg,store,draft,private=setup
    submitted=[]
    class Provider:
        def __init__(self,*args): pass
        def _safe(self,value): return str(value)
        def generate(self,video,faces,clothes,prompt,output,**kw):
            if not kw['resume_task_id']:
                submitted.append('one')
                kw['on_submitted']('cloud-1')
                raise ProviderError('query offline',error_kind='query_unavailable',provider_task_id='cloud-1')
            assert kw['resume_task_id']=='cloud-1'
            output.write_bytes(b'finished')
    monkeypatch.setattr(worker,'VideoProvider',Provider)
    run=store.create_run(draft['id'],1,'a',private)
    worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='needs_attention'
    store.resume_run(run['id'])
    worker.execute_run(cfg,store,store.claim_next())
    assert submitted==['one']
    assert store.get_run(run['id'])['status']=='succeeded'


def test_uncertain_submit_is_not_automatically_requeued(setup,monkeypatch):
    cfg,store,draft,private=setup
    class Provider:
        def __init__(self,*args): pass
        def _safe(self,value): return str(value)
        def generate(self,*args,**kw):
            raise ProviderError('response lost',submission_uncertain=True,error_kind='submission_uncertain')
    monkeypatch.setattr(worker,'VideoProvider',Provider)
    run=store.create_run(draft['id'],1,'a',private)
    worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='needs_attention'
    store.recover()
    assert store.claim_next() is None
    with pytest.raises(Conflict): store.resume_run(run['id'])


def test_callback_write_failure_after_id_keeps_task_resumable(setup,monkeypatch):
    cfg,store,draft,private=setup
    class Provider:
        def __init__(self,*args): pass
        def _safe(self,value): return str(value)
        def generate(self,*args,**kw):
            kw['on_submitted']('cloud-1')
            raise OSError('result metadata save failed')
    monkeypatch.setattr(worker,'VideoProvider',Provider)
    run=store.create_run(draft['id'],1,'a',private)
    worker.execute_run(cfg,store,store.claim_next())
    value=store.get_run(run['id'])
    assert value['status']=='needs_attention'
    assert value['provider_task_id']=='cloud-1'


def test_resumed_cloud_task_cannot_be_cancelled_as_local_queue(setup):
    cfg,store,draft,private=setup
    run=store.create_run(draft['id'],1,'a',private)
    store.update_run(run['id'],provider_task_id='cloud-1')
    with pytest.raises(Conflict):store.cancel_run(run['id'])


def test_queue_has_two_workers_and_only_one_process_leader(setup, monkeypatch):
    import threading
    cfg, store, draft, private = setup
    for key in ('a', 'b', 'c'):
        store.create_run(draft['id'], 1, key, private)
    entered = threading.Event()
    release = threading.Event()
    all_done = threading.Event()
    gate = threading.Lock()
    seen = []
    active = 0
    peak = 0
    def execute(settings, db, run):
        nonlocal active, peak
        with gate:
            active += 1
            peak = max(peak, active)
            seen.append(run['id'])
            if active == 2: entered.set()
        assert release.wait(5)
        db.update_run(run['id'], status='succeeded')
        with gate:
            active -= 1
            if len(seen) == 3 and active == 0: all_done.set()
    monkeypatch.setattr(worker, 'execute_run', execute)
    leader = worker.QueueManager(cfg)
    follower = worker.QueueManager(cfg)
    try:
        assert leader.start()
        assert not follower.start()
        assert entered.wait(5)
        assert len(seen) == 2
        assert sum(x['status'] == 'queued' for x in store.list_runs()) == 1
        release.set()
        assert all_done.wait(5)
        assert len(set(seen)) == 3
        assert peak == 2
    finally:
        release.set()
        leader.stop.set()
        for thread in leader.threads: thread.join(5)
        if leader.lockfile: leader.lockfile.close()


@pytest.mark.parametrize('enabled',[True,False])
def test_optional_reference_snapshot_reaches_provider_only_when_enabled(setup,monkeypatch,enabled):
    import base64
    cfg,store,draft,private=setup
    for kind in ('hairstyle','scene'):
        path=cfg.storage_dir/(kind+'.png');path.write_bytes(kind.encode())
        store.add_asset(kind,path.name,kind,path,path.stat().st_size,'image/png',hashlib.sha256(path.read_bytes()).hexdigest())
    draft=store.save_draft(draft['id'],draft['revision'],{'hairstyle_asset_ids':['hairstyle'],'scene_asset_ids':['scene'],
        'hairstyle_enabled':enabled,'scene_enabled':enabled,'scene_description':'frozen room'})
    private['generation']=asdict(GenerationConfig(mode='http',provider='ark',protocol='ark',api_key='fixture'))
    monkeypatch.setattr(worker,'publish_video',lambda *args:'https://example.test/redacted.mp4')
    sent=[]
    monkeypatch.setattr(worker.VideoProvider,'_request',lambda self,method,path,**kw:sent.append(kw['json']) or {'id':'remote'})
    monkeypatch.setattr(worker.VideoProvider,'_poll',lambda self,*args:{})
    run=store.create_run(draft['id'],draft['revision'],'extra-worker',private)
    store.save_draft(draft['id'],draft['revision'],{'scene_enabled':not enabled,'scene_description':'edited later'})
    worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='succeeded'
    refs=[base64.b64decode(x['image_url']['url'].split(',')[1]) for x in sent[0]['content'] if x['type']=='image_url']
    assert refs==([b'face',b'clothes',b'hairstyle',b'scene'] if enabled else [b'face',b'clothes'])
    text=sent[0]['content'][0]['text']
    assert ('frozen room' in text)==enabled
    assert 'edited later' not in text
