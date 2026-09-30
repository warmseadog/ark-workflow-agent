from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app import main, media
from tests.media_fixtures import video_bytes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path, max_upload_mb=1))
    # No lifespan: these route tests must not initialize the user's task database.
    return TestClient(main.app)


def test_import_returns_downloaded_video_and_cleans_temporary_files(client, monkeypatch, tmp_path):
    def download(url, destination, settings):
        assert url == 'https://v.douyin.com/example/'
        destination.write_bytes(video_bytes())
        return destination
    monkeypatch.setattr(media, 'download_video', download)
    response = client.post('/api/video-link/import', json={'text': '分享 https://v.douyin.com/example/ 复制打开'})
    assert response.status_code == 200
    assert response.content == video_bytes()
    assert response.headers['content-type'] == 'video/mp4'
    assert not list(tmp_path.rglob('*.mp4'))


@pytest.mark.parametrize('text', ['', '没有链接', 'file:///private/video.mp4'])
def test_invalid_import_does_not_download(client, monkeypatch, text):
    def unexpected(*args):
        pytest.fail('Invalid input must not start a download')
    monkeypatch.setattr(media, 'download_video', unexpected)
    assert client.post('/api/video-link/import', json={'text': text}).status_code == 422


def test_import_failure_is_actionable_and_cleans_partial_download(client, monkeypatch, tmp_path):
    def download(url, destination, settings):
        destination.write_bytes(b'partial')
        raise media.MediaPipelineError('TikHub 余额不足，请检查余额。')
    monkeypatch.setattr(media, 'download_video', download)
    response = client.post('/api/video-link/import', json={'text': 'https://v.douyin.com/example/'})
    assert response.status_code == 422
    assert '余额不足' in response.json()['detail']
    assert not list(tmp_path.rglob('*.mp4'))


@pytest.mark.parametrize('size', [0, 1024 * 1024 + 1])
def test_import_rejects_empty_or_oversized_video(client, monkeypatch, size):
    def download(url, destination, settings):
        destination.write_bytes(b'x' * size)
        return destination
    monkeypatch.setattr(media, 'download_video', download)
    assert client.post('/api/video-link/import', json={'text': 'https://v.douyin.com/example/'}).status_code == 422


def test_import_rejects_foreign_origin(client, monkeypatch):
    def unexpected(*args):
        pytest.fail('Foreign origin must not start a download')
    monkeypatch.setattr(media, 'download_video', unexpected)
    response = client.post('/api/video-link/import', json={'text': 'https://v.douyin.com/example/'}, headers={'Origin': 'https://other.example'})
    assert response.status_code == 403


def test_import_rejects_fake_video_and_cleans_download(client, monkeypatch, tmp_path):
    def download(url, destination, settings):
        destination.write_bytes(b'fixture')
        return destination
    monkeypatch.setattr(media,'download_video',download)
    response=client.post('/api/video-link/import',json={'text':'https://v.douyin.com/example/'})
    assert response.status_code==422
    assert not list(tmp_path.rglob('*.mp4'))
