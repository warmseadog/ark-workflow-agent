"""Fail-closed boundary for a restored snapshot awaiting operator reconciliation.

This module deliberately does not import application settings or provider clients.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

MARKER = '.restore-hold.json'


def storage_root(settings_or_path) -> Path:
    if hasattr(settings_or_path, 'storage_dir'):
        return Path(getattr(settings_or_path, 'config_root', None) or settings_or_path.storage_dir)
    return Path(settings_or_path)


def is_restore_held(settings_or_path) -> bool:
    marker = storage_root(settings_or_path) / MARKER
    # Malformed and dangling markers cannot turn the safety boundary off.
    return marker.exists() or marker.is_symlink()


def install(app, settings_getter):
    from starlette.responses import JSONResponse
    @app.middleware('http')
    async def restored_data_boundary(request, call_next):
        if is_restore_held(settings_getter()) and not request.url.path.startswith('/static/'):
            return JSONResponse({'detail': '备份已恢复，正在等待管理员核对任务；自动处理和接口操作暂时停止。',
                                 'status': 'restore_held'}, status_code=503,
                                headers={'Cache-Control': 'no-store', 'Retry-After': '60'})
        return await call_next(request)


def release_hold(root, *, snapshot_id: str, operator: str, note: str) -> dict:
    """Record explicit reconciliation and release the global gate only.

    Quarantined historical records stay quarantined. This never requeues work.
    Run while the application is stopped, then start it to launch workers.
    """
    root = Path(root)
    marker = root / MARKER
    if marker.is_symlink():
        raise ValueError('Refusing a symlink restore marker')
    if not all(isinstance(value, str) and value.strip() for value in (snapshot_id, operator, note)):
        raise ValueError('Snapshot ID, operator and reconciliation note are required')
    if len(operator) > 128 or len(note) > 2000:
        raise ValueError('Operator or reconciliation note is too long')
    original = marker.read_bytes()
    try:
        held = json.loads(original)
    except (ValueError, UnicodeError):
        raise ValueError('Invalid restore marker; inspect the restored snapshot before releasing it') from None
    if not isinstance(held, dict) or held.get('snapshot_id') != snapshot_id:
        raise ValueError('Snapshot ID does not match the held restore')
    record = {'snapshot_id': snapshot_id, 'operator': operator.strip(), 'note': note.strip(),
              'released_at': datetime.now(timezone.utc).isoformat(), 'historical_tasks': 'remain_quarantined'}
    audit = root / 'private' / 'recovery-releases'
    if (root/'private').is_symlink() or audit.is_symlink():
        raise ValueError('Refusing a symlink audit directory')
    audit.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = audit / (uuid.uuid4().hex + '.json')
    with output.open('x', encoding='utf-8') as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    if os.name != 'nt':
        output.chmod(0o600)
    if marker.read_bytes() != original:
        raise ValueError('Restore marker changed; release aborted')
    marker.unlink()
    return record


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    status = commands.add_parser('status')
    status.add_argument('storage_root', type=Path)
    release = commands.add_parser('release-hold')
    release.add_argument('storage_root', type=Path)
    release.add_argument('--snapshot-id', required=True)
    release.add_argument('--operator', required=True)
    release.add_argument('--note', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'status':
            held = is_restore_held(args.storage_root)
            print(json.dumps({'restore_held': held}))
            return 2 if held else 0
        print(json.dumps(release_hold(args.storage_root, snapshot_id=args.snapshot_id,
                                      operator=args.operator, note=args.note), ensure_ascii=False))
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(1, f'Recovery action refused: {exc}\n')


if __name__ == '__main__':
    raise SystemExit(main())
