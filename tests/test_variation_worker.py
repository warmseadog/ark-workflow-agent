from dataclasses import asdict
from app import production_worker as worker, variation
from app.variation_settings import VariationConfig
from app.video_provider import ProviderError
from tests.test_production_worker import setup
from tests.test_variation import example
from tests.test_video_provider import media, response
from app.video_provider import VideoProvider
from app.generation_settings import GenerationConfig
import pytest


def intent():
    c=VariationConfig(api_key='secret-camera-key')
    return {'config':asdict(c),'inspiration':'','group_key':'same-assets','skill_version':c.skill_version}


def test_atomic_idempotency_includes_variation_mode_and_inspiration(setup):
    from app.production_store import Conflict
    cfg,store,draft,private=setup
    original=store.create_run(draft['id'],1,'same-key',private)
    changed={**private,'variation':intent()}
    with pytest.raises(Conflict):store.create_run(draft['id'],1,'same-key',changed)
    store.create_run(draft['id'],1,'another-key',changed)
    changed['variation']['inspiration']='侧拍'
    with pytest.raises(Conflict):store.create_run(draft['id'],1,'another-key',changed)


def test_variation_audio_retry_not_offered_as_ordinary_generation(setup):
    cfg,store,draft,private=setup;private['variation']=intent()
    private['generation'].update(model='doubao-seedance-2-5-260628',generate_audio=True)
    run=store.create_run(draft['id'],1,'audio-camera',private)
    from app.audio_policy import AUDIO_COPYRIGHT_CODE
    store.update_run(run['id'],status='failed',error=AUDIO_COPYRIGHT_CODE,error_kind='audio_copyright')
    assert not store.get_run(run['id'])['can_retry_without_audio']
    assert not store.page_runs(1,10,[])['items'][0]['can_retry_without_audio']


def test_plan_persisted_and_resume_never_replans(setup,monkeypatch):
    cfg,store,draft,private=setup
    private['variation']=intent()
    calls=[]
    monkeypatch.setattr(variation,'source_frames',lambda *a:(8,[]))
    monkeypatch.setattr(variation,'plan_variation',lambda *a,**kw:calls.append(kw) or example())
    class Provider:
        def __init__(self,*a):pass
        def generate(self,video,faces,clothes,prompt,output,**kw):
            if not kw['resume_task_id']:
                assert kw['variation_plan']['summary']=='缓慢推进展示穿搭'
                kw['on_submitted']('cloud-camera')
                raise ProviderError('查询中断',error_kind='query_unavailable')
            assert kw['resume_task_id']=='cloud-camera'
            output.write_bytes(b'video')
    monkeypatch.setattr(worker,'VideoProvider',Provider)
    run=store.create_run(draft['id'],1,'camera',private)
    worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='needs_attention'
    assert store.get_variation(run['id'])['plan']['shots']
    store.resume_run(run['id']);worker.execute_run(cfg,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='succeeded' and len(calls)==1


def test_failed_plan_never_submits_video(setup,monkeypatch):
    cfg,store,draft,private=setup;private['variation']=intent()
    monkeypatch.setattr(variation,'source_frames',lambda *a:(8,[]))
    monkeypatch.setattr(variation,'plan_variation',lambda *a,**kw:(_ for _ in ()).throw(ValueError('规划失败')))
    monkeypatch.setattr(worker.VideoProvider,'generate',lambda *a,**kw:(_ for _ in ()).throw(AssertionError('video submitted')))
    run=store.create_run(draft['id'],1,'camera',private)
    worker.execute_run(cfg,store,store.claim_next())
    result=store.get_run(run['id'])
    assert result['status']=='needs_attention' and '规划失败' in result['error']
    assert not result['provider_task_id']
    assert result['can_resume']
    assert store.page_runs(1,10,[])['items'][0]['can_resume']
    store.resume_run(run['id'])
    assert store.get_run(run['id'])['status']=='queued'


def test_auto_duration_plans_positive_source_duration(setup,monkeypatch):
    cfg,store,draft,private=setup;private['variation']=intent()
    config=GenerationConfig(duration=-1)
    private['generation']=asdict(config)
    run=store.create_run(draft['id'],1,'camera',private)
    run=store.get_run(run['id'],private=True)
    monkeypatch.setattr(variation,'source_frames',lambda *a:(8,[]))
    seen=[]
    monkeypatch.setattr(variation,'plan_variation',lambda *a,**kw:seen.append(kw['context']['duration']) or example())
    variation.prepare(cfg,store,run,cfg.storage_dir/'source.mp4',[],[],{},config)
    assert seen==[8]


@pytest.mark.parametrize('protocol',['ark','toapis','adapter'])
def test_actual_provider_prompt_unlocks_camera(protocol,media,tmp_path,monkeypatch):
    seen=[]
    def request(method,url,**kw):
        if url.endswith('/uploads/videos'):return response({'data':{'url':'https://example.org/source.mp4'}})
        body=kw.get('json') or kw.get('data')
        seen.append(body['content'][0]['text'] if 'content' in body else body['prompt'])
        return response({'id':'camera-task'})
    monkeypatch.setattr('app.video_provider.requests.request',request)
    monkeypatch.setattr(VideoProvider,'_poll',lambda *a:{'task_id':'camera-task'})
    c=GenerationConfig(protocol=protocol,mode='http',model='my-model',api_key='private-key',base_url='https://example.org/v1',public_base_url='https://example.org')
    VideoProvider(c).generate(media[0],[media[1]],[media[3]],'严格保留原运镜',tmp_path/'out.mp4',video_url='https://example.org/source.mp4',variation_plan=example())
    assert '严格保留原运镜' not in seen[0] and '严格遵循动作顺序' not in seen[0]
    assert '缓慢推进' in seen[0] and '@Image1' in seen[0] and '@Image2' in seen[0]
