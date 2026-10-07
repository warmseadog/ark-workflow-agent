"""The selected motion profile travels from settings to planning and submission."""
from dataclasses import asdict
import json

import pytest
import requests

from app import variation, variation_settings
from app.generation_settings import GenerationConfig
from app.reference_prompt import compose_exclusive_prompt
from app.variation_llm import plan_variation, validate_plan
from app.variation_settings import VariationConfig
from app.video_provider import VideoProvider
from tests.test_production_worker import setup
from tests.test_production_api import client
from tests.test_video_provider import media, response


def motion_plan(durations=(3, 3, 3, 3)):
    shots = []
    cursor = 0
    for duration in durations:
        shots.append(dict(start=cursor, end=cursor+duration, framing='medium',
                          angle='eye_level', move='track', action='向前走两步后停步转向侧面'))
        cursor += duration
    return dict(summary='行走、停步与转向展示穿搭', accepted_requests=[], conflicts=[], blocked=False, shots=shots)


def test_new_profile_accepts_four_shots_without_changing_legacy_contract():
    plan = motion_plan()
    assert validate_plan(plan, 12, prompt_mode='motion') == plan
    with pytest.raises(ValueError):
        validate_plan(plan, 12)
    assert validate_plan(motion_plan((8,)), 8)['shots'][0]['end'] == 8


@pytest.mark.parametrize('durations', [(2, 5), (2.5, 2.5, 2.5, 2.5), (1.5,), (5,)])
def test_motion_duration_boundaries(durations):
    plan = motion_plan(durations)
    assert validate_plan(plan, sum(durations), prompt_mode='motion') == plan


@pytest.mark.parametrize('durations', [(6,), (1, 3), (1, 1)])
def test_motion_rejects_long_shots_and_short_tail(durations):
    with pytest.raises(ValueError, match='时长'):
        validate_plan(motion_plan(durations), sum(durations), prompt_mode='motion')


def test_motion_keeps_material_and_timing_checks():
    for field, value in [('action', '换成红裙'), ('action', '参考 @Image99'),
                         ('move', 'teleport'), ('start', .1)]:
        plan = motion_plan()
        plan['shots'][0][field] = value
        with pytest.raises(ValueError):
            validate_plan(plan, 12, prompt_mode='motion')


def test_motion_setting_and_assist_follow_selected_version(client, monkeypatch):
    from tests.test_inspiration_assist import mock_provider
    assert client.put('/api/variation-settings', json={'prompt_mode':'motion'}).status_code == 200
    assert client.put('/api/inspiration-settings', json={'inherit_provider':False, 'model':'fast', 'api_key':'test'}).status_code == 200
    calls = mock_provider(monkeypatch)
    result = client.post('/api/production/inspiration-assist', json={'inspiration':'正常行走并转身展示'})
    assert result.status_code == 200
    system = calls[0]['messages'][0]['content']
    assert '动作变化' in system and '2～5秒' in system
    assert '不安排新增人物、换装、换景、绕背、复杂转身' not in system
    assert len(calls[0]['messages']) == 2
    restored = client.put('/api/variation-settings', json={'prompt_mode':'strict'})
    assert restored.status_code == 200


def test_planner_uses_motion_system_and_validates_many_shots(monkeypatch):
    captured = []
    def post(url, **kwargs):
        captured.append(kwargs['json'])
        reply = requests.Response(); reply.status_code = 200
        reply._content = json.dumps({'choices':[{'message':{'content':json.dumps(motion_plan())}}]}).encode()
        return reply
    monkeypatch.setattr('app.variation_llm.requests.post', post)
    config = VariationConfig(api_key='test', prompt_mode='motion')
    assert plan_variation(config, context={'duration':12, 'inspiration':'行走转身'}, frames=[], references=[]) == motion_plan()
    system = captured[0]['messages'][0]['content']
    assert '每个镜头通常2～5秒' in system and '只允许轻微姿态调整' not in system
    assert '相邻镜头应在人物动作' in system


def test_frozen_motion_profile_survives_switch_and_resume(setup, monkeypatch):
    cfg, store, draft, private = setup
    profile = VariationConfig(api_key='test', prompt_mode='motion')
    private['variation'] = dict(config=asdict(profile), inspiration='停步转向', group_key='assets', skill_version=profile.skill_version)
    created = store.create_run(draft['id'], 1, 'motion', private)
    run = store.get_run(created['id'], private=True)
    variation_settings.save_config(cfg, {'prompt_mode':'strict'})
    monkeypatch.setattr(variation, 'source_frames', lambda *args: (12, []))
    def planner(config, **kwargs):
        assert config.prompt_mode == 'motion'
        return motion_plan()
    monkeypatch.setattr(variation, 'plan_variation', planner)
    plan = variation.prepare(cfg, store, run, cfg.storage_dir/'video.mp4', [], [], {}, GenerationConfig())
    assert plan['prompt_mode'] == 'motion'
    assert variation.prepare(cfg, store, run, cfg.storage_dir/'video.mp4', [], [], {}, GenerationConfig()) == plan


def test_old_snapshot_without_profile_keeps_original_rules_after_switch(setup, monkeypatch):
    cfg, store, draft, private = setup
    old = {'api_key':'test', 'model':'legacy-model', 'template':'历史模板', 'skill':'历史Skill'}
    private['variation'] = dict(config=old, inspiration='缓慢推进', group_key='old-assets', skill_version='old-version')
    created = store.create_run(draft['id'], 1, 'legacy-frozen', private)
    run = store.get_run(created['id'], private=True)
    variation_settings.save_config(cfg, {'prompt_mode':'motion'})
    monkeypatch.setattr(variation, 'source_frames', lambda *args: (8, []))
    calls = []
    def planner(config, **kwargs):
        calls.append(config)
        assert config.prompt_mode == 'strict' and config.active_skill == '历史Skill'
        assert kwargs['context']['default_template'] == '历史模板'
        return motion_plan((8,))
    monkeypatch.setattr(variation, 'plan_variation', planner)
    plan = variation.prepare(cfg, store, run, cfg.storage_dir/'video.mp4', [], [], {}, GenerationConfig())
    assert 'prompt_mode' not in plan
    assert '轻微姿态调整' in compose_exclusive_prompt('', ['人物','衣服'], variation_plan=plan)
    assert variation.prepare(cfg, store, run, cfg.storage_dir/'video.mp4', [], [], {}, GenerationConfig()) == plan
    assert len(calls) == 1


@pytest.mark.parametrize('protocol', ['ark', 'toapis', 'adapter'])
def test_provider_carries_motion_without_legacy_action_lock(protocol, media, tmp_path, monkeypatch):
    captured = []
    def request(method, url, **kwargs):
        if url.endswith('/uploads/videos'):
            return response({'data':{'url':'https://example.org/source.mp4'}})
        body = kwargs.get('json') or kwargs.get('data')
        captured.append(body['content'][0]['text'] if 'content' in body else body['prompt'])
        return response({'id':'motion-task'})
    monkeypatch.setattr('app.video_provider.requests.request', request)
    monkeypatch.setattr(VideoProvider, '_poll', lambda *args: {'task_id':'motion-task'})
    config = GenerationConfig(protocol=protocol, mode='http', model='my-model', api_key='test',
                              base_url='https://example.org/v1', public_base_url='https://example.org')
    plan = dict(motion_plan(), prompt_mode='motion')
    VideoProvider(config).generate(media[0], [media[1]], [media[3]], '原提示词', tmp_path/'out.mp4',
        video_url='https://example.org/source.mp4', variation_plan=plan)
    assert '镜头4' in captured[0] and '停步转向侧面' in captured[0]
    assert '轻微姿态调整' not in captured[0]
    assert '相邻镜头在人物动作、身体朝向或移动方式上形成明显变化' in captured[0]
    assert '锁定脸型' in captured[0]
    legacy = compose_exclusive_prompt('', ['人物','衣服'], variation_plan=motion_plan((8,)))
    assert '轻微姿态调整' in legacy
