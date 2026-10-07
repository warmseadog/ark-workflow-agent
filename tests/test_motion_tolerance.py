"""Real incident timing, bounded repairs and a shared planning retry budget."""
import copy
import hashlib
import json
from dataclasses import asdict

import pytest
import requests

from app import variation, variation_settings
from app.generation_settings import GenerationConfig
from app.variation_llm import validate_plan
from app.variation_settings import VariationConfig
from tests.test_motion_variation import motion_plan
from tests.test_production_worker import setup

POLICY = 'motion-timing-tolerant-v1'


def check(plan, duration):
    return validate_plan(plan, duration, prompt_mode='motion', timing_policy=POLICY)


@pytest.mark.parametrize('durations', [(3.1, 3.1, 3.133333333333334), (2.8, 3.2, 3.333333333333334), (1, 4), (1, 1, 1, 1), (.6,)])
def test_accepted_timing_preserves_shots_and_actions(durations):
    plan = motion_plan(durations)
    assert check(plan, sum(durations)) == plan


@pytest.mark.parametrize('durations', [(.99, 2), (4.01,), (.5, .5)])
def test_outside_product_tolerance_requires_replanning(durations):
    with pytest.raises(ValueError, match='镜头|单镜头'):
        check(motion_plan(durations), sum(durations))


def test_numeric_strings_and_small_gaps_are_normalized_without_mutating_input():
    plan = motion_plan((3.1, 3.1, 3.133))
    plan['shots'][0]['start'] = '0.000'
    plan['shots'][0]['end'] = '3.100'
    plan['shots'][1]['start'] = '3.120'
    before = copy.deepcopy(plan)
    normalized = check(plan, 9.333333333333334)
    assert normalized['shots'][0]['start'] == 0
    assert normalized['shots'][1]['start'] == 3.1
    assert normalized['shots'][-1]['end'] == 9.333333333333334
    assert plan == before


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '2秒', '', True, None])
def test_unsafe_numeric_values_are_not_coerced(value):
    plan = motion_plan((2, 2)); plan['shots'][0]['end'] = value
    with pytest.raises(ValueError):
        check(plan, 4)


def test_small_repairs_cannot_accumulate_or_repair_large_gaps():
    plan = motion_plan((2, 2, 2, 2, 2))
    for shot in plan['shots'][1:]: shot['start'] += .04
    with pytest.raises(ValueError): check(plan, 10)
    plan = motion_plan((2, 2)); plan['shots'][1]['start'] += .051
    with pytest.raises(ValueError): check(plan, 4)


def test_legacy_timing_and_material_checks_are_not_relaxed():
    with pytest.raises(ValueError): validate_plan(motion_plan((3.1, 3.1)), 6.2, prompt_mode='motion')
    plan = motion_plan((3.1, 3.1)); plan['shots'][0]['action'] = '参考 @Image99 换成红裙'
    with pytest.raises(ValueError): check(plan, 6.2)


def make_run(setup, monkeypatch, *, random=False, old=False):
    cfg, store, draft, private = setup
    profile = VariationConfig(api_key='test', prompt_mode='motion')
    private['variation'] = dict(config=asdict(profile), inspiration='微笑比耶', group_key='same', skill_version=profile.skill_version)
    if not old: private['variation']['timing_policy'] = POLICY
    if random: private['variation'].update(creation_mode='random', creative_rules='本次随机规则')
    created = store.create_run(draft['id'], 1, 'test-timing', private)
    monkeypatch.setattr(variation, 'source_frames', lambda *a: (9.333333333333334, []))
    return cfg, store, store.get_run(created['id'], private=True)


def responses(monkeypatch, plans):
    calls = []
    def post(url, **kwargs):
        calls.append(copy.deepcopy(kwargs['json']))
        assert len(calls) <= len(plans), 'Unexpected extra paid planning request'
        response = requests.Response(); response.status_code = 200
        response._content = json.dumps({'choices':[{'message':{'content':json.dumps(plans[len(calls)-1])}}]}).encode()
        return response
    monkeypatch.setattr('app.variation_llm.requests.post', post)
    return calls


@pytest.mark.parametrize(('mode', 'expected_sha256'), [
    ('guided', '0c64663725a7ad8f4c87e9ef024f3dfbf1a7a97f0fbbec46cae5a6c962e9ac06'),
    ('random', '53ebe17c2f9433c155349c68d124e0027f83b02bc442711c1676148d78a5c2d7'),
])
def test_planner_restores_v7_system_without_losing_timing_repairs(monkeypatch, mode, expected_sha256):
    from app.random_inspiration import CREATIVE_RULES, GUIDED_RULES
    from app.variation_llm import plan_variation
    plan = motion_plan((3.1, 3.1))
    plan['shots'][1]['start'] = '3.120'
    calls = responses(monkeypatch, [plan])
    result = plan_variation(
        VariationConfig(api_key='test', prompt_mode='motion'),
        context={'duration':6.2, 'inspiration':'保持自然行走'}, frames=[], references=[],
        creative_rules=GUIDED_RULES if mode == 'guided' else CREATIVE_RULES,
        timing_policy=POLICY,
    )
    # Full system-message fingerprints from commit 84e9343, before the v8 suffix.
    system = calls[0]['messages'][0]['content']
    assert hashlib.sha256(system.encode()).hexdigest() == expected_sha256
    assert result['shots'][1]['start'] == 3.1
    assert result['shots'][1]['action'] == plan['shots'][1]['action']
    assert len(calls) == 1


def prepare(cfg, store, run):
    return variation.prepare(cfg, store, run, cfg.storage_dir/'video.mp4', [], [], {}, GenerationConfig(model='doubao-seedance-2-5-260628',duration=-1))


def test_bad_timing_replans_once_with_precise_feedback_and_keeps_both_diagnostics(setup, monkeypatch):
    cfg, store, run = make_run(setup, monkeypatch)
    calls = responses(monkeypatch, [motion_plan((5, 4.333333333333334)), motion_plan((3.1, 3.1, 3.133333333333334))])
    result = prepare(cfg, store, run)
    assert len(result['shots']) == 3 and result['timing_policy'] == POLICY
    assert len(calls) == 2
    request = json.loads(calls[1]['messages'][1]['content'][0]['text'])
    assert '5' in request['revision_request'] and '1～4' in request['revision_request']
    diagnostic = json.loads((cfg.storage_dir/'work'/run['id']/'variation-diagnostic.json').read_text(encoding='utf-8'))
    assert [a['validation'] for a in diagnostic['attempts']] == ['failed', 'passed']
    assert prepare(cfg, store, run) == result and len(calls) == 2


def test_second_bad_plan_stops_without_saving_a_plan(setup, monkeypatch):
    cfg, store, run = make_run(setup, monkeypatch)
    calls = responses(monkeypatch, [motion_plan((5, 4.333333333333334))]*2)
    with pytest.raises(ValueError, match='镜头'): prepare(cfg, store, run)
    assert len(calls) == 2 and not store.get_variation(run['id']).get('plan')


@pytest.mark.parametrize('timing_first', [True, False])
def test_random_similarity_and_timing_share_one_retry(setup, monkeypatch, timing_first):
    cfg, store, run = make_run(setup, monkeypatch, random=True)
    valid = motion_plan((3.1, 3.1, 3.133333333333334))
    invalid = motion_plan((5, 4.333333333333334))
    monkeypatch.setattr(store, 'recent_variation_plans', lambda *a: [{'summary':'previous','shots':valid['shots']}])
    calls = responses(monkeypatch, [invalid, valid] if timing_first else [valid, invalid])
    with pytest.raises(ValueError): prepare(cfg, store, run)
    assert len(calls) == 2 and not store.get_variation(run['id']).get('plan')


def test_manual_execution_of_old_2to3_task_reuses_diagnostic_without_llm_call(setup, monkeypatch):
    cfg, store, run = make_run(setup, monkeypatch, old=True)
    store.update_run(run['id'],status='needs_attention',stage='variation_planning',error='动作分镜的每镜头时长需为2～3秒，请重试规划；尚未提交视频生成。')
    store.resume_run(run['id'])
    run=store.get_run(run['id'],private=True)
    path = cfg.storage_dir/'work'/run['id']/'variation-diagnostic.json'; path.parent.mkdir(parents=True)
    plan = motion_plan((3.1, 3.1, 3.133333333333334))
    path.write_text(json.dumps({'duration':9.333333333333334,'validation':'failed','raw_plan':json.dumps(plan)}))
    monkeypatch.setattr('app.variation_llm.requests.post', lambda *a, **k: pytest.fail('Should reuse valid diagnostic'))
    result = prepare(cfg, store, run)
    assert result['shots'] == plan['shots'] and result['timing_policy'] == POLICY


def test_old_queued_task_does_not_adopt_new_policy_without_manual_resume(setup, monkeypatch):
    cfg,store,run=make_run(setup,monkeypatch,old=True)
    calls=responses(monkeypatch,[motion_plan((3.1,3.1,3.133333333333334))])
    with pytest.raises(ValueError,match='2～3'):prepare(cfg,store,run)
    assert len(calls)==1 and not store.get_variation(run['id']).get('timing_policy')


def test_new_motion_request_freezes_tolerance_and_overrides_generated_legacy_rules(setup):
    cfg, store, draft, private = setup
    variation_settings.save_config(cfg, {'api_key':'test', 'prompt_mode':'motion'})
    frozen = variation.preflight(cfg, store, draft, {'inspiration':'微笑'})
    assert frozen['timing_policy'] == POLICY


@pytest.mark.parametrize('mode', ['guided', 'random'])
def test_tolerant_prompt_reaches_provider_with_accepted_longer_shot(mode):
    from app.reference_prompt import compose_exclusive_prompt
    plan = dict(motion_plan((3.1, 3.1, 3.133333333333334)), prompt_mode='motion', timing_policy=POLICY, creation_mode=mode)
    text = compose_exclusive_prompt('', ['人物','衣服'], variation_plan=plan)
    assert '1～4秒' in text and '3.1' in text and '镜头3' in text


def test_material_violation_is_not_retried(setup, monkeypatch):
    cfg, store, run = make_run(setup, monkeypatch)
    bad = motion_plan((3.1, 3.1, 3.133333333333334)); bad['shots'][0]['action']='换成红裙'
    calls = responses(monkeypatch, [bad])
    with pytest.raises(ValueError, match='固定条件'): prepare(cfg, store, run)
    assert len(calls) == 1


def test_old_2to5_snapshot_does_not_silently_adopt_tolerance(setup, monkeypatch):
    cfg, store, draft, private = setup
    profile = VariationConfig(api_key='test', prompt_mode='motion', motion_skill='每镜头通常2～5秒')
    private['variation']=dict(config=asdict(profile),inspiration='微笑',group_key='old',skill_version='old')
    created=store.create_run(draft['id'],1,'old25',private)
    monkeypatch.setattr(variation,'source_frames',lambda *a:(9.333333333333334,[]))
    calls=responses(monkeypatch,[motion_plan((3.1,3.1,3.133333333333334))])
    with pytest.raises(ValueError,match='2～3'):prepare(cfg,store,store.get_run(created['id'],private=True))
    assert len(calls)==1 and not store.get_variation(created['id']).get('timing_policy')
