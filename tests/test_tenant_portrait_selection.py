"""Tenant portrait selection through real auth, routes, SQLite and local media.

Only official provider transport is replaced. No generation or paid calls run.
"""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main, portrait_service
from app.accounts import Accounts
from app.portrait_library import PortraitLibrary
from app.tenancy import user_settings


INITIAL = 'Initial-password-42!'
CHANGED = 'Changed-password-43!'
ORIGIN = 'http://testserver'


def sign_in(client, username, password=INITIAL):
    response = client.post('/api/auth/login', json={'username': username, 'password': password},
                           headers={'Origin': ORIGIN})
    assert response.status_code == 200, response.text
    identity = response.json()
    client.headers.update({'Origin': ORIGIN, 'X-CSRF-Token': identity['csrf_token']})
    if identity['user']['must_change_password']:
        response = client.post('/api/auth/password', json={'current_password': password, 'new_password': CHANGED})
        assert response.status_code == 200, response.text
        return sign_in(client, username, CHANGED)
    return identity['user']


@pytest.fixture
def portraits(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'true')
    monkeypatch.setenv('APP_COOKIE_SECURE', 'false')
    monkeypatch.delenv('APP_PUBLIC_ORIGIN', raising=False)
    settings = replace(main.settings, storage_dir=tmp_path, seedance_mode='mock')
    monkeypatch.setattr(main, 'settings', settings)
    portrait_service.save_config(settings, {'access_key': 'test-ak', 'secret_key': 'test-sk'})
    Accounts(tmp_path).init_admin('admin', INITIAL)
    # Do not enter lifespan: this test selects existing media, not background jobs.
    admin = TestClient(main.app, base_url=ORIGIN)
    sign_in(admin, 'admin')
    clients = [admin]
    provider = {'assets': {}, 'groups': {}, 'calls': []}

    def remote_request(self, action, payload):
        provider['calls'].append((action, payload['Id']))
        assert action in {'GetAsset', 'GetAssetGroup'}, 'selection must never create remote assets'
        source = provider['assets'] if action == 'GetAsset' else provider['groups']
        return dict(source[payload['Id']])

    monkeypatch.setattr(portrait_service.ArkPortraitClient, '_request', remote_request)
    image = BytesIO()
    Image.new('RGB', (400, 400), 'navy').save(image, format='PNG')
    video_path = tmp_path / 'fixture.mp4'
    writer = cv2.VideoWriter(str(video_path), cv2.VideoWriter_fourcc(*'mp4v'), 24, (640, 640))
    assert writer.isOpened()
    for _ in range(48):
        writer.write(np.full((640, 640, 3), 90, dtype=np.uint8))
    writer.release()
    media = {'face': ('face.png', image.getvalue(), 'image/png'),
             'person_video': ('person.mp4', video_path.read_bytes(), 'video/mp4')}
    owners = {}
    for username, person_type in [('alice', 'LivenessFace'), ('bob', 'AIGC')]:
        created = admin.post('/api/admin/users', json={'username': username, 'password': INITIAL,
                                                      'max_concurrent': 1, 'max_queued': 10})
        assert created.status_code == 201, created.text
        client = TestClient(main.app, base_url=ORIGIN)
        clients.append(client)
        user = sign_in(client, username)
        tenant = user_settings(settings, user)
        lib = PortraitLibrary(tenant)
        group_id = 'group-' + username
        person = lib.add_person(group_id, username + '人物', person_type)
        provider['groups'][group_id] = {'Id': group_id, 'GroupType': person_type, 'ProjectName': 'default'}
        assets, photos = {}, {}
        for kind, file in media.items():
            response = client.post('/api/production/assets', data={'kind': kind}, files={'file': file})
            assert response.status_code == 200, response.text
            asset = response.json()
            photo = lib.enqueue(person['id'], asset['id'])
            remote_id = f'asset-{username}-{kind.replace("_", "-")}'
            lib.update(photo['id'], status='active', remote_id=remote_id)
            provider['assets'][remote_id] = {'Id': remote_id, 'GroupId': group_id, 'ProjectName': 'default',
                                             'AssetType': 'Video' if kind == 'person_video' else 'Image',
                                             'Status': 'Active', 'Name': file[0]}
            assets[kind], photos[kind] = asset, lib.get_photo(photo['id'], private=True)
        owners[username] = SimpleNamespace(client=client, user=user, settings=tenant, lib=lib,
                                           person=person, assets=assets, photos=photos)
    yield SimpleNamespace(owners=owners, provider=provider, admin=admin)
    for client in clients:
        client.close()


def use(owner, kind):
    return owner.client.post('/api/portrait/photos/' + owner.photos[kind]['id'] + '/use', json={})


@pytest.mark.parametrize('kind', ['face', 'person_video'])
def test_own_active_reference_returns_local_person_and_persists_binding(portraits, kind):
    for owner in portraits.owners.values():
        response = use(owner, kind)
        assert response.status_code == 200, response.text
        selected = response.json()
        assert selected['id'] == owner.assets[kind]['id']
        assert selected['person_id'] == owner.person['id']
        assert selected['person_type'] == owner.person['person_type']
        assert selected['kind'] == kind
        assert selected['portrait']['status'] == 'Active'
        assert selected['portrait']['remote_asset_id'] == owner.photos[kind]['remote_id']
        binding = owner.lib.store.portrait_binding(selected['id'])
        assert binding['group_id'] == owner.lib.person(owner.person['id'], private=True)['group_id']
        assert binding['project'] == 'default' and binding['fingerprint'] == owner.lib.account
        assert 'path' not in selected and 'fingerprint' not in selected['portrait']
        assert owner.client.get(selected['url']).status_code == 200
        foreign = next(item for item in portraits.owners.values() if item is not owner)
        assert foreign.client.get(selected['url']).status_code == 404


@pytest.mark.parametrize('kind', ['face', 'person_video'])
def test_ungranted_real_and_foreign_virtual_reference_are_404_without_remote_lookup(portraits, kind):
    alice, bob = portraits.owners.values()
    policy = portraits.admin.put('/api/admin/portrait-access/' + alice.person['id'],
                                json={'mode': 'selected', 'user_ids': [alice.user['id']]})
    assert policy.status_code == 200, policy.text
    for owner, foreign in [(alice, bob), (bob, alice)]:
        response = owner.client.post('/api/portrait/photos/' + foreign.photos[kind]['id'] + '/use', json={})
        assert response.status_code == 404, response.text
    assert portraits.provider['calls'] == []


@pytest.mark.parametrize('kind', ['face', 'person_video'])
def test_shared_real_reference_can_be_selected_but_revocation_blocks_reuse(portraits, kind):
    alice, bob = portraits.owners.values()
    endpoint = '/api/portrait/photos/' + alice.photos[kind]['id'] + '/use'
    gallery = bob.client.get('/api/portrait/people/' + alice.person['id'] + '/photos')
    assert gallery.status_code == 200, gallery.text
    assert all(photo['can_manage'] is False for photo in gallery.json()['items'])
    selected = bob.client.post(endpoint, json={})
    assert selected.status_code == 200, selected.text
    asset = selected.json()
    assert asset['person_id'] == alice.person['id']
    assert asset['id'] != alice.assets[kind]['id']
    assert bob.client.get(asset['url']).status_code == 200
    policy = portraits.admin.put('/api/admin/portrait-access/' + alice.person['id'],
                                json={'mode': 'selected', 'user_ids': [alice.user['id']]})
    assert policy.status_code == 200, policy.text
    portraits.provider['calls'].clear()
    assert bob.client.post(endpoint, json={}).status_code == 404
    assert bob.client.get(asset['url']).status_code == 404
    assert portraits.provider['calls'] == []


@pytest.mark.parametrize('kind', ['face', 'person_video'])
@pytest.mark.parametrize('mismatch', ['group', 'project', 'asset_type', 'person_type', 'inactive'])
def test_reference_mismatch_rejected_without_binding(portraits, kind, mismatch):
    owner = portraits.owners['alice']
    remote = portraits.provider['assets'][owner.photos[kind]['remote_id']]
    if mismatch == 'group':
        remote['GroupId'] = 'group-bob'
        # Keep the cloud group type valid, so only the local ownership check rejects it.
        portraits.provider['groups']['group-bob']['GroupType'] = 'LivenessFace'
    elif mismatch == 'project':
        remote['ProjectName'] = 'foreign-project'
    elif mismatch == 'asset_type':
        remote['AssetType'] = 'Video' if kind == 'face' else 'Image'
    elif mismatch == 'person_type':
        portraits.provider['groups']['group-alice']['GroupType'] = 'AIGC'
    else:
        remote['Status'] = 'Processing'
    response = use(owner, kind)
    assert response.status_code == 422, response.text
    assert owner.lib.store.portrait_binding(owner.assets[kind]['id']) is None


@pytest.mark.parametrize('kind', ['face', 'person_video'])
def test_reference_local_file_must_stay_in_own_storage(portraits, kind):
    alice, bob = portraits.owners.values()
    foreign = bob.lib.store.get_asset(bob.assets[kind]['id'], private=True)
    with alice.lib.store.connection() as db:
        db.execute('UPDATE production_assets SET path=? WHERE id=?', (foreign['path'], alice.assets[kind]['id']))
    assert use(alice, kind).status_code == 422
    assert portraits.provider['calls'] == []


def test_ordinary_global_import_and_resolve_remain_forbidden(portraits):
    for owner in portraits.owners.values():
        for path, body in [('/api/portrait/import', {'remote_asset_id': owner.photos['face']['remote_id']}),
                           ('/api/portrait/people/resolve', {'group_id': 'group-alice', 'person_type': 'LivenessFace'})]:
            assert owner.client.post(path, json=body).status_code == 403
    assert portraits.provider['calls'] == []


@pytest.fixture(scope='module')
def selection_browser():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(headless=True)
        except playwright.Error:
            try:
                browser = pw.chromium.launch(channel='msedge', headless=True)
            except playwright.Error:
                pytest.skip('Chromium or Edge is required for the selection browser regression')
        yield browser
        browser.close()


@pytest.mark.parametrize('kind', ['face', 'person_video'])
def test_browser_click_selects_own_reference_without_global_resolution(portraits, selection_browser, kind):
    """Real production HTML/JS, cookies, auth and local routes; no stubbed APIs."""
    from urllib.parse import urlsplit
    from playwright.sync_api import expect

    owner = portraits.owners['bob']
    context = selection_browser.new_context(viewport={'width': 1440, 'height': 1000})
    context.add_cookies([{'name': 'ark_session', 'value': owner.client.cookies['ark_session'],
                         'url': ORIGIN, 'httpOnly': True, 'sameSite': 'Lax'}])
    transport = TestClient(main.app, base_url=ORIGIN)
    calls, errors = [], []

    def route(request_route):
        request = request_route.request
        assert urlsplit(request.url).netloc == 'testserver', 'No external browser requests are allowed'
        transport.cookies.clear()
        response = transport.request(request.method, request.url, content=request.post_data_buffer,
            headers={key: value for key, value in request.all_headers().items()
                     if key.lower() not in {'host', 'content-length'}}, follow_redirects=False)
        calls.append((request.method, urlsplit(request.url).path, response.status_code))
        request_route.fulfill(status=response.status_code, body=response.content,
            headers={key: value for key, value in response.headers.items()
                     if key.lower() not in {'content-length', 'content-encoding', 'transfer-encoding'}})

    context.route('**/*', route)
    page = context.new_page()
    page.set_default_timeout(12000)
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(ORIGIN + '/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        if kind == 'person_video':
            page.locator('[data-person-media="video"]').click()
        page.locator('#person-picker summary').click()
        page.locator('[data-person-id="' + owner.person['id'] + '"]').click()
        page.locator('[data-photo-id="' + owner.photos[kind]['id'] + '"] [data-photo-use]').click()
        expect(page.locator('#person-photos-dialog')).not_to_be_visible()
        expect(page.locator('#person-current')).to_contain_text(owner.person['name'])
        assert page.evaluate('window.productionPortraits.currentPhoto.id') == owner.assets[kind]['id']
        draft = owner.client.get('/api/production/drafts').json()['items'][0]
        assert draft['person_id'] == owner.person['id']
        assert draft['person_input_policy'] == 'existing_person'
        assert draft['person_reference_mode'] == ('video' if kind == 'person_video' else 'image')
        if kind == 'face':
            assert draft['face_asset_ids'] == [owner.assets[kind]['id']]
        else:
            assert draft['person_video_asset_id'] == owner.assets[kind]['id']
        assert ('POST', '/api/portrait/photos/' + owner.photos[kind]['id'] + '/use', 200) in calls
        # A missing or foreign local ID must never fall back to global resolution.
        rejected = page.evaluate("""async foreignId => {
            const asset = window.productionPortraits.currentPhoto;
            const results = [];
            for (const person_id of [foreignId, null]) {
                try { await window.productionPortraits.importAsset({...asset, person_id}); results.push(false); }
                catch (_) { results.push(true); }
            }
            return results;
        }""", portraits.owners['alice'].person['id'])
        assert rejected == [True, True]
        assert page.evaluate('window.portraitPeople.selected') == owner.person['id']
        assert not any(path in {'/api/portrait/import', '/api/portrait/people/resolve'} for _, path, _ in calls)
        assert not errors
    finally:
        context.close()
        transport.close()
