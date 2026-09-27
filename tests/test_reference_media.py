import pytest
from app.reference_media import publish_video, get_video


def test_public_reference_only_exposes_scoped_video_with_expiry(tmp_path, monkeypatch):
    path = tmp_path / 'work' / 'job' / 'defaced.mp4'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'redacted')
    monkeypatch.setattr('app.reference_media.time.time', lambda: 100)
    url = publish_video(path, tmp_path, 'https://studio.example')
    token = url.rsplit('/', 1)[1]
    assert len(token) >= 32
    assert get_video(token) == path.resolve()
    assert get_video('unknown') is None
    monkeypatch.setattr('app.reference_media.time.time', lambda: 7401)
    assert get_video(token) is None


def test_cannot_publish_private_config_or_arbitrary_path(tmp_path):
    config = tmp_path / 'private' / 'generation-settings.json'
    config.parent.mkdir()
    config.write_text('secret')
    with pytest.raises(ValueError):
        publish_video(config, tmp_path, 'https://studio.example')


def test_reference_survives_process_memory_loss_without_extending_expiry(tmp_path, monkeypatch):
    from app import reference_media
    path = tmp_path / 'work' / 'job' / 'defaced.mp4'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'redacted')
    monkeypatch.setattr(reference_media.time, 'time', lambda: 100)
    token = publish_video(path, tmp_path, 'https://studio.example').rsplit('/', 1)[1]
    reference_media._videos.clear()
    monkeypatch.setattr(reference_media.time, 'time', lambda: 200)
    assert get_video(token, tmp_path) == path.resolve()
    reference_media._videos.clear()
    monkeypatch.setattr(reference_media.time, 'time', lambda: 7301)
    assert get_video(token, tmp_path) is None


def test_storage_scope_applies_to_cached_and_recovered_reference(tmp_path, monkeypatch):
    from app import reference_media
    storage = tmp_path / 'storage'
    path = storage / 'work' / 'job' / 'defaced.mp4'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'redacted')
    token = publish_video(path, storage, 'https://studio.example').rsplit('/', 1)[1]
    assert get_video(token, tmp_path / 'other-storage') is None
    reference_media._videos.clear()
    assert get_video(token, tmp_path / 'other-storage') is None
    assert get_video(token, storage) == path.resolve()


def test_recovered_record_cannot_expose_private_file_or_traverse_record_directory(tmp_path):
    import json
    import time
    from app import reference_media
    config = tmp_path / 'private' / 'generation-settings.json'
    config.parent.mkdir()
    config.write_text('secret')
    records = tmp_path / 'private' / 'reference-videos'
    records.mkdir()
    token = 'a' * 43
    (records / (token + '.json')).write_text(json.dumps({'path': str(config), 'expires_at': time.time() + 1000}))
    reference_media._videos.clear()
    assert get_video(token, tmp_path) is None
    assert get_video('../generation-settings', tmp_path) is None


@pytest.mark.parametrize('content', ['not json', '[]', '{"path": 42, "expires_at": 99999999999}', '{"path": "missing", "expires_at": "forever"}'])
def test_broken_durable_record_is_unavailable_not_server_error(tmp_path, content):
    from app import reference_media
    records = tmp_path / 'private' / 'reference-videos'
    records.mkdir(parents=True)
    token = 'b' * 43
    (records / (token + '.json')).write_text(content)
    reference_media._videos.clear()
    assert get_video(token, tmp_path) is None
