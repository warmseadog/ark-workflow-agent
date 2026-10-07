"""Typed inspiration follows the user's plan without random novelty pressure."""
import pytest

from app import main, variation, variation_settings
from app.generation_settings import GenerationConfig
from app.production_store import ProductionStore
from tests.test_production_api import client, complete_draft
from tests.test_production_worker import setup
from tests.test_variation_api import body
from tests.test_variation import example


def test_guided_submission_freezes_distinct_intent(client):
    variation_settings.save_config(main.settings, {'api_key':'test'})
    draft=complete_draft(client)
    payload=body(draft,inspiration='固定机位，不要走动')
    payload['variation']['creation_mode']='guided'
    result=client.post('/api/production/runs',json=payload)
    assert result.status_code==200,result.text
    store=ProductionStore(main.settings.storage_dir)
    saved=store.get_run(result.json()['id'],private=True)
    assert saved['private']['variation']['creation_mode']=='guided'
    assert '不加入随机创意' in saved['private']['variation']['creative_rules']
    assert saved['variation']['recipe']==''
    assert client.post('/api/production/runs',json=payload).json()['id']==saved['id']
    payload['variation']['creation_mode']='random'
    assert client.post('/api/production/runs',json=payload).status_code==409


@pytest.mark.parametrize('text',['','  \n　'])
def test_guided_blank_rejected_before_queue(client,text):
    draft=complete_draft(client)
    payload=body(draft,inspiration=text)
    payload['variation']['creation_mode']='guided'
    result=client.post('/api/production/runs',json=payload)
    assert result.status_code==422 and '填写' in result.text
    assert not ProductionStore(main.settings.storage_dir).list_runs()


def test_guided_does_not_read_history_or_replan_duplicate(setup,monkeypatch):
    cfg,store,draft,private=setup
    variation_settings.save_config(cfg,{'api_key':'test'})
    private['variation']=variation.preflight(cfg,store,draft,{'inspiration':'保持原有展示动作，自然站立','creation_mode':'guided'})
    old=store.create_run(draft['id'],1,'old',private)
    store.update_variation(old['id'],plan=example())
    created=store.create_run(draft['id'],1,'new',private)
    run=store.get_run(created['id'],private=True)
    monkeypatch.setattr(variation,'source_frames',lambda *a:(8,[]))
    monkeypatch.setattr(store,'recent_variation_plans',lambda *a:pytest.fail('Guided generation must not read random history'))
    seen=[]
    monkeypatch.setattr(variation,'plan_variation',lambda *a,**kw:seen.append(kw) or example())
    plan=variation.prepare(cfg,store,run,cfg.storage_dir/'video.mp4',[],[],{},GenerationConfig())
    assert len(seen)==1
    assert not {'suggested_recipe','recent_recipes','recent_plans','creation_nonce'} & seen[0]['context'].keys()
    assert '不加入随机创意' in seen[0]['creative_rules']
    assert plan['creation_mode']=='guided'
    assert variation.prepare(cfg,store,run,cfg.storage_dir/'video.mp4',[],[],{},GenerationConfig())==plan
    assert len(seen)==1


@pytest.mark.parametrize('profile',['strict','motion'])
def test_guided_motion_final_prompt_does_not_demand_extra_action_variety(profile):
    from app.reference_prompt import compose_exclusive_prompt
    plan={**example(),'prompt_mode':profile,'creation_mode':'guided'}
    text=compose_exclusive_prompt('', ['人物','衣服'], variation_plan=plan)
    assert '不额外加入随机动作' in text
    assert '相邻镜头在人物动作、身体朝向或移动方式上形成明显变化' not in text
    assert '不复制其运镜、构图和镜头节奏' not in text
