from pathlib import Path

import pytest

from app import main, tenancy
from app.production_store import ProductionStore
from tests.test_prompt_privacy_delegation import users
from tests.test_production_api import complete_draft
from tests.test_tenant_portrait_selection import portraits


def setup_copy(users):
    accounts, (_, target, actor), (_, alice, operator) = users
    draft = complete_draft(alice)
    source = ProductionStore(tenancy.user_settings(main.settings, target).storage_dir)
    destination = ProductionStore(tenancy.user_settings(main.settings, actor).storage_dir)
    run = source.create_run(draft['id'], draft['revision'], 'original', {})
    source.update_run(run['id'], status='succeeded', provider_task_id='must-not-copy')
    url = f"/api/admin/task-records/{target['id']}/{run['id']}/copy-draft"
    return source, destination, run, url


def test_copy_owns_materials_and_submission_without_changing_sessions(users):
    accounts, (owner, target, actor), (_, alice, operator) = users
    source, destination, run, url = setup_copy(users)
    cookies = [dict(client.cookies) for client in (alice, operator)]
    response = operator.post(url, json={'idempotency_key':'copy'})
    assert response.status_code == 200, response.text
    draft = response.json()
    assert 'delegated_user' not in draft and 'delegation' not in draft
    assert draft['copied_from'] == {'user_id':target['id'], 'run_id':run['id']}
    assert destination.get_draft(draft['id'])['prompt'] == run['snapshot']['prompt']
    assert len(source.list_drafts()) == 1
    assert operator.post(url, json={'idempotency_key':'copy'}).json()['id'] == draft['id']
    assert len(destination.list_drafts()) == 1
    assert len(draft['assets']) == 3
    for item in draft['assets']:
        private = destination.get_asset(item['id'], private=True)
        assert Path(private['path']).is_relative_to(destination.storage / 'assets')
        assert item['id'] not in [asset['id'] for asset in run['snapshot'].get('assets', [])]
        assert operator.get(item['url']).status_code == 200
        assert alice.get(item['url']).status_code == 404
    # The target's full quota must not block a task now owned by the admin.
    accounts.update_user(target['id'], owner['id'], max_queued=0)
    submitted = operator.post('/api/production/runs', json={
        'draft_id':draft['id'], 'revision':draft['revision'], 'idempotency_key':'admin-generation'})
    assert submitted.status_code == 200, submitted.text
    new = destination.get_run(submitted.json()['id'], private=True)
    assert new['provider_task_id'] is None and 'delegation' not in new['private']
    assert len(source.list_runs()) == 1
    assert [dict(client.cookies) for client in (alice, operator)] == cookies
    for client, expected in ((alice, target), (operator, actor)):
        assert client.get('/api/auth/me').json()['user']['id'] == expected['id']
    records = operator.get('/api/admin/task-records').json()['items']
    original = next(item for item in records if item['id'] == run['id'])
    assert original['copy_url'] == url


@pytest.mark.parametrize('damage', ['missing', 'changed'])
def test_copy_rejects_broken_materials_without_creating_draft(users, damage):
    source, destination, run, url = setup_copy(users)
    asset = source.get_asset(run['snapshot']['source_asset_id'], private=True)
    path = Path(asset['path'])
    if damage == 'missing': path.unlink()
    else: path.write_bytes(b'changed')
    response = users[2][2].post(url, json={'idempotency_key':'broken'})
    assert response.status_code == 409, response.text
    assert destination.list_drafts() == []


def test_copy_permissions_and_uncertain_submission(users):
    source, destination, run, url = setup_copy(users)
    assert users[2][1].post(url, json={'idempotency_key':'no'}).status_code == 403
    assert users[2][2].post(url, json={}).status_code == 422
    source.update_run(run['id'], error_kind='submission_uncertain')
    assert users[2][2].post(url, json={'idempotency_key':'uncertain'}).status_code == 409
    assert destination.list_drafts() == []


def test_copy_ignores_inactive_missing_material_and_strips_connection_secrets(users):
    source, destination, run, url = setup_copy(users)
    # Historical data may retain inactive references and old connection fields.
    draft = source.get_draft(run['draft_id'])
    draft = source.save_draft(draft['id'], draft['revision'], {
        'scene_enabled':False, 'scene_asset_ids':['missing'],
        'person_video_asset_id':'missing-video', 'model':{**draft['model'], 'api_key':'old-secret'}})
    run = source.create_run(draft['id'], draft['revision'], 'inactive', {})
    url = url.replace(url.split('/')[-2], run['id'])
    response = users[2][2].post(url, json={'idempotency_key':'inactive'})
    assert response.status_code == 200, response.text
    assert response.json()['scene_asset_ids'] == []
    assert response.json()['person_video_asset_id'] is None
    assert 'old-secret' not in response.text


def test_concurrent_copy_retries_publish_one_draft(users):
    from concurrent.futures import ThreadPoolExecutor
    from app.task_copy import copy_draft
    source, destination, run, _ = setup_copy(users)
    _, (_, target, actor), _ = users
    def copy(_):
        return copy_draft(main.settings, actor, target['id'], run['id'], 'concurrent')
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(copy, range(2)))
    assert results[0]['id'] == results[1]['id']
    assert len(destination.list_drafts()) == 1
    with destination.connection() as db:
        assert db.execute('SELECT COUNT(*) FROM production_assets').fetchone()[0] == 3
    assert len(list((destination.storage/'assets').iterdir())) == 3


@pytest.mark.parametrize('username', ['alice', 'bob'])
@pytest.mark.parametrize('kind', ['face', 'person_video'])
def test_copy_preserves_usable_official_person_in_admin_library(portraits, monkeypatch, username, kind):
    from app import production_worker
    from app.portrait_generation import prepare
    from app.generation_settings import GenerationConfig
    from app.portrait_library import PortraitLibrary
    from app.shared_portraits import authorize_asset
    monkeypatch.setattr(production_worker, 'wake', lambda *_:None)
    owner = portraits.owners[username]
    draft = complete_draft(owner.client)
    draft = owner.lib.store.save_draft(draft['id'], draft['revision'], {
        'person_id':owner.person['id'], 'person_input_policy':'existing_person',
        'person_reference_mode':'video' if kind == 'person_video' else 'image',
        'person_video_asset_id':owner.assets[kind]['id'] if kind == 'person_video' else None,
        'face_asset_ids':[owner.assets[kind]['id']] if kind == 'face' else []})
    run = owner.lib.store.create_run(draft['id'], draft['revision'], 'portrait-copy', {})
    url = f"/api/admin/task-records/{owner.user['id']}/{run['id']}/copy-draft"
    response = portraits.admin.post(url, json={'idempotency_key':'portrait'})
    assert response.status_code == 200, response.text
    copied = response.json()
    actor = portraits.admin.get('/api/auth/me').json()['user']
    effective = tenancy.user_settings(main.settings, actor)
    library = PortraitLibrary(effective)
    asset_id = copied['person_video_asset_id'] if kind == 'person_video' else copied['face_asset_ids'][0]
    assert library.person(copied['person_id'])['person_type'] == owner.person['person_type']
    assert authorize_asset(effective, asset_id)[1]['status'] == 'active'
    prepared = prepare(effective, library.store, copied, GenerationConfig())
    assert prepared['bindings'][asset_id]['remote_asset_id'] == owner.photos[kind]['remote_id']
    assert portraits.admin.get(next(a for a in copied['assets'] if a['id']==asset_id)['url']).status_code == 200
    if kind == 'face':
        submitted = portraits.admin.post('/api/production/runs', json={
            'draft_id':copied['id'], 'revision':copied['revision'], 'idempotency_key':'official-copy-submit'})
        assert submitted.status_code == 200, submitted.text
    # Removing a copied virtual photo must not affect its source, and another
    # copy must not falsely report success while the local photo remains revoked.
    if username == 'bob':
        from app.shared_portraits import SharedPortraitCatalog
        photo = library.photos_for_person(copied['person_id'])[0]
        SharedPortraitCatalog(effective).remove_photo(photo['id'])
        assert owner.client.get(owner.assets[kind]['url']).status_code == 200
        rejected = portraits.admin.post(url, json={'idempotency_key':'after-removal'})
        assert rejected.status_code == 404, rejected.text
    else:
        from app.shared_portraits import SharedPortraitCatalog
        SharedPortraitCatalog(effective).remove_photo(owner.photos[kind]['id'])
        with pytest.raises(LookupError): authorize_asset(effective, asset_id)
