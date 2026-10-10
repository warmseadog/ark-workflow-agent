from dataclasses import replace
import json

import pytest
import requests
from fastapi.testclient import TestClient
from app import main
from app.config import settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(settings, storage_dir=tmp_path))
    for key in ('TOS_ACCESS_KEY', 'TOS_SECRET_KEY', 'TOS_BUCKET', 'TIKHUB_API_KEY'):
        monkeypatch.delenv(key, raising=False)
    with TestClient(main.app) as test_client:
        yield test_client


def tos_payload(**changes):
    return dict(enabled=True, region='cn-beijing', endpoint='https://tos-cn-beijing.volces.com',
                bucket='example-video-bucket', access_key='test-access-key', secret_key='test-secret-key',
                prefix='ark/redacted/', expires_seconds=86400, **changes)


def test_tos_persists_but_never_returns_credentials(client):
    response = client.put('/api/storage-settings', json=tos_payload())
    assert response.status_code == 200
    assert response.json()['config']['ready'] is True
    assert 'test-secret-key' not in response.text
    assert 'test-access-key' not in response.text
    updated = client.put('/api/storage-settings', json={'secret_key': '', 'access_key': '', 'prefix': 'new/'}).json()['config']
    assert updated['has_secret_key'] and updated['has_access_key']
    assert client.get('/api/storage-settings').json()['config']['prefix'] == 'new/'
    cleared = client.put('/api/storage-settings', json={'clear_credentials': True}).json()['config']
    assert not cleared['ready'] and not cleared['has_secret_key']


@pytest.mark.parametrize('change', [
    {'endpoint': 'https://evil.example'}, {'endpoint': 'https://tos-cn-shanghai.volces.com'},
    {'bucket': '../private'}, {'expires_seconds': 1}, {'prefix': '../original/'}, {'enabled': 'yes'},
])
def test_bad_tos_configuration_cannot_replace_saved_settings(client, change):
    assert client.put('/api/storage-settings', json=tos_payload()).status_code == 200
    assert client.put('/api/storage-settings', json=change).status_code == 422
    assert client.get('/api/storage-settings').json()['config']['ready'] is True


@pytest.mark.parametrize('route', ['/api/storage-settings', '/api/admin/overview', '/admin/settings'])
def test_admin_configuration_is_local_only(client, route):
    assert client.get(route, headers={'Host': 'public.example'}).status_code == 403


def test_tos_write_and_test_reject_cross_origin(client):
    for method, path in [('PUT', '/api/storage-settings'), ('POST', '/api/storage-settings/test'), ('POST', '/api/link-settings/test')]:
        assert client.request(method, path, json={}, headers={'Origin': 'https://evil.example'}).status_code == 403


def test_tos_removes_ark_public_workbench_requirement(client):
    assert client.put('/api/model-settings', json={'mode': 'http', 'api_key': 'model-secret'}).status_code == 200
    assert client.get('/api/model-settings').json()['config']['generation_message']
    assert client.put('/api/storage-settings', json=tos_payload()).status_code == 200
    assert client.get('/api/model-settings').json()['config']['generation_message'] == ''
    response = client.get('/api/admin/overview')
    assert response.status_code == 200
    assert response.json()['storage']['ready'] is True
    assert all(secret not in response.text for secret in ('model-secret', 'test-secret-key', 'test-access-key'))


def test_tikhub_test_uses_unsaved_key_without_saving_or_leaking_account(client, monkeypatch):
    def get(url, **kwargs):
        assert url == 'https://api.tikhub.io/api/v1/tikhub/user/get_user_info'
        assert kwargs['headers']['Authorization'] == 'Bearer temporary-key'
        assert kwargs['allow_redirects'] is False
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps({'code': 200, 'user_data': {'is_active': True, 'email': 'private@example.com', 'balance': 9}}).encode()
        return response
    monkeypatch.setattr('requests.get', get)
    response = client.post('/api/link-settings/test', json={'api_key': 'temporary-key'})
    assert response.status_code == 200 and response.json()['ok'] is True
    assert 'private@example.com' not in response.text and 'temporary-key' not in response.text
    assert client.get('/api/link-settings').json()['has_api_key'] is False


def test_missing_tikhub_key_is_reported_without_network(client):
    response = client.post('/api/link-settings/test', json={})
    assert response.status_code == 200 and response.json()['ok'] is False

@pytest.mark.parametrize('generation_fails', [False, True])
def test_tos_generation_uploads_only_redacted_video_and_preserves_snapshot(client, monkeypatch, generation_fails):
    from tests.media_fixtures import video_bytes
    from pathlib import Path
    from types import SimpleNamespace
    from app import jobs, storage_settings
    store = jobs.JobStore()
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(jobs, 'store', store)
    job = store.create()
    work = main.settings.storage_dir / 'work' / job.id
    work.mkdir(parents=True)
    (work / 'original.mp4').write_bytes(b'private-original')
    (work / 'defaced.mp4').write_bytes(video_bytes())
    monkeypatch.setattr(jobs, 'run_deface', lambda src,dst,*a: dst.write_bytes(video_bytes()))
    jobs.run_deface_pipeline(job.id, main.settings, work/'original.mp4', None)
    uploaded = {}
    signed = {}
    class FakeTos:
        def put_object_from_file(self, bucket, key, path, **kwargs):
            uploaded.update(bucket=bucket, key=key, content=Path(path).read_bytes(), **kwargs)
        def pre_signed_url(self, method, bucket, key, **kwargs):
            signed.update(bucket=bucket, key=key, expires=kwargs['expires'])
            return SimpleNamespace(signed_url='https://example-video-bucket.tos-cn-beijing.volces.com/redacted.mp4?signature=private-signature')
        def close(self):
            pass
    monkeypatch.setattr(storage_settings, 'make_client', lambda config: FakeTos())
    class Immediate:
        def __init__(self, target, args, kwargs=None, **_):
            self.target, self.args, self.kwargs = target, args, kwargs or {}
        def start(self):
            # Changes after submission must not alter the worker's storage snapshot.
            storage_settings.save_config(main.settings, {'enabled': False})
            self.target(*self.args, **self.kwargs)
    monkeypatch.setattr(main, 'Thread', Immediate)
    submitted = []
    def reply(data):
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(data).encode()
        return response
    def call(method, url, **kwargs):
        if method == 'POST':
            submitted.append(kwargs['json'])
            if generation_fails:
                response = reply({'error': {'message': 'Unable to fetch ' + submitted[-1]['content'][-1]['video_url']['url']}})
                response.status_code = 400
                return response
            return reply({'id': 'tos-integration'})
        return reply({'status': 'succeeded', 'content': {'video_url': 'https://result.example/video.mp4'}})
    def download(url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        response._content = b'generated-result'
        response._content_consumed = True
        response.headers['Content-Type'] = 'video/mp4'
        return response
    monkeypatch.setattr('app.video_provider.requests.request', call)
    monkeypatch.setattr('app.video_provider.requests.get', download)
    client.put('/api/model-settings', json={'mode': 'http', 'api_key': 'model-key'})
    client.put('/api/storage-settings', json=tos_payload())
    response = client.post(f'/api/jobs/{job.id}/generate', data={'prompt': 'keep motion'})
    assert response.status_code == 200
    assert response.json()['status'] == ('failed' if generation_fails else 'succeeded')
    assert uploaded['content'] == video_bytes()
    assert uploaded['content_type'] == 'video/mp4'
    import tos
    assert uploaded['acl'] == tos.ACLType.ACL_Private
    assert signed['expires'] == 86400
    assert signed['key'] == uploaded['key']
    assert submitted[0]['content'][-1]['video_url']['url'].endswith('signature=private-signature')
    assert 'private-signature' not in response.text and 'model-key' not in response.text
    if generation_fails:
        assert 'private-signature' not in client.get(f'/api/jobs/{job.id}').text
    else:
        assert (main.settings.storage_dir / 'outputs' / f'{job.id}.mp4').read_bytes() == b'generated-result'


def test_tos_upload_rejects_originals_and_scrubs_sdk_errors(client, monkeypatch):
    from app import storage_settings
    config = storage_settings.save_config(main.settings, tos_payload())
    work = main.settings.storage_dir / 'work' / 'job'
    work.mkdir(parents=True)
    original = work / 'original.mp4'
    original.write_bytes(b'original')
    with pytest.raises(ValueError, match='打码'):
        storage_settings.upload_redacted_video(original, main.settings, config)
    video = work / 'defaced.mp4'
    video.write_bytes(b'redacted')
    def fail(_):
        raise RuntimeError('test-secret-key https://bucket.example?signature=leaked')
    monkeypatch.setattr(storage_settings, 'make_client', fail)
    with pytest.raises(RuntimeError) as error:
        storage_settings.upload_redacted_video(video, main.settings, config)
    assert 'test-secret-key' not in str(error.value)
    assert 'signature=' not in str(error.value)
    response = client.post('/api/storage-settings/test', json={})
    assert response.status_code == 200 and response.json()['ok'] is False
    assert 'test-secret-key' not in response.text


def test_tos_changing_destination_or_access_key_does_not_reuse_wrong_secret(client):
    client.put('/api/storage-settings', json=tos_payload())
    config = client.put('/api/storage-settings', json={'access_key': 'changed-ak'}).json()['config']
    assert config['has_access_key'] and not config['has_secret_key'] and not config['ready']
    client.put('/api/storage-settings', json=tos_payload())
    config = client.put('/api/storage-settings', json={'region': 'cn-shanghai', 'endpoint': 'https://tos-cn-shanghai.volces.com'}).json()['config']
    assert not config['has_access_key'] and not config['has_secret_key']


def test_tos_test_validates_bucket_without_saving_or_uploading(client, monkeypatch):
    from app import storage_settings
    buckets = []
    class Client:
        def head_bucket(self, bucket):
            buckets.append(bucket)
        def close(self):
            pass
    monkeypatch.setattr(storage_settings, 'make_client', lambda config: Client())
    response = client.post('/api/storage-settings/test', json=tos_payload())
    assert response.status_code == 200 and response.json()['ok'] is True
    assert buckets == ['example-video-bucket']
    assert not storage_settings.config_path(main.settings).exists()


@pytest.mark.parametrize('status,payload', [
    (401, {}), (200, {'code': 200, 'user_data': {'account_disabled': True}}),
    (200, {'code': 200, 'user_data': {'is_active': False}}), (200, {'code': 200}),
])
def test_tikhub_connection_rejects_invalid_or_disabled_account(client, monkeypatch, status, payload):
    def get(*args, **kwargs):
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        return response
    monkeypatch.setattr('requests.get', get)
    response = client.post('/api/link-settings/test', json={'api_key': 'example-key'})
    assert response.status_code == 200 and response.json()['ok'] is False

def test_provider_errors_do_not_expose_signed_tos_video_urls(client, monkeypatch):
    from app.video_provider import VideoProvider, ProviderError
    from app.generation_settings import GenerationConfig
    signed_url = 'https://bucket.tos-cn-beijing.volces.com/defaced.mp4?X-Tos-Credential=private-access-key%2Fscope&X-Tos-Signature=private-token'
    def call(*args, **kwargs):
        response = requests.Response()
        response.status_code = 400
        response._content = json.dumps({'error': {'message': 'Unable to fetch ' + signed_url}}).encode()
        return response
    monkeypatch.setattr('app.video_provider.requests.request', call)
    provider = VideoProvider(GenerationConfig(mode='http', api_key='model-key'))
    with pytest.raises(ProviderError) as error:
        provider._request('POST', '/contents/generations/tasks', json={})
    assert 'private-access-key' not in str(error.value)
    assert 'private-token' not in str(error.value)
    assert 'Unable to fetch' in str(error.value)
