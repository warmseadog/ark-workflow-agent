import hashlib
import json

import pytest


def release_files(root):
    root.mkdir(exist_ok=True)
    content = {'schema': 'ark-release', 'version': 1, 'source_commit': 'a' * 40,
               'capabilities': ['restore-hold-v1'],
               'files': {'app/main.py': {'sha256': 'b' * 64, 'size': 2, 'mode': 420}},
               'dependencies': {'fastapi': '0.116.1'}, 'source_dirty': True,
               'untracked_files': ['app/main.py']}
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    manifest = {**content, 'content_sha256': digest, 'revision': content['source_commit'] + '+' + digest[:16]}
    (root / 'release-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    (root / 'REVISION').write_text(manifest['revision'] + '\n', encoding='utf-8')
    return manifest


def test_runtime_identity_preserves_worktree_content_id(tmp_path):
    from app.release_identity import read_release_identity
    manifest = release_files(tmp_path)
    identity = read_release_identity(tmp_path)
    assert identity['revision'] == manifest['revision']
    assert identity['source_commit'] == 'a' * 40
    assert identity['dependencies'] == manifest['dependencies']
    assert 'files' not in identity
    assert read_release_identity(tmp_path / 'development') is None


@pytest.mark.parametrize('damage', ['revision', 'content', 'json', 'missing_revision', 'missing_manifest'])
def test_corrupt_release_identity_never_falls_back_to_a_plausible_version(tmp_path, damage):
    from app.release_identity import read_release_identity, ReleaseIdentityError
    manifest = release_files(tmp_path)
    if damage == 'revision':
        (tmp_path / 'REVISION').write_text('old-release')
    elif damage == 'content':
        manifest['dependencies']['fastapi'] = '9.9.9'
        (tmp_path / 'release-manifest.json').write_text(json.dumps(manifest))
    elif damage == 'json':
        (tmp_path / 'release-manifest.json').write_text('{')
    elif damage == 'missing_revision':
        (tmp_path / 'REVISION').unlink()
    else:
        (tmp_path / 'release-manifest.json').unlink()
    with pytest.raises(ReleaseIdentityError):
        read_release_identity(tmp_path)


def test_backup_records_actual_release_identity_without_a_handwritten_sha(tmp_path):
    from app.backup_operations import run_backup
    from tests.test_backup_operations import config, Service
    from tests.test_backup_transport import ObjectStore
    cfg = config(tmp_path)
    manifest = release_files(tmp_path / 'release')
    cfg['release_root'] = str(tmp_path / 'release')
    cfg.pop('code_revision')
    receipt = run_backup(cfg, service=Service(), client=ObjectStore())
    from pathlib import Path
    manifests = list(Path(cfg['snapshot_root']).glob('snapshot-*/manifest.json'))
    assert len(manifests) == 1
    backup = json.loads(manifests[0].read_text())
    assert backup['snapshot_id'] == receipt['snapshot_id']
    assert backup['code_revision'] == manifest['source_commit']
    assert backup['release_identity']['revision'] == manifest['revision']


@pytest.mark.parametrize('damage', ['source_commit', 'revision', 'missing_digest'])
def test_snapshot_verification_rejects_inconsistent_release_identity(tmp_path, damage):
    from app.backup_operations import run_backup
    from app.backup import verify_snapshot, BackupError
    from tests.test_backup_operations import config, Service
    from tests.test_backup_transport import ObjectStore
    from pathlib import Path
    cfg = config(tmp_path)
    release_files(tmp_path / 'release')
    cfg['release_root'] = str(tmp_path / 'release')
    run_backup(cfg, service=Service(), client=ObjectStore())
    path = next(Path(cfg['snapshot_root']).glob('snapshot-*/manifest.json'))
    manifest = json.loads(path.read_text())
    if damage == 'source_commit':
        manifest['release_identity']['source_commit'] = 'c' * 40
    elif damage == 'revision':
        manifest['release_identity']['revision'] = 'corrupt-identity'
    else:
        manifest['release_identity'].pop('content_sha256')
    path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(BackupError):
        verify_snapshot(path.parent)


def test_backup_rejects_wrong_manual_revision_before_stopping_service(tmp_path):
    from app.backup_operations import run_backup
    from tests.test_backup_operations import config, Service
    from tests.test_backup_transport import ObjectStore
    cfg = config(tmp_path)
    release_files(tmp_path / 'release')
    cfg.update(release_root=str(tmp_path / 'release'), code_revision='c' * 40)
    service = Service()
    with pytest.raises(ValueError):
        run_backup(cfg, service=service, client=ObjectStore())
    assert service.events == []


def test_backup_refuses_identity_changed_after_preflight(tmp_path):
    from app.backup_operations import run_backup
    from tests.test_backup_operations import config, Service
    from tests.test_backup_transport import ObjectStore
    cfg = config(tmp_path)
    release_files(tmp_path / 'release')
    cfg['release_root'] = str(tmp_path / 'release')

    class ChangedRelease(Service):
        def stop(self):
            super().stop()
            (tmp_path / 'release' / 'REVISION').write_text('unexpected-switch')

    client = ObjectStore()
    with pytest.raises(ValueError):
        run_backup(cfg, service=ChangedRelease(), client=client)
    assert not client.objects


def test_backup_refuses_a_current_link_that_disagrees_with_running_service(tmp_path):
    from app.backup_operations import run_backup
    from tests.test_backup_operations import config, Service
    from tests.test_backup_transport import ObjectStore
    cfg = config(tmp_path)
    release_files(tmp_path / 'release')
    cfg['release_root'] = str(tmp_path / 'release')

    class OtherRelease(Service):
        def running_release_identity(self):
            return None  # Running legacy code while current points at a new package.

    service = OtherRelease()
    with pytest.raises(ValueError):
        run_backup(cfg, service=service, client=ObjectStore())
    assert service.events == []


def test_systemd_backup_reads_actual_process_release(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from pathlib import Path
    from app import backup_operations as operations
    root = tmp_path / 'proc' / '123' / 'cwd'
    root.mkdir(parents=True)
    manifest = release_files(root)
    monkeypatch.setattr(operations, 'Path', lambda value: tmp_path / 'proc' if value == '/proc' else Path(value))
    monkeypatch.setattr(operations.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout='123\n'))
    identity = operations.SystemdService('ark-video-workflow.service').running_release_identity()
    assert identity['revision'] == manifest['revision']


def test_version_api_reports_identity_but_not_package_contents(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app import main
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    manifest = release_files(tmp_path)
    monkeypatch.setattr(main, 'RELEASE_ROOT', tmp_path, raising=False)
    response = TestClient(main.app).get('/api/version')
    assert response.status_code == 200
    assert response.json()['revision'] == manifest['revision']
    assert 'files' not in response.json()
    (tmp_path / 'REVISION').write_text('stale')
    assert TestClient(main.app).get('/api/version').status_code == 503
