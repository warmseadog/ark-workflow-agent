from dataclasses import asdict
import pytest
from tests.test_production_worker import setup
from app import production_worker as worker, continuation, continuation_media, continuation_llm
from app.continuation_settings import ContinuationConfig
from app.video_provider import ProviderError
from app.production_store import Conflict


def configure(setup,monkeypatch):
    cfg,store,draft,private=setup
    private['continuation']={'config':asdict(ContinuationConfig(api_key='llm-secret')),'target_duration':9,'source_duration':8,'skill_version':'version'}
    calls=[]
    monkeypatch.setattr('app.person_video.probe',lambda *a:{'duration':8,'fps':24})
    monkeypatch.setattr(continuation_media,'ending_frames',lambda *a:[{'timestamp':7.9,'path':cfg.storage_dir/'face.png'}])
    monkeypatch.setattr(continuation_media,'finalize',lambda src,out,*a:out.write_bytes(src.read_bytes()))
    monkeypatch.setattr(continuation_llm,'plan_continuation',lambda config,**kw:calls.append(('llm',kw)) or {'continuation_prompt':'自由向前走','ending_state':'站立','invariants':['人物一致'],'beats':[]})
    monkeypatch.setattr(continuation,'publish_base',lambda *a:'https://assets/base')
    return cfg,store,draft,private,calls


def test_two_stages_preserve_prompt_and_resume_only_extension(setup,monkeypatch):
    cfg,store,draft,private,calls=configure(setup,monkeypatch)
    class Provider:
        def __init__(self,*a):pass
        def _safe(self,x):return str(x)
        def generate(self,video,faces,clothes,prompt,output,**kw):
            calls.append(('base',prompt));kw['on_submitted']('base-1');output.write_bytes(b'base')
        def extend(self,video,prompt,output,**kw):
            calls.append(('extend',prompt))
            if not kw.get('resume_task_id'):
                kw['on_submitted']('extension-1')
                raise ProviderError('network',error_kind='query_unavailable')
            assert kw['resume_task_id']=='extension-1'
            output.write_bytes(b'extended')
    monkeypatch.setattr(worker,'VideoProvider',Provider)
    monkeypatch.setattr('app.video_provider.VideoProvider',Provider)
    run=store.create_run(draft['id'],1,'x',private)
    worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='needs_attention'
    assert not (cfg.storage_dir/'outputs'/(run['id']+'.mp4')).exists()
    assert store.get_continuation(run['id'])['base_ready']
    store.resume_run(run['id']);worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='succeeded'
    assert [x for x in calls if x[0]=='base']==[('base','original')]
    assert len([x for x in calls if x[0]=='llm'])==1
    assert store.get_run(run['id'])['snapshot']['prompt']=='original'
    assert store.get_run(run['id'])['continuation']['plan']['continuation_prompt']=='自由向前走'


def test_authorization_rechecked_after_base_before_llm_or_extension(setup,monkeypatch):
    cfg,store,draft,private,calls=configure(setup,monkeypatch)
    class Provider:
        def __init__(self,*a):pass
        def _safe(self,x):return str(x)
        def generate(self,video,faces,clothes,prompt,output,**kw):
            output.write_bytes(b'base')
            monkeypatch.setattr(worker,'authorize_run_inputs',lambda *a:(_ for _ in ()).throw(ValueError('授权已撤销')))
        def extend(self,*a,**kw):pytest.fail('revoked input sent')
    monkeypatch.setattr(worker,'VideoProvider',Provider)
    monkeypatch.setattr('app.video_provider.VideoProvider',Provider)
    run=store.create_run(draft['id'],1,'x',private)
    worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='needs_attention'
    assert not calls


def test_extension_uncertain_is_not_resubmittable(setup,monkeypatch):
    cfg,store,draft,private,calls=configure(setup,monkeypatch)
    class Provider:
        def __init__(self,*a):pass
        def _safe(self,x):return str(x)
        def generate(self,video,faces,clothes,prompt,output,**kw):
            kw['on_submitted']('base-1');output.write_bytes(b'base')
        def extend(self,*a,**kw):raise ProviderError('lost',submission_uncertain=True)
    monkeypatch.setattr(worker,'VideoProvider',Provider)
    monkeypatch.setattr('app.video_provider.VideoProvider',Provider)
    run=store.create_run(draft['id'],1,'x',private)
    worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['error_kind']=='submission_uncertain'
    with pytest.raises(Conflict):store.resume_run(run['id'])
