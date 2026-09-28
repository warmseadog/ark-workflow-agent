"""Central account/session store. Pass the global storage root, never a user root.

Public users never contain password hashes. ``authenticate`` returns a session
mapping with ``user``, ``csrf_token`` and Unix ``expires_at`` (fixed 12 hours).
All state is shared through SQLite, including login throttling across workers.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import unicodedata
from uuid import uuid4

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError


SESSION_SECONDS = 12 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_USERNAME_LIMIT = 5
LOGIN_IP_LIMIT = 20
_HASHER = PasswordHasher(type=Type.ID)
_DUMMY_HASH = _HASHER.hash(secrets.token_urlsafe(32))
_PUBLIC_FIELDS = (
    'id', 'username', 'role', 'enabled', 'must_change_password',
    'max_concurrent', 'max_queued', 'legacy_owner', 'created_at',
)


class AccountError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


def _username(value: str) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise AccountError(422, '用户名需为 1–64 个字母、数字或 . @ + _ - 字符。')
    value = unicodedata.normalize('NFKC', value).strip().casefold()
    if not re.fullmatch(r'[\w.@+-]{1,64}', value):
        raise AccountError(422, '用户名需为 1–64 个字母、数字或 . @ + _ - 字符。')
    return value


def _password(value: str) -> str:
    if not isinstance(value, str) or not 8 <= len(value) <= 1024:
        raise AccountError(422, '密码长度需为 8–1024 个字符。')
    return value


def _quota(value: int, minimum: int, name: str) -> int:
    if type(value) is not int or not minimum <= value <= 10000:
        label = '最大并发数' if name == 'max_concurrent' else '最大排队数'
        raise AccountError(422, f'{label}需为 {minimum}–10000 之间的整数。')
    return value


def _public(row) -> dict:
    result = {key: row[key] for key in _PUBLIC_FIELDS}
    for key in ('enabled', 'must_change_password', 'legacy_owner'):
        result[key] = bool(result[key])
    return result


def _text(value, limit=128) -> str:
    return ''.join(char for char in str(value or '') if not unicodedata.category(char).startswith('C'))[:limit]


def _details(details) -> dict:
    # Positive allowlist: callers cannot accidentally persist arbitrary payloads,
    # nested credentials, password hashes, cookies, or authorization headers.
    result = {}
    if not isinstance(details, dict):
        return result
    for key in ('username', 'role', 'remote_ip', 'reason', 'enabled',
                'must_change_password', 'legacy_owner', 'max_concurrent', 'max_queued'):
        value = details.get(key)
        if isinstance(value, (str, int, bool)):
            result[key] = _text(value, 128) if isinstance(value, str) else value
    return result


def _verify(encoded: str, password: str) -> bool:
    try:
        return _HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError):
        return False


class Accounts:
    def __init__(self, root: Path):
        self.root = Path(root)
        private = self.root / 'private'
        private.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = private / 'accounts.db'
        with self._connect() as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
                    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                    must_change_password INTEGER NOT NULL DEFAULT 1 CHECK(must_change_password IN (0, 1)),
                    max_concurrent INTEGER NOT NULL DEFAULT 1 CHECK(max_concurrent >= 1),
                    max_queued INTEGER NOT NULL DEFAULT 10 CHECK(max_queued >= 0),
                    legacy_owner INTEGER NOT NULL DEFAULT 0 CHECK(legacy_owner IN (0, 1)),
                    created_at REAL NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_legacy_owner ON users(legacy_owner) WHERE legacy_owner = 1;
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    csrf_token TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    username TEXT NOT NULL,
                    remote_ip TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS attempts_username ON login_attempts(username, created_at);
                CREATE INDEX IF NOT EXISTS attempts_ip ON login_attempts(remote_ip, created_at);
                CREATE INDEX IF NOT EXISTS attempts_time ON login_attempts(created_at);
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    details TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            ''')
        if os.name != 'nt':
            private.chmod(0o700)
            self.db_path.chmod(0o600)

    @contextmanager
    def _connect(self, *, write=False):
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        try:
            if write:
                conn.execute('BEGIN IMMEDIATE')
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _user(conn, user_id):
        row = conn.execute('SELECT * FROM users WHERE id=?', (user_id,)).fetchone()
        if row is None:
            raise AccountError(404, '账号不存在。')
        return row

    @staticmethod
    def _admin(conn, actor_id):
        row = conn.execute('SELECT role, enabled FROM users WHERE id=?', (actor_id,)).fetchone()
        if row is None or not row['enabled'] or row['role'] != 'admin':
            raise AccountError(403, '需要管理员权限。')

    @staticmethod
    def _audit(conn, actor_id, action, target='', details=None):
        conn.execute('INSERT INTO audit(actor_id,action,target,details,created_at) VALUES(?,?,?,?,?)',
                     (_text(actor_id), _text(action), _text(target),
                      json.dumps(_details(details), ensure_ascii=False), time.time()))

    def init_admin(self, username: str, password: str) -> dict:
        """Bootstrap only an empty database; subsequent calls never alter users."""
        with self._connect(write=True) as conn:
            existing = conn.execute('SELECT * FROM users ORDER BY created_at, id LIMIT 1').fetchone()
            if existing is not None:
                return _public(existing)
            username, password = _username(username), _password(password)
            user_id = uuid4().hex
            conn.execute('''INSERT INTO users(id,username,password_hash,role,legacy_owner,created_at)
                            VALUES(?,?,?,'admin',1,?)''',
                         (user_id, username, _HASHER.hash(password), time.time()))
            self._audit(conn, user_id, 'user.init_admin', user_id, {'username': username})
            return _public(self._user(conn, user_id))

    def create_user(self, username: str, password: str, actor_id: str,
                    max_concurrent: int = 1, max_queued: int = 10) -> dict:
        with self._connect(write=True) as conn:
            self._admin(conn, actor_id)
            username, password = _username(username), _password(password)
            max_concurrent = _quota(max_concurrent, 1, 'max_concurrent')
            max_queued = _quota(max_queued, 0, 'max_queued')
            user_id = uuid4().hex
            try:
                conn.execute('''INSERT INTO users(id,username,password_hash,role,max_concurrent,max_queued,created_at)
                                VALUES(?,?,?,'user',?,?,?)''',
                             (user_id, username, _HASHER.hash(password), max_concurrent, max_queued, time.time()))
            except sqlite3.IntegrityError:
                raise AccountError(409, '用户名已存在。') from None
            self._audit(conn, actor_id, 'user.create', user_id, {'username': username,
                        'max_concurrent': max_concurrent, 'max_queued': max_queued})
            return _public(self._user(conn, user_id))

    def get_user(self, user_id: str) -> dict:
        with self._connect() as conn:
            return _public(self._user(conn, user_id))

    def list_users(self) -> list[dict]:
        with self._connect() as conn:
            return [_public(row) for row in conn.execute('SELECT * FROM users ORDER BY created_at, id')]

    def update_user(self, user_id: str, actor_id: str, *, enabled: bool | None = None,
                    max_concurrent: int | None = None, max_queued: int | None = None) -> dict:
        with self._connect(write=True) as conn:
            self._admin(conn, actor_id)
            user = self._user(conn, user_id)
            changes = {}
            if enabled is not None:
                if type(enabled) is not bool:
                    raise AccountError(422, '账号启用状态必须为布尔值。')
                if not enabled and user['role'] == 'admin' and user['enabled']:
                    others = conn.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND enabled=1 AND id<>?",
                                          (user_id,)).fetchone()[0]
                    if not others:
                        raise AccountError(409, '不能停用最后一个已启用的管理员账号。')
                changes['enabled'] = enabled
            if max_concurrent is not None:
                changes['max_concurrent'] = _quota(max_concurrent, 1, 'max_concurrent')
            if max_queued is not None:
                changes['max_queued'] = _quota(max_queued, 0, 'max_queued')
            if changes:
                # Column names come exclusively from the fixed mapping above.
                assignments = ','.join(f'{key}=?' for key in changes)
                conn.execute(f'UPDATE users SET {assignments} WHERE id=?', (*changes.values(), user_id))
                if enabled is False:
                    conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
                self._audit(conn, actor_id, 'user.update', user_id, changes)
            return _public(self._user(conn, user_id))

    def reset_password(self, user_id: str, password: str, actor_id: str) -> dict:
        with self._connect(write=True) as conn:
            self._admin(conn, actor_id)
            self._user(conn, user_id)
            encoded = _HASHER.hash(_password(password))
            conn.execute('UPDATE users SET password_hash=?,must_change_password=1 WHERE id=?', (encoded, user_id))
            conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            self._audit(conn, actor_id, 'user.reset_password', user_id)
            return _public(self._user(conn, user_id))

    def change_password(self, user_id: str, current: str, new: str) -> dict:
        with self._connect(write=True) as conn:
            user = self._user(conn, user_id)
            if (not user['enabled'] or not isinstance(current, str) or len(current) > 1024
                    or not _verify(user['password_hash'], current)):
                raise AccountError(401, '当前密码不正确，或账号不可用。')
            new = _password(new)
            if current == new:
                raise AccountError(422, '新密码不能与当前密码相同。')
            conn.execute('UPDATE users SET password_hash=?,must_change_password=0 WHERE id=?',
                         (_HASHER.hash(new), user_id))
            conn.execute('DELETE FROM sessions WHERE user_id=?', (user_id,))
            self._audit(conn, user_id, 'user.change_password', user_id)
            return _public(self._user(conn, user_id))

    def login(self, username: str, password: str, remote_ip: str) -> dict:
        username = _username(username)
        remote_ip = _text(remote_ip, 128) or 'unknown'
        now = time.time()
        result = None
        # Serialize checks and failure recording: parallel workers cannot bypass
        # either limit. The failure is raised AFTER committing the attempt.
        with self._connect(write=True) as conn:
            conn.execute('DELETE FROM login_attempts WHERE created_at<=?', (now - LOGIN_WINDOW_SECONDS,))
            conn.execute('DELETE FROM sessions WHERE expires_at<=?', (now,))
            names = conn.execute('SELECT COUNT(*) FROM login_attempts WHERE username=?', (username,)).fetchone()[0]
            ips = conn.execute('SELECT COUNT(*) FROM login_attempts WHERE remote_ip=?', (remote_ip,)).fetchone()[0]
            if names >= LOGIN_USERNAME_LIMIT or ips >= LOGIN_IP_LIMIT:
                raise AccountError(429, '登录尝试过于频繁，请在 15 分钟后重试。')
            user = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            encoded = user['password_hash'] if user else _DUMMY_HASH
            valid = isinstance(password, str) and len(password) <= 1024 and _verify(encoded, password)
            if user is None or not valid or not user['enabled']:
                conn.execute('INSERT INTO login_attempts(username,remote_ip,created_at) VALUES(?,?,?)',
                             (username, remote_ip, now))
                self._audit(conn, '', 'auth.login_failed', details={'username': username, 'remote_ip': remote_ip})
            else:
                if _HASHER.check_needs_rehash(encoded):
                    conn.execute('UPDATE users SET password_hash=? WHERE id=?', (_HASHER.hash(password), user['id']))
                token, csrf_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                conn.execute('''INSERT INTO sessions(token_hash,user_id,csrf_token,created_at,expires_at)
                                VALUES(?,?,?,?,?)''',
                             (hashlib.sha256(token.encode()).hexdigest(), user['id'], csrf_token, now, now + SESSION_SECONDS))
                self._audit(conn, user['id'], 'auth.login', user['id'], {'remote_ip': remote_ip})
                result = {'token': token, 'csrf_token': csrf_token, 'user': _public(user)}
        if result is None:
            raise AccountError(401, '用户名或密码不正确。')
        return result

    def authenticate(self, token: str) -> dict:
        if not isinstance(token, str) or not 32 <= len(token) <= 256:
            raise AccountError(401, '请先登录，或重新登录已过期的会话。')
        with self._connect() as conn:
            row = conn.execute('''SELECT users.*,sessions.csrf_token,sessions.expires_at
                                  FROM sessions JOIN users ON users.id=sessions.user_id
                                  WHERE token_hash=? AND expires_at>? AND users.enabled=1''',
                               (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
            if row is None:
                raise AccountError(401, '请先登录，或重新登录已过期的会话。')
            return {'user': _public(row), 'csrf_token': row['csrf_token'], 'expires_at': row['expires_at']}

    def logout(self, token: str) -> None:
        if not isinstance(token, str) or len(token) > 256:
            return
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect(write=True) as conn:
            row = conn.execute('SELECT user_id FROM sessions WHERE token_hash=?', (digest,)).fetchone()
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (digest,))
            if row:
                self._audit(conn, row['user_id'], 'auth.logout', row['user_id'])

    def audit(self, actor_id: str, action: str, target: str = '', details: dict | None = None) -> None:
        with self._connect(write=True) as conn:
            self._audit(conn, actor_id, action, target, details)

    def list_audit(self, limit: int = 100) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise AccountError(422, '审计记录条数需为 1–1000 之间的整数。')
        with self._connect() as conn:
            rows = conn.execute('SELECT * FROM audit ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
            return [{**dict(row), 'details': json.loads(row['details'])} for row in rows]
