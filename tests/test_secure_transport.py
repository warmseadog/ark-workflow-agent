"""Credential transport must fail before any network request or SDK creation."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.generation_settings import GenerationConfig, validate_url
from app.video_provider import ProviderError, VideoProvider


@pytest.fixture(autouse=True)
def transport_environment(monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    monkeypatch.delenv('APP_ALLOW_INSECURE_LOCAL_HTTP', raising=False)


@pytest.mark.parametrize('url', ['http://api.example/v1', 'http://localhost:9001/v1', 'http://127.0.0.1/v1'])
def test_api_url_rejects_cleartext_by_default(url):
    with pytest.raises(ValueError, match='HTTPS'):
        validate_url(url)


@pytest.mark.parametrize('auth', ['true', 'yes', 'invalid'])
def test_local_exception_never_applies_when_auth_is_enabled(monkeypatch, auth):
    monkeypatch.setenv('APP_AUTH_ENABLED', auth)
    monkeypatch.setenv('APP_ALLOW_INSECURE_LOCAL_HTTP', 'true')
    with pytest.raises(ValueError, match='HTTPS'):
        validate_url('http://127.0.0.1:9001/v1')


@pytest.mark.parametrize('url', ['http://localhost:9001/v1', 'http://127.0.0.1/v1', 'http://[::1]/v1'])
def test_explicit_local_development_exception(monkeypatch, url):
    monkeypatch.setenv('APP_ALLOW_INSECURE_LOCAL_HTTP', 'true')
    assert validate_url(url) == url


@pytest.mark.parametrize('url', ['http://api.example', 'http://localhost.evil.example', 'http://192.168.1.1'])
def test_local_opt_in_does_not_allow_public_or_lan_http(monkeypatch, url):
    monkeypatch.setenv('APP_ALLOW_INSECURE_LOCAL_HTTP', 'true')
    with pytest.raises(ValueError, match='HTTPS'):
        validate_url(url)


def no_network(*args, **kwargs):
    pytest.fail('Unsafe configuration reached network transport')


def test_video_provider_checks_historical_snapshot_at_request_time(monkeypatch):
    monkeypatch.setattr('app.video_provider.requests.request', no_network)
    provider = VideoProvider(GenerationConfig(mode='http', base_url='http://api.example', api_key='fixture-private'))
    with pytest.raises(ProviderError, match='HTTPS') as caught:
        provider._request('POST', '/tasks', json={})
    assert caught.value.error_kind == 'configuration'
    assert not caught.value.submission_uncertain
    assert 'fixture-private' not in str(caught.value)


def test_connection_blocks_cleartext_before_request(monkeypatch):
    from app.model_connection import test_connection
    monkeypatch.setattr('app.model_connection.requests.get', no_network)
    result = test_connection(GenerationConfig(mode='http', base_url='http://api.example', api_key='fixture-private'))
    assert result['status'] == 'invalid_config'


def test_storage_environment_cannot_bypass_endpoint_validation(monkeypatch):
    from app.storage_settings import StorageConfig, make_client
    monkeypatch.setattr('tos.TosClientV2', no_network)
    with pytest.raises(ValueError, match='HTTPS'):
        make_client(StorageConfig(endpoint='http://tos-cn-beijing.volces.com', access_key='fixture', secret_key='fixture'))


def test_redaction_saved_configuration_is_checked_at_runtime(tmp_path, monkeypatch):
    from app.redaction_service import ServiceConfig, process
    from app.media_errors import MediaPipelineError
    from app.media import BlurOptions
    monkeypatch.setattr('app.redaction_service.requests.post', no_network)
    source = tmp_path / 'input.mp4'
    source.write_bytes(b'fixture')
    with pytest.raises(MediaPipelineError, match='HTTPS'):
        process(source, tmp_path / 'output.mp4', SimpleNamespace(max_upload_mb=1),
                BlurOptions(), ServiceConfig(mode='http', endpoint='http://api.example', api_key='fixture'))


def test_legacy_seedance_environment_is_checked_at_runtime(tmp_path, monkeypatch):
    from app.config import settings
    from app.seedance import SeedanceClient, SeedanceError
    monkeypatch.setattr('app.seedance.requests.post', no_network)
    source = tmp_path / 'input.mp4'
    source.write_bytes(b'fixture')
    config = replace(settings, seedance_mode='http', seedance_api_url='http://api.example', seedance_api_key='fixture')
    with pytest.raises(SeedanceError, match='HTTPS'):
        SeedanceClient(config).generate(source, None, None, 'fixture', tmp_path / 'output.mp4')


def test_public_reference_url_requires_https_before_issuing_token(tmp_path):
    from app.reference_media import publish_video
    source = tmp_path / 'work' / 'job' / 'defaced.mp4'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'fixture')
    with pytest.raises(ValueError, match='HTTPS'):
        publish_video(source, tmp_path, 'http://studio.example')
    assert not (tmp_path / 'private' / 'reference-videos').exists()


def test_continuation_snapshot_requires_https_at_runtime(tmp_path, monkeypatch):
    from app.continuation_llm import plan_continuation
    from app.continuation_settings import ContinuationConfig
    monkeypatch.setattr('app.continuation_llm.requests.post', no_network)
    frame = tmp_path / 'tail.jpg'
    frame.write_bytes(b'fixture')
    with pytest.raises(ValueError, match='HTTPS'):
        plan_continuation(ContinuationConfig(base_url='http://api.example', api_key='fixture'),
                          original_prompt='fixture', frames=[{'timestamp': 1, 'path': frame}],
                          source_duration=2, target_duration=3, reference_roles={})


def test_legacy_seedance_never_follows_credential_redirects(tmp_path, monkeypatch):
    import requests
    from app.config import settings
    from app.seedance import SeedanceClient, SeedanceError
    source = tmp_path / 'input.mp4'
    source.write_bytes(b'fixture')
    def redirect(url, **kwargs):
        assert kwargs.get('allow_redirects') is False
        response = requests.Response()
        response.status_code = 307
        response._content = b'{"id": "fixture"}'
        response.headers['Location'] = 'http://leak.example/?private=fixture'
        return response
    monkeypatch.setattr('app.seedance.requests.post', redirect)
    monkeypatch.setattr('app.seedance.requests.get', no_network)
    config = replace(settings, seedance_mode='http', seedance_api_url='https://api.example', seedance_api_key='fixture')
    with pytest.raises(SeedanceError) as caught:
        SeedanceClient(config).generate(source, None, None, 'fixture', tmp_path / 'output.mp4')
    assert 'private=' not in str(caught.value)


@pytest.mark.parametrize('legacy', [False, True])
def test_result_download_rejects_cleartext_signed_urls(tmp_path, monkeypatch, legacy):
    from app.seedance import SeedanceClient, SeedanceError
    monkeypatch.setattr('requests.get', no_network)
    with pytest.raises((SeedanceError, ProviderError), match='HTTPS'):
        if legacy:
            SeedanceClient._download_result('http://media.example/video?token=fixture', tmp_path / 'out.mp4')
        else:
            VideoProvider(GenerationConfig())._download('http://media.example/video?token=fixture', tmp_path / 'out.mp4')


def test_redaction_settings_reject_public_http_on_save(tmp_path):
    from app.config import settings
    from app.redaction_service import save_config
    with pytest.raises(ValueError, match='HTTPS'):
        save_config(replace(settings, storage_dir=tmp_path), {'mode': 'http', 'endpoint': 'http://api.example'})


@pytest.mark.parametrize('extension', [False, True])
def test_ark_reference_media_rejects_http_before_submission(tmp_path, monkeypatch, extension):
    monkeypatch.setattr('app.video_provider.requests.request', no_network)
    monkeypatch.setattr('app.person_video.probe', lambda path: {'duration': 5, 'fps': 24, 'width': 1280, 'height': 720})
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'fixture')
    provider = VideoProvider(GenerationConfig(mode='http', api_key='fixture', model='doubao-seedance-2-5-260628'))
    with pytest.raises(ProviderError, match='HTTPS'):
        if extension:
            provider.extend(source, 'fixture', tmp_path / 'out.mp4', video_url='http://media.example/video')
        else:
            provider.generate(source, [], [], 'fixture', tmp_path / 'out.mp4', video_url='http://media.example/video')


def test_upload_response_cannot_introduce_cleartext_media(tmp_path, monkeypatch):
    import requests
    source = tmp_path / 'video.mp4'
    source.write_bytes(b'fixture')
    def upload_only(method, url, **kwargs):
        assert url.endswith('/uploads/videos'), 'Unsafe media URL reached generation submission'
        response = requests.Response()
        response.status_code = 200
        response._content = b'{"data":{"url":"http://media.example/video"}}'
        return response
    monkeypatch.setattr('app.video_provider.requests.request', upload_only)
    provider = VideoProvider(GenerationConfig(mode='http', protocol='toapis', model='seedance-2', api_key='fixture'))
    with pytest.raises(ProviderError, match='HTTPS'):
        provider.generate(source, [], [], 'fixture', tmp_path / 'out.mp4')


def test_https_download_preserves_signed_query_and_blocks_redirects(tmp_path, monkeypatch):
    import requests
    signed = 'https://media.example/video?signature=fixture/'
    def download(url, **kwargs):
        assert url == signed
        assert kwargs.get('allow_redirects') is False
        assert 'headers' not in kwargs
        response = requests.Response()
        response.status_code = 307
        response._content = b''
        response._content_consumed = True
        response.headers['Location'] = 'http://media.example/video?signature=fixture/'
        return response
    monkeypatch.setattr('app.video_provider.requests.get', download)
    with pytest.raises(ProviderError):
        VideoProvider(GenerationConfig())._download(signed, tmp_path / 'out.mp4')
    assert not (tmp_path / 'out.mp4').exists()
