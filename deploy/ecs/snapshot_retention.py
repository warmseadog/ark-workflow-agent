"""Keep recent verified release snapshots. Dry-run unless --apply is explicit.

Installed independently of immutable releases; uses the current release's offline
backup verifier and shared maintenance lock. Never restarts application services.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid


def _json(path):
    from app.backup import _no_links
    value = json.loads(_no_links(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object: ' + str(path))
    return value


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _present(path):
    return path.exists() or path.is_symlink()


def _mount_points():
    if sys.platform != 'linux':
        return set()
    # st_dev / ismount alone do not detect bind mounts on the same filesystem.
    lines = Path('/proc/self/mountinfo').read_text().splitlines()
    return {Path(re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), line.split()[4]))
            for line in lines}


def _tree_boundary(path, root):
    """Reject links (including junctions), nested mounts and device crossings."""
    from app.backup import _no_links
    if _no_links(path).resolve().parent != root.resolve():
        raise ValueError('Snapshot is not a direct child of the backup root')
    device = root.stat().st_dev
    mounts = _mount_points()
    for directory, folders, files in os.walk(path, followlinks=False):
        for entry in [Path(directory), *(Path(directory) / name for name in folders + files)]:
            _no_links(entry)
            if entry.stat().st_dev != device or entry in mounts or os.path.ismount(entry):
                raise ValueError('Refusing snapshot with a mount/device boundary')


def _audit(root, report):
    from app.backup import _no_links
    output = _no_links(root / 'retention-last-run.json')
    temporary = root / ('.retention-' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, output)


def prune_snapshots(snapshot_root, storage_root, current_revision, *, keep_last=2, apply=False, guard=None):
    from app.backup import _no_links, _load_manifest, _remove_owned, verify_snapshot
    from app.release_lock import maintenance_lock

    if type(keep_last) is not int or keep_last < 2:
        raise ValueError('keep_last must be an integer of at least 2')
    if not Path(snapshot_root).is_absolute() or not Path(storage_root).is_absolute():
        raise ValueError('Absolute backup and storage paths are required')
    root, storage = _no_links(snapshot_root), _no_links(storage_root)
    if root == storage or root.is_relative_to(storage) or storage.is_relative_to(root):
        raise ValueError('Snapshot root must be separate from business data')
    if not root.is_dir() or not storage.is_dir():
        raise ValueError('Backup and storage directories must already exist')

    with maintenance_lock(root):
        if guard:
            guard()
        for marker in (root / '.release-maintenance.json', root / '.release-nginx-original',
                       storage / '.restore-hold.json'):
            if _present(marker):
                raise ValueError('Pending release/restore operation: ' + marker.name)
        recovery = root / 'service-recovery.json'
        if _present(recovery):
            state = _json(recovery)
            if state.get('service') != 'ark-video-workflow.service' or state.get('restart_required') is not False:
                raise ValueError('Pending backup service recovery')
        if list(root.glob('.*.partial-*')):
            raise ValueError('Incomplete snapshot requires operator inspection')

        receipt_root = _no_links(root / 'release-receipts')
        current_receipt = _json(receipt_root / (hashlib.sha256(current_revision.encode()).hexdigest() + '.json'))
        if current_receipt.get('phase') != 'verified' or current_receipt.get('revision') != current_revision:
            raise ValueError('Current release has no verified receipt')
        protected = _no_links(current_receipt['snapshot'])
        if protected.parent != root or not protected.is_dir():
            raise ValueError('Current rollback snapshot is missing or outside backup root')

        receipts = {}
        for path in receipt_root.glob('*.json'):
            receipt = _json(path)
            if receipt.get('phase') == 'verified' and isinstance(receipt.get('snapshot'), str):
                receipts.setdefault(receipt['snapshot'], []).append(receipt)

        snapshots = []
        unreceipted = set()
        for path in root.glob('before-release-*'):
            if not re.fullmatch(r'before-release-[a-f0-9]{32}', path.name):
                raise ValueError('Unknown snapshot directory requires operator inspection')
            _no_links(path)
            if not path.is_dir():
                raise ValueError('Snapshot must be a directory')
            _no_links(path / 'manifest.json')
            manifest = _load_manifest(path)
            if Path(manifest['roots']['storage']['source']) != storage:
                raise ValueError('Snapshot belongs to a different storage root')
            digest = _hash(path / 'manifest.json')
            matches = [r for r in receipts.get(str(path), [])
                       if r.get('snapshot_manifest_sha256') == digest
                       and r.get('snapshot_id') == manifest['snapshot_id']]
            if receipts.get(str(path)) and not matches:
                raise ValueError('Snapshot differs from its recorded release receipt: ' + path.name)
            if not matches:
                unreceipted.add(path)
            if path == protected and current_receipt not in matches:
                raise ValueError('Current release receipt does not match its rollback snapshot')
            created = datetime.fromisoformat(manifest['created_at'])
            if created.tzinfo is None:
                raise ValueError('Snapshot timestamp requires a timezone')
            snapshots.append((created, path, digest))

        if protected not in [item[1] for item in snapshots]:
            raise ValueError('Current rollback snapshot is not a managed release snapshot')
        protected_time = next(item[0] for item in snapshots if item[1] == protected)
        if any(created > protected_time and path in unreceipted for created, path, _ in snapshots):
            raise ValueError('Unreceipted snapshot newer than the current release requires inspection')
        snapshots.sort(key=lambda item: (item[0], item[1].name))
        keep = {item[1] for item in snapshots[-keep_last:]} | {protected}
        candidates = [item for item in snapshots if item[1] not in keep]
        report = {'time': datetime.now(timezone.utc).isoformat(), 'keep_last': keep_last,
                  'current_revision': current_revision, 'apply': apply,
                  'kept': [str(item[1]) for item in snapshots if item[1] in keep],
                  'candidates': [str(item[1]) for item in candidates], 'deleted': [],
                  'phase': 'planned', 'protected_snapshot': str(protected)}

        # Full media hashing is needed only before an actual prune; no repeated
        # multi-gigabyte scan on every timer tick when already within the limit.
        if candidates:
            for _, path, _ in snapshots:
                _tree_boundary(path, root)
            # Revision-keyed receipts are overwritten when a version is
            # reactivated. Independently verify these complete older snapshots
            # rather than permanently disabling retention after a rollback.
            for path in keep | unreceipted:
                verify_snapshot(path)
            for _, path, digest in snapshots:
                if _hash(path / 'manifest.json') != digest:
                    raise ValueError('Snapshot manifest changed during verification')
        if apply:
            if guard:
                guard()
            _audit(root, report)
            for _, path, _ in candidates:
                _tree_boundary(path, root)
                report['deleting'] = str(path)
                _audit(root, report)
                _remove_owned(path)
                report['deleted'].append(str(path))
                report.pop('deleting')
                _audit(root, report)
                print(json.dumps({'deleted_snapshot': str(path)}), flush=True)
            report['phase'] = 'complete'
            _audit(root, report)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, help='Existing ECS release configuration')
    parser.add_argument('--keep-last', type=int, default=2)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        # Read only paths needed for the maintenance command; never load app.env.
        cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
        root = Path(cfg['root'])
        if not root.is_absolute():
            raise ValueError('Application root must be absolute')
        current = (root / 'current').resolve(strict=True)
        if current.parent != root / 'releases':
            raise ValueError('Current release is outside managed releases')
        sys.path.insert(0, str(current))
        from app.backup import _no_links
        from app.release_identity import read_release_identity
        from app.release_operations import ECSHost
        _no_links(current)
        identity = read_release_identity(current)
        if identity is None:
            raise ValueError('Packaged release identity is required')
        host = ECSHost(cfg)
        def guard():
            if host.current_release() != current:
                raise ValueError('Current release changed; retry retention later')
            if not host.is_active() or host.runtime_env().get('STORAGE_DIR') != cfg['storage_root']:
                raise ValueError('Running service/storage does not match release configuration')
        report = prune_snapshots(cfg['snapshot_root'], cfg['storage_root'], identity['revision'],
                                 keep_last=args.keep_last, apply=args.apply, guard=guard)
        print(json.dumps(report, sort_keys=True), flush=True)
        return 0
    except (ValueError, RuntimeError, OSError, KeyError, TypeError) as error:
        print('Snapshot retention refused: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
