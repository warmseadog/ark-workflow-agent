"""Separate cloud admission, local CPU work, and tenant-specific cache writers."""
from contextlib import contextmanager
import os
import threading


def _cloud_capacity():
    # MediaKit's documented account-wide default is 20 asynchronous tasks.
    # Raise only after the provider grants a higher quota (worker ceiling: 50).
    try:
        return max(1, min(50, int(os.getenv('APP_EXTERNAL_REDACTION_CONCURRENCY', '20'))))
    except ValueError:
        return 20


cloud_slots = threading.BoundedSemaphore(_cloud_capacity())
# All local video masking entry points (including external-service fallback)
# share these slots in the single application process. Other CPU preparation
# retains its existing lock and cannot accidentally serialize video masking.
LOCAL_REDACTION_CONCURRENCY = 3
local_redaction_slots = threading.BoundedSemaphore(LOCAL_REDACTION_CONCURRENCY)
local_lock = threading.RLock()
_cache_guard = threading.Lock()
_cache_locks = {}


@contextmanager
def cache_writer(path):
    key = str(path.resolve())
    with _cache_guard:
        entry = _cache_locks.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    try:
        with entry[0]:
            yield
    finally:
        with _cache_guard:
            entry[1] -= 1
            if entry[1] == 0:
                del _cache_locks[key]
