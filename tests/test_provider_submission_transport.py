"""Regression coverage for concurrent large reference uploads."""
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import threading

import pytest
import requests
from urllib3.exceptions import ProtocolError

from app.generation_settings import GenerationConfig
from app.video_provider import ProviderError, VideoProvider


def client():
    return VideoProvider(GenerationConfig(mode='http', api_key='never-log-this-key'))


def response():
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps({'id': 'fixture-task'}).encode()
    return result


def test_large_submission_allows_eighteen_second_write(monkeypatch):
    def transport(method, url, **kwargs):
        if kwargs['timeout'][0] < 18:
            raise requests.ConnectionError(ProtocolError('Connection aborted.', TimeoutError('The write operation timed out')))
        return response()
    monkeypatch.setattr('app.video_provider.requests.request', transport)
    assert client()._request('POST', '/contents/generations/tasks', json={})['id'] == 'fixture-task'


def test_wrapped_write_timeout_is_clear_safe_and_never_retried(monkeypatch, caplog):
    calls = []
    def transport(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError(ProtocolError('never-log-this-key data:image/png;base64,PRIVATE', TimeoutError('The write operation timed out')))
    monkeypatch.setattr('app.video_provider.requests.request', transport)
    with caplog.at_level(logging.WARNING), pytest.raises(ProviderError, match='参考素材上传超时') as caught:
        client()._request('POST', '/contents/generations/tasks', json={})
    assert caught.value.submission_uncertain
    assert not caught.value.retryable
    assert caught.value.error_kind == 'submission_uncertain'
    assert len(calls) == 1
    assert 'TimeoutError' in caplog.text and 'ConnectionError' in caplog.text
    assert 'never-log-this-key' not in caplog.text and 'PRIVATE' not in caplog.text


def test_uploads_serialize_but_polling_is_not_blocked(monkeypatch):
    first_entered, second_started, second_entered, release = [threading.Event() for _ in range(4)]
    def transport(method, url, **kwargs):
        if method == 'GET':
            return response()
        if kwargs['json']['number'] == 1:
            first_entered.set()
            assert release.wait(5)
        else:
            second_entered.set()
        return response()
    monkeypatch.setattr('app.video_provider.requests.request', transport)
    def second():
        second_started.set()
        return client()._request('POST', '/contents/generations/tasks', json={'number': 2})
    with ThreadPoolExecutor(max_workers=3) as pool:
        a = pool.submit(client()._request, 'POST', '/contents/generations/tasks', json={'number': 1})
        try:
            assert first_entered.wait(2)
            b = pool.submit(second)
            assert second_started.wait(2)
            assert pool.submit(client()._request, 'GET', '/contents/generations/tasks/existing').result(2)['id'] == 'fixture-task'
            assert not second_entered.wait(0.2), 'Concurrent upload bypassed the upload gate'
        finally:
            release.set()
        assert a.result(2)['id'] == b.result(2)['id'] == 'fixture-task'


def test_upload_gate_is_released_after_failure(monkeypatch):
    calls = []
    def transport(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise requests.ConnectionError('connection reset')
        return response()
    monkeypatch.setattr('app.video_provider.requests.request', transport)
    with pytest.raises(ProviderError):
        client()._request('POST', '/contents/generations/tasks', json={})
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(client()._request, 'POST', '/contents/generations/tasks', json={}).result(2)['id'] == 'fixture-task'


@pytest.mark.parametrize('error,phrase', [
    (requests.ConnectTimeout('connect timed out'), '连接模型接口超时'),
    (requests.ReadTimeout('read timed out'), '等待模型接口响应超时'),
])
def test_connection_and_response_timeouts_are_distinguished(monkeypatch, error, phrase):
    def transport(*args, **kwargs):
        raise error
    monkeypatch.setattr('app.video_provider.requests.request', transport)
    with pytest.raises(ProviderError, match=phrase):
        client()._request('POST', '/contents/generations/tasks', json={})
