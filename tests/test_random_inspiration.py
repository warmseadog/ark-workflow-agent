"""Random creation is an explicit, durable task intent; polishing is text only."""
import copy

import pytest

from app import main, variation, variation_settings
from app.generation_settings import GenerationConfig
from app.production_store import ProductionStore, Conflict
from tests.test_production_api import client, complete_draft
from tests.test_production_worker import setup
from tests.test_variation_api import body
from tests.test_variation_worker import intent
from tests.test_variation import example
from tests.test_inspiration_assist import mock_provider


@pytest.mark.parametrize('text', ['', ' \n\t　'])
def test_polish_rejects_blank_without_calling_model(client, monkeypatch, text):
    variation_settings.save_config(main.settings, {'api_key': 'test'})
    calls = mock_provider(monkeypatch)
    result = client.post('/api/production/inspiration-assist', json={'inspiration': text})
    assert result.status_code == 422
    assert not calls


@pytest.mark.parametrize('text', ['', '先拍袖口，再拉远'])
def test_random_submission_freezes_intent_and_deduplicates(client, text):
    variation_settings.save_config(main.settings, {'api_key': 'test'})
    draft = complete_draft(client)
    payload = body(draft, inspiration=text)
    payload['variation']['creation_mode'] = 'random'
    result = client.post('/api/production/runs', json=payload)
    assert result.status_code == 200, result.text
    store = ProductionStore(main.settings.storage_dir)
    saved = store.get_run(result.json()['id'], private=True)
    frozen = saved['private']['variation']
    assert frozen['creation_mode'] == 'random' and frozen['inspiration'] == text
    assert frozen['creative_rules']
    assert not saved['variation']['recipe']
    assert saved['status'] == 'queued'
    assert client.post('/api/production/runs', json=payload).json()['id'] == saved['id']
    payload['variation'].pop('creation_mode')
    assert client.post('/api/production/runs', json=payload).status_code == 409


def test_store_atomic_idempotency_checks_creation_mode(setup):
    cfg, store, draft, private = setup
    private['variation'] = {**intent(), 'inspiration': '侧拍', 'creation_mode': 'random'}
    store.create_run(draft['id'], 1, 'same', private)
    other = copy.deepcopy(private)
    other['variation'].pop('creation_mode')
    with pytest.raises(Conflict):
        store.create_run(draft['id'], 1, 'same', other)


def test_random_uses_recent_actual_plans_replans_once_and_reuses_saved_result(setup, monkeypatch):
    cfg, store, draft, private = setup
    private['variation'] = {**intent(), 'creation_mode': 'random', 'creative_rules': 'frozen creative rules'}
    older = store.create_run(draft['id'], 1, 'older', private)
    new = store.create_run(draft['id'], 1, 'new', private)
    # The earlier plan finishes after the next task was already queued.
    store.update_variation(older['id'], plan=example())
    changed = example()
    changed['shots'][0].update(move='pan', action='侧身行走两步后停步面向镜头')
    seen = []
    monkeypatch.setattr(variation, 'source_frames', lambda *a: (8, []))
    def planner(config, **kwargs):
        seen.append(copy.deepcopy(kwargs))
        return example() if len(seen) == 1 else changed
    monkeypatch.setattr(variation, 'plan_variation', planner)
    run = store.get_run(new['id'], private=True)
    result = variation.prepare(cfg, store, run, cfg.storage_dir/'video.mp4', [], [], {}, GenerationConfig())
    assert len(seen) == 2
    assert 'suggested_recipe' not in seen[0]['context']
    assert seen[0]['context']['recent_plans'][0]['shots'] == example()['shots']
    assert seen[0]['creative_rules'] == 'frozen creative rules'
    assert result['shots'] == changed['shots']
    assert variation.prepare(cfg, store, run, cfg.storage_dir/'video.mp4', [], [], {}, GenerationConfig()) == result
    assert len(seen) == 2


def test_duplicate_random_plan_stops_before_video_and_has_bounded_retry(setup, monkeypatch):
    cfg, store, draft, private = setup
    private['variation'] = {**intent(), 'creation_mode': 'random', 'creative_rules': 'rules'}
    old = store.create_run(draft['id'], 1, 'old', private)
    store.update_variation(old['id'], plan=example())
    created = store.create_run(draft['id'], 1, 'next', private)
    run = store.get_run(created['id'], private=True)
    calls = []
    monkeypatch.setattr(variation, 'source_frames', lambda *a: (8, []))
    monkeypatch.setattr(variation, 'plan_variation', lambda *a, **kw: calls.append(kw) or example())
    with pytest.raises(ValueError, match='相似'):
        variation.prepare(cfg, store, run, cfg.storage_dir/'video.mp4', [], [], {}, GenerationConfig())
    assert len(calls) == 2 and not store.get_variation(run['id']).get('plan')


def test_blank_random_user_priority_plan_reaches_video_prompt(setup, monkeypatch):
    from app.reference_prompt import compose_exclusive_prompt
    cfg, store, draft, private = setup
    private['variation'] = {**intent(), 'creation_mode':'random', 'creative_rules':'rules'}
    private['variation']['config']['prompt_mode'] = 'user_priority'
    run = store.create_run(draft['id'], 1, 'random-priority', private)
    monkeypatch.setattr(variation, 'source_frames', lambda *a: (8, []))
    monkeypatch.setattr(variation, 'plan_variation', lambda *a, **kw: example())
    plan = variation.prepare(cfg, store, store.get_run(run['id'], private=True),
                             cfg.storage_dir/'video.mp4', [], [], {}, GenerationConfig())
    text = compose_exclusive_prompt('', ['人物','衣服'], variation_plan=plan)
    assert '本次随机摄影方案' in text and '缓慢推进展示穿搭' in text
    assert '@Image1' in text and '@Image2' in text
    legacy = {**plan}
    legacy.pop('creation_mode')
    with pytest.raises(ValueError, match='缺少'):
        compose_exclusive_prompt('', ['人物','衣服'], variation_plan=legacy)


def test_random_requires_planner_and_rejects_unknown_modes(client):
    draft=complete_draft(client)
    payload=body(draft,inspiration='')
    payload['variation']['creation_mode']='random'
    result=client.post('/api/production/runs',json=payload)
    assert result.status_code==422
    assert not ProductionStore(main.settings.storage_dir).list_runs()
    for invalid in (None, '', 'guided', [], {}):
        payload['variation']['creation_mode']=invalid
        assert client.post('/api/production/runs',json=payload).status_code==422


def test_random_rule_is_sent_as_system_instruction_and_preserves_shot_validation(monkeypatch):
    import json
    import requests
    from app.random_inspiration import CREATIVE_RULES
    from app.variation_llm import plan_variation
    from app.variation_settings import VariationConfig
    from tests.test_motion_variation import motion_plan
    calls=[]
    def post(url, **kwargs):
        calls.append(kwargs['json'])
        response=requests.Response();response.status_code=200
        response._content=json.dumps({'choices':[{'message':{'content':json.dumps(motion_plan())}}]}).encode()
        return response
    monkeypatch.setattr('app.variation_llm.requests.post',post)
    result=plan_variation(VariationConfig(api_key='test',prompt_mode='motion'),
                          context={'duration':12,'inspiration':'固定姿态，不要行走'},
                          frames=[],references=[],creative_rules=CREATIVE_RULES)
    assert result['shots']==motion_plan()['shots']
    system=calls[0]['messages'][0]['content']
    assert CREATIVE_RULES in system and '2～3秒' in system
    assert '固定姿态，不要行走' in calls[0]['messages'][1]['content'][0]['text']
