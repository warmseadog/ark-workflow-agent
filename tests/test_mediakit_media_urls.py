import socket

import pytest

from app import mediakit_redaction, tikhub


@pytest.mark.parametrize('address', ['198.18.1.84', '127.0.0.1', '10.0.0.1', '169.254.169.254'])
def test_media_url_rejects_nonpublic_dns_without_proxy_resolution(monkeypatch, address):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))])
    monkeypatch.setattr(tikhub, '_resolve_fake_ip',
                        lambda *a: pytest.fail('MediaKit must not resolve proxy Fake-IP addresses'))
    with pytest.raises(ValueError, match='内网'):
        mediakit_redaction.public_media_url('https://upload.example/video?signature=keep')


def test_media_url_preserves_public_signed_url(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))])
    url = 'https://upload.example/video?signature=keep'
    assert mediakit_redaction.public_media_url(url) == url
