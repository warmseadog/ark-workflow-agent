from tests.media_fixtures import image_bytes, video_bytes, media_bytes
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app import main


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(settings, storage_dir=tmp_path))
    return TestClient(main.app)


def config(**overrides):
    return dict(provider='toapis', protocol='toapis', mode='http',
                base_url='https://toapis.cn/v1', model='seedance-2',
                api_key='test-key-not-real', duration=8, fps=0,
                resolution='720p', **overrides)


def test_save_reload_and_no_key_in_response(client):
    response = client.put('/api/model-settings', json=config())
    assert response.status_code == 200
    assert 'test-key-not-real' not in response.text
    saved = client.get('/api/model-settings').json()
    assert saved['config']['has_api_key'] is True
    assert saved['config']['duration'] == 8
    assert saved['config']['status'] == 'configured'
    assert {'ark', 'toapis', 'custom'} <= saved['presets'].keys()


def test_blank_key_preserves_only_for_same_destination(client, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    monkeypatch.setenv('APP_ALLOW_INSECURE_LOCAL_HTTP', 'true')
    assert client.put('/api/model-settings', json=config()).status_code == 200
    edited = config()
    edited.update(api_key='', model='my-model', base_url='https://toapis.cn/v1')
    assert client.put('/api/model-settings', json=edited).json()['config']['has_api_key']
    edited['base_url'] = 'http://127.0.0.1:9001/v1'
    edited['provider'] = 'custom'
    assert not client.put('/api/model-settings', json=edited).json()['config']['has_api_key']


def test_clear_key_and_validation(client):
    client.put('/api/model-settings', json=config())
    edited = config()
    edited.update(api_key='', clear_api_key=True)
    assert not client.put('/api/model-settings', json=edited).json()['config']['has_api_key']
    edited.update(base_url='https://user:secret@example.com/v1')
    response = client.put('/api/model-settings', json=edited)
    assert response.status_code == 422
    assert 'secret' not in response.text


def test_cross_origin_write_rejected(client):
    response = client.put('/api/model-settings', json=config(), headers={'Origin': 'https://other.example'})
    assert response.status_code == 403
    response = client.put('/api/model-settings', json=config(), headers={'Host': 'untrusted.example'})
    assert response.status_code == 403


def test_saved_http_config_starts_real_worker(client, monkeypatch):
    from app.jobs import JobStore
    store = JobStore()
    monkeypatch.setattr(main, 'store', store)
    job = store.create()
    store.update(job.id, status='defaced', defaced_name='defaced.mp4')
    seen = []
    class Worker:
        def __init__(self, **kwargs): seen.append(kwargs)
        def start(self): pass
    monkeypatch.setattr(main, 'Thread', Worker)
    client.put('/api/model-settings', json=config())
    result = client.post(f'/api/jobs/{job.id}/generate', data={'prompt': 'keep motion'})
    assert result.status_code == 200
    assert len(seen) == 1
    assert seen[0]['kwargs']['generation_config'].mode == 'http'
    assert seen[0]['kwargs']['generation_config'].duration == 8
    assert store.get(job.id).status == 'running'
    assert 'test-key-not-real' not in result.text


def test_incomplete_configuration_does_not_start_demo(client, monkeypatch):
    from app.jobs import JobStore
    store = JobStore()
    monkeypatch.setattr(main, 'store', store)
    job = store.create()
    store.update(job.id, status='defaced', defaced_name='defaced.mp4')
    edited = config()
    edited['api_key'] = ''
    client.put('/api/model-settings', json=edited)
    response = client.post(f'/api/jobs/{job.id}/generate')
    assert response.status_code == 409
    assert store.get(job.id).status == 'defaced'


def test_connection_uses_unsaved_fields_without_persisting(client, monkeypatch):
    from app.generation_settings import config_path
    calls = []
    monkeypatch.setattr(main, 'test_connection', lambda c: calls.append(c) or {'ok': True, 'status': 'connected', 'message': 'ok', 'latency_ms': 12}, raising=False)
    response = client.post('/api/model-settings/test', json=config())
    assert response.status_code == 200
    assert response.json()['ok'] is True
    assert calls[0].api_key == 'test-key-not-real'
    assert not config_path(main.settings).exists()
    assert 'test-key-not-real' not in response.text


def test_connection_preserves_key_only_for_saved_destination(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main, 'test_connection', lambda c: calls.append(c) or {'ok': True}, raising=False)
    client.put('/api/model-settings', json=config())
    edited = config()
    edited['api_key'] = ''
    client.post('/api/model-settings/test', json=edited)
    assert calls[0].api_key == 'test-key-not-real'
    edited['base_url'] = 'https://elsewhere.example/v1'
    client.post('/api/model-settings/test', json=edited)
    assert calls[1].api_key == ''
    assert client.get('/api/model-settings').json()['config']['base_url'] == 'https://toapis.cn/v1'


def test_connection_rejects_cross_origin(client):
    response = client.post('/api/model-settings/test', json=config(), headers={'Origin': 'https://other.example'})
    assert response.status_code == 403


def test_ark_missing_public_video_address_is_explained(client):
    from app.generation_settings import GenerationConfig
    c = GenerationConfig(mode='http', api_key='test-key')
    assert '公网' in c.generation_problem()
    assert not replace(c, public_base_url='https://studio.example').generation_problem()


@pytest.mark.parametrize('address', ['http://0.0.0.0:8000', 'http://127.0.0.2:8000', 'http://192.168.1.2:8000', 'http://[::1]:8000'])
def test_public_video_address_rejects_non_public_ips(client, address):
    edited = config()
    edited['public_base_url'] = address
    assert client.put('/api/model-settings', json=edited).status_code == 422


def test_generate_api_completes_real_pipeline_with_all_references(client, monkeypatch):
    import json
    import requests
    from app import jobs
    store = jobs.JobStore()
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(jobs, 'store', store)
    job = store.create()
    work = main.settings.storage_dir / 'work' / job.id
    work.mkdir(parents=True)
    (work/'defaced.mp4').write_bytes(b'redacted-input')
    (work/'source.mp4').write_bytes(b'original')
    monkeypatch.setattr(jobs, 'run_deface', lambda src,dst,*a: dst.write_bytes(b'redacted-input'))
    jobs.run_deface_pipeline(job.id, main.settings, work/'source.mp4', None)
    class ImmediateWorker:
        def __init__(self, target, args, kwargs=None, **_):
            self.target, self.args, self.kwargs = target, args, kwargs or {}
        def start(self): self.target(*self.args, **self.kwargs)
    monkeypatch.setattr(main, 'Thread', ImmediateWorker)
    submitted = []
    def reply(payload):
        r=requests.Response()
        r.status_code=200
        r._content=json.dumps(payload).encode()
        return r
    def request(method, url, **kwargs):
        if url.endswith('/uploads/videos'):
            assert kwargs['files']['file'][1].read() == b'redacted-input'
            return reply({'success':True,'data':{'url':'https://media.example/redacted.mp4'}})
        if method == 'POST':
            submitted.append(kwargs['json'])
            return reply({'id':'remote-integration'})
        return reply({'status':'completed','result':{'data':[{'url':'https://media.example/final.mp4'}]}})
    def download(url, **kwargs):
        assert 'headers' not in kwargs
        r=requests.Response()
        r.status_code=200
        r._content=b'generated-video'
        r._content_consumed=True
        r.headers['Content-Type']='video/mp4'
        return r
    monkeypatch.setattr('app.video_provider.requests.request',request)
    monkeypatch.setattr('app.video_provider.requests.get',download)
    client.put('/api/model-settings',json=config())
    response=client.post(f'/api/jobs/{job.id}/generate',data={'prompt':'custom prompt'},files=[
        ('face_image',('face1.png',image_bytes(color=(10,20,30)),'image/png')),
        ('face_image',('face2.png',image_bytes(color=(20,30,40)),'image/png')),
        ('clothing_image',('cloth.png',image_bytes(),'image/png'))])
    assert response.status_code == 200
    result=response.json()
    assert result['status'] == 'succeeded'
    assert result['provider'] == 'toapis'
    assert submitted[0]['duration'] == 8
    assert submitted[0]['model'] == 'seedance-2'
    assert len(submitted[0]['image_with_roles']) == 3
    assert 'custom prompt' in submitted[0]['prompt']
    assert 'test-key-not-real' not in response.text
    assert any('remote-integration' in line for line in result['logs'])
    assert (main.settings.storage_dir/'outputs'/f'{job.id}.mp4').read_bytes() == b'generated-video'
