from tests.media_fixtures import image_bytes, video_bytes, media_bytes
"""Offline contracts for durable automatic virtual-person preparation."""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
import shutil
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main, portrait_service, production_worker, storage_settings
from app.portrait_library import PortraitLibrary
from app.production_store import ProductionStore


@pytest.fixture
def env(tmp_path, monkeypatch):
    settings = replace(main.settings, storage_dir=tmp_path, seedance_mode='mock')
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(production_worker, 'wake', lambda _: None)
    portrait_service.save_config(settings, {'access_key': 'test-ak', 'secret_key': 'test-sk'})
    storage_settings.save_config(settings, {'enabled': True, 'bucket': 'test-bucket',
        'access_key': 'test-ak', 'secret_key': 'test-sk'})
    client = TestClient(main.app)
    store = ProductionStore(tmp_path)
    calls = []

    def ensure(lib, ids):
        calls.append(tuple(ids))
        person = lib.add_person('group-auto', '自动人物', 'AIGC')
        return {'status': 'ready', 'person_id': person['id'], 'request_id': 'auto-stable', 'message': '就绪'}
    monkeypatch.setattr(PortraitLibrary, 'ensure_auto_virtual', ensure, raising=False)
    class API:
        def __init__(self, config, **kwargs): pass
        def get_asset(self, ident): return {'group_id': 'group-auto', 'remote_asset_id': ident}
    monkeypatch.setattr(portrait_service, 'ArkPortraitClient', API)
    monkeypatch.setattr(production_worker, 'run_deface', lambda source, dest, *args: shutil.copyfile(source, dest))
    return client, store, calls, settings


def upload(client, kind, data=None, name=None):
    name = name or ('source.mp4' if kind == 'video' else 'image.png')
    if data is None: data = media_bytes(name)
    response = client.post('/api/production/assets', data={'kind': kind},
        files={'file': (name or ('source.mp4' if kind == 'video' else 'image.png'), data)})
    assert response.status_code == 200, response.text
    return response.json()


def draft(env, color='red'):
    client, *_ = env
    buf = BytesIO(); Image.new('RGB', (400, 400), color).save(buf, format='PNG')
    face = upload(client, 'face', buf.getvalue())
    video = upload(client, 'video'); clothing = upload(client, 'clothing')
    created = client.post('/api/production/drafts', json={'person_input_policy': 'auto_virtual'}).json()
    response = client.put('/api/production/drafts/' + created['id'], json={
        'revision': created['revision'], 'source_asset_id': video['id'],
        'face_asset_ids': [face['id']], 'clothing_asset_ids': [clothing['id']],
        'model': {'protocol': 'ark', 'base_url': 'https://ark.cn-beijing.volces.com/api/v3'}})
    assert response.status_code == 200, response.text
    return response.json()


def submit(env, item, key='auto-submit'):
    return env[0].post('/api/production/runs', json={'draft_id': item['id'],
        'revision': item['revision'], 'idempotency_key': key})


def active_photos(env, run_id):
    _, store, _, settings = env
    prep = store.get_preparation(run_id)
    lib = PortraitLibrary(settings)
    for job in prep['uploads'].values():
        lib.update(job, status='active', remote_id='asset-auto')


def test_explicit_new_policy_and_old_api_defaults_are_distinct(env):
    client = env[0]
    old = client.post('/api/production/drafts', json={}).json()
    new = client.post('/api/production/drafts', json={'person_input_policy': 'auto_virtual'}).json()
    assert old.get('person_input_policy') in (None, 'legacy_raw')
    assert new['person_input_policy'] == 'auto_virtual'
    assert client.post('/api/production/drafts', json={'person_input_policy': 'skip_checks'}).status_code == 422
    assert client.put('/api/production/drafts/' + new['id'], json={
        'revision': new['revision'], 'person_input_policy': 'skip_checks'}).status_code == 422


def test_submit_freezes_intent_before_remote_work_and_continues_after_active(env, monkeypatch):
    client, store, calls, settings = env
    item = draft(env)
    response = submit(env, item); assert response.status_code == 200, response.text
    run = response.json()
    frozen = store.get_run(run['id'])['snapshot']
    assert run['snapshot']['person_input_policy'] == 'auto_virtual'
    assert run['person_preparation']['state'] == 'pending'
    assert not calls
    assert submit(env, item).json()['id'] == run['id']
    assert 'test-sk' not in response.text and 'fingerprint' not in response.text
    generated = []
    class Provider:
        def __init__(self, *args): pass
        def generate(self, video, faces, clothes, prompt, output, **kwargs):
            generated.append(kwargs); Path(output).write_bytes(b'output')
        def _safe(self, text): return text
    monkeypatch.setattr(production_worker, 'VideoProvider', Provider)
    production_worker.execute_run(settings, store, store.claim_next())
    waiting = store.get_run(run['id'])
    assert waiting['status'] == 'queued' and waiting['stage'] == 'authorizing'
    assert store.claim_next() is None and not generated
    assert len(calls) == 1
    client.put('/api/production/drafts/' + item['id'], json={
        'revision': item['revision'], 'face_asset_ids': [], 'prompt': 'changed'})
    active_photos(env, run['id'])
    production_worker.execute_run(settings, store, store.get_run(run['id'], private=True))
    result = store.get_run(run['id'])
    assert result['status'] == 'succeeded', result
    assert len(calls) == 1 and list(generated[0]['image_asset_uris'].values()) == ['asset://asset-auto']
    assert result['snapshot'] == frozen
    assert result['person_preparation']['person_id']


def test_stale_revision_and_missing_storage_fail_before_creating_groups(env):
    client, _, calls, settings = env
    item = draft(env)
    client.put('/api/production/drafts/' + item['id'], json={'revision': item['revision'], 'prompt': 'new'})
    assert submit(env, item).status_code == 409
    assert not calls
    item = draft(env, 'blue')
    storage_settings.save_config(settings, {'enabled': False})
    assert submit(env, item, 'no-tos').status_code in (409, 422)
    assert not calls


def test_uncertain_group_can_retry_preparation_without_resubmitting_video(env, monkeypatch):
    client, store, _, settings = env
    response = submit(env, draft(env)); assert response.status_code == 200, response.text
    run = response.json()
    original = PortraitLibrary.ensure_auto_virtual
    monkeypatch.setattr(PortraitLibrary, 'ensure_auto_virtual', lambda *args: {
        'status': 'uncertain', 'request_id': 'auto-stable', 'person_id': None, 'message': '入库结果待确认'})
    production_worker.execute_run(settings, store, store.claim_next())
    for _ in range(3):
        production_worker.execute_run(settings, store, store.get_run(run['id'], private=True))
    waiting = client.get('/api/production/runs/' + run['id']).json()
    assert waiting['status'] == 'needs_attention' and waiting['can_retry_preparation']
    assert client.post('/api/production/runs/' + run['id'] + '/resume').status_code == 409
    page = client.get('/api/production/runs?page=1').json()
    assert page['items'][0]['can_retry_preparation']
    monkeypatch.setattr(PortraitLibrary, 'ensure_auto_virtual', original)
    retry = client.post('/api/production/runs/' + run['id'] + '/person-preparation/retry')
    assert retry.status_code == 200, retry.text
    production_worker.execute_run(settings, store, store.claim_next())
    assert store.get_run(run['id'])['status'] == 'queued'
    assert store.get_preparation(run['id'])['person_id']


def test_cancelled_wait_never_restarts_and_keeps_ingested_person(env):
    client, store, _, settings = env
    run = submit(env, draft(env)).json()
    production_worker.execute_run(settings, store, store.claim_next())
    assert client.post('/api/production/runs/' + run['id'] + '/cancel').status_code == 200
    active_photos(env, run['id'])
    production_worker.execute_run(settings, store, store.get_run(run['id'], private=True))
    assert store.get_run(run['id'])['status'] == 'cancelled'
    assert store.get_preparation(run['id'])['person_id']
    assert client.post('/api/production/runs/' + run['id'] + '/person-preparation/retry').status_code == 409


def test_changed_account_blocks_before_automatic_creation(env):
    _, store, calls, settings = env
    run = submit(env, draft(env)).json()
    portrait_service.save_config(settings, {'access_key': 'changed', 'secret_key': 'changed'})
    production_worker.execute_run(settings, store, store.claim_next())
    assert store.get_run(run['id'])['status'] == 'failed'
    assert not calls


def test_failed_photo_is_not_retryable_and_copy_uses_resolved_person(env):
    client, store, _, settings = env
    run = submit(env, draft(env)).json()
    production_worker.execute_run(settings, store, store.claim_next())
    prep = store.get_preparation(run['id'])
    lib = PortraitLibrary(settings)
    for ident in prep['uploads'].values():
        lib.update(ident, status='failed', message='照片未通过官方检查')
    production_worker.execute_run(settings, store, store.get_run(run['id'], private=True))
    failed = store.get_run(run['id'])
    assert failed['status'] == 'failed' and not failed['can_retry_preparation']
    assert client.post('/api/production/runs/' + run['id'] + '/person-preparation/retry').status_code == 409
    copied = client.post('/api/production/runs/' + run['id'] + '/copy').json()
    assert copied['person_input_policy'] == 'existing_person'
    assert copied['person_id'] == prep['person_id']
    assert store.get_run(run['id'])['snapshot'] == {k: v for k, v in run['snapshot'].items() if k != 'assets'}


def test_stopped_photo_requires_explicit_continue_and_reuses_original_record(env):
    client, store, _, settings = env
    run = submit(env, draft(env)).json()
    production_worker.execute_run(settings, store, store.claim_next())
    prep = store.get_preparation(run['id'])
    lib = PortraitLibrary(settings)
    for ident in prep['uploads'].values():
        lib.update(ident, status='stopped', remote_id='asset-original', retryable=True,
                   message='自动查询已停止，请继续检查原记录')
    production_worker.execute_run(settings, store, store.get_run(run['id'], private=True))
    paused = store.get_run(run['id'])
    assert paused['status'] == 'needs_attention'
    assert paused['can_retry_preparation'] is True
    response = client.post('/api/production/runs/' + run['id'] + '/person-preparation/retry')
    assert response.status_code == 200, response.text
    production_worker.execute_run(settings, store, store.claim_next())
    assert store.get_preparation(run['id'])['uploads'] == prep['uploads']
    for ident in prep['uploads'].values():
        photo = lib.get_photo(ident, private=True)
        assert photo['status'] == 'processing'
        assert photo['remote_id'] == 'asset-original'
        assert photo['query_window']['attempts'] == 0


def test_old_client_explicit_person_choice_normalizes_policy(env):
    client, _, _, settings = env
    item = draft(env)
    person = PortraitLibrary(settings).add_person('group-real')
    response = client.put('/api/production/drafts/' + item['id'], json={
        'revision': item['revision'], 'person_id': person['id']})
    assert response.status_code == 200, response.text
    assert response.json()['person_input_policy'] == 'existing_person'
    response = client.put('/api/production/drafts/' + item['id'], json={
        'revision': response.json()['revision'], 'person_id': person['id'], 'person_input_policy': 'auto_virtual'})
    assert response.status_code == 422


def test_automatic_person_success_statistics_exclude_mock(env):
    from dataclasses import asdict
    from app.generation_settings import GenerationConfig
    from app.person_preparation import preflight
    client, store, _, settings = env
    item = draft(env)
    config = GenerationConfig(protocol='ark', base_url='https://ark.cn-beijing.volces.com/api/v3')
    intent = preflight(settings, store, item, config)
    person = PortraitLibrary(settings).add_person('group-stats', '统计人物', 'AIGC')
    for mode in ['mock', 'http']:
        saved = store.create_draft({**{k:v for k,v in item.items() if k not in ('id','revision','assets')},
            'model': {**item['model'], 'mode': mode}})
        run = store.create_run(saved['id'], saved['revision'], 'stats-'+mode,
            {'person_preparation': intent, 'generation': asdict(config)})
        store.update_preparation(run['id'], person_id=person['id'], state='ready')
        store.update_run(run['id'], status='succeeded')
    lib = PortraitLibrary(settings)
    assert lib.person(person['id'])['generated_count'] == 1
    assert lib.virtual_status()['last_success']['mode'] == 'http'


def test_new_wait_uses_run_deadline_not_age_of_reused_photo(env):
    _, store, _, settings = env
    run = submit(env, draft(env)).json()
    production_worker.execute_run(settings, store, store.claim_next())
    prep = store.get_preparation(run['id'])
    with store.connection() as db:
        db.execute('UPDATE portrait_photos SET created=?', (time.time()-86400,))
    production_worker.execute_run(settings, store, store.get_run(run['id'], private=True))
    assert store.get_run(run['id'])['status'] == 'queued'
    store.update_preparation(run['id'], deadline_at=time.time()-1)
    production_worker.execute_run(settings, store, store.get_run(run['id'], private=True))
    assert store.get_run(run['id'])['can_retry_preparation']
    assert store.get_preparation(run['id'])['uploads'] == prep['uploads']


def test_remote_generation_resume_skips_preparation_and_local_inputs(env, monkeypatch):
    _, store, calls, settings = env
    run = submit(env, draft(env)).json()
    store.update_run(run['id'], status='needs_attention', stage='generating', provider_task_id='remote-original')
    for asset in run['snapshot']['assets']:
        Path(store.get_asset(asset['id'], private=True)['path']).unlink()
    from app import person_preparation
    monkeypatch.setattr(person_preparation, 'prepare_run', lambda *args: pytest.fail('must not reprepare'))
    class Provider:
        def __init__(self, *args): pass
        def _safe(self, text): return text
        def generate(self, *args, **kwargs):
            assert kwargs['resume_task_id'] == 'remote-original'
            args[4].write_bytes(b'recovered')
    monkeypatch.setattr(production_worker, 'VideoProvider', Provider)
    store.resume_run(run['id'])
    production_worker.execute_run(settings, store, store.claim_next())
    assert store.get_run(run['id'])['status'] == 'succeeded' and not calls


def test_auto_video_ingests_only_video_and_preserves_reference_roles(env, tmp_path, monkeypatch):
    from tests.test_person_video import video_bytes
    client, store, _, settings = env
    item = draft(env)
    source = upload(client, 'video', video_bytes(tmp_path, 7))
    person_video = upload(client, 'person_video', video_bytes(tmp_path, 3), 'person.mp4')
    response = client.put('/api/production/drafts/'+item['id'], json={
        'revision': item['revision'], 'person_reference_mode': 'video',
        'source_asset_id': source['id'], 'person_video_asset_id': person_video['id'],
        'model': {'model': 'doubao-seedance-2-0-260128'}})
    assert response.status_code == 200, response.text
    result = submit(env, response.json()); assert result.status_code == 200, result.text
    run = result.json()
    production_worker.execute_run(settings, store, store.claim_next())
    assert set(store.get_preparation(run['id'])['uploads']) == {person_video['id']}
    active_photos(env, run['id'])
    class Provider:
        def __init__(self, *args): pass
        def _safe(self, text): return text
        def generate(self, video, faces, clothes, prompt, output, **kwargs):
            assert faces == [] and len(clothes) == 1
            assert kwargs['person_video_uri'] == 'asset://asset-auto'
            assert 'image_asset_uris' not in kwargs
            output.write_bytes(b'video output')
    monkeypatch.setattr(production_worker, 'VideoProvider', Provider)
    production_worker.execute_run(settings, store, store.get_run(run['id'], private=True))
    assert store.get_run(run['id'])['status'] == 'succeeded'


def test_old_account_official_binding_cannot_be_relabelled_virtual(env):
    _, store, calls, _ = env
    item = draft(env)
    store.bind_portrait(item['face_asset_ids'][0], {'remote_asset_id': 'asset-real',
        'group_id': 'group-real', 'project': 'old', 'status': 'Active'}, 'old-account')
    response = submit(env, item)
    assert response.status_code == 422
    assert not calls and not store.list_runs()


def test_uncertain_group_is_automatically_checked_before_needing_user(env, monkeypatch):
    _, store, calls, settings = env
    run = submit(env, draft(env)).json()
    original = PortraitLibrary.ensure_auto_virtual
    checks = []
    def transient(lib, ids):
        checks.append(ids)
        if len(checks) == 1:
            return {'status': 'uncertain', 'request_id': 'auto-stable', 'person_id': None, 'message': '待确认'}
        return original(lib, ids)
    monkeypatch.setattr(PortraitLibrary, 'ensure_auto_virtual', transient)
    production_worker.execute_run(settings, store, store.claim_next())
    assert store.get_run(run['id'])['status'] == 'queued'
    production_worker.execute_run(settings, store, store.get_run(run['id'], private=True))
    assert store.get_preparation(run['id'])['person_id']
    assert len(checks) == 2 and len(calls) == 1


def test_retryable_upload_failure_reuses_photo_job(env):
    client, store, _, settings = env
    run = submit(env, draft(env)).json()
    production_worker.execute_run(settings, store, store.claim_next())
    prep = store.get_preparation(run['id'])
    photo_id = next(iter(prep['uploads'].values()))
    lib = PortraitLibrary(settings)
    lib.update(photo_id, status='failed', message='上传暂时失败')
    with store.connection() as db:
        db.execute('INSERT OR REPLACE INTO portrait_photo_errors VALUES (?,1)', (photo_id,))
    production_worker.execute_run(settings, store, store.get_run(run['id'], private=True))
    assert store.get_run(run['id'])['can_retry_preparation']
    assert client.post('/api/production/runs/'+run['id']+'/person-preparation/retry').status_code == 200
    production_worker.execute_run(settings, store, store.claim_next())
    assert store.get_preparation(run['id'])['uploads'] == prep['uploads']
    assert lib.get_photo(photo_id)['status'] == 'queued'


@pytest.mark.parametrize('new_status', ['failed', 'uncertain'])
def test_photo_transition_during_verify_preserves_recovery(env, monkeypatch, new_status):
    from app import person_preparation
    _, store, _, settings = env
    run = submit(env, draft(env)).json()
    original = person_preparation.verify
    def raced(snapshot, db, **kwargs):
        photo_id = next(iter(snapshot['uploads'].values()))
        PortraitLibrary(settings).update(photo_id, status=new_status, message='上传暂时不可用')
        if new_status == 'failed':
            with db.connection() as connection:
                connection.execute('INSERT OR REPLACE INTO portrait_photo_errors VALUES (?,1)', (photo_id,))
        return original(snapshot, db, **kwargs)
    monkeypatch.setattr(person_preparation, 'verify', raced)
    production_worker.execute_run(settings, store, store.claim_next())
    result = store.get_run(run['id'])
    assert result['status'] == 'needs_attention' and result['can_retry_preparation']


def test_delete_racing_preparation_retry_cannot_enqueue_hidden_run(env, monkeypatch):
    client, store, _, _ = env
    run = submit(env, draft(env)).json()
    store.update_preparation(run['id'], state='needs_attention', retryable=True)
    store.update_run(run['id'], status='needs_attention', stage='authorizing')
    visible = ProductionStore.require_visible
    def delete_after_check(db, ident):
        visible(db, ident)
        db.delete_run(ident)
    monkeypatch.setattr(ProductionStore, 'require_visible', delete_after_check)
    response = client.post('/api/production/runs/'+run['id']+'/person-preparation/retry')
    assert response.status_code == 404
    assert store.claim_next() is None
