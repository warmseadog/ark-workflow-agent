import json
import shutil
import subprocess
from dataclasses import replace

import cv2
import numpy as np
import imageio_ffmpeg
import pytest

from app import main
from tests.test_redaction_settings import client
from tests.test_access_control import protected, accounts_clients

BASE = '/api/admin/redaction-tests'


@pytest.fixture
def video(tmp_path):
    path = tmp_path / 'sample.mp4'
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64, 64))
    for _ in range(10):
        writer.write(np.zeros((64, 64, 3), dtype=np.uint8))
    writer.release()
    browser_video = tmp_path / 'sample-h264.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-loglevel', 'error', '-i', str(path),
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(browser_video)], check=True, capture_output=True)
    return browser_video.read_bytes()


def submit(client, video, profile='local', values=None, connection=None):
    return client.post(BASE, files={'video': ('sample.mp4', video, 'video/mp4')},
                       data={'profile': profile, 'values': json.dumps(values or {'blur_style': 'solid'}),
                             'connection': json.dumps(connection or {})})


@pytest.fixture
def pending(monkeypatch):
    from app import redaction_test
    calls = []
    monkeypatch.setattr(redaction_test, '_launch', lambda *args: calls.append(args))
    monkeypatch.setattr(redaction_test, '_jobs', {})
    return calls


def test_unsaved_parameters_run_fresh_without_mutating_defaults_or_cache(client, video, pending, monkeypatch):
    from app import redaction_test, media
    before = client.get('/api/redaction-settings').json()
    seen = []
    def process(source, output, settings, options):
        seen.append(options.style)
        shutil.copyfile(source, output)
    monkeypatch.setattr(media, 'run_deface', process)
    response = submit(client, video)
    assert response.status_code == 200, response.text
    ident = response.json()['id']
    assert client.get(f'{BASE}/{ident}/file').status_code == 409
    assert submit(client, video).status_code == 429
    redaction_test._work(*pending.pop())
    result = client.get(f'{BASE}/{ident}').json()
    assert result['status'] == 'succeeded'
    assert result['profile'] == 'local'
    assert client.get(result['output_url']).status_code == 200
    assert client.get('/api/redaction-settings').json() == before
    assert not (main.settings.storage_dir / 'cache' / 'redacted').exists()
    assert seen == ['solid']
    assert not list((main.settings.storage_dir / 'work' / 'redaction-tests').glob('*/source.*'))
    assert submit(client, video).json()['id'] != ident


def test_external_failure_does_not_fall_back_or_publish_output(client, video, pending, monkeypatch):
    from app import redaction_test, redaction_service, media
    from app.media_errors import MediaPipelineError
    service = {'mode': 'http', 'endpoint': 'https://mediakit.cn-beijing.volces.com', 'api_key': 'secret'}
    client.put('/api/redaction-service', json=service)
    def fail(*args):
        raise MediaPipelineError('secret remote response')
    monkeypatch.setattr(redaction_service, 'process', fail)
    monkeypatch.setattr(media, 'run_deface', lambda *args: pytest.fail('must not fall back'))
    values = {'mask_mode': 'blur', 'mask_strength': 'high', 'face_box_expand': 1.0, 'face_confidence': 0.2}
    response = submit(client, video, 'mediakit', values, {'endpoint': service['endpoint'], 'timeout_seconds': 600})
    assert response.status_code == 200, response.text
    redaction_test._work(*pending.pop())
    result = client.get(BASE + '/' + response.json()['id'])
    assert result.json()['status'] == 'failed'
    assert 'secret' not in result.text
    assert 'output_url' not in result.json()


def test_test_failure_explains_safe_cause_without_exposing_frozen_key(client, video, pending, monkeypatch, caplog):
    from app import redaction_test, redaction_service
    from app.media_errors import MediaPipelineError
    endpoint = 'https://mediakit.cn-beijing.volces.com'
    client.put('/api/redaction-service', json={'mode':'http', 'endpoint':endpoint, 'api_key':'old-private-key'})
    result = submit(client, video, 'mediakit', {'mask_mode':'blur'}, {'endpoint':endpoint, 'timeout_seconds':600})
    client.put('/api/redaction-service', json={'api_key':'new-private-key'})
    def fail(*args):
        raise MediaPipelineError('媒体连接失败 old-private-key https://upload.example/file?secret=token')
    monkeypatch.setattr(redaction_service, 'process', fail)
    redaction_test._work(*pending.pop())
    response = client.get(BASE + '/' + result.json()['id'])
    assert '媒体连接失败' in response.json()['message']
    assert '媒体连接失败' in caplog.text
    assert 'old-private-key' not in response.text + caplog.text
    assert 'secret=token' not in response.text + caplog.text


def test_validation_and_access_are_enforced_before_processing(client, video, pending, monkeypatch):
    assert submit(client, b'not a video').status_code == 422
    assert submit(client, video, 'mediakit', {'face_box_expand': 3}).status_code == 422
    assert submit(client, video, 'mediakit', {'mask_mode': 'blur'}).status_code == 422
    assert submit(client, video, '../local').status_code == 422
    assert not pending
    assert client.get(BASE + '/missing', headers={'Host': 'public.example'}).status_code == 403
    response = submit(client, video)
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=main.settings.storage_dir / 'other'))
    assert client.get(BASE + '/' + response.json()['id']).status_code == 404


def test_only_three_test_directories_are_retained(client, video, pending, monkeypatch):
    from app import redaction_test, media
    monkeypatch.setattr(media, 'run_deface', lambda source, output, *args: shutil.copyfile(source, output))
    ids = []
    for _ in range(5):
        result = submit(client, video)
        assert result.status_code == 200, result.text
        ids.append(result.json()['id'])
        redaction_test._work(*pending.pop())
    folders = list((main.settings.storage_dir / 'work' / 'redaction-tests').iterdir())
    assert len(folders) == 3
    assert client.get(BASE + '/' + ids[0]).status_code == 404
    assert client.get(BASE + '/' + ids[-1] + '/file').status_code == 200


def test_system_config_permissions_and_csrf(accounts_clients, video, pending):
    _, _, (admin, alice, bob) = accounts_clients
    for user in (alice, bob):
        assert submit(user, video).status_code == 403
        assert user.get(BASE + '/' + 'a' * 32).status_code == 403
        assert user.get(BASE + '/' + 'a' * 32 + '/file').status_code == 403
    csrf = admin.headers.pop('X-CSRF-Token')
    assert submit(admin, video).status_code == 403
    admin.headers['X-CSRF-Token'] = csrf
    assert submit(admin, video).status_code == 200


def test_cloud_native_parameters_are_frozen_and_forwarded(client, video, pending, monkeypatch):
    from app import redaction_test, redaction_service
    from app.mediakit_redaction import parameters
    endpoint = 'https://mediakit.cn-beijing.volces.com'
    client.put('/api/redaction-service', json={'mode': 'http', 'endpoint': endpoint, 'api_key': 'first-key'})
    values = {'mask_mode': 'blur', 'mask_strength': 'high', 'face_box_expand': 0.8, 'face_confidence': 0.35}
    result = submit(client, video, 'mediakit', values, {'endpoint': endpoint, 'timeout_seconds': 600})
    assert result.status_code == 200
    client.put('/api/redaction-service', json={'mode': 'local', 'api_key': 'second-key'})
    seen = []
    def process(source, output, settings, options, config):
        seen.append((parameters(options), config.api_key))
        shutil.copyfile(source, output)
    monkeypatch.setattr(redaction_service, 'process', process)
    redaction_test._work(*pending.pop())
    assert seen == [(values, 'first-key')]
    assert client.get(BASE + '/' + result.json()['id']).json()['status'] == 'succeeded'
