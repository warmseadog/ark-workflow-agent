"""Root-shared durable logical concurrency; physical worker capacity stays fixed."""
from contextlib import closing
import os
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictInt

from .tenancy import config_root


def worker_capacity():
    try:
        return max(1, min(50, int(os.getenv('APP_VIDEO_WORKERS', '50'))))
    except ValueError:
        return 50


def actual_capacity(settings):
    from .production_worker import _managers
    manager = _managers.get(str(config_root(settings).resolve()))
    return manager.worker_count if manager else worker_capacity()


def _connect(settings):
    path = config_root(settings) / 'private' / 'scheduling.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.execute('CREATE TABLE IF NOT EXISTS scheduling (id INTEGER PRIMARY KEY CHECK(id=1), global_concurrency INTEGER NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, actor_id TEXT, before_value INTEGER, after_value INTEGER, created_at TEXT DEFAULT CURRENT_TIMESTAMP)')
    db.commit()
    return db


def load_config(settings, *, capacity=None):
    capacity = actual_capacity(settings) if capacity is None else capacity
    with closing(_connect(settings)) as db, db:
        db.execute('INSERT OR IGNORE INTO scheduling VALUES (1,?)', (capacity,))
        configured = db.execute('SELECT global_concurrency FROM scheduling WHERE id=1').fetchone()[0]
    return {'global_concurrency': min(configured, capacity), 'configured_concurrency': configured,
            'worker_capacity': capacity}


def save_config(settings, value, *, actor_id=''):
    capacity = actual_capacity(settings)
    if type(value) is not int or not 1 <= value <= capacity:
        raise ValueError(f'全局并发必须是 1–{capacity} 的整数。')
    with closing(_connect(settings)) as db, db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT global_concurrency FROM scheduling WHERE id=1').fetchone()
        db.execute('INSERT OR REPLACE INTO scheduling VALUES (1,?)', (value,))
        db.execute('INSERT INTO audit(actor_id,before_value,after_value) VALUES (?,?,?)',
                   (actor_id, previous[0] if previous else capacity, value))
    return load_config(settings, capacity=capacity)


def usage(settings):
    from .tenancy import tenant_settings, root_settings
    from .production_store import ProductionStore
    totals = {'running': 0, 'queued': 0}
    for _, tenant in tenant_settings(root_settings(settings), include_disabled=True):
        with ProductionStore(tenant.storage_dir).connection() as db:
            for state, count in db.execute("SELECT status,COUNT(*) FROM production_runs WHERE status IN ('running','queued') GROUP BY status"):
                totals[state] += count
    config = load_config(settings)
    totals['available_slots'] = max(0, config['global_concurrency'] - totals['running'])
    return totals


class SchedulingInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    global_concurrency: StrictInt


def get_router(settings_getter):
    router = APIRouter()

    def actor(request, write=False):
        from .tenancy import enabled
        from .permissions import is_admin, is_super_admin
        user = getattr(request.state, 'user', None)
        if enabled() and not (is_super_admin(user) if write else is_admin(user)):
            raise HTTPException(403, '仅超级管理员可修改全局并发。' if write else '仅管理员可查看全局并发。')
        return user or {}

    def response(settings, user):
        from .tenancy import enabled
        from .permissions import is_super_admin
        return {'config': load_config(settings), 'usage': usage(settings),
                'can_edit': not enabled() or is_super_admin(user)}

    @router.get('/api/admin/scheduling')
    def get(request: Request):
        user = actor(request)
        return response(settings_getter(), user)

    @router.put('/api/admin/scheduling')
    def put(values: SchedulingInput, request: Request):
        user = actor(request, write=True)
        settings = settings_getter()
        previous = load_config(settings)
        try:
            config = save_config(settings, values.global_concurrency, actor_id=user.get('id', ''))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if user.get('id'):
            from .accounts import Accounts
            Accounts(config_root(settings)).audit(user['id'], 'settings.scheduling', 'global',
                {'previous': previous['global_concurrency'], 'global_concurrency': config['global_concurrency']})
        return response(settings, user)

    return router
