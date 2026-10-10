"""The admin editor and provider must use the same effective prompt rules."""
import pytest
from app import main
from tests.test_production_api import client, complete_draft
from tests.test_prompt_privacy_delegation import users
from tests.test_video_provider import media


def test_catalog_exposes_system_defaults_and_accessory_rules(client):
    response=client.get('/api/admin/prompt-workbench')
    assert response.status_code==200
    data=response.json()
    assert {'素材联动','系统提示词','拍摄灵感','续写','更多搭配'} <= {x['group'] for x in data['items']}
    assert all(isinstance(x['default'],str) and isinstance(x['content'],str) for x in data['items'])
    assert any(x['id']=='accessory.bag' for x in data['items'])


def test_unsaved_preview_and_save_change_real_composer_and_freeze_tasks(client):
    before=client.get('/api/admin/prompt-workbench')
    assert before.status_code==200
    data=before.json()
    payload={'roles':['人物','衣服','包包'],'prompt':'展示服装','rule_version':'yoyo-v3',
             'overrides':{'accessory.bag':'包包保持短肩带，贴合肩部。'}}
    preview=client.post('/api/admin/prompt-workbench/preview',json=payload)
    assert preview.status_code==200,preview.text
    assert '包包保持短肩带' in preview.json()['final_prompt']
    assert '包包保持短肩带' not in client.post('/api/admin/prompt-workbench/preview',json={**payload,'overrides':{}}).json()['final_prompt']
    draft=complete_draft(client)
    old=client.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'before-prompt-edit'}).json()
    saved=client.put('/api/admin/prompt-workbench',json={'revision':data['revision'],'changes':payload['overrides']})
    assert saved.status_code==200,saved.text
    assert '包包保持短肩带' in client.post('/api/admin/prompt-workbench/preview',json={**payload,'overrides':{}}).json()['final_prompt']
    from app.production_store import ProductionStore
    store=ProductionStore(main.settings.storage_dir)
    assert 'accessory.bag' not in store.get_run(old['id'],private=True)['private']['prompt_overrides']
    new=client.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'after-prompt-edit'}).json()
    assert store.get_run(new['id'],private=True)['private']['prompt_overrides']['accessory.bag']==payload['overrides']['accessory.bag']
    conflict=client.put('/api/admin/prompt-workbench',json={'revision':data['revision'],'changes':{'accessory.bag':'并发覆盖'}})
    assert conflict.status_code==409


def test_regular_user_cannot_read_or_edit_system_prompts(users):
    ordinary=users[2][1]
    assert ordinary.get('/api/admin/prompt-workbench').status_code==403
    assert ordinary.post('/api/admin/prompt-workbench/preview',json={}).status_code==403
    assert ordinary.put('/api/admin/prompt-workbench',json={}).status_code==403


def test_operator_admin_can_manage_prompts_without_access_to_api_credentials(users):
    admin=users[2][2]
    response=admin.get('/api/admin/prompt-workbench')
    assert response.status_code==200,response.text
    assert 'api_key' not in response.text
    data=response.json()
    assert 'id="prompt-workbench"' in admin.get('/admin/templates').text
    assert admin.post('/api/admin/prompt-workbench/preview',json={}).status_code==200
    assert admin.put('/api/admin/prompt-workbench',json={'revision':data['revision'],'changes':{'accessory.bag':'管理员编辑的包包规则'}}).status_code==200
    assert admin.get('/api/generation-settings').status_code==403


def test_placeholder_validation_keeps_the_previous_saved_configuration(client):
    data=client.get('/api/admin/prompt-workbench').json()
    field=next(x for x in data['items'] if x['variables'])
    for content in ('删除了所有占位符','{unknown.__class__}','{unknown}'):
        response=client.put('/api/admin/prompt-workbench',json={'revision':data['revision'],'changes':{field['id']:content}})
        assert response.status_code==422
        assert client.get('/api/admin/prompt-workbench').json()['revision']==data['revision']


def test_provider_receives_exact_preview_and_old_snapshot_keeps_old_rules(client,media,tmp_path,monkeypatch):
    from app.prompt_config import scope
    from app.video_provider import VideoProvider
    from app.generation_settings import GenerationConfig
    rules={'accessory.bag':'只采用本次包包的短肩带。'}
    payload={'roles':['人物','衣服','包包'],'prompt':'服装展示','rule_version':'yoyo-v3','overrides':rules}
    expected=client.post('/api/admin/prompt-workbench/preview',json=payload).json()['final_prompt']
    sent=[]
    def request(self,method,path,**kwargs):
        sent.append(kwargs['json']['content'][0]['text']);return {'id':'mock-paid-id'}
    monkeypatch.setattr(VideoProvider,'_request',request)
    monkeypatch.setattr(VideoProvider,'_poll',lambda *args,**kwargs:{'task_id':'mock-paid-id'})
    config=GenerationConfig(mode='http',api_key='fake',public_base_url='https://test.example',model='my-model')
    provider=VideoProvider(config)
    for frozen in (rules,{}):
        with scope(frozen):
            provider.generate(media[0],[media[1]],[media[3]],'服装展示',tmp_path/'result.mp4',
                video_url='https://test.example/video.mp4',accessories={'bag':[media[2]]},prompt_rule_version='yoyo-v3')
    assert sent[0]==expected
    assert '只采用本次包包的短肩带' not in sent[1]


@pytest.mark.parametrize('mode',['strict','motion','user_priority','continuation','inspiration'])
def test_system_edit_preview_uses_real_system_composer(client,mode):
    from dataclasses import replace
    from app import prompt_config,variation_settings,variation_llm,continuation_settings,continuation_llm
    key=('planner.'+mode if mode in ('strict','motion','user_priority') else 'continuation.system' if mode=='continuation' else 'inspiration.strict')
    value='管理员编辑的系统指令，仍按规定格式输出。'
    response=client.post('/api/admin/prompt-workbench/preview',json={'mode':mode,'overrides':{key:value}})
    assert response.status_code==200,response.text
    assert value in response.json()['system_prompt']
    with prompt_config.scope({key:value}):
        if mode in ('strict','motion','user_priority'):
            actual=variation_llm.system_prompt(replace(variation_settings.load_config(main.settings),prompt_mode=mode))
        elif mode=='continuation':actual=continuation_llm.system_prompt(continuation_settings.load_config(main.settings),4,8)
        else:actual=value
    assert response.json()['system_prompt']==actual


@pytest.mark.parametrize('key',['variation.template','continuation.skill','template.default-yoyo-v3'])
def test_existing_prompt_fields_share_the_original_store(client,key):
    from app import variation_settings,continuation_settings,local_preferences
    data=client.get('/api/admin/prompt-workbench').json()
    result=client.put('/api/admin/prompt-workbench',json={'revision':data['revision'],'changes':{key:'从统一页面修改的正文'}})
    assert result.status_code==200,result.text
    if key=='variation.template':actual=variation_settings.load_config(main.settings).template
    elif key=='continuation.skill':actual=continuation_settings.load_config(main.settings).skill
    else:actual=next(x['content'] for x in local_preferences.list_shared_templates(main.settings) if x['is_default'])
    assert actual=='从统一页面修改的正文'
