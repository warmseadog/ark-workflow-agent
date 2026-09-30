"""Expiring, durable access to an explicitly submitted redacted video."""
import json
import math
import os
from pathlib import Path
import re
import secrets
import tempfile
from threading import Lock
import time
from .secure_transport import validate_endpoint

_videos: dict[str, tuple[Path, float]] = {}
_lock = Lock()


def _scoped_video(path: Path, storage: Path) -> bool:
    work = (storage / 'work').resolve()
    if not path.is_relative_to(work) or not path.is_file():
        return False
    if path.name == 'defaced.mp4':
        return True
    # A generated base is a separate, durable artifact. Never admit arbitrary
    # uploaded originals or other files merely because they sit under work/.
    if path.name == 'base.mp4' and path.parent.parent == work and re.fullmatch(r'[0-9a-f]{32}',path.parent.name):
        import sqlite3
        database=storage/'production.db'
        if not database.is_file():
            return False
        try:
            with sqlite3.connect(database.resolve().as_uri()+'?mode=ro',uri=True) as db:
                row=db.execute('SELECT c.data FROM production_continuations c JOIN production_runs r ON r.id=c.run_id WHERE c.run_id=? AND r.id NOT IN (SELECT id FROM production_deleted_runs)',(path.parent.name,)).fetchone()
                return bool(row and json.loads(row[0]).get('base_ready'))
        except (sqlite3.Error,ValueError):
            return False
    return False


def publish_video(path: Path, storage: Path, public_base_url: str) -> str:
    public_base_url = validate_endpoint(public_base_url, allow_local=False)
    resolved = path.resolve()
    if not _scoped_video(resolved, storage):
        raise ValueError('只能分享当前任务的打码视频。')
    now = time.time()
    expiry = now + 7200
    token = secrets.token_urlsafe(32)
    records = storage / 'private' / 'reference-videos'
    records.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=records, prefix='.reference-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as target:
            json.dump({'path': str(resolved), 'expires_at': expiry}, target)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, records / (token + '.json'))
    finally:
        Path(temporary).unlink(missing_ok=True)
    # Return a URL only after its original expiry and scoped path are durable.
    with _lock:
        for old in [k for k, (_, until) in _videos.items() if until <= now]:
            _videos.pop(old, None)
        _videos[token] = (resolved, expiry)
    return f'{public_base_url.rstrip("/")}/api/reference-videos/{token}'


def get_video(token: str, storage: Path | None = None) -> Path | None:
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,128}', token):
        return None
    with _lock:
        record = _videos.get(token)
        if record:
            path, expiry = record
            if expiry > time.time() and path.is_file():
                if storage is None or _scoped_video(path, storage):
                    return path
                return None
            _videos.pop(token, None)
        if storage is None:
            return None
        try:
            data = json.loads((storage / 'private' / 'reference-videos' / (token + '.json')).read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('path'), str):
                return None
            expiry = data.get('expires_at')
            if type(expiry) not in {int, float} or not math.isfinite(expiry) or expiry <= time.time():
                return None
            path = Path(data['path']).resolve()
            if not _scoped_video(path, storage):
                return None
        except (OSError, ValueError, TypeError):
            return None
        _videos[token] = (path, expiry)
        return path
