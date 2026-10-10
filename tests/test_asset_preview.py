import hashlib
import subprocess
from dataclasses import replace

import imageio_ffmpeg
import pytest

from app import main, portrait_library
from app.shared_portraits import authorize_asset, photo_key, policy_store, provenance_store
from tests.test_person_video import setup, upload, video_bytes


def removed_video(client, tmp_path):
    content = video_bytes(tmp_path)
    original = upload(client, content).json()
    lib = portrait_library.PortraitLibrary(main.settings)
    person = lib.add_person('group-preview', '授权人物')
    job = lib.enqueue(person['id'], original['id'])
    lib.update(job['id'], status='active', remote_id='asset-verified', checked=1234)
    with policy_store(main.settings).connection() as db:
        db.execute('INSERT INTO shared_portrait_removed VALUES (?,?)', ('photo', photo_key(person['id'], original['sha256'])))
    return upload(client, content).json(), lib, person, job


def test_own_reupload_can_preview_without_restoring_generation(setup, tmp_path):
    asset, _, _, _ = removed_video(setup, tmp_path)
    url = '/api/production/assets/' + asset['id']
    assert setup.get(url + '/thumbnail').status_code == 200
    assert setup.get(url + '/file').status_code == 200
    status = setup.get(url + '/reference-status')
    assert status.status_code == 200, status.text
    assert status.json()['can_preview'] and not status.json()['can_use']
    assert status.json()['state'] == 'blocked'
    with pytest.raises(LookupError):
        authorize_asset(main.settings, asset['id'])


def test_preview_h264_cached_range_and_original_unchanged(setup, tmp_path, monkeypatch):
    # HEVC matches the user's file; the derivative must actually decode as H.264.
    original = tmp_path/'hevc.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=blue:s=720x1280:r=24:d=2', '-c:v', 'libx265', '-threads', '1',
                    '-x265-params', 'log-level=error:pools=1', '-tag:v', 'hvc1', '-y', str(original)], check=True, capture_output=True)
    asset = upload(setup, original.read_bytes()).json()
    url = '/api/production/assets/' + asset['id'] + '/preview'
    result = setup.get(url)
    assert result.status_code == 200, result.text[:200]
    out = tmp_path/'preview.mp4'; out.write_bytes(result.content)
    probe = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-i', str(out)], capture_output=True)
    assert b'Video: h264' in probe.stderr and b'yuv420p' in probe.stderr
    raw = setup.get('/api/production/assets/' + asset['id'] + '/file')
    assert hashlib.sha256(raw.content).hexdigest() == asset['sha256']
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('cached preview transcoded again'))
    partial = setup.get(url, headers={'Range': 'bytes=0-99'})
    assert partial.status_code == 206 and partial.content == result.content[:100]


def test_preview_never_opens_other_tenants_or_revoked_mirrors(setup, tmp_path, monkeypatch):
    asset = upload(setup, video_bytes(tmp_path)).json()
    lib = portrait_library.PortraitLibrary(main.settings)
    provenance_store(lib.store)
    with lib.store.connection() as db:
        db.execute('INSERT INTO shared_portrait_provenance VALUES (?,?,?)', (asset['id'], 'revoked-person', 'missing-photo'))
    for suffix in ('file', 'thumbnail', 'preview', 'reference-status'):
        assert setup.get('/api/production/assets/' + asset['id'] + '/' + suffix).status_code == 404
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path/'other'))
    assert setup.get('/api/production/assets/' + asset['id'] + '/preview').status_code == 404


def test_status_distinguishes_fresh_failed_and_verified(setup, tmp_path):
    asset = upload(setup, video_bytes(tmp_path)).json()
    url = '/api/production/assets/' + asset['id'] + '/reference-status'
    fresh = setup.get(url).json()
    assert fresh['state'] == 'unchecked' and fresh['can_use'] and fresh['duration'] == 3
    lib = portrait_library.PortraitLibrary(main.settings)
    person = lib.add_person('group-status', '人物')
    job = lib.enqueue(person['id'], asset['id'])
    lib.update(job['id'], status='failed', message='视频与所选人物不一致。')
    failed = setup.get(url).json()
    assert not failed['can_use'] and '不一致' in failed['message']
    lib.update(job['id'], status='active', remote_id='asset-active')
    active = setup.get(url).json()
    assert active['state'] == 'verified' and active['can_use'] and active['person_id'] == person['id']
