import pytest
from app import main
from app.mediakit_redaction import parameters
from app.production_worker import mask_options
from tests.test_redaction_settings import client


def test_profiles_are_independent_and_native_values_reach_new_drafts(client):
    assert client.put('/api/redaction-settings', json={'mask_scale': 3.0, 'mosaic_size': 60}).status_code == 200
    native = {'mask_mode': 'blur', 'mask_strength': 'high', 'face_box_expand': 1.0, 'face_confidence': 0.6}
    response = client.put('/api/redaction-settings', json={'profile': 'mediakit', 'values': native})
    assert response.status_code == 200, response.text
    assert client.get('/api/redaction-settings').json()['profiles']['mediakit'] == native
    client.put('/api/redaction-service', json={'mode': 'http', 'endpoint': 'https://mediakit.cn-beijing.volces.com'})
    mask = client.post('/api/production/drafts', json={}).json()['mask']
    assert parameters(mask_options(mask)) == native
    client.put('/api/redaction-service', json={'mode': 'local'})
    mask = client.post('/api/production/drafts', json={}).json()['mask']
    assert mask['mask_scale'] == 3 and mask['mosaic_size'] == 60
    assert client.get('/api/redaction-settings').json()['profiles']['mediakit'] == native


@pytest.mark.parametrize('bad', [
    {'face_box_expand': 0}, {'face_box_expand': 1.1}, {'face_box_expand': True}, {'face_box_expand': 1e-20},
    {'face_confidence': 0.09}, {'face_confidence': 1.1}, {'face_confidence': '0.3'},
    {'mask_mode': 'solid'}, {'mask_strength': 60}, {'mosaic_size': 60},
])
def test_native_profile_validation_is_atomic(client, bad):
    before = client.get('/api/redaction-settings').json()
    assert 'profiles' in before
    response = client.put('/api/redaction-settings', json={'profile': 'mediakit', 'values': bad})
    assert response.status_code == 422
    assert client.get('/api/redaction-settings').json() == before


def test_legacy_local_file_is_preserved_and_cloud_defaults_are_valid(client):
    client.put('/api/redaction-settings', json={'mask_scale': 3.0, 'mosaic_size': 60})
    data = client.get('/api/redaction-settings').json()
    assert data['profiles']['local']['mask_scale'] == 3
    assert data['profiles']['mediakit']['face_box_expand'] == 0.2
    assert data['profiles']['mediakit']['face_confidence'] == 0.35


def test_generic_http_migrates_legacy_settings_once(client):
    import json
    from app import redaction_settings
    from app.redaction_service import save_config
    values = redaction_settings._defaults(main.settings)
    values.update(mask_scale=2.8, mosaic_size=77)
    path = redaction_settings.config_path(main.settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values), encoding='utf-8')
    save_config(main.settings, {'mode': 'http', 'endpoint': 'https://mask.example/process'})
    assert client.get('/api/redaction-settings').json()['config']['mask_scale'] == 2.8
    client.put('/api/redaction-settings', json={'mask_scale': 1.5})
    data = client.get('/api/redaction-settings').json()
    assert data['profiles']['http']['mask_scale'] == 2.8
    assert data['profiles']['local']['mask_scale'] == 1.5
