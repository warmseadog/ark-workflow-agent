from dataclasses import replace
import json

import pytest
from fastapi.testclient import TestClient

from app import main
from app.generation_settings import GenerationConfig
from app.video_provider import VideoProvider


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path))
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.mark.parametrize('key', ['FAKE-CREDENTIAL-' + 'X' * 2050, ['FAKE-CREDENTIAL']], ids=['overlong', 'wrong-type'])
def test_invalid_secret_input_never_echoes_rejected_value(client, key):
    result = client.put('/api/link-settings', json={'api_key': key})
    assert result.status_code == 422
    assert 'FAKE-CREDENTIAL' not in result.text
    assert 'input' not in result.json()


def test_filesystem_error_returns_no_path(client, monkeypatch):
    from app.production_store import ProductionStore
    def unavailable(*args, **kwargs):
        raise PermissionError(13, 'Permission denied', '/srv/private/customer/production.db')
    monkeypatch.setattr(ProductionStore, 'list_drafts', unavailable)
    response = client.get('/api/production/drafts')
    assert response.status_code == 403
    assert '/srv/' not in response.text
    assert 'production.db' not in response.text


def test_unexpected_error_is_generic_and_has_reference(client, monkeypatch):
    from app.production_store import ProductionStore
    def unavailable(*args, **kwargs):
        raise RuntimeError('FAKE-CREDENTIAL /srv/private/customer/file')
    monkeypatch.setattr(ProductionStore, 'list_drafts', unavailable)
    response = client.get('/api/production/drafts')
    assert response.status_code == 500
    assert 'FAKE-CREDENTIAL' not in response.text and '/srv/' not in response.text
    assert response.json()['request_id']


def test_historical_run_errors_are_sanitized_without_modifying_data(client):
    from app.production_store import ProductionStore
    store = ProductionStore(main.settings.storage_dir)
    draft = store.create_draft({})
    run = store.create_run(draft['id'], draft['revision'], 'security-history',
                           {'generation': {'api_key': 'FAKE-SNAPSHOT-KEY'}})
    message = 'failed /srv/private/video.mp4 secret_key=FAKE-TOS-KEY FAKE-SNAPSHOT-KEY'
    store.update_run(run['id'], status='failed', error=message)
    response = client.get('/api/production/runs/' + run['id'])
    assert response.status_code == 200
    assert '/srv/' not in response.text and 'FAKE-TOS-KEY' not in response.text
    assert 'FAKE-SNAPSHOT-KEY' not in response.text
    assert store.get_run(run['id'], private=True)['error'] == message


def test_provider_error_removes_paths_credentials_and_preserves_error_code():
    result = VideoProvider(GenerationConfig(api_key='FAKE-MODEL-KEY'))._safe(
        'PolicyViolation FAKE-MODEL-KEY /opt/private/video.mp4 '
        r'C:\private\video.mp4 secret_key=FAKE-TOS-KEY Bearer FAKE-BEARER')
    assert 'PolicyViolation' in result
    for secret in ('FAKE-MODEL-KEY', '/opt/private', 'C:\\private', 'FAKE-TOS-KEY', 'FAKE-BEARER'):
        assert secret not in result


def test_stored_unlabelled_service_secret_is_removed_from_error(client):
    from app import generation_settings
    from app.jobs import Job
    generation_settings.save_config(main.settings, {'api_key': 'FAKE-STORED-KEY'})
    # The public response boundary must also protect old records and logs.
    job = Job(id='a'*32, error='provider echoed FAKE-STORED-KEY', logs=['token FAKE-STORED-KEY'])
    main.store._jobs[job.id] = job
    try:
        response = client.get('/api/jobs/' + job.id)
        assert response.status_code == 200
        assert 'FAKE-STORED-KEY' not in response.text
    finally:
        main.store._jobs.pop(job.id, None)


@pytest.mark.parametrize('value', [
    'secret_key=FAKE-CREDENTIAL', '"api_key": "FAKE-CREDENTIAL"',
    'Authorization: Bearer FAKE-CREDENTIAL',
    r'Cannot open C:\private folder\video.mp4',
    r'Cannot open \\server\private\video.mp4',
    "Cannot open '/private folder/video.mp4'",
])
def test_independent_error_patterns_are_removed(value):
    from app.security import safe_error
    result = safe_error(value)
    assert 'FAKE-CREDENTIAL' not in result
    assert 'video.mp4' not in result


def test_legacy_failure_log_does_not_persist_frozen_key(client, monkeypatch):
    from app import jobs
    store = jobs.JobStore()
    monkeypatch.setattr(jobs, 'store', store)
    job = store.create()
    directory = main.settings.storage_dir/'work'/job.id
    directory.mkdir(parents=True)
    (directory/'defaced.mp4').write_bytes(b'fixture-output')
    store.update(job.id, status='defaced', defaced_name='defaced.mp4')
    def failed(*args, **kwargs):
        raise RuntimeError('provider echoed FAKE-FROZEN-KEY /srv/private/video.mp4')
    monkeypatch.setattr(VideoProvider, 'generate', failed)
    jobs.run_generation_pipeline(job.id, main.settings, None, None, 'test',
                                 generation_config=GenerationConfig(api_key='FAKE-FROZEN-KEY'))
    persisted = json.dumps(store.get(job.id).public())
    assert 'FAKE-FROZEN-KEY' not in persisted
    assert '/srv/private' not in persisted
