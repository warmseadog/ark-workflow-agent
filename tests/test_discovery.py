from tests.media_fixtures import image_bytes, video_bytes, media_bytes
from fastapi.testclient import TestClient
import pytest

from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import database, jobs, main
    engine = database.configure_database('sqlite:///' + (tmp_path / 'studio.db').as_posix())
    database.Base.metadata.drop_all(engine)
    database.Base.metadata.create_all(engine)
    persistent = jobs.JobStore(persistent=True)
    monkeypatch.setattr(jobs, 'store', persistent)
    monkeypatch.setattr(main, 'store', persistent)
    return TestClient(app)


def test_import_normalizes_duplicates_and_rejects_foreign_hosts(client):
    response = client.post('/api/discovery/import', json={'links': [
        'https://www.douyin.com/video/123456789?utm_source=share',
        'https://www.douyin.com/video/123456789',
        'https://www.douyin.com.evil.example/video/1',
        'javascript:alert(1)',
    ], 'title': '通勤西装', 'tags': ['通勤']})
    assert response.status_code == 200
    data = response.json()
    assert len(data['imported']) == 1
    assert len(data['duplicates']) == 1
    assert len(data['errors']) == 2
    item = client.get('/api/discovery/candidates').json()['items'][0]
    assert item['canonical_url'] == 'https://www.douyin.com/video/123456789'
    assert item['metrics'] == {}
    assert item['status'] == 'pending'


def test_case_guided_plan_contains_real_search_links_without_fake_results(client):
    case = client.post('/api/discovery/cases', json={
        'title': '通勤西装成功案例', 'tags': ['西装', '通勤'],
        'shot_notes': '全身固定机位', 'outcome': 'success', 'notes': '镜头稳定',
    })
    assert case.status_code == 200
    response = client.post('/api/discovery/runs', json={
        'topic': '秋季通勤', 'platforms': ['douyin', 'xiaohongshu'],
        'case_ids': [case.json()['id']],
    })
    assert response.status_code == 200
    run = response.json()
    assert run['candidate_count'] == 0
    assert any('西装' in query['query'] for query in run['queries'])
    assert any(query['url'].startswith('https://www.douyin.com/search/') for query in run['queries'])
    assert any(query['url'].startswith('https://www.xiaohongshu.com/search_result?') for query in run['queries'])
    assert run['sources']['http']['status'] == 'not_configured'


def test_candidate_requires_review_and_v1_handoff_is_idempotent(client, monkeypatch):
    from app import main
    started = []
    class HeldThread:
        def __init__(self, target, args, daemon):
            self.args = args
        def start(self):
            started.append(self.args)
    monkeypatch.setattr(main, 'Thread', HeldThread)
    item = client.post('/api/discovery/import', json={
        'links': ['https://www.douyin.com/video/987654321']
    }).json()['imported'][0]
    assert client.post('/api/jobs', data={'candidate_id': item['id']}).status_code == 409
    assert client.post(f"/api/discovery/candidates/{item['id']}/review", json={
        'decision': 'approved', 'note': '全身固定镜头',
    }).status_code == 200
    first = client.post('/api/jobs', data={'candidate_id': item['id'], 'mask_mode': 'face_hair_all'})
    assert first.status_code == 200
    second = client.post('/api/jobs', data={'candidate_id': item['id']})
    assert first.json()['id'] == second.json()['id']
    assert len(started) == 1
    assert started[0][-1].mask_mode == 'face_hair_all'
    assert started[0][3] == 'https://www.douyin.com/video/987654321'
    assert client.post(f"/api/discovery/candidates/{item['id']}/review", json={
        'decision': 'rejected', 'note': '误点',
    }).status_code == 409


def test_approved_candidate_allows_local_upload_when_platform_download_fails(client, monkeypatch):
    from app import main
    from dataclasses import replace
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=__import__('pathlib').Path(client.__dict__.get('_unused', 'storage'))))
    item = client.post('/api/discovery/import', json={'links': ['https://www.douyin.com/video/11111111']}).json()['imported'][0]
    client.post(f"/api/discovery/candidates/{item['id']}/review", json={'decision': 'approved'})
    class HeldThread:
        def __init__(self, **kwargs):
            self.args = kwargs['args']
        def start(self):
            assert self.args[2].read_bytes() == video_bytes()
            assert self.args[3] is None
    monkeypatch.setattr(main, 'Thread', HeldThread)
    response = client.post('/api/jobs', data={'candidate_id': item['id']}, files={'video': ('source.mp4', video_bytes(), 'video/mp4')})
    assert response.status_code == 200


def test_jobs_and_review_survive_store_recreation(client):
    from app import jobs
    job = jobs.store.create()
    jobs.store.update(job.id, status='defaced', defaced_name='defaced.mp4')
    restored = jobs.JobStore(persistent=True)
    assert restored.get(job.id).public()['defaced_url'] == f'/api/jobs/{job.id}/defaced'
    active = jobs.store.create()
    jobs.store.update(active.id, status='running')
    restored.recover_interrupted()
    assert restored.get(active.id).status == 'failed'
    assert restored.get(job.id).status == 'defaced'
