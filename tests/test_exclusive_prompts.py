import sqlite3
from dataclasses import replace

import pytest

from app import local_preferences, reference_prompt
from app.config import settings
from tests.test_production_api import client, asset
from tests.test_video_provider import media, response


def test_shared_migration_preserves_old_body_and_does_not_resurrect_deleted(tmp_path):
    cfg = replace(settings, storage_dir=tmp_path, config_root=None, user_id='')
    with sqlite3.connect(tmp_path/'local-preferences.db') as db:
        db.executescript("CREATE TABLE preferences (key TEXT PRIMARY KEY,value TEXT NOT NULL);"
                         "CREATE TABLE prompt_templates (id TEXT PRIMARY KEY,name TEXT NOT NULL,content TEXT NOT NULL);"
                         "INSERT INTO preferences VALUES ('prompts_initialized','1');"
                         "INSERT INTO preferences VALUES ('prompts_material_roles_v1','1');")
        db.execute('INSERT INTO prompt_templates VALUES (?,?,?)', ('default-0', '动作保留', '我的旧正文'))
    items = local_preferences.list_templates(cfg)
    assert [x['name'] for x in items] == ['yoyo提示词', '默认提示词', '默认提示词2']
    yoyo, new, old = items
    assert new['rule_version'] == 'exclusive-v2' and not new['is_default']
    assert yoyo['rule_version'] == 'yoyo-v3' and yoyo['is_default']
    from app.prompt_templates import SCARF_HAND_CONSTRAINT
    assert old['content'] == '我的旧正文\n\n'+SCARF_HAND_CONSTRAINT and old['rule_version'] == 'legacy-v1'
    local_preferences.delete_template(cfg, new['id'])
    assert [x['id'] for x in local_preferences.list_templates(cfg)] == [yoyo['id'], 'default-0']


def test_concurrent_first_reads_migrate_once(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    import time
    connect=sqlite3.connect
    class SlowMigration(sqlite3.Connection):
        def execute(self,sql,*args,**kwargs):
            if sql.startswith('ALTER TABLE prompt_templates'):
                time.sleep(.1)  # Concurrent readers during a slow schema upgrade.
            return super().execute(sql,*args,**kwargs)
    monkeypatch.setattr(local_preferences.sqlite3,'connect',lambda *a,**kw:connect(*a,factory=SlowMigration,**kw))
    cfg=replace(settings,storage_dir=tmp_path,config_root=None,user_id='')
    barrier=Barrier(16)
    def read(_):
        barrier.wait()
        return local_preferences.list_templates(cfg)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results=list(pool.map(read,range(16)))
    assert all([x['name'] for x in rows]==['yoyo提示词','默认提示词','默认提示词2'] for rows in results)


def test_new_drafts_use_saved_default_and_copies_keep_version(client):
    items = client.get('/api/prompt-templates').json()['items']
    new = next(x for x in items if x.get('is_default'))
    assert client.put('/api/prompt-templates/'+new['id'], json={'name':new['name'], 'content':'自定义新版正文'}).status_code == 200
    draft = client.post('/api/production/drafts', json={}).json()
    assert draft['prompt'] == '自定义新版正文'
    assert draft['prompt_template_id'] == new['id']
    assert draft['prompt_rule_version'] == 'yoyo-v3'
    saved = client.put('/api/production/drafts/'+draft['id'], json={'revision':draft['revision'],
        'prompt':'旧稿', 'prompt_template_id':'default-0', 'prompt_rule_version':'legacy-v1'}).json()
    copied = client.post('/api/production/drafts', json={'copy_from':draft['id']}).json()
    assert copied['prompt'] == '旧稿' and copied['prompt_rule_version'] == 'legacy-v1'
    assert client.put('/api/production/drafts/'+draft['id'], json={'revision':saved['revision'], 'prompt_rule_version':'unknown'}).status_code == 422


@pytest.mark.parametrize('person_video', [False, True])
def test_exclusive_hair_fallback_and_optional_sources(person_video):
    roles = ([] if person_video else ['人物']) + ['衣服','场景','包包','鞋子','耳环']
    text = reference_prompt.compose_exclusive_prompt('用户正文', roles, person_video=person_video)
    identity = '@Video2' if person_video else '@Image1'
    assert f'发型来源：{identity}' in text and '忽略 @Video1' in text
    for role in ('场景','包包','鞋子','耳环'):
        assert f'{role}来源：@Image{roles.index(role)+1}' in text
    assert '不保留或叠加旧包' in text
    assert '唯一外观来源' in text
    assert '发型来源：@Video1' not in text


def test_hair_scene_and_supplements_rebind_without_stale_editor_block():
    roles = ['人物','衣服','人物补充','衣服补充','发型','包包']
    text = reference_prompt.compose_exclusive_prompt('正文\n【素材联动】@Image9 旧规则【联动结束】', roles)
    assert '发型来源：@Image5' in text and '包包来源：@Image6' in text
    assert '@Image9' not in text and '旧规则' not in text
    assert '场景来源：@Video1' in text
    assert '场景来源：文字描述' in reference_prompt.compose_exclusive_prompt('正文', ['人物','衣服'], scene_description='海边')


def test_preview_uses_composer_and_rejects_invalid_roles(client):
    payload = {'prompt':'正文', 'roles':['人物','衣服','包包'], 'person_video':False,
               'scene_description':'', 'model':{}, 'rule_version':'exclusive-v2'}
    response = client.post('/api/production/prompt-preview', json=payload)
    assert response.status_code == 200, response.text
    assert response.json()['prompt'] == reference_prompt.compose_exclusive_prompt('正文', payload['roles'])
    assert client.post('/api/production/prompt-preview', json={**payload,'roles':['secret-role']}).status_code == 422
    assert client.post('/api/production/prompt-preview', json={**payload,'rule_version':'unknown'}).status_code == 422


def test_personal_copy_inherits_explicit_version(client):
    result = client.post('/api/prompt-templates', json={'name':'新版副本','content':'正文','rule_version':'exclusive-v2'})
    assert result.status_code == 200
    assert result.json()['rule_version'] == 'exclusive-v2'
    assert not result.json()['is_default']


@pytest.mark.parametrize('label',['包包','帽子','手表','鞋子','项链','眼镜','耳环'])
def test_every_accessory_has_exclusive_role_and_disappears_when_disabled(label):
    text=reference_prompt.compose_exclusive_prompt('正文',['人物','衣服',label])
    assert f'{label}来源：@Image3' in text
    assert f'其他参考素材中的{label}' in text
    assert '只提取'+label in text
    assert label+'来源：' not in reference_prompt.compose_exclusive_prompt('正文',['人物','衣服'])


def test_deleted_default_is_not_silently_applied_to_new_tasks(client):
    default=next(x for x in client.get('/api/prompt-templates').json()['items'] if x['is_default'])
    assert client.delete('/api/prompt-templates/'+default['id']).status_code==200
    draft=client.post('/api/production/drafts',json={}).json()
    assert draft['prompt']=='' and draft['prompt_template_id'] is None
    assert not any(x['is_default'] for x in client.get('/api/prompt-templates').json()['items'])


@pytest.mark.parametrize('protocol', ['ark','toapis','adapter'])
@pytest.mark.parametrize('rule_version', ['exclusive-v2','yoyo-v3'])
def test_provider_uses_exact_preview_composer(protocol, rule_version, media, tmp_path, monkeypatch):
    from app.video_provider import VideoProvider
    from app.generation_settings import GenerationConfig
    config = GenerationConfig(provider=protocol if protocol!='adapter' else 'custom', protocol=protocol,
        mode='http',api_key='test',base_url='https://provider.example/v1',model='test-model',duration=8,
        public_base_url='https://studio.example',fps=30 if protocol=='adapter' else 0)
    posted = []
    def request(method,url,**kwargs):
        if url.endswith('/uploads/videos'):
            return response({'data':{'url':'https://uploaded.example/video.mp4'}})
        if method=='POST':
            posted.append(kwargs['data']['prompt'] if protocol=='adapter' else
                kwargs['json']['content'][0]['text'] if protocol=='ark' else kwargs['json']['prompt'])
            return response({'id':'task'})
        if protocol=='ark': return response({'status':'succeeded','content':{'video_url':'https://result.example/video.mp4'}})
        if protocol=='toapis': return response({'status':'completed','result':{'data':[{'url':'https://result.example/video.mp4'}]}})
        return response({'status':'completed','output_url':'https://result.example/video.mp4'})
    monkeypatch.setattr('app.video_provider.requests.request',request)
    monkeypatch.setattr(VideoProvider,'_download',lambda self,url,path:path.write_bytes(b'final'))
    body = '保留动作、镜头和场景。\n【素材联动】旧编号 @Image8【联动结束】'
    VideoProvider(config,0).generate(media[0],[media[1]],[media[3]],body,tmp_path/'out.mp4',
        video_url='https://studio.example/video.mp4',scenes=[media[2]],accessories={'bag':[media[2]],'shoes':[media[2]]},
        scene_description='暖光',prompt_rule_version=rule_version)
    expected = reference_prompt.compose_exclusive_prompt(body,['人物','衣服','场景','包包','鞋子'],scene_description='暖光',rule_version=rule_version)
    assert posted == [expected]


@pytest.mark.parametrize('rule_version', ['exclusive-v2','yoyo-v3'])
def test_editing_model_preview_and_submission_match(client,media,tmp_path,monkeypatch,rule_version):
    from app import main
    from app.generation_settings import GenerationConfig, save_config
    from app.model_catalog import SD25
    from app.video_provider import VideoProvider
    config=GenerationConfig(provider='ark',protocol='ark',mode='http',api_key='test',
        base_url='https://provider.example/v1',model=SD25,duration=-1)
    from dataclasses import asdict
    save_config(main.settings,asdict(config))
    monkeypatch.setattr('app.person_video.validate_file',lambda *a,**k:None)
    posted=[]
    def request(method,url,**kwargs):
        if method=='POST':
            posted.append(kwargs['json']['content'][0]['text']);return response({'id':'task'})
        return response({'status':'succeeded','content':{'video_url':'https://result.example/video.mp4'}})
    monkeypatch.setattr('app.video_provider.requests.request',request)
    monkeypatch.setattr(VideoProvider,'_download',lambda self,url,path:path.write_bytes(b'final'))
    preview=client.post('/api/production/prompt-preview',json={'prompt':'保持自然','roles':['人物','衣服'],
        'model':{'model':SD25,'duration':-1},'rule_version':rule_version})
    assert preview.status_code==200,preview.text
    VideoProvider(config,0).generate(media[0],[media[1]],[media[3]],'保持自然',tmp_path/'out.mp4',
        video_url='https://studio.example/video.mp4',prompt_rule_version=rule_version)
    assert posted==[preview.json()['prompt']]
    assert '唯一编辑目标为 @Video1' in posted[0]
