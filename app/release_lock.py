"""Mutual exclusion for release and backup operations that can restart the service."""
from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def maintenance_lock(backup_root):
    from .backup import _no_links
    if not Path(backup_root).is_absolute():
        raise ValueError('Shared backup root must be absolute')
    root = _no_links(backup_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = root / '.maintenance-operation.lock'
    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except OSError:
            raise RuntimeError('A release or backup holds the maintenance lock') from None
        os.lseek(handle, 0, os.SEEK_SET)
        os.write(handle, str(os.getpid()).encode())
        os.ftruncate(handle, os.lseek(handle, 0, os.SEEK_CUR))
        os.fsync(handle)
        yield
    finally:
        if acquired:
            os.lseek(handle, 0, os.SEEK_SET)
            if os.name == 'nt':
                msvcrt.locking(handle, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
        os.close(handle)
        # Keep one stable inode. Unlinking permits a new process to lock a
        # different inode while an existing opener still owns the old one.
