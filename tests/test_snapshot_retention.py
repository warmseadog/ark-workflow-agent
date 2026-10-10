"""Retention deletes only completed release snapshots, after verifying survivors."""
import hashlib
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

from app.backup import create_snapshot
from app.release_lock import maintenance_lock


def retention():
    path = Path(__file__).resolve().parents[1] / 'deploy/ecs/snapshot_retention.py'
    assert path.exists(), 'Snapshot retention command is not implemented'
    spec = importlib.util.spec_from_file_location('snapshot_retention', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def snapshots(tmp_path, monkeypatch):
    for key in ('DATABASE_URL', 'WORKFLOW_DB', 'WORKFLOW_STORAGE'):
        monkeypatch.delenv(key, raising=False)
    source = tmp_path / 'data'
    source.mkdir()
    (source / 'media.mp4').write_bytes(b'original media')
    with sqlite3.connect(source / 'test.db') as db:
        db.execute('CREATE TABLE example (value TEXT)')
        db.execute("INSERT INTO example VALUES ('preserve')")
    root = tmp_path / 'backups'
    receipts = root / 'release-receipts'
    receipts.mkdir(parents=True)
    paths = []
    for index in range(4):
        path = root / ('before-release-' + format(index, '032x'))
        create_snapshot(source, path, quiesced=True)
        manifest = json.loads((path / 'manifest.json').read_text(encoding='utf-8'))
        manifest['created_at'] = f'2026-10-08T0{index}:00:00+00:00'
        (path / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        receipt = {'phase': 'verified', 'revision': f'revision-{index}',
                   'snapshot': str(path), 'snapshot_id': manifest['snapshot_id'],
                   'snapshot_manifest_sha256': hashlib.sha256((path / 'manifest.json').read_bytes()).hexdigest()}
        (receipts / (hashlib.sha256(receipt['revision'].encode()).hexdigest() + '.json')).write_text(json.dumps(receipt))
        paths.append(path)
    return source, root, paths


def run(snapshots, **kwargs):
    source, root, _ = snapshots
    return retention().prune_snapshots(root, source, 'revision-3', **kwargs)


def test_dry_run_and_apply_keep_two_and_preserve_unrelated_data(snapshots):
    source, root, paths = snapshots
    unrelated = root / 'task-archive-example'
    unrelated.mkdir()
    (unrelated / 'keep').write_text('audit')
    report = run(snapshots)
    assert report['candidates'] == [str(p) for p in paths[:2]]
    assert all(p.exists() for p in paths)
    report = run(snapshots, apply=True)
    assert report['deleted'] == [str(p) for p in paths[:2]]
    assert [p.exists() for p in paths] == [False, False, True, True]
    assert (source / 'media.mp4').read_bytes() == b'original media'
    assert (unrelated / 'keep').read_text() == 'audit'
    assert len(list((root / 'release-receipts').glob('*.json'))) == 4
    assert run(snapshots, apply=True)['deleted'] == []


def test_configurable_count(snapshots):
    assert len(run(snapshots, keep_last=3, apply=True)['deleted']) == 1


@pytest.mark.parametrize('count', [0, 1, -1, True, '2', 2.5])
def test_invalid_count_never_deletes(snapshots, count):
    with pytest.raises(ValueError):
        run(snapshots, keep_last=count, apply=True)
    assert all(p.exists() for p in snapshots[2])


@pytest.mark.parametrize('mutation', ['media', 'manifest', 'receipt', 'missing_receipt'])
def test_bad_retained_snapshot_prevents_all_deletion(snapshots, mutation):
    _, root, paths = snapshots
    receipt = root / 'release-receipts' / (hashlib.sha256(b'revision-3').hexdigest() + '.json')
    if mutation == 'media':
        (paths[-1] / 'roots/storage/media.mp4').write_bytes(b'corrupt')
    elif mutation == 'manifest':
        (paths[-1] / 'manifest.json').write_text('{}')
    elif mutation == 'receipt':
        data = json.loads(receipt.read_text())
        data['phase'] = 'recovery_required'
        receipt.write_text(json.dumps(data))
    else:
        receipt.unlink()
    with pytest.raises((ValueError, OSError)):
        run(snapshots, apply=True)
    assert all(p.exists() for p in paths)


@pytest.mark.parametrize('marker', ['.release-maintenance.json', '.release-nginx-original', 'service-recovery.json'])
def test_pending_operation_prevents_deletion(snapshots, marker):
    (snapshots[1] / marker).write_text('{}')
    with pytest.raises(ValueError):
        run(snapshots, apply=True)
    assert all(p.exists() for p in snapshots[2])


def test_restore_hold_and_partial_snapshot_prevent_deletion(snapshots):
    source, root, paths = snapshots
    marker = source / '.restore-hold.json'
    marker.write_text('{}')
    with pytest.raises(ValueError):
        run(snapshots, apply=True)
    marker.unlink()
    (root / '.before-release-example.partial-123').mkdir()
    with pytest.raises(ValueError):
        run(snapshots, apply=True)
    assert all(p.exists() for p in paths)


def test_release_lock_prevents_deletion(snapshots):
    with maintenance_lock(snapshots[1]):
        with pytest.raises(RuntimeError):
            run(snapshots, apply=True)
    assert all(p.exists() for p in snapshots[2])


def test_current_rollback_snapshot_is_protected_even_if_older(snapshots):
    source, root, paths = snapshots
    report = retention().prune_snapshots(root, source, 'revision-0', apply=True)
    assert report['deleted'] == [str(paths[1])]
    assert paths[0].exists()


def test_overwritten_historical_receipt_does_not_break_retention(snapshots):
    # Reactivating revision-0 replaces its revision-keyed receipt; the old
    # snapshot remains complete and must not disable all future pruning.
    receipt = snapshots[1] / 'release-receipts' / (hashlib.sha256(b'revision-0').hexdigest() + '.json')
    newest = snapshots[1] / 'release-receipts' / (hashlib.sha256(b'revision-3').hexdigest() + '.json')
    data = json.loads(newest.read_text())
    data['revision'] = 'revision-0'
    receipt.write_text(json.dumps(data))
    report = retention().prune_snapshots(snapshots[1], snapshots[0], 'revision-0', apply=True)
    assert report['deleted'] == [str(p) for p in snapshots[2][:2]]


def test_corrupt_old_snapshot_without_historical_receipt_blocks_deletion(snapshots):
    receipt = snapshots[1] / 'release-receipts' / (hashlib.sha256(b'revision-0').hexdigest() + '.json')
    receipt.unlink()
    (snapshots[2][0] / 'roots/storage/media.mp4').write_bytes(b'corrupt')
    with pytest.raises(ValueError):
        run(snapshots, apply=True)
    assert all(p.exists() for p in snapshots[2])


def test_unreceipted_snapshot_newer_than_current_release_blocks_deletion(snapshots):
    receipt = snapshots[1] / 'release-receipts' / (hashlib.sha256(b'revision-3').hexdigest() + '.json')
    receipt.unlink()
    with pytest.raises(ValueError):
        retention().prune_snapshots(snapshots[1], snapshots[0], 'revision-2', apply=True)
    assert all(p.exists() for p in snapshots[2])


def test_data_cannot_be_used_as_snapshot_root(snapshots):
    source, root, _ = snapshots
    with pytest.raises(ValueError):
        retention().prune_snapshots(root, root, 'revision-3', apply=True)


def test_symlink_inside_delete_candidate_is_refused(snapshots, tmp_path):
    target = tmp_path / 'outside'
    target.mkdir()
    (target / 'keep').write_text('do not delete')
    try:
        (snapshots[2][0] / 'linked').symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip('Symlink creation unavailable on this host')
    with pytest.raises(ValueError):
        run(snapshots, apply=True)
    assert (target / 'keep').exists()
    assert all(p.exists() for p in snapshots[2])


def test_completed_backup_recovery_journal_does_not_block_retention(snapshots):
    (snapshots[1] / 'service-recovery.json').write_text(json.dumps({
        'service': 'ark-video-workflow.service', 'restart_required': False}))
    assert len(run(snapshots, apply=True)['deleted']) == 2


def test_nested_bind_mount_is_refused(snapshots, monkeypatch):
    module = retention()
    monkeypatch.setattr(module, '_mount_points', lambda: {snapshots[2][0] / 'roots'})
    with pytest.raises(ValueError, match='mount'):
        module.prune_snapshots(snapshots[1], snapshots[0], 'revision-3', apply=True)
    assert all(p.exists() for p in snapshots[2])


def test_runtime_guard_rechecked_after_verification_before_deletion(snapshots):
    calls = []
    def guard():
        calls.append(True)
        if len(calls) == 2:
            raise ValueError('Current release changed')
    with pytest.raises(ValueError, match='changed'):
        run(snapshots, apply=True, guard=guard)
    assert len(calls) == 2
    assert all(p.exists() for p in snapshots[2])
