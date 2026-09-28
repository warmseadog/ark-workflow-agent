"""Exercise real tenant routes, SQLite and executor; only deface is replaced."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from app.config import settings
from app.production_store import ProductionStore
from app import redaction_settings, tenancy


@pytest.fixture
def preview(tmp_path, monkeypatch):
    module = importlib.import_module('app.preview_router')
    from app import production_worker
    base = replace(settings, storage_dir=tmp_path)
    tenants = {str(i): tenancy.user_settings(base, {'id': f'{i:032x}'})
               for i in range(1, 13)}
    gate, entered = threading.Event(), threading.Event()
    gate.set()
    calls = []
    failures = []

    def deface(source, output, frozen, options):
        calls.append((source, output, frozen, options.model_dump()))
        entered.set()
        assert gate.wait(15), 'test did not release deface'
        if failures:
            raise RuntimeError(failures[0])
        output.write_bytes(source.read_bytes() + b'-redacted')
        return output

    monkeypatch.setattr(production_worker, 'run_deface', deface)
    app = FastAPI()

    @app.middleware('http')
    async def tenant_context(request: Request, call_next):
        selected = tenants.get(request.headers.get('X-Test-User'))
        token = tenancy._request_settings.set(selected)
        try:
            return await call_next(request)
        finally:
            tenancy._request_settings.reset(token)

    def guard(request: Request):
        if request.headers.get('X-Test-User') not in tenants:
            raise HTTPException(401, 'Login required')
        if request.method == 'POST' and request.headers.get('Origin') != 'http://testserver':
            raise HTTPException(403, 'Origin denied')

    def getter():
        value = tenancy._request_settings.get()
        assert value is not None, 'worker must not use request getter'
        return value

    app.include_router(module.get_router(getter, guard))
    with TestClient(app) as client:
        yield client, tenants, gate, entered, calls, failures, module, getter, guard
        gate.set()
        # Drain the real single executor before monkeypatch and tmp_path teardown.
        if module._executor is not None:
            module._executor.submit(lambda: None).result(timeout=20)


def headers(user='1'):
    return {'X-Test-User': user, 'Origin': 'http://testserver'}


def asset(tenant, ident='source', kind='video', content=b'video', path=None):
    store = ProductionStore(tenant.storage_dir)
    if path is None:
        path = tenant.storage_dir/'assets'/(ident+'.mp4')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return store.add_asset(ident, ident+'.mp4', kind, path, path.stat().st_size,
                           'video/mp4', hashlib.sha256(path.read_bytes()).hexdigest())


def post(client, ident='source', user='1', **fields):
    return client.post('/api/previews', json={'source_asset_id': ident, **fields}, headers=headers(user))


def terminal(client, ident, user='1'):
    deadline = time.monotonic()+10
    while time.monotonic() < deadline:
        response = client.get('/api/previews/'+ident, headers=headers(user))
        assert response.status_code == 200, response.text
        if response.json()['status'] in {'defaced', 'failed'}:
            return response.json()
        time.sleep(.01)
    pytest.fail('preview did not finish')


def test_guard_tenant_ids_private_file_and_explicit_settings(preview):
    client, tenants, _, _, calls, *_ = preview
    asset(tenants['1'])
    assert client.post('/api/previews', json={'source_asset_id':'source'}).status_code == 401
    assert client.get('/api/previews/missing').status_code == 401
    assert client.get('/api/previews/missing/file').status_code == 401
    assert client.post('/api/previews', json={'source_asset_id':'source'},
                       headers={'X-Test-User':'1', 'Origin':'https://evil.test'}).status_code == 403
    assert post(client, user='2').status_code == 404
    response = post(client)
    assert response.status_code == 200, response.text
    job = terminal(client, response.json()['id'])
    assert job['status'] == 'defaced' and job['progress'] == 100 and job['error'] is None
    assert job['defaced_url'] == '/api/previews/'+job['id']+'/file'
    assert client.get('/api/previews/'+job['id'], headers=headers('2')).status_code == 404
    assert client.get(job['defaced_url'], headers=headers('2')).status_code == 404
    result = client.get(job['defaced_url'], headers=headers())
    assert result.content == b'video-redacted'
    assert result.headers['cache-control'] == 'private, no-store'
    assert calls[0][2] == tenants['1']
    with ProductionStore(tenants['1'].storage_dir).connection() as db:
        row = db.execute('SELECT * FROM production_previews WHERE id=?', (job['id'],)).fetchone()
    assert row['status'] == 'defaced' and row['source_asset_id'] == 'source'
    assert str(tenants['1'].storage_dir) not in json.dumps(job)


def test_duplicate_admission_and_per_user_capacity(preview):
    client, tenants, gate, entered, calls, *_ = preview
    gate.clear()
    for ident in ('source', 'two', 'three'):
        asset(tenants['1'], ident)
    first = post(client).json()
    assert entered.wait(5)
    assert client.get('/api/previews/'+first['id']+'/file', headers=headers()).status_code == 409
    with ThreadPoolExecutor(max_workers=6) as submitters:
        repeats = list(submitters.map(lambda _: post(client).json()['id'], range(12)))
    assert set(repeats) == {first['id']}
    assert post(client, 'two').status_code == 200
    assert post(client, 'three').status_code == 429
    with ProductionStore(tenants['1'].storage_dir).connection() as db:
        assert db.execute('SELECT count(*) FROM production_previews').fetchone()[0] == 2
    assert len(calls) == 1
    gate.set()
    assert terminal(client, first['id'])['status'] == 'defaced'


def test_global_capacity_across_users_and_router_instances(preview):
    client, tenants, gate, entered, calls, _, module, getter, guard = preview
    gate.clear()
    other_app = FastAPI()
    # A second router must not reset global accounting or restart active jobs.
    other_app.include_router(module.get_router(getter, guard))
    jobs = []
    for user in map(str, range(1, 11)):
        for ident in ('source', 'two'):
            asset(tenants[user], ident)
            response = post(client, ident, user)
            assert response.status_code == 200, response.text
            jobs.append((user, response.json()['id']))
    assert entered.wait(5)
    asset(tenants['11'])
    assert post(client, user='11').status_code == 429
    assert len(calls) == 1
    gate.set()
    for user, ident in jobs:
        assert terminal(client, ident, user)['status'] == 'defaced'
    assert post(client, user='11').status_code == 200


@pytest.mark.parametrize('mask', [None, [], {'style':'img'}, {'replace_image':'C:/secret.png'},
    {'mask_scale':0}, {'mask_scale':3.1}, {'mosaic_size':3}, {'threshold':0},
    {'detection_size':123}, {'shape':'triangle'}, {'unknown':'secret'},
    {'style':'solid','mask_mode':'face_hair_all'}])
def test_invalid_mask_rejected_before_submission(preview, mask):
    client, tenants, _, _, calls, *_ = preview
    asset(tenants['1'])
    response = post(client, mask=mask)
    assert response.status_code == 422, response.text
    assert not calls and 'C:/secret.png' not in response.text


def test_saved_mask_snapshot_and_global_default_policy(preview):
    client, tenants, gate, entered, calls, *_ = preview
    asset(tenants['1'])
    asset(tenants['2'])
    redaction_settings.save_config(tenants['1'], {'mask_scale':1.8})
    assert post(client, mask={'mask_scale':2.1}).status_code == 422
    saved = {**redaction_settings.load_config(tenants['1']), 'mask_scale':2.1}
    store = ProductionStore(tenants['1'].storage_dir)
    draft = store.create_draft({'source_asset_id':'source', 'mask':saved})
    gate.clear()
    first = post(client, mask=saved).json()
    assert entered.wait(5)
    store.save_draft(draft['id'], 1, {'mask':{'mask_scale':1.5}})
    redaction_settings.save_config(tenants['1'], {'mask_scale':1.9})
    assert post(client, user='2', mask=saved).status_code == 422
    gate.set()
    assert terminal(client, first['id'])['status'] == 'defaced'
    assert calls[0][3]['mask_scale'] == 2.1
    second = post(client).json()
    assert terminal(client, second['id'])['status'] == 'defaced'
    assert calls[-1][3]['mask_scale'] == 1.9


def test_cache_reuse_stays_inside_tenant(preview):
    client, tenants, _, _, calls, *_ = preview
    for user in ('1', '2'):
        asset(tenants[user])
        for _ in range(2):
            job = post(client, user=user).json()
            assert terminal(client, job['id'], user)['status'] == 'defaced'
    assert len(calls) == 2
    assert calls[0][2].storage_dir != calls[1][2].storage_dir


def test_asset_kind_paths_missing_files_and_unknown_fields(preview, tmp_path):
    client, tenants, _, _, calls, *_ = preview
    asset(tenants['1'], 'image', kind='face')
    foreign = asset(tenants['2'])
    foreign_path = tenants['2'].storage_dir/'assets'/'source.mp4'
    asset(tenants['1'], 'escape', path=foreign_path)
    asset(tenants['1'], 'missing')
    (tenants['1'].storage_dir/'assets'/'missing.mp4').unlink()
    assert post(client, 'image').status_code == 422
    assert post(client, 'escape').status_code == 404
    assert post(client, 'missing').status_code == 404
    assert post(client, foreign['id']).status_code == 404
    assert post(client, 'image', video_url='https://example.test/private').status_code == 422
    assert post(client, '../secret').status_code == 404
    assert not calls


def test_worker_failure_sanitized_and_capacity_released(preview):
    client, tenants, _, _, _, failures, *_ = preview
    asset(tenants['1'])
    failures.append('C:/private/tenant/video.mp4 secret-key traceback')
    job = post(client).json()
    failed = terminal(client, job['id'])
    assert failed['status'] == 'failed' and failed['error']
    assert 'private' not in json.dumps(failed) and 'secret-key' not in json.dumps(failed)
    assert not failed.get('defaced_url')
    failures.clear()
    next_job = post(client).json()
    assert terminal(client, next_job['id'])['status'] == 'defaced'


def test_shared_preprocess_lock_blocks_preview_and_rechecks_queued_source(preview):
    from app import production_worker
    client, tenants, _, _, calls, *_ = preview
    asset(tenants['1'])
    with production_worker._preprocess_lock:
        job = post(client).json()
        assert client.get('/api/previews/'+job['id'], headers=headers()).json()['status'] == 'queued'
        assert not calls
        (tenants['1'].storage_dir/'assets'/'source.mp4').unlink()
    assert terminal(client, job['id'])['status'] == 'failed'
    assert not calls


def test_competing_distinct_submissions_cannot_exceed_tenant_capacity(preview):
    client, tenants, gate, _, _, *_ = preview
    gate.clear()
    for i in range(10):
        asset(tenants['1'], str(i))
    with ThreadPoolExecutor(max_workers=10) as submitters:
        results = list(submitters.map(lambda i: post(client, str(i)), range(10)))
    assert sorted(r.status_code for r in results) == [200, 200] + [429]*8
    gate.set()
    for response in results:
        if response.status_code == 200:
            assert terminal(client, response.json()['id'])['status'] == 'defaced'


def test_mask_aliases_deduplicate_and_queued_defaults_are_frozen(preview):
    from app import production_worker
    client, tenants, _, _, calls, *_ = preview
    asset(tenants['1'])
    with production_worker._preprocess_lock:
        first = post(client).json()
        alias = redaction_settings.load_config(tenants['1'])
        alias['style'] = alias.pop('blur_style')
        alias['detection_size'] = ''
        assert post(client, mask=alias).json()['id'] == first['id']
        redaction_settings.save_config(tenants['1'], {'mask_scale':2.2})
    assert terminal(client, first['id'])['status'] == 'defaced'
    assert calls[0][3]['mask_scale'] == 1.4


def test_symlink_source_and_cache_cannot_escape_tenant(preview, tmp_path):
    client, tenants, _, _, calls, *_ = preview
    foreign = tmp_path/'foreign'
    foreign.mkdir()
    (foreign/'source.mp4').write_bytes(b'private foreign content')
    tenant = tenants['1'].storage_dir
    tenant.mkdir(parents=True, exist_ok=True)
    try:
        (tenant/'assets').symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip('Windows symlink privilege is unavailable')
    asset(tenants['1'], path=tenant/'assets'/'source.mp4')
    assert post(client).status_code == 404
    assert not calls
    asset(tenants['2'])
    (tenants['2'].storage_dir/'cache').symlink_to(foreign, target_is_directory=True)
    job = post(client, user='2').json()
    assert terminal(client, job['id'], '2')['status'] == 'failed'
    assert not calls
    assert list(foreign.iterdir()) == [foreign/'source.mp4']


def test_restart_recovery_once_per_database(preview):
    client, tenants, gate, entered, _, _, module, getter, guard = preview
    store = ProductionStore(tenants['1'].storage_dir)
    with store.connection() as db:
        db.execute('CREATE TABLE production_previews (id TEXT PRIMARY KEY, status TEXT NOT NULL, '
                   'source_asset_id TEXT NOT NULL, mask TEXT NOT NULL, created_at TEXT NOT NULL, error TEXT)')
        for status in ('queued', 'running', 'defaced'):
            db.execute('INSERT INTO production_previews VALUES (?,?,?,?,?,?)',
                       (status, status, 'source', '{}', '2026-01-01', None))
    for status in ('queued', 'running'):
        value = client.get('/api/previews/'+status, headers=headers()).json()
        assert value['status'] == 'failed' and value['error']
    asset(tenants['1'])
    gate.clear()
    job = post(client).json()
    assert entered.wait(5)
    second = FastAPI()
    second.include_router(module.get_router(lambda: tenants['1'], lambda: None))
    with TestClient(second) as another:
        assert another.get('/api/previews/'+job['id']).json()['status'] == 'running'
    gate.set()
    assert terminal(client, job['id'])['status'] == 'defaced'


def test_import_has_no_filesystem_or_thread_side_effects(tmp_path):
    root = str(Path(__file__).resolve().parents[1])
    program = ('import sys, threading, pathlib; sys.path.insert(0, '+repr(root)+'); '
               'before=set(threading.enumerate()); import app.preview_router; '
               'assert set(threading.enumerate())==before; '
               'assert not list(pathlib.Path.cwd().iterdir())')
    result = subprocess.run([sys.executable, '-c', program], cwd=tmp_path,
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
