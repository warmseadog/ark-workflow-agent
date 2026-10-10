"""Real MP4 export checks: layout, clip timing, freeze frame, and tenant access."""
from pathlib import Path
import cv2
import numpy as np
import pytest

from tests.test_production_api import client, complete_draft
from tests.test_access_control import protected, accounts_clients


def make_video(path, colors, seconds=1):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*'mp4v'), 10, (64, 48))
    assert writer.isOpened()
    for color in colors:
        for _ in range(seconds * 10):
            writer.write(np.full((48, 64, 3), color, dtype=np.uint8))
    writer.release()


@pytest.mark.parametrize('clip, samples', [
    ({'start': 1, 'duration': 2}, [(0.2, 1), (1.2, 2), (4, 2)]),
    ({'start': 2, 'duration': 4, 'retime': 'slow'}, [(0.2, 2), (1.5, 2), (2.5, 3), (4.5, 3)]),
])
def test_export_aligns_clip_slow_motion_and_freezes_last_frame(tmp_path, clip, samples):
    from app.comparison_export import render_comparison
    from app.person_video import probe
    colors = [(0, 0, 240), (0, 240, 0), (240, 0, 0), (0, 240, 240)]
    source, result, output = [tmp_path/name for name in ('source.mp4', 'result.mp4', 'comparison.mp4')]
    make_video(source, colors)
    make_video(result, [(180, 180, 180)], seconds=6)
    render_comparison(source, result, output, clip, 'result')
    info = probe(output)
    assert info['duration'] == pytest.approx(6, abs=.1)
    assert (info['width'], info['height']) == (128, 48)
    cap = cv2.VideoCapture(str(output))
    try:
        for second, index in samples:
            cap.set(cv2.CAP_PROP_POS_MSEC, second*1000)
            ok, frame = cap.read()
            assert ok
            assert np.max(np.abs(frame[24, 32].astype(int)-colors[index])) < 20
            assert np.max(np.abs(frame[24, 96].astype(int)-180)) < 20
    finally:
        cap.release()


def test_download_is_an_attachment_and_reuses_cached_export(client, monkeypatch):
    from app import main
    from app.production_store import ProductionStore
    draft = complete_draft(client)
    store = ProductionStore(main.settings.storage_dir)
    run = store.create_run(draft['id'], draft['revision'], 'comparison', {})
    url = '/api/production/runs/'+run['id']+'/comparison'
    assert client.get(url).status_code == 404
    output = main.settings.storage_dir/'outputs'/(run['id']+'.mp4')
    output.parent.mkdir(exist_ok=True)
    make_video(output, [(180, 180, 180)], seconds=3)
    store.update_run(run['id'], status='succeeded')
    detail = client.get('/api/production/runs/'+run['id']).json()
    assert detail['comparison_url'] == url
    response = client.get(url)
    assert response.status_code == 200, response.text[:200]
    assert response.headers['content-type'] == 'video/mp4'
    assert 'attachment;' in response.headers['content-disposition']
    from app import comparison_export
    monkeypatch.setattr(comparison_export, 'render_comparison', lambda *a, **kw: pytest.fail('cache not reused'))
    assert client.get(url).content == response.content
    assert client.get(url+'?audio=invalid').status_code == 422
    store.delete_run(run['id'])
    assert client.get(url).status_code == 404


def test_other_user_cannot_export_or_cancel_processing_task(accounts_clients):
    from app import main, tenancy
    from app.production_store import ProductionStore
    _, users, (_, alice, bob) = accounts_clients
    draft = complete_draft(alice)
    cfg = tenancy.user_settings(main.settings, users[1])
    store = ProductionStore(cfg.storage_dir)
    run = store.create_run(draft['id'], draft['revision'], 'private-export', {})
    store.update_run(run['id'], status='running', stage='preprocess')
    base = '/api/production/runs/'+run['id']
    assert bob.get(base+'/comparison').status_code == 404
    assert bob.post(base+'/cancel').status_code == 404
    assert alice.post(base+'/cancel').json()['status'] == 'cancelled'


@pytest.mark.parametrize('audio, frequency', [('source', 440), ('result', 880), ('mute', None)])
def test_export_selects_one_audio_track_and_pads_source_with_silence(tmp_path, audio, frequency):
    import imageio_ffmpeg
    import subprocess
    from app.comparison_export import render_comparison
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    for name, tone, duration in [('source', 440, 4), ('result', 880, 6)]:
        subprocess.run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=s=64x48:d={duration}',
                        '-f', 'lavfi', '-i', f'sine=frequency={tone}:duration={duration}',
                        '-c:v', 'libx264', '-c:a', 'aac', '-shortest', str(tmp_path/(name+'.mp4'))],
                       check=True, capture_output=True)
    target = tmp_path/'comparison.mp4'
    render_comparison(tmp_path/'source.mp4', tmp_path/'result.mp4', target,
                      {'start': 2, 'duration': 4, 'retime': 'slow'}, audio)
    decoded = subprocess.run([ffmpeg, '-v', 'error', '-i', str(target), '-map', '0:a:0',
                              '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'], capture_output=True)
    if frequency is None:
        assert decoded.returncode != 0 and not decoded.stdout
        return
    assert decoded.returncode == 0
    samples = np.frombuffer(decoded.stdout, dtype=np.float32)
    window = samples[8000:12000]
    peak = np.fft.rfftfreq(len(window), 1/8000)[np.abs(np.fft.rfft(window)).argmax()]
    assert peak == pytest.approx(frequency, abs=4)
    if audio == 'source':
        assert np.max(np.abs(samples[40000:44000])) < .001


def test_admin_export_uses_owner_media_and_audits_download(accounts_clients):
    from app import main, tenancy
    from app.production_store import ProductionStore
    accounts, users, (admin, alice, bob) = accounts_clients
    draft = complete_draft(alice)
    cfg = tenancy.user_settings(main.settings, users[1])
    store = ProductionStore(cfg.storage_dir)
    run = store.create_run(draft['id'], draft['revision'], 'admin-comparison', {})
    result = cfg.storage_dir/'outputs'/(run['id']+'.mp4')
    result.parent.mkdir(exist_ok=True)
    make_video(result, [(180, 180, 180)], seconds=3)
    store.update_run(run['id'], status='succeeded')
    base = '/api/admin/task-records/'+users[1]['id']+'/'+run['id']
    listing = admin.get('/api/admin/task-records?user_id='+users[1]['id'])
    assert listing.status_code == 200, listing.text
    assert listing.json()['items'][0]['download_url'] == base+'/download'
    detail = admin.get(base).json()
    assert detail['comparison_url'] == base+'/comparison'
    assert bob.get(base+'/comparison').status_code == 403
    response = admin.get(detail['comparison_url'])
    assert response.status_code == 200
    assert response.headers['content-type'] == 'video/mp4'
    assert any(item['action'] == 'view_user_media' and item['details']['resource'] == 'comparison'
               for item in accounts.list_audit())
    delegated = '/api/admin/delegated/'+users[1]['id']+'/production/runs/'+run['id']+'/comparison'
    assert admin.get(delegated).status_code == 200
