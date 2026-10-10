"""Every new masking attempt uses the source, never a prior masked result."""
from pathlib import Path
from dataclasses import replace

from tests.test_production_worker import setup
from tests.test_tenant_preview import preview, asset, post, terminal
from tests.test_hairstyle_mask import asset_at
from app import production_worker as worker, hairstyle_mask as hair
from app.config import settings


def test_legacy_candidate_deduplication_only_matches_inflight_jobs():
    from app.jobs import JobStore
    store=JobStore();job=store.create(candidate_id='candidate')
    assert store.find_active_for_candidate('candidate').id==job.id
    store.update(job.id,status='defaced')
    assert store.find_active_for_candidate('candidate') is None


def test_legacy_generation_redacts_again_from_original(tmp_path,monkeypatch):
    from app import jobs
    cfg=replace(settings,storage_dir=tmp_path)
    store=jobs.JobStore();monkeypatch.setattr(jobs,'store',store)
    job=store.create();source=tmp_path/'source.mp4';source.write_bytes(b'original')
    calls=[]
    def mask(src,dst,*args):
        calls.append(src.read_bytes());dst.write_bytes(('mask-'+str(len(calls))).encode())
    monkeypatch.setattr(jobs,'run_deface',mask)
    class Provider:
        def __init__(self,*args): pass
        def generate(self,video,face,clothes,prompt,output):
            assert video.read_bytes()==b'mask-2'
            output.write_bytes(b'done');return {'provider':'mock','message':'done'}
    monkeypatch.setattr(jobs,'SeedanceClient',Provider)
    jobs.run_deface_pipeline(job.id,cfg,source,None)
    jobs.run_generation_pipeline(job.id,cfg,None,None,'test')
    assert store.get(job.id).status=='succeeded'
    assert calls==[b'original',b'original']


def test_workflow_new_generation_redacts_original_instead_of_prior_artifact(tmp_path,monkeypatch):
    from app import workflow_worker as module
    from threading import Event
    source=tmp_path/'original.mp4';source.write_bytes(b'original')
    old=tmp_path/'old.mp4';old.write_bytes(b'old-mask')
    class Store:
        def get_stage_task(self,ident):
            return {'id':'redaction','project_id':'project','status':'succeeded',
                    'input':{'source_asset_id':'source','options':{}},
                    'output':{'artifact':{'path':str(old)}}}
        def list_assets(self,project,**kwargs):
            return [{'id':ident,'uri':str(source),'kind':'upload'} for ident in ('source','face','clothes')]
    seen=[]
    monkeypatch.setattr(module,'run_deface',lambda src,dst,*args:seen.append(src.read_bytes()) or dst.write_bytes(b'new-mask'))
    def generate(store,ident,project,video,*args,**kwargs):
        assert Path(video).read_bytes()==b'new-mask'
    monkeypatch.setattr(module,'run_generation_task',generate)
    task={'id':'generation','project_id':'project','stage':'generation','provider_state':{},'execution_token':'one',
          'input':{'redaction_task_id':'redaction','face_asset_id':'face','garment_asset_id':'clothes'}}
    module._execute_record(Store(),task,replace(settings,storage_dir=tmp_path),Event())
    assert seen==[b'original']


def test_identical_successful_runs_redact_every_time_without_cache(setup, monkeypatch):
    cfg, store, draft, private = setup
    calls = []
    def redact(src, dst, *args):
        calls.append(src.read_bytes())
        dst.write_bytes(('mask-'+str(len(calls))).encode())
    monkeypatch.setattr(worker, 'run_deface', redact)
    for index in range(2):
        run = store.create_run(draft['id'], 1, str(index), private)
        worker.execute_run(cfg, store, store.claim_next())
        assert store.get_run(run['id'])['status'] == 'succeeded'
        assert (cfg.storage_dir/'work'/run['id']/'defaced.mp4').read_bytes() == ('mask-'+str(index+1)).encode()
    assert calls == [b'source', b'source']
    assert not list((cfg.storage_dir/'cache'/'redacted').glob('*'))
    assert not list((cfg.storage_dir/'work').glob('*/redaction-cache*'))


def test_preexisting_same_run_file_is_never_a_shortcut(setup, monkeypatch):
    cfg, store, draft, private = setup
    run = store.create_run(draft['id'], 1, 'retry', private)
    folder = cfg.storage_dir/'work'/run['id']; folder.mkdir(parents=True)
    (folder/'defaced.mp4').write_bytes(b'old-result')
    seen = []
    def redact(src, dst, *args):
        seen.append(src.read_bytes()); dst.write_bytes(b'fresh-result')
    monkeypatch.setattr(worker, 'run_deface', redact)
    worker.execute_run(cfg, store, store.claim_next())
    assert store.get_run(run['id'])['status'] == 'succeeded'
    assert seen == [b'source']
    assert (folder/'defaced.mp4').read_bytes() == b'fresh-result'


def test_action_video_adaptation_is_scoped_to_each_attempt(setup, monkeypatch):
    from app import person_video, source_clip, reference_adaptation
    cfg, store, draft, private = setup
    private['generation'].update(mode='http', protocol='ark', model='doubao-seedance-2-0-260128', api_key='fake')
    monkeypatch.setattr(source_clip, 'validate_source', lambda *a,**kw: {})
    directories = []
    def adapt(source, directory, **kw):
        directories.append(directory)
        return source, None
    monkeypatch.setattr(reference_adaptation, 'adapt_video', adapt)
    monkeypatch.setattr(worker, 'publish_video', lambda *a: 'https://test.example/masked.mp4')
    monkeypatch.setattr(worker.VideoProvider, 'generate', lambda self,v,f,c,p,o,**kw: o.write_bytes(b'done'))
    for i in range(2):
        run=store.create_run(draft['id'],1,str(i),private)
        worker.execute_run(cfg,store,store.claim_next())
        assert store.get_run(run['id'])['status']=='succeeded'
    assert directories[0] != directories[1]
    assert all(path.is_relative_to(cfg.storage_dir/'work') for path in directories)


def test_repeated_completed_previews_are_processed_independently(preview):
    client, tenants, _, _, calls, *_ = preview
    asset(tenants['1'])
    first = terminal(client, post(client).json()['id'])
    second = terminal(client, post(client).json()['id'])
    assert first['status'] == second['status'] == 'defaced'
    assert first['id'] != second['id']
    assert len(calls) == 2
    assert not list((tenants['1'].storage_dir/'cache'/'redacted').glob('*'))


def test_identical_hair_inputs_are_processed_with_distinct_outputs(tmp_path, monkeypatch):
    item, _ = asset_at(tmp_path)
    cfg = replace(settings, storage_dir=tmp_path)
    seen = []
    monkeypatch.setattr(hair, '_detect_faces', lambda *args: seen.append(1) or [])
    first = hair.process_hairstyle(cfg, item)
    second = hair.process_hairstyle(cfg, item)
    assert len(seen) == 2
    assert first['path'] != second['path']
    assert first['path'].is_relative_to(tmp_path/'work')
    assert not list((tmp_path/'cache'/'hairstyle-mask').glob('*'))
