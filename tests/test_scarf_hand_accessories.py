"""Scarf/hand jewelry contracts, including reversible saved-template updates."""
import sqlite3
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from app import local_preferences, reference_prompt
from app.config import settings
from tests.test_production_api import client, asset, complete_draft


@pytest.mark.parametrize('version', ['exclusive-v2', 'yoyo-v3'])
@pytest.mark.parametrize('person_video', [False, True])
def test_new_accessories_fallback_override_and_no_clothing(version, person_video):
    roles = ([] if person_video else ['人物']) + ['衣服', '衣服补充']
    kwargs = {'rule_version': version, 'person_video': person_video}
    fallback = reference_prompt.compose_exclusive_prompt('原正文', roles, **kwargs)
    for label in ('围巾', '手饰'):
        assert f'{label}来源：穿搭参考' in fallback
    explicit = reference_prompt.compose_exclusive_prompt('原正文', roles + ['围巾', '手饰'], **kwargs)
    for offset, label in enumerate(('围巾', '手饰'), 1):
        assert f'{label}来源：@Image{len(roles)+offset}' in explicit
        assert f'{label}来源：穿搭参考' not in explicit
    empty = reference_prompt.compose_exclusive_prompt('原正文', [], **kwargs)
    assert '围巾来源：穿搭参考' not in empty
    assert '手饰来源：穿搭参考' not in empty
    if version == 'exclusive-v2':
        assert '包包来源：穿搭参考' not in fallback


def test_legacy_new_accessories_use_correct_sources():
    fallback = reference_prompt.strict_reference_rules([('face.png', '人物'), ('clothes.png', '衣服')])
    assert '围巾来源：穿搭参考 @Image2' in fallback
    assert '手饰来源：穿搭参考 @Image2' in fallback
    explicit = reference_prompt.strict_reference_rules([('clothes.png', '衣服'), ('scarf.png', '围巾'), ('hand.png', '手饰')], True)
    assert '@Image2 仅控制围巾' in explicit
    assert '@Image3 仅控制手饰' in explicit
    assert '围巾来源：穿搭参考' not in explicit
    assert '手饰来源：穿搭参考' not in explicit


def test_adapter_accepts_full_reference_capacity_with_new_accessories():
    roles = ['人物', '衣服'] + ['人物补充']*29 + ['衣服补充']*29
    roles += ['发型', '场景', '包包', '帽子', '手表', '鞋子', '项链', '眼镜', '耳环', '围巾', '手饰']
    prompt = reference_prompt.compose_exclusive_prompt('原正文', roles)
    assert '围巾来源：@Image70' in prompt
    assert '手饰来源：@Image71' in prompt
    with pytest.raises(ValueError):
        reference_prompt.compose_exclusive_prompt('原正文', roles+['衣服补充'])


def test_new_accessories_survive_draft_run_copy_and_disable(client):
    draft = complete_draft(client)
    assert draft['scarf_asset_ids'] == [] and draft['hand_jewelry_enabled'] is False
    refs = {kind: asset(client, kind, kind+'.png')['id'] for kind in ('scarf', 'hand_jewelry')}
    values = {kind+suffix: ([ident] if suffix == '_asset_ids' else True)
              for kind, ident in refs.items() for suffix in ('_asset_ids', '_enabled')}
    saved = client.put('/api/production/drafts/'+draft['id'], json={'revision': draft['revision'], **values})
    assert saved.status_code == 200, saved.text
    draft = saved.json()
    run = client.post('/api/production/runs', json={'draft_id': draft['id'], 'revision': draft['revision'], 'idempotency_key': 'scarf-hand'})
    assert run.status_code == 200, run.text
    assert all(run.json()['snapshot'][key] == value for key, value in values.items())
    copied = client.post('/api/production/runs/'+run.json()['id']+'/copy').json()
    disabled = client.put('/api/production/drafts/'+copied['id'], json={'revision': copied['revision'], 'scarf_enabled': False}).json()
    assert disabled['scarf_asset_ids'] == [refs['scarf']]
    assert disabled['hand_jewelry_enabled'] is True


@pytest.mark.parametrize('version', ['legacy-v1', 'exclusive-v2', 'yoyo-v3'])
def test_provider_submission_includes_new_images_with_correct_binding(tmp_path, monkeypatch, version):
    from app.generation_settings import GenerationConfig
    from app.video_provider import VideoProvider
    paths = []
    for name in ('video.mp4', 'face.png', 'clothing.png', 'scarf.png', 'hand.png'):
        path = tmp_path/name
        path.write_bytes(name.encode())
        paths.append(path)
    requests = []
    monkeypatch.setattr(VideoProvider, '_request', lambda self, *a, **kw: requests.append(kw) or {'id': 'test-task'})
    monkeypatch.setattr(VideoProvider, '_poll', lambda *a: {})
    provider = VideoProvider(GenerationConfig(mode='http', api_key='test', public_base_url='https://example.test'))
    provider.generate(paths[0], [paths[1]], [paths[2]], '原正文', tmp_path/'out.mp4',
                      video_url='https://example.test/video', accessories={'scarf': [paths[3]], 'hand_jewelry': [paths[4]]},
                      prompt_rule_version=version)
    content = requests[0]['json']['content']
    assert len([part for part in content if part['type'] == 'image_url']) == 4
    text = content[0]['text']
    if version == 'legacy-v1':
        assert '@Image3（图片3）为围巾参考图' in text
        assert '@Image4（图片4）为手饰参考图' in text
    else:
        assert '围巾来源：@Image3' in text and '手饰来源：@Image4' in text
    assert '围巾来源：穿搭参考' not in text
    assert '手饰来源：穿搭参考' not in text


def seed_old_templates(tmp_path):
    cfg = replace(settings, storage_dir=tmp_path, config_root=None, user_id='')
    with sqlite3.connect(tmp_path/'local-preferences.db') as db:
        db.executescript('CREATE TABLE preferences(key TEXT PRIMARY KEY,value TEXT);'
                         'CREATE TABLE prompt_templates(id TEXT PRIMARY KEY,name TEXT,content TEXT,rule_version TEXT);')
        for key in ('prompts_initialized', 'prompts_material_roles_v1', 'prompts_exclusive_v2', 'prompts_yoyo_v3'):
            db.execute('INSERT INTO preferences VALUES (?,?)', (key, '1'))
        for ident, version in [('default-0', 'legacy-v1'), ('default-exclusive-v2', 'exclusive-v2'), ('default-yoyo-v3', 'yoyo-v3'), ('personal', 'yoyo-v3')]:
            db.execute('INSERT INTO prompt_templates VALUES (?,?,?,?)', (ident, ident, '人工调好的正文 '+ident, version))
    return cfg


def test_saved_defaults_upgrade_once_preserve_body_and_personal_templates(tmp_path):
    cfg = seed_old_templates(tmp_path)
    first = local_preferences.list_templates(cfg)
    assert first == local_preferences.list_templates(cfg)
    for item in first:
        assert item['content'].startswith('人工调好的正文 '+item['id'])
        assert ('【围巾手饰兼容补充 v1】' in item['content']) == (item['id'] != 'personal')
    local_preferences.save_template(cfg, '默认', '后来人工修改', 'default-0')
    assert next(x for x in local_preferences.list_templates(cfg) if x['id'] == 'default-0')['content'] == '后来人工修改'
    assert not any(x['id'] == 'default-1' for x in first)


def test_template_rollback_is_preview_first_and_preserves_later_edits(tmp_path):
    cfg = seed_old_templates(tmp_path)
    local_preferences.list_templates(cfg)
    local_preferences.save_template(cfg, '后来改名', '后续修改需保留', 'default-0')
    script = Path(__file__).resolve().parents[1]/'deploy/rollback_scarf_hand_prompts.py'
    command = [sys.executable, str(script), '--storage-dir', str(tmp_path)]
    preview = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
    assert preview.returncode == 0, preview.stderr
    assert '围巾' in next(x for x in local_preferences.list_templates(cfg) if x['id'] == 'default-yoyo-v3')['content']
    restored = subprocess.run(command+['--apply'], capture_output=True, text=True, encoding='utf-8')
    assert restored.returncode == 0, restored.stderr
    items = {x['id']: x for x in local_preferences.list_templates(cfg)}
    assert items['default-0']['content'] == '后续修改需保留'
    assert items['default-yoyo-v3']['content'] == '人工调好的正文 default-yoyo-v3'
    assert items['default-exclusive-v2']['content'] == '人工调好的正文 default-exclusive-v2'
