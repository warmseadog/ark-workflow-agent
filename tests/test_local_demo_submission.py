from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import main, generation_settings, person_preparation, production_worker
from tests.test_production_api import complete_draft


@pytest.mark.parametrize('profile,host,auth,expected', [
    (None, '127.0.0.1', 'true', False),
    (None, '127.0.0.1', 'false', False),
    ('integration', '127.0.0.1', 'false', False),
    ('demo', '127.0.0.1', 'true', False),
    ('demo', '0.0.0.0', 'false', False),
    ('demo', '127.0.0.1', 'false', True),
])
def test_demo_requires_explicit_profile_loopback_and_disabled_auth(monkeypatch, profile, host, auth, expected):
    from app.config import local_demo_enabled
    if profile is None:
        monkeypatch.delenv('APP_LOCAL_DEV_PROFILE', raising=False)
    else:
        monkeypatch.setenv('APP_LOCAL_DEV_PROFILE', profile)
    monkeypatch.setenv('APP_HOST', host)
    monkeypatch.setenv('APP_AUTH_ENABLED', auth)
    assert local_demo_enabled() is expected


@pytest.mark.parametrize('mode', ['mock', 'http'])
def test_mock_auto_virtual_needs_no_cloud_but_http_still_validates(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path, config_root=tmp_path))
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    monkeypatch.setenv('APP_LOCAL_DEV_PROFILE', 'demo')
    monkeypatch.setenv('APP_HOST', '127.0.0.1')
    monkeypatch.setattr(production_worker, 'wake', lambda *_: None)
    generation_settings.save_config(main.settings, {
        'provider':'ark','protocol':'ark','mode':mode,
        'base_url':'https://ark.cn-beijing.volces.com/api/v3',
        'model':'doubao-seedance-2-0-260128','api_key':'test-key' if mode == 'http' else '',
        'public_base_url':'https://test.invalid'})
    calls = []
    def preflight(*args):
        calls.append(True)
        raise ValueError('cloud-validation-required')
    monkeypatch.setattr(person_preparation, 'preflight', preflight)
    client = TestClient(main.app)
    draft = complete_draft(client)
    response = client.put('/api/production/drafts/'+draft['id'], json={
        'revision':draft['revision'],'person_input_policy':'auto_virtual'})
    assert response.status_code == 200, response.text
    draft = response.json()
    result = client.post('/api/production/runs', json={
        'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'demo-submit'})
    if mode == 'mock':
        assert result.status_code == 200, result.text
        assert not calls
        from app.production_store import ProductionStore
        private = ProductionStore(tmp_path).get_run(result.json()['id'], private=True)['private']
        assert 'person_preparation' not in private and 'portrait' not in private
    else:
        assert result.status_code == 422 and 'cloud-validation-required' in result.text
        assert calls == [True]


def test_local_demo_person_video_completes_without_cloud_identity(tmp_path, monkeypatch):
    from tests.test_person_video import video_bytes
    from app.production_store import ProductionStore
    import shutil
    monkeypatch.setenv('APP_LOCAL_DEV_PROFILE', 'demo')
    monkeypatch.setenv('APP_HOST', '127.0.0.1')
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    monkeypatch.setattr(main, 'settings', replace(main.settings,storage_dir=tmp_path,config_root=tmp_path))
    monkeypatch.setattr(production_worker, 'wake', lambda *_: None)
    monkeypatch.setattr(production_worker, 'run_deface', lambda src,dst,*args:shutil.copyfile(src,dst))
    client = TestClient(main.app)
    draft = complete_draft(client)
    uploaded = client.post('/api/production/assets', data={'kind':'person_video'},
        files={'file':('person.mp4',video_bytes(tmp_path,3),'video/mp4')})
    assert uploaded.status_code == 200, uploaded.text
    response = client.put('/api/production/drafts/'+draft['id'],json={
        'revision':draft['revision'],'person_input_policy':'auto_virtual',
        'person_reference_mode':'video','person_video_asset_id':uploaded.json()['id'],
        'face_asset_ids':[]})
    assert response.status_code == 200, response.text
    draft = response.json()
    response = client.post('/api/production/runs',json={
        'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'demo-person-video'})
    assert response.status_code == 200,response.text
    store = ProductionStore(tmp_path)
    production_worker.execute_run(main.settings,store,store.claim_next())
    result = store.get_run(response.json()['id'])
    assert result['status'] == 'succeeded', result.get('error') or result.get('message')
