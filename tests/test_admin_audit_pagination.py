"""Audit pagination must filter before counting and slicing."""
from app.accounts import Accounts


def test_audit_user_filter_counts_and_pages_before_slicing(tmp_path):
    accounts = Accounts(tmp_path)
    for index in range(23):
        accounts.audit('alice', 'user.update', 'alice', {'index': index})
        accounts.audit('bob', 'user.update', 'bob')
    first = accounts.audit_page(page=1, page_size=10, scope='all', user_id='alice')
    second = accounts.audit_page(page=2, page_size=10, scope='all', user_id='alice')
    last = accounts.audit_page(page=99, page_size=10, scope='all', user_id='alice')
    assert (first['total'], first['pages'], len(first['items'])) == (23, 3, 10)
    assert len(second['items']) == 10
    assert (last['page'], len(last['items'])) == (3, 3)
    assert not {r['id'] for r in first['items']} & {r['id'] for r in second['items']}
    assert all(r['actor_id'] == 'alice' for r in first['items'] + second['items'] + last['items'])


def test_audit_related_user_scope_and_empty_results(tmp_path):
    accounts = Accounts(tmp_path)
    accounts.audit('admin', 'user.update', 'alice')
    accounts.audit('admin', 'DELETE /api/production/runs/123', 'alice:123')
    accounts.audit('alice', 'auth.login', 'alice')
    accounts.audit('admin', 'user.update', 'alice-other')
    important = accounts.audit_page(scope='important', user_id='alice')
    assert important['total'] == 2
    assert accounts.audit_page(scope='all', user_id='alice')['total'] == 3
    empty = accounts.audit_page(page=5, user_id="' OR 1=1 --")
    assert (empty['total'], empty['page'], empty['pages'], empty['items']) == (0, 1, 1, [])


def test_audit_endpoint_filters_and_keeps_admin_guard(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app import authentication

    accounts = Accounts(tmp_path)
    admin = accounts.init_admin('admin', '123456')
    user = accounts.create_user('alice', '123456', admin['id'])
    for _ in range(15):
        accounts.audit(user['id'], 'user.change_password', user['id'])
    monkeypatch.setattr(authentication, 'auth_enabled', lambda: True)
    app = FastAPI()
    actor = {'user': admin}

    @app.middleware('http')
    async def identity(request, call_next):
        request.state.user = actor['user']
        return await call_next(request)

    app.include_router(authentication.get_router(lambda: SimpleNamespace(storage_dir=tmp_path), None))
    with TestClient(app) as client:
        result = client.get('/api/admin/audit', params={'user_id':user['id']}).json()
        assert result['page_size'] == 10 and len(result['items']) == 10
        assert all(row['actor_id'] == user['id'] or row['target'] == user['id'] for row in result['items'])
        assert client.get('/api/admin/audit?user_id=missing').status_code == 404
        assert client.get('/api/admin/audit?page=0').status_code == 422
        actor['user'] = user
        assert client.get('/api/admin/audit').status_code == 403
