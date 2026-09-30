from dataclasses import replace
import subprocess

import imageio_ffmpeg
import pytest
from fastapi.testclient import TestClient
from app import main, production_worker, jobs
from app.production_store import ProductionStore


@pytest.fixture
def library(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path))
    monkeypatch.setattr(production_worker, 'wake', lambda *_: None)
    monkeypatch.setattr(jobs, 'store', jobs.JobStore())
    store = ProductionStore(tmp_path)
    draft = store.create_draft({'name': '我的作品', 'model': {'duration': 5}})
    runs = [store.create_run(draft['id'], 1, str(i), {}) for i in range(4)]
    for run in runs[:3]:
        store.update_run(run['id'], status='succeeded')
    (tmp_path / 'outputs').mkdir()
    return TestClient(main.app), store, runs, tmp_path


def test_library_filters_before_paging_and_keeps_deleted_private(library):
    client, store, runs, root = library
    for run in runs[:3]:
        (root / 'outputs' / (run['id'] + '.mp4')).write_bytes(b'video')
    first = client.get('/api/production/videos?page=1&page_size=2')
    assert first.status_code == 200
    first = first.json()
    second = client.get('/api/production/videos?page=2&page_size=2').json()
    assert first['total'] == 3 and len(first['items']) == 2 and len(second['items']) == 1
    assert len({v['id'] for v in first['items'] + second['items']}) == 3
    assert all('snapshot' not in v and v['poster_url'].endswith('/poster') for v in first['items'])
    store.delete_run(runs[0]['id'])
    assert client.get('/api/production/videos').json()['total'] == 2
    assert client.get('/api/production/runs/' + runs[0]['id'] + '/poster').status_code == 404
    assert client.get('/api/production/runs/unknown/poster').status_code == 404
    assert client.get('/api/production/videos?page_size=1000').status_code == 422


def test_poster_is_cached_still_not_video(library, monkeypatch):
    client, _, runs, root = library
    output = root / 'outputs' / (runs[0]['id'] + '.mp4')
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=red:s=180x320:d=1', '-y', str(output)], check=True)
    url = '/api/production/runs/' + runs[0]['id'] + '/poster'
    response = client.get(url)
    assert response.status_code == 200 and response.headers['content-type'] == 'image/jpeg'
    assert response.content[:2] == b'\xff\xd8'
    monkeypatch.setattr(subprocess, 'run', lambda *a, **k: pytest.fail('Cached poster decoded again'))
    assert client.get(url).content == response.content
    assert client.get('/api/production/runs/' + runs[3]['id'] + '/poster').status_code == 404


def test_workspace_pages_use_shared_navigation(library):
    client, *_ = library
    for path in ['/', '/videos', '/people', '/admin/settings', '/account/password']:
        response = client.get(path)
        assert response.status_code == 200
        assert 'id="workspace-sidebar"' in response.text
        assert 'href="/#tasks"' in response.text
        assert 'href="/videos"' in response.text
        assert 'workspace-account' in response.text


def test_legacy_video_uses_same_gallery_and_deleted_guard(library):
    client, store, _, root = library
    job = jobs.store.create()
    jobs.store.update(job.id, status='succeeded', output_name='legacy.mp4')
    (root / 'outputs' / 'legacy.mp4').write_bytes(b'broken-video')
    ident = 'legacy-' + job.id
    items = client.get('/api/production/videos').json()['items']
    item = next(item for item in items if item['id'] == ident)
    assert item['download_url'] == '/api/jobs/' + job.id + '/download'
    response = client.get(item['poster_url'])
    assert response.status_code == 200 and response.headers['content-type'] == 'image/svg+xml'
    store.delete_run(ident, legacy_status='succeeded')
    assert client.get(item['poster_url']).status_code == 404
