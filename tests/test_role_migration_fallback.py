"""Preserve an operational administrator when upgrading valid two-role databases."""
import sqlite3

import pytest

from app.accounts import Accounts


@pytest.mark.parametrize('owner_role,owner_enabled,expected', [
    ('admin', 0, 'admin-a'),
    ('user', 1, 'admin-a'),
    ('admin', 1, 'original'),
])
def test_role_upgrade_preserves_enabled_admin_when_original_owner_unavailable(owner_role,owner_enabled,expected):
    with sqlite3.connect(':memory:',isolation_level=None) as db:
        db.row_factory=sqlite3.Row
        db.execute("""CREATE TABLE users (
            id TEXT PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,
            role TEXT CHECK(role IN ('admin','user')),enabled INTEGER,
            must_change_password INTEGER,max_concurrent INTEGER,max_queued INTEGER,
            legacy_owner INTEGER,created_at REAL)""")
        db.executemany('INSERT INTO users VALUES (?,?,?,?,?,0,1,10,?,?)',[
            ('original','original','original-hash',owner_role,owner_enabled,1,10),
            ('admin-b','manager-b','b-hash','admin',1,0,1),
            ('admin-a','manager-a','a-hash','admin',1,0,1),
        ])
        Accounts._migrate_roles(db)
        promoted=db.execute("SELECT id FROM users WHERE role='super_admin' AND enabled=1").fetchall()
        assert [row['id'] for row in promoted]==[expected]
        owner=db.execute("SELECT * FROM users WHERE id='original'").fetchone()
        assert owner['enabled']==owner_enabled and owner['password_hash']=='original-hash'
        before=[tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')]
        Accounts._migrate_roles(db)
        assert [tuple(row) for row in db.execute('SELECT * FROM users ORDER BY id')]==before
