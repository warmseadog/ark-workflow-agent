"""Admin-only standby saves cannot change production prompt selection or bodies."""
import sqlite3

import pytest

from app import main
from tests.test_production_api import client, complete_draft
from tests.test_prompt_privacy_delegation import users


def test_save_standby_keeps_restored_templates_drafts_and_preview_identical(client):
    client.get('/api/admin/prompt-templates')
    # Model the actual production rollback; retain the one-time migration marker.
    with sqlite3.connect(main.settings.storage_dir/'local-preferences.db') as db:
        db.execute('UPDATE prompt_templates SET content=(SELECT before_content FROM prompt_scarf_hand_backups b WHERE b.id=prompt_templates.id) WHERE id IN (SELECT id FROM prompt_scarf_hand_backups)')
    before = client.get('/api/admin/prompt-templates').json()['items']
    draft = complete_draft(client)
    default = next(x for x in before if x['is_default'])
    payload = {'prompt': default['content'], 'roles': ['人物', '衣服', '围巾', '手饰'], 'rule_version': default['rule_version'], 'model': {}}
    preview = client.post('/api/production/prompt-preview', json=payload).json()
    run = client.post('/api/production/runs', json={'draft_id': draft['id'], 'revision': draft['revision'], 'idempotency_key': 'frozen-before-standby'}).json()
    overview = client.get('/api/admin/prompt-standby')
    assert overview.status_code == 200, overview.text
    data = overview.json()
    assert data['current']['id'] == 'default-yoyo-v3'
    assert data['current']['content'] == default['content']
    assert data['items'] == [] and data['activation_available'] is False
    result = client.post('/api/admin/prompt-standby', json={'name': '候选方案', 'content': '仅供待用的新正文', 'note': '先保存观察', 'source_template_id': default['id']})
    assert result.status_code == 200, result.text
    saved = result.json()
    assert saved['status'] == 'standby' and saved['rule_version'] == 'yoyo-v3'
    assert saved['content'] == '仅供待用的新正文'
    assert saved['source_content'] == default['content']
    assert '【素材联动】' in saved['example_prompt']
    assert '围巾手饰兼容补充 v1' not in saved['source_content']
    assert client.get('/api/admin/prompt-templates').json()['items'] == before
    assert all(x['id'] != saved['id'] for x in client.get('/api/prompt-templates').json()['items'])
    assert client.get('/api/production/drafts/'+draft['id']).json() == draft
    assert client.get('/api/production/runs/'+run['id']).json()['snapshot'] == run['snapshot']
    new = client.post('/api/production/drafts', json={}).json()
    assert new['prompt'] == default['content'] and new['prompt_rule_version'] == default['rule_version']
    assert client.post('/api/production/prompt-preview', json=payload).json() == preview
    assert client.get('/api/admin/prompt-standby').json()['items'] == [saved]


def test_standby_records_are_immutable_and_keep_source_snapshot(client):
    source = next(x for x in client.get('/api/admin/prompt-templates').json()['items'] if x['is_default'])
    payload = {'name': '版本一', 'content': '正文一', 'source_template_id': source['id']}
    first = client.post('/api/admin/prompt-standby', json=payload)
    assert first.status_code == 200, first.text
    first = first.json()
    second = client.post('/api/admin/prompt-standby', json={**payload, 'name': '版本二', 'content': '正文二'}).json()
    assert first['id'] != second['id']
    client.put('/api/admin/prompt-templates/'+source['id'], json={'name': source['name'], 'content': '独立修改源模板'})
    items = client.get('/api/admin/prompt-standby').json()['items']
    assert next(x for x in items if x['id'] == first['id']) == first
    assert client.put('/api/admin/prompt-standby/'+first['id'], json=payload).status_code in (404, 405)
    assert client.post('/api/admin/prompt-standby/'+first['id']+'/activate', json={}).status_code in (404, 405)


@pytest.mark.parametrize('extra', [{'activate': True}, {'is_default': True}, {'rule_version': 'legacy-v1'}])
def test_standby_rejects_hidden_activation_or_rule_overrides(client, extra):
    client.get('/api/admin/prompt-templates')
    response = client.post('/api/admin/prompt-standby', json={'name': 'test', 'content': 'test', 'source_template_id': 'default-yoyo-v3', **extra})
    assert response.status_code == 422


def test_standby_validates_missing_source_and_whitespace(client):
    client.get('/api/admin/prompt-templates')
    response = client.post('/api/admin/prompt-standby', json={'name': 'test', 'content': 'test', 'source_template_id': 'missing'})
    assert response.status_code == 404
    for field in ('name', 'content'):
        response = client.post('/api/admin/prompt-standby', json={'name': 'test', 'content': 'test', 'source_template_id': 'default-yoyo-v3', field: '  '})
        assert response.status_code == 422


def test_standby_is_admin_only_and_shared_across_admins(users):
    _, _, (owner, ordinary, admin) = users
    assert ordinary.get('/api/admin/prompt-standby').status_code == 403
    payload = {'name': 'admin pending', 'content': 'admin text', 'source_template_id': 'default-yoyo-v3'}
    assert ordinary.post('/api/admin/prompt-standby', json=payload).status_code == 403
    saved = owner.post('/api/admin/prompt-standby', json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json() in admin.get('/api/admin/prompt-standby').json()['items']
    assert saved.json()['id'] not in {x['id'] for x in owner.get('/api/prompt-templates').json()['items']}


def test_deleted_default_is_reported_without_recreation(client):
    client.get('/api/admin/prompt-templates')
    client.delete('/api/admin/prompt-templates/default-yoyo-v3')
    response = client.get('/api/admin/prompt-standby')
    assert response.status_code == 200, response.text
    assert response.json()['current'] is None
    assert not any(x['is_default'] for x in client.get('/api/admin/prompt-templates').json()['items'])


def test_overview_handles_first_save_database_before_schema_exists(client):
    # A concurrent first save (or an interrupted one) can leave an empty file.
    path = main.settings.storage_dir/'private'/'prompt-standby.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    response = client.get('/api/admin/prompt-standby')
    assert response.status_code == 200, response.text
    assert response.json()['items'] == []
    saved = client.post('/api/admin/prompt-standby', json={'name': '首次保存', 'content': '正文', 'source_template_id': 'default-yoyo-v3'})
    assert saved.status_code == 200, saved.text
    assert client.get('/api/admin/prompt-standby').json()['items'] == [saved.json()]
