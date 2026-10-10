"""A failed real-person attempt must not claim a later independent upload."""
from dataclasses import replace
from io import BytesIO
import hashlib

from PIL import Image
import pytest

from app import main, portrait_service, storage_settings, tenancy
from app.accounts import Accounts
from app.asset_preview import reference_status
from app.person_preparation import preflight
from app.generation_settings import GenerationConfig
from app.portrait_library import PortraitLibrary
from app.shared_portraits import SharedPortraitCatalog, authorize_asset
from tests.test_person_video import video_bytes


@pytest.fixture
def scope(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'true')
    base = replace(main.settings, storage_dir=tmp_path, config_root=None, user_id='')
    accounts = Accounts(tmp_path)
    admin = accounts.init_admin('admin', 'test-password')
    user = accounts.create_user('member', 'test-password', admin['id'])
    owner = tenancy.user_settings(base, admin)
    member = tenancy.user_settings(base, user)
    portrait_service.save_config(owner, {'access_key': 'test-ak', 'secret_key': 'test-sk'})
    storage_settings.save_config(owner, {'enabled': True, 'bucket': 'test-bucket',
                                        'access_key': 'test-ak', 'secret_key': 'test-sk'})
    monkeypatch.setattr(portrait_service.ArkPortraitClient, '_request',
                        lambda *args: pytest.fail('Unexpected cloud call'))
    return owner, member


@pytest.fixture(params=['face', 'person_video'])
def media(request, tmp_path):
    if request.param == 'person_video':
        return 'person_video', '.mp4', 'video/mp4', video_bytes(tmp_path)
    image = BytesIO()
    Image.new('RGB', (400, 400), 'navy').save(image, format='PNG')
    return 'face', '.png', 'image/png', image.getvalue()


def upload(lib, ident, media):
    kind, suffix, mime, content = media
    root = lib.settings.storage_dir / 'assets'
    root.mkdir(parents=True, exist_ok=True)
    path = root / (ident + suffix)
    path.write_bytes(content)
    lib.store.add_asset(ident, path.name, kind, path, len(content), mime,
                        hashlib.sha256(content).hexdigest())
    return ident


def failed_real(settings, media):
    lib = PortraitLibrary(settings)
    person = lib.add_person('group-real', 'Shared real person', 'LivenessFace')
    original = upload(lib, 'old-upload', media)
    photo = lib.enqueue(person['id'], original)
    lib.update(photo['id'], status='failed', remote_id='asset-rejected', message='FaceMismatch')
    return lib, person, photo


def bind_verified(lib, original, person):
    lib.store.bind_portrait(original, {'remote_asset_id': 'asset-verified',
        'group_id': lib.person(person['id'], private=True)['group_id'],
        'project': 'default', 'status': 'Active'}, lib.account)


def test_new_upload_can_prepare_virtual_after_failed_real_attempt(scope, media):
    lib, person, photo = failed_real(scope[0], media)
    duplicate = upload(lib, 'new-upload', media)
    assert lib.resolve_virtual_assets([duplicate]) is None
    assert authorize_asset(scope[0], duplicate) is None
    assert reference_status(scope[0], duplicate)['can_use']
    draft = {'person_reference_mode': 'video' if media[0] == 'person_video' else 'image',
             'person_video_asset_id': duplicate if media[0] == 'person_video' else None,
             'face_asset_ids': [duplicate] if media[0] == 'face' else []}
    config = GenerationConfig(protocol='ark', base_url='https://ark.cn-beijing.volces.com/api/v3')
    assert preflight(scope[0], lib.store, draft, config)['account'] == lib.account
    # The historical failed entry is still inspectable and not relabelled.
    assert lib.get_photo(photo['id'])['status'] == 'failed'
    assert lib.person(person['id'])['person_type'] == 'LivenessFace'
    assert not reference_status(scope[0], photo['asset_id'])['can_use']
    with pytest.raises(ValueError):
        lib.resolve_virtual_assets([photo['asset_id']])


@pytest.mark.parametrize('restriction', ['denied', 'removed'])
def test_failed_shared_real_attempt_cannot_block_another_users_upload(scope, media, restriction):
    lib, person, photo = failed_real(scope[0], media)
    catalog = SharedPortraitCatalog(scope[0])
    if restriction == 'denied':
        catalog.set_policy(person['id'], 'selected', [])
    else:
        catalog.remove_photo(photo['id'])
    other = PortraitLibrary(scope[1])
    duplicate = upload(other, 'new-upload', media)
    assert authorize_asset(scope[1], duplicate) is None
    assert reference_status(scope[1], duplicate)['can_use']
    assert other.resolve_virtual_assets([duplicate]) is None


@pytest.mark.parametrize('state', ['queued', 'uploading', 'submitting', 'processing', 'uncertain', 'active'])
def test_unresolved_or_successful_real_attempt_still_blocks_reclassification(scope, media, state):
    lib, person, photo = failed_real(scope[0], media)
    lib.update(photo['id'], status=state)
    duplicate = upload(lib, 'new-upload', media)
    with pytest.raises(ValueError):
        lib.resolve_virtual_assets([duplicate])
    SharedPortraitCatalog(scope[0]).set_policy(person['id'], 'selected', [])
    other = PortraitLibrary(scope[1])
    duplicate = upload(other, 'new-upload', media)
    with pytest.raises(LookupError):
        authorize_asset(scope[1], duplicate)


@pytest.mark.parametrize('evidence', ['binding', 'checked'])
def test_verified_real_binding_survives_a_later_failed_attempt(scope, media, evidence):
    lib, person, photo = failed_real(scope[0], media)
    if evidence == 'binding':
        bind_verified(lib, photo['asset_id'], person)
    else:
        lib.update(photo['id'], checked=1234)
    duplicate = upload(lib, 'new-upload', media)
    with pytest.raises(ValueError):
        lib.resolve_virtual_assets([duplicate])
    SharedPortraitCatalog(scope[0]).set_policy(person['id'], 'selected', [])
    other = PortraitLibrary(scope[1])
    duplicate = upload(other, 'new-upload', media)
    with pytest.raises(LookupError):
        authorize_asset(scope[1], duplicate)


def test_failed_virtual_attempt_is_reused_instead_of_recreated(scope, media):
    lib = PortraitLibrary(scope[0])
    person = lib.add_person('group-virtual', 'Virtual', 'AIGC')
    photo = lib.enqueue(person['id'], upload(lib, 'old-upload', media))
    lib.update(photo['id'], status='failed', remote_id='asset-rejected', message='ContentRestricted')
    duplicate = upload(lib, 'new-upload', media)
    assert lib.resolve_virtual_assets([duplicate])['id'] == person['id']
    assert lib.enqueue(person['id'], duplicate)['id'] == photo['id']
