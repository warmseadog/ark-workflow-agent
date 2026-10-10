import json

import pytest
import requests

from app.generation_settings import GenerationConfig
from app.video_provider import ProviderError, VideoProvider


def response(payload, status=200, request_id=None):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    if request_id:
        result.headers['X-Request-Id'] = request_id
    return result


@pytest.fixture
def provider():
    return VideoProvider(GenerationConfig(mode='http', api_key='secret', public_base_url='https://studio.example'), poll_seconds=0)


@pytest.fixture
def inputs(tmp_path):
    from tests.media_fixtures import video_bytes,image_bytes
    video = tmp_path / 'video.mp4'
    video.write_bytes(video_bytes())
    face = tmp_path / 'face.png'
    face.write_bytes(image_bytes())
    return video, [face], [], 'prompt', tmp_path / 'result.mp4'


def test_persists_submission_before_query_and_result_before_download(provider, inputs, monkeypatch):
    events = []
    def request(method, url, **kwargs):
        events.append(method)
        if method == 'POST':
            return response({'id': 'remote-1'})
        assert events == ['POST', ('submitted', 'remote-1'), 'GET']
        return response({'status': 'succeeded', 'content': {'video_url': 'https://result.example/video.mp4'}})
    def download(self, url, output):
        assert events[-1] == ('result', url)
        events.append('download')
        output.write_bytes(b'result')
    monkeypatch.setattr('app.video_provider.requests.request', request)
    monkeypatch.setattr(VideoProvider, '_download', download)
    result = provider.generate(*inputs, video_url='https://studio.example/video.mp4', on_submitted=lambda task_id: events.append(('submitted', task_id)), on_result=lambda url: events.append(('result', url)))
    assert result['task_id'] == 'remote-1'
    assert inputs[-1].read_bytes() == b'result'


@pytest.mark.parametrize('protocol,endpoint,payload', [
    ('ark', '/contents/generations/tasks/', {'status': 'succeeded', 'content': {'video_url': 'https://result.example/video.mp4'}}),
    ('toapis', '/videos/generations/', {'status': 'completed', 'result': {'data': [{'url': 'https://result.example/video.mp4'}]}}),
    ('adapter', '/tasks/', {'status': 'done', 'output_url': 'https://result.example/video.mp4'}),
])
def test_resume_queries_without_inputs_upload_or_post(protocol, endpoint, payload, tmp_path, monkeypatch):
    provider = VideoProvider(GenerationConfig(protocol=protocol, mode='http', api_key='secret'), poll_seconds=0)
    def request(method, url, **kwargs):
        assert method == 'GET'
        assert url.endswith(endpoint + 'remote-2')
        return response(payload)
    monkeypatch.setattr('app.video_provider.requests.request', request)
    monkeypatch.setattr(VideoProvider, '_download', lambda self, url, output: output.write_bytes(b'recovered'))
    output = tmp_path / 'recovered.mp4'
    result = provider.generate(tmp_path / 'missing.mp4', [], [], '', output, resume_task_id='remote-2')
    assert output.read_bytes() == b'recovered'
    assert result['task_id'] == 'remote-2'


def test_resume_result_downloads_without_any_model_request(provider, tmp_path, monkeypatch):
    monkeypatch.setattr('app.video_provider.requests.request', lambda *a, **kw: pytest.fail('must not query or submit'))
    monkeypatch.setattr(VideoProvider, '_download', lambda self, url, output: output.write_bytes(url.encode()))
    output = tmp_path / 'result.mp4'
    result = provider.generate(tmp_path / 'missing.mp4', [], [], '', output, resume_task_id='remote-3', resume_result_url='https://result.example/video.mp4')
    assert output.read_bytes() == b'https://result.example/video.mp4'
    assert result['task_id'] == 'remote-3'


@pytest.mark.parametrize('payload', [{}, {'id': ''}, {'id': '   '}, {'id': 42}, ['bad']])
def test_malformed_submission_is_uncertain_and_never_reposted(provider, inputs, monkeypatch, payload):
    calls = []
    def request(method, *a, **kw):
        calls.append(method)
        return response(payload, request_id='request-uncertain')
    monkeypatch.setattr('app.video_provider.requests.request', request)
    with pytest.raises(ProviderError) as caught:
        provider.generate(*inputs, video_url='https://studio.example/video.mp4')
    assert caught.value.error_kind == 'submission_uncertain'
    assert caught.value.submission_uncertain is True
    assert caught.value.retryable is False
    assert calls == ['POST']


@pytest.mark.parametrize('failure', ['timeout', 'network', 'nonjson'])
def test_ambiguous_submission_is_not_retried(provider, inputs, monkeypatch, failure):
    calls = []
    def request(method, *a, **kw):
        calls.append(method)
        if failure == 'timeout':
            raise requests.Timeout()
        if failure == 'network':
            raise requests.ConnectionError()
        result = response(None, 502, 'request-ambiguous')
        result._content = b'<html>bad gateway</html>'
        return result
    monkeypatch.setattr('app.video_provider.requests.request', request)
    with pytest.raises(ProviderError) as caught:
        provider.generate(*inputs, video_url='https://studio.example/video.mp4')
    assert caught.value.error_kind == 'submission_uncertain'
    assert caught.value.submission_uncertain
    assert calls == ['POST']


def test_material_rejection_identifies_first_person_reference_and_keeps_request_id(provider, inputs, monkeypatch):
    monkeypatch.setattr('app.video_provider.requests.request', lambda *a, **kw: response({'error': {'code': 'InvalidParameter', 'message': 'content[1] may contain real person secret https://private.example/image'}}, 400, 'request-material'))
    with pytest.raises(ProviderError) as caught:
        provider.generate(*inputs, video_url='https://studio.example/video.mp4')
    error = caught.value
    assert error.error_kind == 'material_rejected'
    assert error.request_id == 'request-material'
    assert '第 1 张人物参考图' in str(error)
    assert 'content[1]' in str(error)
    assert 'secret' not in str(error) and 'private.example' not in str(error)
    assert not error.retryable and not error.submission_uncertain


def test_query_outage_retains_task_identity(provider, tmp_path, monkeypatch):
    monkeypatch.setattr('app.video_provider.requests.request', lambda *a, **kw: response({'error': {'message': 'busy'}}, 503, 'request-query'))
    monkeypatch.setattr('app.video_provider.time.sleep', lambda _: None)
    with pytest.raises(ProviderError) as caught:
        provider.generate(tmp_path / 'missing.mp4', [], [], '', tmp_path / 'output.mp4', resume_task_id='remote-query')
    assert caught.value.error_kind == 'query_unavailable'
    assert caught.value.provider_task_id == 'remote-query'
    assert caught.value.request_id == 'request-query'
    assert caught.value.retryable and not caught.value.submission_uncertain


@pytest.mark.parametrize('callback', ['on_submitted', 'on_result'])
def test_persistence_failure_stops_next_remote_operation(provider, inputs, monkeypatch, callback):
    events = []
    def request(method, *a, **kw):
        events.append(method)
        return response({'id': 'remote-persist'}) if method == 'POST' else response({'status': 'succeeded', 'content': {'video_url': 'https://result.example/video.mp4'}})
    def fail(value):
        raise OSError('disk full')
    monkeypatch.setattr('app.video_provider.requests.request', request)
    monkeypatch.setattr(VideoProvider, '_download', lambda *a: pytest.fail('must persist before download'))
    with pytest.raises(OSError, match='disk full'):
        provider.generate(*inputs, video_url='https://studio.example/video.mp4', **{callback: fail})
    assert events == (['POST'] if callback == 'on_submitted' else ['POST', 'GET'])


def test_download_failure_without_task_id_does_not_submit(provider, tmp_path, monkeypatch):
    monkeypatch.setattr('app.video_provider.requests.request', lambda *a, **kw: pytest.fail('must only download'))
    monkeypatch.setattr('app.video_provider.requests.get', lambda *a, **kw: (_ for _ in ()).throw(requests.ConnectionError()))
    with pytest.raises(ProviderError) as caught:
        provider.generate(tmp_path / 'missing.mp4', [], [], '', tmp_path / 'output.mp4', resume_result_url='https://result.example/video.mp4')
    assert caught.value.error_kind == 'download_failed'
    assert caught.value.provider_task_id is None
    assert caught.value.retryable and not caught.value.submission_uncertain


@pytest.mark.parametrize('fresh_download_fails', [False, True])
def test_expired_result_url_queries_same_task_then_persists_fresh_url(provider, tmp_path, monkeypatch, fresh_download_fails):
    stale = 'https://result.example/video.mp4?signature=expired'
    fresh = 'https://result.example/video.mp4?signature=fresh'
    events = []
    def download(url, **kwargs):
        events.append(('download', url))
        result = response({}, 403 if url == stale or fresh_download_fails else 200)
        result._content = b'finished-video'
        result._content_consumed = True
        result.headers['Content-Type'] = 'video/mp4'
        return result
    def request(method, url, **kwargs):
        events.append((method, url))
        assert method == 'GET' and url.endswith('/contents/generations/tasks/remote-download')
        return response({'status': 'succeeded', 'content': {'video_url': fresh}})
    def saved(url):
        events.append(('saved', url))
    monkeypatch.setattr('app.video_provider.requests.get', download)
    monkeypatch.setattr('app.video_provider.requests.request', request)
    output = tmp_path / 'result.mp4'
    def run():
        return provider.generate(tmp_path / 'missing.mp4', [], [], '', output,
            resume_task_id='remote-download', resume_result_url=stale, on_result=saved)
    if fresh_download_fails:
        with pytest.raises(ProviderError) as caught:
            run()
        assert caught.value.error_kind == 'download_failed'
        assert caught.value.provider_task_id == 'remote-download'
        assert not output.exists()
    else:
        assert run()['task_id'] == 'remote-download'
        assert output.read_bytes() == b'finished-video'
    assert events == [
        ('download', stale),
        ('GET', 'https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/remote-download'),
        ('saved', fresh),
        ('download', fresh),
    ]
