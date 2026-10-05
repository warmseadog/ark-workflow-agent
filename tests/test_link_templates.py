from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import main
from app.config import settings
from app.media import extract_video_url


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(settings, storage_dir=tmp_path))
    monkeypatch.delenv('TIKHUB_API_KEY', raising=False)
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.mark.parametrize('url', ['https://v.kuaishou.com/abc', 'https://b23.tv/abc', 'https://xhslink.cn/abc'])
def test_share_text_prefers_supported_video_over_unrelated_url(url):
    assert extract_video_url(f'帮助 https://example.com 看作品「{url}」，复制打开') == url


def test_templates_crud_persists_and_deleted_defaults_do_not_return(client):
    response = client.get('/api/prompt-templates')
    assert response.status_code == 200
    initial = response.json()['items']
    assert initial
    created = client.post('/api/prompt-templates', json={'name': '自定义', 'content': '保持动作'}).json()
    template_id = created['id']
    assert client.put(f'/api/prompt-templates/{template_id}', json={'name': '改名', 'content': '保留镜头'}).status_code == 200
    fresh_client = TestClient(main.app)
    item = next(x for x in fresh_client.get('/api/prompt-templates').json()['items'] if x['id'] == template_id)
    assert (item['name'], item['content']) == ('改名', '保留镜头')
    for item in fresh_client.get('/api/prompt-templates').json()['items']:
        assert client.delete(f"/api/prompt-templates/{item['id']}").status_code == 200
    assert fresh_client.get('/api/prompt-templates').json()['items'] == []


def test_template_validation_and_missing_ids(client):
    assert client.post('/api/prompt-templates', json={'name': ' ', 'content': 'x'}).status_code == 422
    assert client.post('/api/prompt-templates', json={'name': 'x', 'content': ''}).status_code == 422
    assert client.put('/api/prompt-templates/missing', json={'name': 'x', 'content': 'x'}).status_code == 404


def test_tikhub_key_saved_without_exposure_and_can_be_cleared(client):
    response = client.put('/api/link-settings', json={'api_key': 'secret-test-key'})
    assert response.status_code == 200
    assert 'secret-test-key' not in response.text
    assert client.get('/api/link-settings').json()['has_api_key']
    assert client.put('/api/link-settings', json={'api_key': ''}).json()['has_api_key']
    assert not client.put('/api/link-settings', json={'clear_api_key': True}).json()['has_api_key']


def test_cross_origin_writes_rejected(client):
    headers = {'Origin': 'https://other.example'}
    assert client.put('/api/link-settings', json={'api_key': 'secret'}, headers=headers).status_code == 403
    assert client.post('/api/prompt-templates', json={'name': 'x', 'content': 'x'}, headers=headers).status_code == 403


def test_link_inspection_is_local_and_recognizes_share_text(client):
    response = client.post('/api/video-link/inspect', json={'text': '看看这个 https://b23.tv/abc。'})
    assert response.status_code == 200
    assert response.json()['platform'] == 'bilibili'
    assert response.json()['url'] == 'https://b23.tv/abc'
    assert not response.json()['configured']


def test_builtin_prompt_upgrade_preserves_custom_edits_and_deleted_templates(client):
    import sqlite3
    from app import local_preferences
    root=main.settings.storage_dir
    client.get('/api/prompt-templates')
    with sqlite3.connect(root/'local-preferences.db') as db:
        db.execute("DELETE FROM preferences WHERE key='prompts_material_roles_v1'")
        db.execute("UPDATE prompt_templates SET name='动作保留' WHERE id='default-0'")
        db.execute("INSERT OR IGNORE INTO prompt_templates (id,name,content) VALUES ('default-1','自然换装','保留我改过的模板')")
        db.execute("UPDATE prompt_templates SET content=? WHERE id='default-0'",('保持@Video1原视频的动作、镜头、场景和节奏；应用@Image1人物参考图中的脸、五官与身份；应用@Image2衣服参考图中的服装款式、颜色和材质。',))
        db.execute("UPDATE prompt_templates SET content='保留我改过的模板' WHERE id='default-1'")
        db.execute("DELETE FROM prompt_templates WHERE id='default-2'")
    items={x['id']:x for x in client.get('/api/prompt-templates').json()['items']}
    assert '步态' in items['default-0']['content']
    assert items['default-1']['content']=='保留我改过的模板'
    assert 'default-2' not in items


def test_saved_templates_do_not_pin_generated_image_numbers(client):
    result=client.post('/api/prompt-templates',json={'name':'我的模板','content':'自然光\n\n【素材联动】\n@Image8 发型参考：旧编号\n【联动结束】'}).json()
    assert result['content']=='自然光'
