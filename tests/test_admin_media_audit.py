"""Direct cross-user media reads are attributable without logging every Range."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from PIL import Image

from app.accounts import Accounts
from tests.test_access_control import protected, accounts_clients
from tests.test_admin_task_records import seed


@pytest.fixture
def media(accounts_clients):
    accounts, users, clients = accounts_clients
    settings, store, runs = seed(users[1])
    run = runs[0]
    output = settings.storage_dir/'outputs'/(run['id']+'.mp4')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b'0123456789')
    defaced = settings.storage_dir/'work'/run['id']/'defaced.mp4'
    defaced.parent.mkdir(parents=True, exist_ok=True)
    defaced.write_bytes(b'redacted-video')
    asset = settings.storage_dir/'assets'/'included.png'
    asset.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (64, 64), 'blue').save(asset)
    store.add_asset('included', 'included.png', 'face', asset, asset.stat().st_size, 'image/png', 'hash')
    # A saved task's material must be a member of its immutable snapshot.
    draft = store.create_draft({'face_asset_ids': ['included']})
    material_run = store.create_run(draft['id'], draft['revision'], 'material-run', {})
    return accounts, users, clients, run, material_run


def events(accounts):
    return [x for x in accounts.list_audit(1000) if x['action']=='view_user_media']


@pytest.mark.parametrize('resource', ['download', 'defaced', 'assets/included/file', 'assets/included/thumbnail'])
def test_direct_cross_user_read_records_actor_owner_run_and_resource(media, resource):
    accounts, users, (admin, _, _), video_run, material_run = media
    run = material_run if resource.startswith('assets/') else video_run
    base = '/api/admin/task-records/'+users[1]['id']+'/'+run['id']
    result = admin.get(base+'/'+resource)
    assert result.status_code == 200, result.text
    recorded = events(accounts)
    assert len(recorded) == 1
    event = recorded[0]
    assert event['actor_id'] == users[0]['id']
    assert event['target'] == users[1]['id']+':'+run['id']
    assert event['details'] == {'resource': resource}
    assert event['created_at'] > 0
    for user in (users[0], users[1]):
        visible = admin.get('/api/admin/audit', params={'user_id': user['id'], 'scope': 'important'}).json()
        assert event['id'] in [x['id'] for x in visible['items']]


def test_list_direct_download_and_range_requests_produce_one_media_event(media):
    accounts, users, (admin, _, _), run, _ = media
    records = admin.get('/api/admin/task-records', params={'user_id': users[1]['id']}).json()
    url = next(x['download_url'] for x in records['items'] if x['id']==run['id'])
    assert events(accounts) == []
    assert admin.head(url).status_code == 200
    assert admin.get(url, headers={'Range': 'bytes=2-5'}).content == b'2345'
    assert admin.get(url, headers={'Range': 'bytes=6-9'}).content == b'6789'
    assert len(events(accounts)) == 1


def test_playback_file_logs_without_a_prior_detail_or_status_request(media, monkeypatch):
    from app import playback
    accounts, users, (admin, _, _), run, _ = media
    # Use the existing original output; do not invoke ffmpeg on fixture bytes.
    monkeypatch.setattr(playback, 'media_path', lambda settings, ident, quality: playback.source_path(settings, ident))
    base = '/api/admin/task-records/'+users[1]['id']+'/'+run['id']
    assert admin.get(base+'/playback/original').content == b'0123456789'
    assert events(accounts)[0]['details'] == {'resource': 'playback/original'}


def test_denied_missing_and_own_media_do_not_create_cross_user_access_events(media):
    accounts, users, (admin, alice, bob), run, _ = media
    base = '/api/admin/task-records/'+users[1]['id']+'/'+run['id']
    assert alice.get(base+'/download').status_code == 403
    assert bob.get(base+'/download').status_code == 403
    assert admin.get(base+'/assets/unrelated/file').status_code == 404
    assert admin.get(base+'/unknown').status_code == 404
    assert admin.get(base.replace(run['id'], 'missing')+'/download').status_code == 404
    assert admin.get(base+'/download', headers={'Range': 'bytes=999-1000'}).status_code == 416
    settings, store, runs = seed(users[0])
    output = settings.storage_dir/'outputs'/(runs[0]['id']+'.mp4')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b'own-video')
    assert admin.get('/api/admin/task-records/'+users[0]['id']+'/'+runs[0]['id']+'/download').status_code == 200
    assert events(accounts) == []


def test_media_audit_dedup_is_atomic_persistent_and_expires(accounts_clients, monkeypatch):
    from app import accounts as module
    accounts, users, _ = accounts_clients
    actor, owner = users[0]['id'], users[1]['id']
    now = [1000.0]
    monkeypatch.setattr(module.time, 'time', lambda: now[0])
    def read(_):
        Accounts(accounts.root).audit_media_access(actor, owner, 'run', 'download')
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(read, range(12)))
    assert len(events(accounts)) == 1
    now[0] += 59
    read(None)
    assert len(events(accounts)) == 1
    now[0] += 2
    read(None)
    assert len(events(accounts)) == 2
    accounts.audit_media_access(actor, owner, 'run', 'defaced')
    accounts.audit_media_access(actor, owner, 'other-run', 'download')
    accounts.audit_media_access(actor, users[2]['id'], 'run', 'download')
    accounts.update_user(users[2]['id'], actor, role='admin')
    accounts.audit_media_access(users[2]['id'], owner, 'run', 'download')
    assert len(events(accounts)) == 6
