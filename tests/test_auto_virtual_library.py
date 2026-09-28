"""Automatic preparation must reuse exact identities without repeated cloud creates."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from threading import Event
from types import SimpleNamespace

from PIL import Image
import pytest

from app import portrait_service as service
from app.portrait_library import PortraitLibrary


@pytest.fixture
def library(tmp_path, monkeypatch):
    settings = SimpleNamespace(storage_dir=tmp_path)
    service.save_config(settings, {'access_key': 'test-ak', 'secret_key': 'test-sk'})
    monkeypatch.setattr(service.ArkPortraitClient, '_request',
                        lambda *args: pytest.fail('Unexpected cloud call'))
    return PortraitLibrary(settings)


def asset(lib, ident, color='red', kind='face'):
    root = lib.settings.storage_dir / 'assets'
    root.mkdir(exist_ok=True)
    path = root / (ident + '.png')
    Image.new('RGB', (400, 400), color).save(path)
    lib.store.add_asset(ident, path.name, kind, path, path.stat().st_size,
                        'image/png', hashlib.sha256(path.read_bytes()).hexdigest())
    return ident


def group(ident='group-auto', **values):
    return {'Id': ident, 'GroupType': 'AIGC', 'ProjectName': 'default', **values}


def bind(lib, asset_id, group_id):
    lib.store.bind_portrait(asset_id, {'remote_asset_id': 'asset-bound', 'group_id': group_id,
                                     'project': 'default', 'status': 'Active'}, lib.account)


def test_resolve_reuses_photo_queue_by_content_without_cloud_or_writes(library):
    lib = library
    person = lib.add_person('group-v', 'Virtual', 'AIGC')
    original, duplicate = asset(lib, 'original'), asset(lib, 'duplicate')
    job = lib.enqueue(person['id'], original)
    with lib.store.connection() as db:
        before = db.execute('SELECT count(*) FROM portrait_group_requests').fetchone()[0]
    resolved = lib.resolve_virtual_assets([duplicate])
    assert resolved['id'] == person['id'] and resolved['group_id'] == 'group-v'
    assert resolved['person_type'] == 'AIGC' and not resolved['verified']
    assert lib.enqueue(resolved['id'], duplicate)['id'] == job['id']
    with lib.store.connection() as db:
        assert db.execute('SELECT count(*) FROM portrait_group_requests').fetchone()[0] == before


def test_resolve_reuses_official_binding_even_without_photo_queue(library):
    person = library.add_person('group-v', 'Virtual', 'AIGC')
    original, duplicate = asset(library, 'original'), asset(library, 'duplicate')
    bind(library, original, 'group-v')
    assert library.resolve_virtual_assets([duplicate])['id'] == person['id']


@pytest.mark.parametrize('conflict', ['real', 'hidden', 'multiple', 'other_reference', 'unmapped_binding'])
def test_resolve_rejects_every_incompatible_association(library, conflict):
    first = asset(library, 'first')
    second = asset(library, 'second', 'blue')
    person = library.add_person('group-v', 'Virtual', 'AIGC')
    library.enqueue(person['id'], first)
    if conflict == 'hidden':
        library.set_removed(person['id'], True)
    elif conflict == 'unmapped_binding':
        bind(library, second, 'group-unknown')
    else:
        other = library.add_person('group-other', 'Other', 'LivenessFace' if conflict == 'real' else 'AIGC')
        library.enqueue(other['id'], second if conflict == 'other_reference' else first)
    with pytest.raises(ValueError):
        library.resolve_virtual_assets([first, second])


def test_resolve_is_account_and_kind_scoped(library):
    person = library.add_person('group-v', 'Virtual', 'AIGC')
    first = asset(library, 'first')
    library.enqueue(person['id'], first)
    same_bytes_video = asset(library, 'video', kind='person_video')
    assert library.resolve_virtual_assets([same_bytes_video]) is None
    service.save_config(library.settings, {'access_key': 'other-ak', 'secret_key': 'other-sk'})
    assert PortraitLibrary(library.settings).resolve_virtual_assets([first]) is None


def test_ensure_reuses_unique_person_and_supplementary_reordering(library, monkeypatch):
    first, second, third = asset(library, 'first'), asset(library, 'second', 'blue'), asset(library, 'third', 'green')
    calls = []
    def request(self, action, payload):
        calls.append((action, payload))
        if action == 'CreateAssetGroup':
            return {'Id': 'group-auto'}
        assert action == 'GetAssetGroup'
        return group()
    monkeypatch.setattr(service.ArkPortraitClient, '_request', request)
    prepared = library.ensure_auto_virtual([first, second, third])
    library.rename(prepared['person_id'], 'My renamed character')
    repeated = PortraitLibrary(library.settings).ensure_auto_virtual([first, third, second])
    assert prepared['status'] == repeated['status'] == 'ready'
    assert prepared['request_id'] == repeated['request_id']
    assert prepared['person_id'] == repeated['person_id']
    assert len([call for call in calls if call[0] == 'CreateAssetGroup']) == 1
    assert len(calls[0][1]['Name']) <= 60 and calls[0][1]['GroupType'] == 'AIGC'
    library.enqueue(prepared['person_id'], second)
    assert library.ensure_auto_virtual([second])['person_id'] == prepared['person_id']


def test_remote_id_is_committed_before_get_and_survives_restart(library, monkeypatch):
    first = asset(library, 'first')
    state = {'fail_get': True, 'creates': 0}
    def request(self, action, payload):
        if action == 'CreateAssetGroup':
            state['creates'] += 1
            return {'Id': 'group-auto'}
        assert action == 'GetAssetGroup'
        with library.store.connection() as db:
            assert db.execute('SELECT remote_group_id FROM portrait_group_requests').fetchone()[0] == 'group-auto'
        if state['fail_get']:
            raise service.PortraitError('查询暂不可用')
        return group()
    monkeypatch.setattr(service.ArkPortraitClient, '_request', request)
    first_result = library.ensure_auto_virtual([first])
    assert first_result['status'] == 'uncertain'
    state['fail_get'] = False
    recovered = PortraitLibrary(library.settings).ensure_auto_virtual([first])
    assert recovered['status'] == 'ready' and state['creates'] == 1
    assert recovered['request_id'] == first_result['request_id']


@pytest.mark.parametrize('listing', ['unique', 'empty', 'multiple', 'incomplete', 'wrong_type', 'wrong_project'])
def test_uncertain_create_only_recovers_proven_unique_complete_listing(library, monkeypatch, listing):
    first = asset(library, 'first')
    state = {'name': None, 'creates': 0}
    def request(self, action, payload):
        if action == 'CreateAssetGroup':
            state.update(name=payload['Name'], creates=state['creates'] + 1)
            raise service.PortraitError('创建结果待确认')
        if action == 'GetAssetGroup':
            return group(Name=state['name'])
        assert action == 'ListAssetGroups'
        items = [group(Name=state['name'])]
        if listing == 'empty': items = []
        if listing == 'multiple': items.append(group('group-other', Name=state['name']))
        if listing == 'wrong_type': items[0]['GroupType'] = 'LivenessFace'
        if listing == 'wrong_project': items[0]['ProjectName'] = 'other'
        return {'Items': items, **({'NextToken': 'repeat'} if listing == 'incomplete' else {})}
    monkeypatch.setattr(service.ArkPortraitClient, '_request', request)
    initial = library.ensure_auto_virtual([first])
    assert initial['status'] == 'uncertain'
    recovered = PortraitLibrary(library.settings).ensure_auto_virtual([first])
    assert recovered['status'] == ('ready' if listing == 'unique' else 'uncertain')
    assert state['creates'] == 1


def test_concurrent_identical_primary_has_one_cloud_create(library, monkeypatch):
    first = asset(library, 'first')
    entered, release = Event(), Event()
    creates = []
    def request(self, action, payload):
        if action == 'CreateAssetGroup':
            creates.append(payload)
            entered.set()
            assert release.wait(5)
            return {'Id': 'group-auto'}
        if action == 'ListAssetGroups': return {'Items': []}
        assert action == 'GetAssetGroup'
        return group()
    monkeypatch.setattr(service.ArkPortraitClient, '_request', request)
    other = PortraitLibrary(library.settings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        running = pool.submit(library.ensure_auto_virtual, [first])
        assert entered.wait(5)
        try:
            duplicate = pool.submit(other.ensure_auto_virtual, [first]).result(timeout=4)
        finally:
            release.set()
        original = running.result(timeout=5)
    assert original['status'] == 'ready' and duplicate['request_id'] == original['request_id']
    assert len(creates) == 1
    assert other.ensure_auto_virtual([first])['person_id'] == original['person_id']


def test_ready_request_does_not_restore_hidden_person(library, monkeypatch):
    first = asset(library, 'first')
    monkeypatch.setattr(service.ArkPortraitClient, '_request',
                        lambda self, action, payload: {'Id': 'group-auto'} if action == 'CreateAssetGroup' else group())
    ready = library.ensure_auto_virtual([first])
    library.set_removed(ready['person_id'], True)
    with pytest.raises(ValueError):
        library.ensure_auto_virtual([first])


def test_auto_identity_distinguishes_account_kind_and_primary_hash(library, monkeypatch):
    first = asset(library, 'first')
    second = asset(library, 'second', 'blue')
    video = asset(library, 'video', kind='person_video')
    monkeypatch.setattr(service.ArkPortraitClient, '_request',
                        lambda *args: (_ for _ in ()).throw(service.PortraitError('offline')))
    ids = {library.ensure_auto_virtual([ident])['request_id'] for ident in [first, second, video]}
    service.save_config(library.settings, {'access_key': 'other-ak', 'secret_key': 'other-sk'})
    ids.add(PortraitLibrary(library.settings).ensure_auto_virtual([first])['request_id'])
    assert len(ids) == 4


@pytest.mark.parametrize('failure', ['upload', 'FaceMismatch', 'ContentRestricted', 'DownloadFailed'])
def test_photo_failure_retryability_is_durable_and_never_retries_official_rejection(library, monkeypatch, failure):
    from app import portrait_library as module
    first = asset(library, 'first')
    person = library.add_person('group-v', 'Virtual', 'AIGC')
    photo = library.enqueue(person['id'], first)
    if failure != 'upload':
        library.update(photo['id'], status='processing', remote_id='asset-photo')
    def request(self, action, payload):
        if action == 'GetAssetGroup': return group('group-v')
        assert action == 'GetAsset'
        return {'Id': 'asset-photo', 'GroupId': 'group-v', 'AssetType': 'Image',
                'ProjectName': 'default', 'Status': 'Failed', 'Error': {'Code': failure}}
    monkeypatch.setattr(service.ArkPortraitClient, '_request', request)
    def upload(*args):
        raise ValueError('本地上传暂时失败')
    monkeypatch.setattr(module, 'upload_photo', upload)
    library.process_one()
    restarted = PortraitLibrary(library.settings)
    result = restarted.get_photo(photo['id'], private=True)
    assert result['status'] == 'failed'
    assert result['retryable'] is (failure == 'upload')
    if failure == 'upload':
        restarted.retry(photo['id'])
        retried = restarted.get_photo(photo['id'], private=True)
        assert retried['status'] == 'queued' and not retried['retryable']


def test_historical_failed_photo_defaults_to_nonretryable(library):
    first = asset(library, 'first')
    person = library.add_person('group-v', 'Virtual', 'AIGC')
    photo = library.enqueue(person['id'], first)
    library.update(photo['id'], status='failed', message='旧错误')
    assert library.get_photo(photo['id'], private=True)['retryable'] is False


def test_verified_import_clears_old_retryable_failure(library):
    first = asset(library, 'first')
    person = library.add_person('group-v', 'Virtual', 'AIGC')
    photo = library.enqueue(person['id'], first)
    library.update(photo['id'], status='failed', retryable=True, message='旧上传错误')
    library.record_verified_import(person['id'], first, 'asset-verified')
    imported = library.get_photo(photo['id'], private=True)
    assert imported['status'] == 'active'
    assert imported['retryable'] is False
