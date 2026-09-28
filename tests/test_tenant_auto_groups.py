"""Cloud recovery must not infer tenant ownership from shared image content."""
import base64
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

from PIL import Image
import pytest

from app.config import settings
from app import portrait_service as service
from app.portrait_library import PortraitLibrary
from app.tenancy import user_settings


@pytest.fixture
def shared_cloud(tmp_path, monkeypatch):
    base = replace(settings, storage_dir=tmp_path, config_root=None, user_id='')
    service.save_config(base, {'access_key':'shared-ak', 'secret_key':'shared-sk'})
    cloud = SimpleNamespace(groups={}, creates=[], reads=[], outcomes=[], fail_get=False)

    def request(client, action, payload):
        if action == 'CreateAssetGroup':
            cloud.creates.append(dict(payload))
            outcome = cloud.outcomes.pop(0) if cloud.outcomes else 'success'
            if outcome == 'no_remote':
                raise service.PortraitError('Connection failed before cloud create')
            ident = 'group-' + str(len(cloud.creates))
            cloud.groups[ident] = {'Id':ident, 'Name':payload['Name'],
                                   'GroupType':'AIGC', 'ProjectName':'default'}
            if outcome == 'lost_response':
                raise service.PortraitError('Cloud created group but response was lost')
            if outcome == 'lost_get':
                cloud.fail_get = True
            return {'Id':ident}
        if action == 'GetAssetGroup':
            cloud.reads.append(payload['Id'])
            if cloud.fail_get:
                cloud.fail_get = False
                raise service.PortraitError('Cloud confirmation unavailable')
            return dict(cloud.groups[payload['Id']])
        assert action == 'ListAssetGroups', (action, payload)
        # Deliberately expose every tenant's groups through one shared account.
        return {'Items':[dict(group) for group in cloud.groups.values()]}

    monkeypatch.setattr(service.ArkPortraitClient, '_request', request)
    return base, cloud


def tenant(base, ident):
    return PortraitLibrary(user_settings(base, {'id':ident, 'legacy_owner':False}))


def picture(library, ident='image'):
    path = library.settings.storage_dir/'assets'/(ident+'.png')
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new('RGB', (400, 400), 'blue').save(path)
    library.store.add_asset(ident, path.name, 'face', path, path.stat().st_size,
                            'image/png', hashlib.sha256(path.read_bytes()).hexdigest())
    return ident


def request_row(library, request_id):
    with library.store.connection() as db:
        return dict(db.execute('SELECT * FROM portrait_group_requests WHERE account=? AND request_id=?',
                               (library.account, request_id)).fetchone())


def test_failed_b_create_never_recovers_same_content_a_group(shared_cloud):
    base, cloud = shared_cloud
    a, b = tenant(base, 'a'*32), tenant(base, 'b'*32)
    a_image, b_image = picture(a), picture(b)
    assert a.account == b.account
    assert a.store.get_asset(a_image)['sha256'] == b.store.get_asset(b_image)['sha256']
    created_a = a.ensure_auto_virtual([a_image])
    assert created_a['status'] == 'ready'
    cloud.outcomes.append('no_remote')
    failed_b = b.ensure_auto_virtual([b_image])
    assert failed_b['status'] == 'uncertain'
    recovered_b = PortraitLibrary(b.settings).ensure_auto_virtual([b_image])
    assert recovered_b['status'] == 'uncertain'
    assert recovered_b['person_id'] is None
    assert recovered_b['request_id'] == failed_b['request_id']
    assert request_row(b, recovered_b['request_id'])['remote_group_id'] is None
    with b.store.connection() as db:
        assert db.execute('SELECT count(*) FROM portrait_people').fetchone()[0] == 0
    assert len(cloud.creates) == 2  # Retry must reconcile, never create again.
    assert cloud.creates[0]['Name'] != cloud.creates[1]['Name']


@pytest.mark.parametrize('outcome', ['lost_response', 'lost_get'])
def test_b_recovers_its_own_uncertain_group_among_shared_cloud_groups(shared_cloud, outcome):
    base, cloud = shared_cloud
    a, b = tenant(base, 'a'*32), tenant(base, 'b'*32)
    a.ensure_auto_virtual([picture(a)])
    b_image = picture(b)
    cloud.outcomes.append(outcome)
    uncertain = b.ensure_auto_virtual([b_image])
    assert uncertain['status'] == 'uncertain'
    before = request_row(b, uncertain['request_id'])
    assert before['remote_group_id'] == ('group-2' if outcome == 'lost_get' else None)
    restored = PortraitLibrary(b.settings)
    recovered = restored.ensure_auto_virtual([b_image])
    assert recovered['status'] == 'ready'
    assert recovered['request_id'] == uncertain['request_id']
    assert restored.person(recovered['person_id'], private=True)['group_id'] == 'group-2'
    assert len(cloud.creates) == 2
    assert cloud.creates[0]['Name'] != cloud.creates[1]['Name']


def test_names_and_request_ids_are_stable_per_user_and_distinct_between_users(shared_cloud):
    base, cloud = shared_cloud
    a, b = tenant(base, 'a'*32), tenant(base, 'b'*32)
    first = a.ensure_auto_virtual([picture(a, 'first-upload')])
    duplicate = PortraitLibrary(a.settings).ensure_auto_virtual([picture(a, 'same-content')])
    other = b.ensure_auto_virtual([picture(b)])
    assert first['status'] == duplicate['status'] == other['status'] == 'ready'
    assert duplicate['request_id'] == first['request_id']
    assert duplicate['person_id'] == first['person_id']
    assert other['request_id'] != first['request_id']
    assert len(cloud.creates) == 2
    names = [request_row(lib, result['request_id'])['name'] for lib, result in ((a, first), (b, other))]
    assert names[0] != names[1] and all(len(name) <= 60 for name in names)
    # The ownership key survives storage relocation; it must not hash disk paths.
    relocated = replace(base, storage_dir=base.storage_dir/'relocated')
    service.save_config(relocated, {'access_key':'shared-ak', 'secret_key':'shared-sk'})
    moved_a = tenant(relocated, 'a'*32)
    moved = moved_a.ensure_auto_virtual([picture(moved_a)])
    assert moved['request_id'] == first['request_id']
    assert cloud.creates[-1]['Name'] == names[0]


def test_initial_admin_recovers_legacy_name_with_unchanged_request_id(shared_cloud):
    base, cloud = shared_cloud
    # Old callers really omit both tenant attributes.
    legacy = PortraitLibrary(SimpleNamespace(storage_dir=base.storage_dir))
    image = picture(legacy)
    source = legacy.store.get_asset(image)
    old_digest = hashlib.sha256(json.dumps(
        ['auto-virtual-v1', legacy.account, 'face', source['sha256']],
        separators=(',', ':')).encode()).digest()
    old_id = 'auto-' + old_digest.hex()
    old_name = 'auto-v-' + base64.b32encode(old_digest).decode().rstrip('=').lower()
    cloud.outcomes.append('lost_response')
    initial = legacy.ensure_auto_virtual([image])
    assert initial['status'] == 'uncertain' and initial['request_id'] == old_id
    assert cloud.creates[0]['Name'] == old_name
    admin_settings = user_settings(base, {'id':'c'*32, 'legacy_owner':True})
    assert admin_settings.user_id and admin_settings.storage_dir == admin_settings.config_root
    admin = PortraitLibrary(admin_settings)
    recovered = admin.ensure_auto_virtual([image])
    assert recovered['status'] == 'ready' and recovered['request_id'] == old_id
    assert admin.person(recovered['person_id'], private=True)['group_id'] == 'group-1'
    assert request_row(admin, old_id)['name'] == old_name
    assert len(cloud.creates) == 1


def test_new_user_never_falls_back_to_legacy_unscoped_cloud_name(shared_cloud):
    base, cloud = shared_cloud
    legacy = PortraitLibrary(SimpleNamespace(storage_dir=base.storage_dir))
    legacy_result = legacy.ensure_auto_virtual([picture(legacy)])
    b = tenant(base, 'b'*32)
    b_image = picture(b)
    # Even a historical unscoped uncertain request in B's local DB must not
    # direct new tenant recovery to the old shared cloud name.
    old = request_row(legacy, legacy_result['request_id'])
    with b.store.connection() as db:
        db.execute('''INSERT INTO portrait_group_requests
            (account,request_id,name,status,person_id,message,remote_group_id,auto_kind,auto_sha256)
            VALUES (?,?,?,'uncertain',NULL,'old request',NULL,?,?)''',
            (b.account, old['request_id'], old['name'], old['auto_kind'], old['auto_sha256']))
    cloud.outcomes.append('no_remote')
    first = b.ensure_auto_virtual([b_image])
    retried = PortraitLibrary(b.settings).ensure_auto_virtual([b_image])
    assert first['status'] == retried['status'] == 'uncertain'
    assert first['request_id'] != old['request_id']
    assert retried['person_id'] is None
    assert len(cloud.creates) == 2
    assert cloud.creates[-1]['Name'] != old['name']


@pytest.mark.parametrize('malformation', [
    'missing_id', 'empty_id', 'invalid_id', 'non_string_id', 'missing_root',
    'null_root', 'empty_root', 'mismatched_storage', 'outside_root',
])
def test_malformed_tenant_context_fails_before_group_request_or_cloud(shared_cloud, malformation):
    base, cloud = shared_cloud
    b = tenant(base, 'b'*32)
    image = picture(b)
    values = {'storage_dir':b.settings.storage_dir, 'config_root':base.storage_dir, 'user_id':'b'*32}
    if malformation == 'missing_id': values.pop('user_id')
    elif malformation == 'empty_id': values['user_id'] = ''
    elif malformation == 'invalid_id': values['user_id'] = '../other-user'
    elif malformation == 'non_string_id': values['user_id'] = None
    elif malformation == 'missing_root': values.pop('config_root')
    elif malformation == 'null_root': values['config_root'] = None
    elif malformation == 'empty_root': values['config_root'] = ''
    elif malformation == 'mismatched_storage': values['user_id'] = 'a'*32
    elif malformation == 'outside_root': values['config_root'] = base.storage_dir/'other-root'
    # Constructor/config loading is outside this fix's scope. Supply the bad
    # context to the real method on an otherwise initialized local library.
    b.settings = SimpleNamespace(**values)
    with pytest.raises(ValueError, match='租户'):
        b.ensure_auto_virtual([image])
    with b.store.connection() as db:
        assert db.execute('SELECT count(*) FROM portrait_group_requests').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM portrait_people').fetchone()[0] == 0
    assert not cloud.creates and not cloud.reads
