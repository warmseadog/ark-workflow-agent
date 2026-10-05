"""Explicit tenant storage boundaries. Credentials stay in the server root."""
from contextvars import ContextVar
from dataclasses import replace
import os
import re
import sqlite3

_request_settings = ContextVar('request_tenant_settings', default=None)
_request_user = ContextVar('request_actor_user', default=None)


def enabled():
    # Unknown non-empty values fail closed, instead of silently disabling auth.
    return os.environ.get('APP_AUTH_ENABLED', '').strip().lower() not in {'','0','false','no','off'}


def config_root(settings):
    return getattr(settings, 'config_root', None) or settings.storage_dir


def root_settings(settings):
    return replace(settings, storage_dir=config_root(settings), config_root=None, user_id='')


def user_settings(base, user):
    ident = user['id']
    if not re.fullmatch(r'[a-f0-9]{32}', ident):
        raise ValueError('Invalid server-side account ID')
    root = config_root(base)
    path = root if user.get('legacy_owner') else root/'users'/ident
    return replace(base, storage_dir=path, config_root=root, user_id=ident)


def current_settings(base):
    return _request_settings.get() or base


def tenant_settings(base, include_disabled=False):
    base = root_settings(base)
    if not enabled():
        return [({'id':'legacy','enabled':True,'legacy_owner':True,'max_concurrent':2,'max_queued':10},base)]
    from .accounts import Accounts
    return [(user,user_settings(base,user)) for user in Accounts(base.storage_dir).list_users()
            if include_disabled or (user['enabled'] and not user.get('deleted_at'))]


def legacy_allowed(settings):
    return not enabled() or settings.storage_dir.resolve() == config_root(settings).resolve()


def callback_settings(base, state):
    """Resolve only a cryptographically unguessable public callback capability."""
    from fastapi import HTTPException
    if not re.fullmatch(r'[a-f0-9]{64}', state):
        raise HTTPException(404, '认证链接不存在。')
    for _, tenant in tenant_settings(base, include_disabled=True):
        path = tenant.storage_dir/'private'/'portrait-sessions.db'
        if not path.is_file():
            continue
        with sqlite3.connect(path) as db:
            if db.execute('SELECT 1 FROM sessions WHERE state=?',(state,)).fetchone():
                return tenant
    raise HTTPException(404, '认证链接不存在。')
