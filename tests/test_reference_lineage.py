"""Historical virtual identities must not claim independent reference uploads."""
from io import BytesIO

from PIL import Image
import pytest

from app.portrait_library import PortraitLibrary
from app.shared_portraits import SharedPortraitCatalog, authorize_asset
from tests.test_failed_real_reupload import scope, upload, bind_verified


@pytest.fixture
def lineage(scope):
    content = BytesIO()
    Image.new('RGB', (400, 400), 'navy').save(content, format='PNG')
    media = ('face', '.png', 'image/png', content.getvalue())
    lib = PortraitLibrary(scope[0])
    virtual = lib.add_person('group-old-virtual', 'Old virtual', 'AIGC')
    real = lib.add_person('group-current-real', 'Current real', 'LivenessFace')
    photos = []
    for person, ident in [(virtual, 'virtual-face'), (real, 'real-face')]:
        photo = lib.enqueue(person['id'], upload(lib, ident, media))
        lib.update(photo['id'], status='active', remote_id='asset-'+ident, checked=1)
        photos.append(photo)
    bind_verified(lib, 'real-face', real)
    upload(lib, 'hair', ('hairstyle', *media[1:]))
    return scope, lib, virtual, real, photos, media


@pytest.mark.parametrize('removal', ['person', 'photo'])
def test_removed_virtual_does_not_block_independent_hairstyle(lineage, removal):
    scope, lib, virtual, real, photos, media = lineage
    cat = SharedPortraitCatalog(scope[0])
    if removal == 'person': cat.remove_person(virtual['id'])
    else: cat.remove_photo(photos[0]['id'])
    # Real lineage remains checked; the unrelated virtual lineage is excluded.
    assert authorize_asset(scope[0], 'hair')[1]['person_id'] == real['id']
    assert lib.store.portrait_binding('hair') is None
    assert authorize_asset(scope[0], 'real-face')[1]['person_id'] == real['id']
    with pytest.raises(LookupError):
        authorize_asset(scope[0], 'virtual-face')


@pytest.mark.parametrize('restriction', ['person', 'photo', 'grant'])
def test_real_restriction_still_blocks_same_content_reupload(lineage, restriction):
    scope, lib, virtual, real, photos, media = lineage
    cat = SharedPortraitCatalog(scope[0])
    if restriction == 'person': cat.remove_person(real['id'])
    elif restriction == 'photo': cat.remove_photo(photos[1]['id'])
    else: cat.set_policy(real['id'], 'selected', [])
    member = PortraitLibrary(scope[1])
    upload(member, 'new-hair', ('hairstyle', *media[1:]))
    with pytest.raises(LookupError):
        authorize_asset(scope[1], 'new-hair')
    if restriction != 'grant':
        for ident in ['real-face', 'hair']:
            assert lib.store.get_asset(ident)
            with pytest.raises(LookupError):
                authorize_asset(scope[0], ident)


def test_shared_mirror_and_its_copy_keep_source_revocation(lineage, monkeypatch):
    from app.portrait_service import ArkPortraitClient
    scope, lib, virtual, real, photos, media = lineage
    monkeypatch.setattr(ArkPortraitClient, 'get_asset', lambda self, ident: {
        'remote_asset_id': ident, 'group_id': 'group-current-real', 'project': 'default',
        'asset_type': 'Image', 'person_type': 'LivenessFace', 'status': 'Active'})
    mirror = SharedPortraitCatalog(scope[1]).use_reference(photos[1]['id'])
    assert authorize_asset(scope[1], mirror['id'])[1]['person_id'] == real['id']
    upload(PortraitLibrary(scope[1]), 'copy', ('hairstyle', *media[1:]))
    SharedPortraitCatalog(scope[0]).set_policy(real['id'], 'selected', [])
    for ident in [mirror['id'], 'copy']:
        with pytest.raises(LookupError):
            authorize_asset(scope[1], ident)
