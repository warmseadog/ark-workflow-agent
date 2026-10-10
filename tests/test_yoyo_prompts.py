import sqlite3
from dataclasses import replace

import pytest

from app import local_preferences, reference_prompt
from app.config import settings
from tests.test_production_api import client
from tests.test_prompt_privacy_delegation import users


def test_yoyo_migration_inherits_saved_default_once_and_preserves_old(tmp_path):
    cfg = replace(settings, storage_dir=tmp_path, config_root=None, user_id='')
    with sqlite3.connect(tmp_path/'local-preferences.db') as db:
        db.executescript('CREATE TABLE preferences(key TEXT PRIMARY KEY,value TEXT);'
                         'CREATE TABLE prompt_templates(id TEXT PRIMARY KEY,name TEXT,content TEXT,rule_version TEXT);')
        for key in ('prompts_initialized','prompts_material_roles_v1','prompts_exclusive_v2'):
            db.execute('INSERT INTO preferences VALUES (?,?)',(key,'1'))
        db.execute('INSERT INTO prompt_templates VALUES (?,?,?,?)',
                   ('default-exclusive-v2','默认提示词','原有自定义正文','exclusive-v2'))
    items=local_preferences.list_templates(cfg)
    new=next(x for x in items if x['is_default'])
    assert new['name']=='yoyo提示词' and new['rule_version']=='yoyo-v3'
    assert new['content'].startswith('原有自定义正文')
    assert '背面' in new['content'] and '眼镜' in new['content']
    from app.prompt_templates import SCARF_HAND_CONSTRAINT
    assert next(x for x in items if x['id']=='default-exclusive-v2')['content']=='原有自定义正文\n\n'+SCARF_HAND_CONSTRAINT
    assert not any(x['id']=='default-0' for x in items)
    local_preferences.save_template(cfg,'yoyo提示词','新正文',new['id'])
    assert local_preferences.default_prompt_values(cfg)['prompt']=='新正文'
    local_preferences.delete_template(cfg,new['id'])
    assert not any(x['is_default'] for x in local_preferences.list_templates(cfg))


@pytest.mark.parametrize('person_video',[False,True])
@pytest.mark.parametrize('label',['包包','帽子','手表','鞋子','项链','眼镜','耳环'])
def test_yoyo_category_fallback_and_explicit_override(label,person_video):
    roles=([] if person_video else ['人物'])+['衣服','衣服补充']
    clothing='@Image1' if person_video else '@Image2'
    fallback=reference_prompt.compose_exclusive_prompt('正文',roles,person_video=person_video,rule_version='yoyo-v3')
    assert f'{label}来源：穿搭参考 {clothing}' in fallback
    assert '背面' in fallback and '拼图' in fallback
    explicit=reference_prompt.compose_exclusive_prompt('正文',roles+[label],person_video=person_video,rule_version='yoyo-v3')
    assert f'{label}来源：@Image{len(roles)+1}' in explicit
    assert f'{label}来源：穿搭参考' not in explicit
    assert '发型来源：'+('@Video2' if person_video else '@Image1') in explicit
    assert '场景来源：@Video1' in explicit
    legacy=reference_prompt.compose_exclusive_prompt('正文',roles,person_video=person_video)
    assert f'{label}来源：' not in legacy


def test_yoyo_new_draft_preview_and_old_copy_version(client):
    draft=client.post('/api/production/drafts',json={}).json()
    assert draft['prompt_rule_version']=='yoyo-v3'
    payload={'prompt':draft['prompt'],'roles':['人物','衣服','眼镜'],'rule_version':'yoyo-v3','model':{}}
    preview=client.post('/api/production/prompt-preview',json=payload)
    assert preview.status_code==200,preview.text
    assert '眼镜来源：@Image3' in preview.json()['prompt']
    assert '鞋子来源：穿搭参考 @Image2' in preview.json()['prompt']
    old=client.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'prompt_rule_version':'exclusive-v2','prompt':'旧稿'}).json()
    copied=client.post('/api/production/drafts',json={'copy_from':old['id']}).json()
    assert copied['prompt_rule_version']=='exclusive-v2' and copied['prompt']=='旧稿'


def test_ordinary_new_draft_uses_yoyo_without_exposing_prompt(users):
    from app import main, tenancy
    from app.production_store import ProductionStore
    _, (_, target, _), (_, ordinary, _) = users
    response=ordinary.post('/api/production/drafts',json={})
    assert response.status_code==200,response.text
    public=response.json()
    assert 'prompt' not in public and 'prompt_rule_version' not in public
    stored=ProductionStore(tenancy.user_settings(main.settings,target).storage_dir).get_draft(public['id'])
    assert stored['prompt_rule_version']=='yoyo-v3'
    assert stored['prompt_template_id']=='system:default-yoyo-v3'
