"""Short SQLite admission transactions; tenant databases remain queue truth.

Reservations cover validation before enqueue. OS leases make abandoned
reservations reclaimable after a crash without expiring a live request.

The limit governs new admission, including manual resume/retry. Recovery and
internal requeue preserve previously accepted work even above the limit;
new admissions remain blocked until that backlog drains. Existing committed
idempotency keys replay normally; an in-flight duplicate receives retryable 429.
"""
from contextlib import contextmanager, closing
from contextvars import ContextVar
from functools import wraps
import os
from pathlib import Path
import sqlite3
import threading
import uuid

from fastapi import HTTPException
from fastapi.routing import APIRoute
import anyio

_reservation = ContextVar('queue_reservation', default=None)
_transaction_state = ContextVar('queue_transaction', default=None)
request_stores = ContextVar('production_request_stores', default=None)
_gate_lock = threading.Lock()
_gates = {}
_lookup_limiter = anyio.CapacityLimiter(4)


class CapacityError(ValueError):
    pass


def positive_setting(name, default):
    try:
        value = int(os.getenv(name, str(default)))
        return value if value > 0 else default
    except ValueError:
        return default


def _lease(path):
    handle = path.open('a+b')
    handle.seek(0, 2)
    if not handle.tell():
        handle.write(b'0'); handle.flush()
    handle.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def _prune(db, folder):
    for (token,) in db.execute('SELECT token FROM reservations').fetchall():
        path = folder / (token + '.lock')
        handle = _lease(path)
        if handle is not None:
            db.execute('DELETE FROM reservations WHERE token=?', (token,))
            handle.close()
            path.unlink(missing_ok=True)


@contextmanager
def transaction(root):
    root = Path(root).resolve()
    current = _transaction_state.get()
    if current and current[0] == root:
        yield current[1]
        return
    folder = root / 'private' / 'queue-admission'
    folder.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(folder/'admission.db', timeout=.15)
    token = None
    try:
        db.execute('CREATE TABLE IF NOT EXISTS reservations '
                   '(token TEXT PRIMARY KEY, tenant TEXT NOT NULL, request_key TEXT NOT NULL, '
                   'UNIQUE(tenant, request_key))')
        db.execute('BEGIN IMMEDIATE')
        _prune(db, folder)
        token = _transaction_state.set((root, db))
        yield db
        db.commit()
    except sqlite3.OperationalError as exc:
        if 'locked' in str(exc).lower() or 'busy' in str(exc).lower():
            raise CapacityError('任务入口繁忙，请稍后重试。') from None
        raise
    finally:
        if token is not None:
            _transaction_state.reset(token)
        db.close()


def _counts(root, db, exclude_token=None):
    counts = {}
    paths = [root/'production.db', *sorted((root/'users').glob('*/production.db'))]
    for path in paths:
        if not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            raise CapacityError('任务队列暂不可用，请稍后重试。')
        with closing(sqlite3.connect(path, timeout=.05)) as tenant:
            # A new tenant's file exists before its schema initialization ends.
            if not tenant.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='production_runs'").fetchone():
                continue
            count = tenant.execute("SELECT COUNT(*) FROM production_runs WHERE status='queued'").fetchone()[0]
        counts[str(path.parent.resolve())] = count
    for token, tenant in db.execute('SELECT token,tenant FROM reservations'):
        if token != exclude_token:
            counts[tenant] = counts.get(tenant, 0) + 1
    return counts


def check_capacity(store, max_queued=None, *, committing=True):
    current = _transaction_state.get()
    if not current:
        return
    root, db = current
    own = _reservation.get()
    exclude = own[2] if committing and own and own[:2] == (root,str(store.storage.resolve())) else None
    counts = _counts(root, db, exclude)
    if sum(counts.values()) >= positive_setting('APP_MAX_QUEUED_TOTAL', 30):
        raise CapacityError('全站等待队列已满，请稍后重试。')
    if max_queued is not None and counts.get(str(store.storage.resolve()), 0) >= max_queued:
        raise CapacityError('当前账户排队任务已达上限，请等待任务开始或撤销排队任务。')


@contextmanager
def reserve(settings, key, max_queued=None):
    from .tenancy import config_root
    from types import SimpleNamespace
    root = Path(config_root(settings)).resolve()
    tenant = str(settings.storage_dir.resolve())
    token = uuid.uuid4().hex
    folder = root/'private'/'queue-admission'
    folder.mkdir(parents=True, exist_ok=True)
    path = folder/(token+'.lock')
    handle = _lease(path)
    if handle is None:
        raise CapacityError('任务入口繁忙，请稍后重试。')
    context = None
    try:
        with transaction(root) as db:
            if db.execute('SELECT 1 FROM reservations WHERE tenant=? AND request_key=?', (tenant,key)).fetchone():
                raise CapacityError('该任务正在提交，请稍后使用相同提交标识重试。')
            check_capacity(SimpleNamespace(storage=settings.storage_dir),max_queued,committing=False)
            db.execute('INSERT INTO reservations VALUES (?,?,?)', (token,tenant,key))
        context = _reservation.set((root,tenant,token))
        yield
    finally:
        if context is not None:
            _reservation.reset(context)
        # Closing first allows the next transaction to reclaim this reservation
        # even when immediate cleanup cannot obtain the database lock.
        handle.close()
        try:
            with transaction(root) as db:
                db.execute('DELETE FROM reservations WHERE token=?', (token,))
                path.unlink(missing_ok=True)
        except CapacityError:
            pass


def queued_write(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if self.queue_root is None:
            return method(self, *args, **kwargs)
        with transaction(self.queue_root):
            return method(self, *args, **kwargs)
    return wrapped


def _existing_request(settings, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('idempotency_key'), str):
        return False
    path = settings.storage_dir/'production.db'
    if not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(path, timeout=.05)) as db:
            return bool(db.execute('SELECT 1 FROM production_runs WHERE idempotency_key=?',
                                   (payload['idempotency_key'],)).fetchone())
    except sqlite3.Error:
        return False


def route_class(settings_getter):
    class SubmissionRoute(APIRoute):
        def get_route_handler(self):
            handler = super().get_route_handler()
            limited = self.path.endswith('/runs') or self.path.endswith(('/resume','/person-preparation/retry','/retry-without-audio'))
            async def serve(request):
                scope = request_stores.set({})
                acquired = False
                key = None
                try:
                    if limited and request.method == 'POST':
                        settings = settings_getter()
                        existing = False
                        if self.path.endswith('/runs'):
                            try:
                                payload = await request.json()
                            except ValueError:
                                payload = None
                            # Admission must not wait behind blocked sync endpoints.
                            existing = await anyio.to_thread.run_sync(
                                _existing_request, settings, payload, limiter=_lookup_limiter)
                        if not existing:
                            from .tenancy import config_root
                            key = str(config_root(settings).resolve())
                            with _gate_lock:
                                count = _gates.get(key, 0)
                                if count < positive_setting('APP_SUBMISSION_WORKERS', 4):
                                    _gates[key] = count + 1
                                    acquired = True
                            if not acquired:
                                raise HTTPException(429,'任务入口繁忙，请稍后重试。',headers={'Retry-After':'2'})
                    return await handler(request)
                finally:
                    if acquired:
                        with _gate_lock:
                            remaining = _gates[key] - 1
                            if remaining:
                                _gates[key] = remaining
                            else:
                                _gates.pop(key, None)
                    request_stores.reset(scope)
            return serve
    return SubmissionRoute
