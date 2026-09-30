from dataclasses import replace
import json

import pytest
from fastapi.testclient import TestClient

from app import main, media


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path))
    return TestClient(main.app)


def test_defaults_and_secret_retention(client):
    url = '/api/redaction-service'
    assert client.get(url).json()['config']['mode'] == 'local'
    saved = client.put(url, json={'mode': 'http', 'endpoint': 'https://mask.example/process', 'api_key': 'private-mask-key'})
    assert saved.status_code == 200
    assert saved.json()['config']['has_api_key'] is True
    assert 'private-mask-key' not in saved.text
    assert client.put(url, json={'api_key': ''}).json()['config']['has_api_key'] is True
    assert client.put(url, json={'clear_api_key': True}).json()['config']['has_api_key'] is False
    client.put(url, json={'api_key': 'private-mask-key'})
    assert client.put(url, json={'endpoint': 'https://other.example/process'}).json()['config']['has_api_key'] is False
    assert client.put(url, json={'mode': 'local'}).json()['config']['mode'] == 'local'
    assert client.get(url, headers={'Host': 'public.example'}).status_code == 403
    assert client.put(url, json={}, headers={'Origin': 'https://evil.example'}).status_code == 403


@pytest.mark.parametrize('payload', [{'mode':'wrong'}, {'mode':'http'}, {'endpoint':'file:///secret'}, {'endpoint':'https://user:key@mask.example'}, {'timeout_seconds':0}, {'timeout_seconds':True}, {'api_key':'bad\nkey'}, {'unknown':True}])
def test_bad_config_does_not_change_defaults(client, payload):
    assert client.put('/api/redaction-service', json=payload).status_code == 422
    assert client.get('/api/redaction-service').json()['config']['mode'] == 'local'


def test_external_upload_receives_options_and_validates_video(client, tmp_path, monkeypatch):
    from app import redaction_service
    from tests.test_person_video import video_bytes
    content = video_bytes(tmp_path, seconds=1)
    source, output = tmp_path/'source.mp4', tmp_path/'masked.mp4'
    source.write_bytes(content)
    client.put('/api/redaction-service', json={'mode':'http', 'endpoint':'https://mask.example/process', 'api_key':'private-mask-key'})
    class Reply:
        status_code = 200
        headers = {'Content-Type':'video/mp4'}
        def iter_content(self, chunk_size): yield content
        def __enter__(self): return self
        def __exit__(self, *_): pass
    def post(url, **kwargs):
        assert url == 'https://mask.example/process'
        assert kwargs['headers']['Authorization'] == 'Bearer private-mask-key'
        assert kwargs['allow_redirects'] is False
        assert kwargs['files']['video'][1].read() == content
        assert json.loads(kwargs['data']['options'])['mask_scale'] == 1.8
        return Reply()
    monkeypatch.setattr(redaction_service.requests, 'post', post)
    monkeypatch.setattr(media, 'build_deface_command', lambda *_: pytest.fail('Should call external API'))
    assert media.run_deface(source, output, main.settings, media.BlurOptions(mask_scale=1.8)) == output
    assert output.read_bytes() == content


def test_external_errors_never_fall_back_or_publish_partial_video(client, tmp_path, monkeypatch):
    from app import redaction_service
    source, output = tmp_path/'source.mp4', tmp_path/'masked.mp4'
    source.write_bytes(b'source')
    client.put('/api/redaction-service', json={'mode':'http', 'endpoint':'https://mask.example/process'})
    def fail(*_, **__):
        raise redaction_service.requests.Timeout('secret-token')
    monkeypatch.setattr(redaction_service.requests, 'post', fail)
    monkeypatch.setattr(media, 'build_deface_command', lambda *_: pytest.fail('Must not silently fall back'))
    with pytest.raises(media.MediaPipelineError, match='超时') as error:
        media.run_deface(source, output, main.settings)
    assert 'secret-token' not in str(error.value)
    assert not output.exists()


def test_frozen_service_and_cache_change_with_provider(client):
    from app import redaction_service
    local = redaction_service.freeze(main.settings)
    before = redaction_service.fingerprint(local)
    client.put('/api/redaction-service', json={'mode':'http', 'endpoint':'https://mask.example/process'})
    assert redaction_service.fingerprint(main.settings) != before
    assert redaction_service.fingerprint(local) == before


@pytest.mark.parametrize('status,content_type,content', [(302,'video/mp4',b'bad'), (401,'application/json',b'private-key'), (200,'application/json',b'{"video_url":"https://example.com"}'), (200,'video/mp4',b'not-video'), (200,'video/mp4',b'')])
def test_bad_response_is_not_published(client, tmp_path, monkeypatch, status, content_type, content):
    from app import redaction_service
    client.put('/api/redaction-service', json={'mode':'http','endpoint':'https://mask.example/process'})
    source, output = tmp_path/'input.mp4', tmp_path/'output.mp4'
    source.write_bytes(b'source')
    class Reply:
        status_code = status
        headers = {'Content-Type':content_type}
        def iter_content(self, chunk_size): yield content
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(redaction_service.requests, 'post', lambda *a, **kw: Reply())
    with pytest.raises(media.MediaPipelineError) as error:
        media.run_deface(source, output, main.settings)
    assert 'private-key' not in str(error.value)
    assert not output.exists()
    assert not list(tmp_path.glob('.mask-*.mp4'))
