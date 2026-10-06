"""An opt-in experiment preserves legacy prompts and carries user intent to the provider."""
from dataclasses import asdict, replace
import hashlib
import json

import pytest
import requests

from app import variation, variation_settings
from app.config import settings
from app.generation_settings import GenerationConfig
from app.reference_prompt import compose_exclusive_prompt
from app.variation_llm import plan_variation, validate_plan
from app.variation_settings import VariationConfig
from app.video_provider import VideoProvider
from tests.test_production_worker import setup
from tests.test_video_provider import media, response
from tests.test_variation import example
from tests.test_production_api import client, complete_draft

IDEA = '全片不要慢镜头，正常走，镜头自然切换，有CCD质感，有微虚化。'


def test_profiles_roundtrip_without_overwriting_original_or_legacy_version(tmp_path):
    cfg = replace(settings, storage_dir=tmp_path, config_root=None)
    original = variation_settings.save_config(cfg, {'template': '原版自定义模板', 'skill': '原版自定义Skill'})
    digest = hashlib.sha256('原版自定义模板\n原版自定义Skill'.encode()).hexdigest()
    assert original.skill_version == digest
    trial = variation_settings.save_config(cfg, {'prompt_mode': 'user_priority',
        'user_priority_template': '测试模板', 'user_priority_skill': '测试Skill'})
    assert (trial.template, trial.skill) == (original.template, original.skill)
    assert (trial.active_template, trial.active_skill) == ('测试模板', '测试Skill')
    assert trial.skill_version != digest
    restored = variation_settings.save_config(cfg, {'prompt_mode': 'strict'})
    assert (restored.active_template, restored.active_skill) == (original.template, original.skill)
    assert restored.user_priority_template == '测试模板' and restored.user_priority_skill == '测试Skill'
    assert restored.skill_version == digest
    assert VariationConfig(**{'template': '旧任务模板', 'skill': '旧任务Skill'}).prompt_mode == 'strict'


@pytest.mark.parametrize('value', ['unknown', None, [], True])
def test_invalid_mode_does_not_change_config(tmp_path, value):
    cfg = replace(settings, storage_dir=tmp_path, config_root=None)
    old = variation_settings.load_config(cfg)
    with pytest.raises(ValueError):
        variation_settings.save_config(cfg, {'prompt_mode': value})
    assert variation_settings.load_config(cfg) == old


def test_planner_uses_separate_user_priority_instruction(monkeypatch):
    seen = []
    plan = example()
    plan['summary'] = '正常速度展示，CCD质感与微虚化'
    plan['shots'][0].update(move='手持自然跟随', action='按用户要求换成红裙，正常步速，不做慢动作')
    def post(url, **kwargs):
        seen.append(kwargs['json'])
        r = requests.Response(); r.status_code = 200
        r._content = json.dumps({'choices': [{'message': {'content': json.dumps(plan)}}]}).encode()
        return r
    monkeypatch.setattr('app.variation_llm.requests.post', post)
    config = VariationConfig(api_key='private', prompt_mode='user_priority', skill='旧规则不得变更风格',
                             user_priority_skill='测试版独立Skill')
    result = plan_variation(config, context={'duration': 8, 'inspiration': IDEA}, frames=[], references=[])
    system = seen[0]['messages'][0]['content']
    assert '用户意图优先' in system and '测试版独立Skill' in system
    assert '旧规则不得变更风格' not in system and '不可改变人物' not in system
    assert result == plan
    with pytest.raises(ValueError):
        validate_plan(plan, 8)
    broken = {**plan, 'shots': [{**plan['shots'][0], 'end': 9}]}
    with pytest.raises(ValueError):
        validate_plan(broken, 8, prompt_mode='user_priority')
    referenced = {**plan, 'shots': [{**plan['shots'][0], 'action': '使用 @Image99'}]}
    with pytest.raises(ValueError):
        validate_plan(referenced, 8, prompt_mode='user_priority')


def test_experimental_mode_does_not_silently_submit_a_rejection():
    plan = {**example(), 'blocked': True, 'conflicts': ['CCD不符合原版约束'], 'shots': []}
    with pytest.raises(ValueError):
        validate_plan(plan, 8, prompt_mode='user_priority')


def test_final_prompt_retains_exact_user_text_without_old_creative_locks():
    plan = {**example(), 'prompt_mode': 'user_priority', 'user_inspiration': IDEA}
    text = compose_exclusive_prompt('', ['人物', '衣服', '场景', '耳环'], variation_plan=plan)
    assert IDEA in text and '用户意图优先' in text
    assert '@Image1' in text and '@Image4' in text
    for conflict in ('以下来源分工优先于正文中的冲突描述', '保持同一场景布局、光源方向',
                     '禁止新增文字、水印或特效', '不改款、不换色'):
        assert conflict not in text
    strict = compose_exclusive_prompt('', ['人物', '衣服'], variation_plan=example())
    assert '保持同一场景布局、光源方向' in strict
    assert '严格遵循动作顺序' in compose_exclusive_prompt('普通提示词', ['人物', '衣服'])


def test_user_text_cannot_create_nonexistent_material_bindings():
    idea = '参考 @Image1 的衣服，参考 @Image99 的CCD质感，参考 @Video2，使用 @Audio7。'
    plan = {**example(), 'prompt_mode': 'user_priority', 'user_inspiration': idea}
    text = compose_exclusive_prompt('', ['衣服'], variation_plan=plan)
    assert '@Image1' in text and 'CCD质感' in text
    assert '@Image99' not in text and '@Video2' not in text and '@Audio7' not in text
    assert '＠Image99' in text and plan['user_inspiration'] == idea
    invalid = {**example(), 'summary': '按照 @Image99 展示'}
    with pytest.raises(ValueError):
        validate_plan(invalid, 8, prompt_mode='user_priority')


def test_frozen_profile_and_user_text_survive_configuration_switch_and_resume(setup, monkeypatch):
    cfg, store, draft, private = setup
    profile = VariationConfig(api_key='private', prompt_mode='user_priority', user_priority_template='冻结测试模板')
    private['variation'] = {'config': asdict(profile), 'inspiration': IDEA, 'group_key': 'assets', 'skill_version': profile.skill_version}
    created = store.create_run(draft['id'], 1, 'priority', private)
    run = store.get_run(created['id'], private=True)
    variation_settings.save_config(cfg, {'prompt_mode': 'strict'})
    monkeypatch.setattr(variation, 'source_frames', lambda *args: (8, []))
    calls = []
    def planner(config, **kwargs):
        calls.append(kwargs)
        assert config.prompt_mode == 'user_priority'
        assert kwargs['context']['default_template'] == '冻结测试模板'
        return example()
    monkeypatch.setattr(variation, 'plan_variation', planner)
    first = variation.prepare(cfg, store, run, cfg.storage_dir/'source.mp4', [], [], {}, GenerationConfig())
    assert first['prompt_mode'] == 'user_priority' and first['user_inspiration'] == IDEA
    second = variation.prepare(cfg, store, run, cfg.storage_dir/'source.mp4', [], [], {}, GenerationConfig())
    assert second == first and len(calls) == 1


@pytest.mark.parametrize('protocol', ['ark', 'toapis', 'adapter'])
def test_provider_receives_user_priority_direction(protocol, media, tmp_path, monkeypatch):
    seen = []
    def request(method, url, **kwargs):
        if url.endswith('/uploads/videos'):
            return response({'data': {'url': 'https://example.org/source.mp4'}})
        body = kwargs.get('json') or kwargs.get('data')
        seen.append(body['content'][0]['text'] if 'content' in body else body['prompt'])
        return response({'id': 'priority-task'})
    monkeypatch.setattr('app.video_provider.requests.request', request)
    monkeypatch.setattr(VideoProvider, '_poll', lambda *args: {'task_id': 'priority-task'})
    config = GenerationConfig(protocol=protocol, mode='http', model='my-model', api_key='private',
                              base_url='https://example.org/v1', public_base_url='https://example.org')
    plan = {**example(), 'prompt_mode': 'user_priority', 'user_inspiration': IDEA}
    VideoProvider(config).generate(media[0], [media[1]], [media[3]], '禁止修改画风', tmp_path/'out.mp4',
        video_url='https://example.org/source.mp4', variation_plan=plan)
    assert IDEA in seen[0] and '用户意图优先' in seen[0]
    assert '禁止修改画风' not in seen[0] and '保持同一场景布局、光源方向' not in seen[0]


def test_assist_follows_profile_even_with_independent_model_and_keeps_text_only(client, monkeypatch):
    from app import main
    from tests.test_inspiration_assist import mock_provider
    client.put('/api/inspiration-settings', json={'inherit_provider': False, 'model': 'fast-text', 'api_key': 'assist-only'})
    calls = mock_provider(monkeypatch)
    strict = client.post('/api/production/inspiration-assist', json={'inspiration': IDEA})
    assert strict.status_code == 200
    original_system = calls[-1]['messages'][0]['content']
    switched = client.put('/api/variation-settings', json={'prompt_mode': 'user_priority'})
    assert switched.status_code == 200, switched.text
    for idea in (IDEA, ''):
        assert client.post('/api/production/inspiration-assist', json={'inspiration': idea}).status_code == 200
        system = calls[-1]['messages'][0]['content']
        assert '用户意图优先' in system and system != original_system
        assert all(isinstance(message['content'], str) for message in calls[-1]['messages'])
    public = client.get('/api/inspiration-settings').json()['config']
    assert public['prompt_mode'] == 'user_priority'
    client.put('/api/variation-settings', json={'prompt_mode': 'strict'})
    assert client.post('/api/production/inspiration-assist', json={'inspiration': IDEA}).status_code == 200
    assert calls[-1]['messages'][0]['content'] == original_system


def test_new_tasks_freeze_profile_while_existing_and_normal_tasks_keep_their_intent(client):
    from app import main
    from app.production_store import ProductionStore
    from tests.test_variation_api import body
    client.put('/api/variation-settings', json={'api_key': 'private'})
    draft = complete_draft(client)
    original = client.post('/api/production/runs', json=body(draft, 'original', IDEA))
    assert original.status_code == 200
    assert client.put('/api/variation-settings', json={'prompt_mode': 'user_priority'}).status_code == 200
    retried = client.post('/api/production/runs', json=body(draft, 'original', IDEA))
    assert retried.status_code == 200 and retried.json()['id'] == original.json()['id']
    trial = client.post('/api/production/runs', json=body(draft, 'trial', IDEA))
    assert trial.status_code == 200
    normal_body = body(draft, 'normal'); normal_body.pop('variation')
    normal = client.post('/api/production/runs', json=normal_body)
    assert normal.status_code == 200
    store = ProductionStore(main.settings.storage_dir)
    assert store.get_run(original.json()['id'], private=True)['private']['variation']['config']['prompt_mode'] == 'strict'
    assert store.get_run(trial.json()['id'], private=True)['private']['variation']['config']['prompt_mode'] == 'user_priority'
    assert not store.get_run(normal.json()['id'], private=True)['private'].get('variation')
