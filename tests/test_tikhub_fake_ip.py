import io
import socket

import pytest
import requests
import urllib3

from app import tikhub
from app.media_errors import MediaPipelineError


def dns(monkeypatch, *addresses):
    monkeypatch.setattr(tikhub.socket, 'getaddrinfo', lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443)) for address in addresses])


def doh(monkeypatch, answers, status=0):
    def get(url, **kwargs):
        assert url == 'https://dns.alidns.com/resolve'
        assert kwargs['params'] == {'name': 'cdn.example', 'type': 'A'}
        assert not kwargs.get('allow_redirects', True)
        assert 'Authorization' not in kwargs.get('headers', {})
        response = requests.Response()
        response.status_code = 200
        import json
        response._content = json.dumps({'Status': status, 'Answer': answers}).encode()
        return response
    monkeypatch.setattr(tikhub.requests, 'get', get)


def test_fake_ip_is_resolved_to_public_ip_without_changing_system_dns(monkeypatch):
    dns(monkeypatch, '198.18.3.39')
    doh(monkeypatch, [{'type': 5, 'data': 'edge.example'}, {'type': 1, 'data': '93.184.216.34'}])
    assert tikhub.validate_public_url('https://cdn.example/movie?signature=keep') == ('93.184.216.34',)


@pytest.mark.parametrize('address', ['127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', '198.18.0.4'])
def test_fake_ip_cannot_turn_private_doh_answers_into_public_targets(monkeypatch, address):
    dns(monkeypatch, '198.18.3.39')
    doh(monkeypatch, [{'type': 1, 'data': address}])
    with pytest.raises(MediaPipelineError):
        tikhub.validate_public_url('https://cdn.example/movie')


def test_mixed_private_and_public_answers_fail_closed(monkeypatch):
    dns(monkeypatch, '198.18.3.39')
    doh(monkeypatch, [{'type': 1, 'data': '93.184.216.34'}, {'type': 1, 'data': '10.0.0.1'}])
    with pytest.raises(MediaPipelineError):
        tikhub.validate_public_url('https://cdn.example/movie')


@pytest.mark.parametrize('url', ['http://198.18.0.1/video', 'http://127.0.0.1/video', 'http://[::1]/video', 'file:///video'])
def test_literal_private_addresses_do_not_trigger_doh(monkeypatch, url):
    def unexpected(*args, **kwargs):
        pytest.fail('No public DNS fallback for a private literal')
    monkeypatch.setattr(tikhub.requests, 'get', unexpected)
    with pytest.raises(MediaPipelineError):
        tikhub.validate_public_url(url)


def test_failed_fake_ip_resolution_explains_dns_problem(monkeypatch):
    dns(monkeypatch, '198.18.3.39')
    doh(monkeypatch, [], status=3)
    with pytest.raises(MediaPipelineError, match='Fake-IP'):
        tikhub.validate_public_url('https://cdn.example/movie')


def test_public_resolution_does_not_need_doh(monkeypatch):
    dns(monkeypatch, '93.184.216.34')
    monkeypatch.setattr(tikhub.requests, 'get', lambda *a, **kw: pytest.fail('Unnecessary DoH request'))
    assert tikhub.validate_public_url('https://cdn.example/movie') is None


def test_fake_ip_download_pins_public_address_preserves_host_tls_and_stream(monkeypatch):
    dns(monkeypatch, '198.18.3.39')
    doh(monkeypatch, [{'type': 1, 'data': '93.184.216.34'}])
    pools = []
    class Pool:
        def __init__(self, host, **kwargs):
            assert host == '93.184.216.34'
            assert kwargs['server_hostname'] == 'cdn.example'
            assert kwargs['assert_hostname'] == 'cdn.example'
            assert kwargs['cert_reqs'] == 'CERT_REQUIRED'
            self.closed = False
            pools.append(self)
        def urlopen(self, method, target, **kwargs):
            assert method == 'GET'
            assert target == '/movie?signature=keep'
            assert kwargs['headers']['Host'] == 'cdn.example'
            assert kwargs['headers']['User-Agent'] == 'test'
            assert not kwargs['redirect'] and not kwargs['preload_content']
            return urllib3.response.HTTPResponse(body=io.BytesIO(b'video bytes'), status=200,
                                                headers={'Content-Type': 'video/mp4'}, preload_content=False)
        def close(self):
            self.closed = True
    monkeypatch.setattr(urllib3, 'HTTPSConnectionPool', Pool)
    with tikhub.public_get('https://cdn.example/movie?signature=keep', {'User-Agent': 'test'})[0] as response:
        assert response.status_code == 200
        assert b''.join(response.iter_content(4)) == b'video bytes'
    assert pools and all(pool.closed for pool in pools)


def test_fake_ip_redirect_still_rejects_private_destination(monkeypatch):
    dns(monkeypatch, '198.18.3.39')
    doh(monkeypatch, [{'type': 1, 'data': '93.184.216.34'}])
    destinations = []
    class Pool:
        def __init__(self, host, **kwargs):
            destinations.append(host)
        def urlopen(self, *args, **kwargs):
            return urllib3.response.HTTPResponse(body=io.BytesIO(b''), status=302,
                    headers={'Location': 'http://127.0.0.1/private'}, preload_content=False)
        def close(self): pass
    monkeypatch.setattr(urllib3, 'HTTPSConnectionPool', Pool)
    with pytest.raises(MediaPipelineError, match='本地网络'):
        tikhub.public_get('https://cdn.example/movie')
    assert destinations == ['93.184.216.34']


def test_fake_ip_network_failure_closes_pool_and_reports_actionable_error(monkeypatch):
    dns(monkeypatch, '198.18.3.39')
    doh(monkeypatch, [{'type': 1, 'data': '93.184.216.34'}])
    closed = []
    class Pool:
        def __init__(self, host, **kwargs): self.host = host
        def urlopen(self, *args, **kwargs):
            raise urllib3.exceptions.SSLError('certificate mismatch')
        def close(self): closed.append(self.host)
    monkeypatch.setattr(urllib3, 'HTTPSConnectionPool', Pool)
    with pytest.raises(MediaPipelineError, match='Fake-IP'):
        tikhub.public_get('https://cdn.example/movie')
    assert closed == ['93.184.216.34']
