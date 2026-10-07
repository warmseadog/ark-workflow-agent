from dataclasses import replace
import json

import pytest
from fastapi.testclient import TestClient
from app import main, production_worker


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path, seedance_mode='mock'))
    monkeypatch.setattr(production_worker, 'wake', lambda settings: None)
    return TestClient(main.app)


def test_saved_defaults_survive_reload_and_only_change_new_drafts(client):
    old = client.post('/api/production/drafts', json={}).json()
    result = client.put('/api/redaction-settings', json={'mask_scale': 1.8, 'keep_audio': False})
    assert result.status_code == 200, result.text
    saved = result.json()['config']
    assert saved['mask_scale'] == 1.8 and saved['keep_audio'] is False
    assert TestClient(main.app).get('/api/redaction-settings').json()['config'] == saved
    assert client.post('/api/production/drafts', json={}).json()['mask'] == saved
    assert client.get('/api/production/drafts/' + old['id']).json()['mask'] == old['mask']
    assert client.post('/api/production/drafts', json={'copy_from': old['id']}).json()['mask'] == old['mask']
    assert client.get('/api/admin/overview').json()['redaction'] == saved


@pytest.mark.parametrize('bad', [
    {'mask_scale': 0}, {'mask_scale': True}, {'threshold': 2}, {'keep_audio': 'yes'},
    {'blur_style': 'img'}, {'replace_image': '/secret'}, {'unknown': 1},
    {'mask_mode': 'face_hair_all', 'blur_style': 'blur'},
    {'mask_mode': 'face_hair_all'},
])
def test_invalid_defaults_leave_saved_config_intact(client, bad):
    baseline = client.get('/api/redaction-settings')
    assert baseline.status_code == 200
    assert client.put('/api/redaction-settings', json=bad).status_code == 422
    assert client.get('/api/redaction-settings').json() == baseline.json()


def test_redaction_settings_enforce_local_and_origin_guards(client):
    assert client.get('/api/redaction-settings', headers={'Host': 'public.example'}).status_code == 403
    assert client.put('/api/redaction-settings', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403


def test_retired_target_defaults_become_face_without_rewriting_existing_drafts(client):
    from app import redaction_settings
    from app.production_store import ProductionStore
    values = client.get('/api/redaction-settings').json()['config']
    old_values = {**values, 'mask_mode': 'face_hair_all', 'mask_scale': 1.8}
    path = redaction_settings.config_path(main.settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(old_values), encoding='utf-8')
    old = ProductionStore(main.settings.storage_dir).create_draft({'mask': old_values})
    current = client.get('/api/redaction-settings').json()['config']
    assert current['mask_mode'] == 'face'
    assert current['mask_scale'] == 1.8
    assert client.post('/api/production/drafts', json={}).json()['mask'] == current
    assert client.get('/api/production/drafts/'+old['id']).json()['mask'] == old_values
    assert client.put('/api/redaction-settings', json={'mask_scale': 1.4}).json()['config']['mask_mode'] == 'face'
    assert json.loads(path.read_text(encoding='utf-8'))['mask_mode'] == 'face'
