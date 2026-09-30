from dataclasses import replace
import json

import pytest
from fastapi.testclient import TestClient
from tests.test_access_control import protected, accounts_clients
from tests.test_account_frontend import browser


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import main
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path, config_root=None))
    return TestClient(main.app)


def test_settings_roundtrip_secrets_and_endpoint_binding(client):
    response = client.get('/api/continuation-settings')
    assert response.status_code == 200
    assert response.json()['config']['has_api_key'] is False
    saved = client.put('/api/continuation-settings', json={'api_key': 'private-test-key', 'skill': '保持人物身份，接着结尾继续走动。'})
    assert saved.status_code == 200
    config = saved.json()['config']
    assert 'private-test-key' not in saved.text and 'api_key' not in config
    assert config['has_api_key'] is True and len(config['skill_version']) == 64
    assert client.put('/api/continuation-settings', json={'api_key': '', 'model': 'other-model'}).json()['config']['has_api_key'] is True
    assert client.put('/api/continuation-settings', json={'base_url': 'https://other.example/v3'}).json()['config']['has_api_key'] is False
    assert client.put('/api/continuation-settings', json={'api_key': 'new-key'}).json()['config']['has_api_key'] is True
    assert client.put('/api/continuation-settings', json={'clear_api_key': True}).json()['config']['has_api_key'] is False


@pytest.mark.parametrize('payload', [
    {'enabled': 'yes'}, {'timeout_seconds': 181}, {'timeout_seconds': True},
    {'skill': ''}, {'skill': 'a' * 20001}, {'model': 'bad\nmodel'},
    {'base_url': 'https://secret@example.com'}, {'clear_api_key': 'yes'},
    {'api_key': 'key\nsecret'}, {'unknown': 'value'},
])
def test_invalid_settings_are_atomic(client, payload):
    before = client.get('/api/continuation-settings').json()
    assert client.put('/api/continuation-settings', json=payload).status_code == 422
    assert client.get('/api/continuation-settings').json() == before


def test_settings_local_origin_guards(client):
    assert client.get('/api/continuation-settings', headers={'Host': 'public.example'}).status_code == 403
    assert client.put('/api/continuation-settings', json={}, headers={'Origin': 'https://evil.example'}).status_code == 403


def test_malformed_settings_shape_does_not_echo_submitted_secret(client):
    response = client.put('/api/continuation-settings', json=[{'api_key': 'malformed-shape-secret'}])
    assert response.status_code == 422
    assert 'malformed-shape-secret' not in response.text


def test_configuration_is_global_and_snapshot_roundtrips(tmp_path):
    from app.config import settings
    from app.continuation_settings import ContinuationConfig, load_config, save_config
    from dataclasses import asdict
    tenant = replace(settings, storage_dir=tmp_path / 'users' / 'one', config_root=tmp_path)
    saved = save_config(tenant, {'api_key': 'private-test-key'})
    assert load_config(replace(tenant, storage_dir=tmp_path / 'users' / 'two')) == saved
    assert ContinuationConfig(**asdict(saved)) == saved
    assert 'private-test-key' not in repr(saved)
    assert (tmp_path / 'private' / 'continuation-settings.json').is_file()


def test_continuation_configuration_requires_current_admin(accounts_clients):
    _, _, (admin, alice, bob) = accounts_clients
    for regular in (alice, bob):
        assert regular.get('/api/continuation-settings').status_code == 403
        assert regular.put('/api/continuation-settings', json={'skill': 'forbidden'}).status_code == 403
    response = admin.put('/api/continuation-settings', json={'api_key': 'admin-only-secret'})
    assert response.status_code == 200
    assert response.json()['config']['has_api_key'] is True
    assert 'admin-only-secret' not in response.text
    assert admin.get('/api/continuation-settings').json() == response.json()
    assert admin.put('/api/continuation-settings', json={'base_url': 'https://new.example/v3'}).json()['config']['has_api_key'] is False


@pytest.mark.parametrize('width', [1440, 390])
def test_admin_continuation_form_saves_and_clears_secret(browser, client, tmp_path, width):
    from playwright.sync_api import expect
    context = browser.new_context(viewport={'width': width, 'height': 950})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def route(r):
        req = r.request
        res = client.request(req.method, req.url, content=req.post_data_buffer,
                             headers={k: v for k, v in req.headers.items() if k not in ('host', 'content-length')})
        r.fulfill(status=res.status_code, body=res.content,
                  headers={k: v for k, v in res.headers.items() if k not in ('content-length', 'content-encoding', 'transfer-encoding')})
    page.route('**/*', route)
    try:
        page.goto('http://testserver/admin/settings#continuation')
        form = page.locator('#continuation-form')
        expect(form.locator('fieldset')).to_be_enabled()
        form.locator('[name=api_key]').fill('ui-test-secret')
        form.locator('[name=skill]').fill('从实际结尾接续正常步伐，保持服装、发型和人物身份。')
        form.locator('button[type=submit]').click()
        expect(page.locator('#continuation-status')).to_contain_text('已保存')
        expect(page.locator('#continuation-key-status')).to_have_text('已保存')
        expect(form.locator('[name=api_key]')).to_have_value('')
        page.reload()
        expect(form.locator('[name=skill]')).to_have_value('从实际结尾接续正常步伐，保持服装、发型和人物身份。')
        expect(form.locator('[name=api_key]')).to_have_value('')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(tmp_path / f'continuation-{width}.png'), full_page=True)
        form.locator('[name=clear_api_key]').check()
        form.locator('button[type=submit]').click()
        expect(page.locator('#continuation-key-status')).to_have_text('未配置')
        assert not errors, errors
    finally:
        context.close()
