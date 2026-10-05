"""Private support records exercise real authentication, CSRF and SQLite."""
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.templating import Jinja2Templates

from app import access_control, authentication, request_timing, security
from app.accounts import Accounts
from app.config import settings as base_settings


@pytest.fixture
def support_app(tmp_path, monkeypatch):
    monkeypatch.setenv('APP_AUTH_ENABLED', 'true')
    settings = replace(base_settings, storage_dir=tmp_path, config_root=None,
                       seedance_api_key='fixture-private-key', max_upload_mb=7)
    app = FastAPI()
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1]/'app/templates'))
    access_control.install(app, lambda: settings)
    request_timing.install(app)
    security.install(app, lambda: settings)
    app.include_router(authentication.get_router(lambda: settings, templates))
    from app.support import get_router
    app.include_router(get_router(lambda: settings, templates))
    accounts = Accounts(tmp_path)
    admin = accounts.init_admin('admin', '123456')
    users = [admin, accounts.create_user('alice', '123456', admin['id']),
             accounts.create_user('bob', '123456', admin['id'])]
    clients = []
    for user in users:
        session = accounts.login(user['username'], '123456', '127.0.0.1')
        client = TestClient(app, base_url='https://testserver')
        client.cookies.set('ark_session', session['token'])
        client.headers['x-csrf-token'] = session['csrf_token']
        clients.append(client)
    yield settings, app, accounts, users, clients
    for client in clients:
        client.close()


def test_public_config_uses_configured_limits_without_private_configuration(support_app):
    _, app, _, _, _ = support_app
    with TestClient(app, base_url='https://testserver') as guest:
        result = guest.get('/api/support/config')
    assert result.status_code == 200
    value = result.json()
    assert '7 MB' in value['input_requirements']['source_video']['text']
    assert value['input_requirements']['source_video']['accept'].startswith('.')
    assert value['input_requirements']['prompt']['max_length'] == 10000
    assert '站内扣款' in value['billing_notice']
    assert 'fixture-private-key' not in result.text
    assert 'storage_dir' not in result.text


def test_feedback_owner_is_server_assigned_and_other_users_cannot_read_or_reply(support_app):
    settings, _, _, users, (admin, alice, bob) = support_app
    response = alice.post('/api/support/requests', json={'message':'生成失败，请协助检查', 'task_name':'任务一','request_id':'req-123'})
    assert response.status_code == 201
    item = response.json()['item']
    assert item['status'] == 'open' and item['reply'] == ''
    assert 'owner_id' not in item
    assert [r['id'] for r in alice.get('/api/support/requests').json()['items']] == [item['id']]
    assert bob.get('/api/support/requests').json()['items'] == []
    assert bob.get('/api/admin/support/requests').status_code == 403
    assert bob.patch('/api/admin/support/requests/'+item['id'],json={'status':'resolved','reply':'伪造回复'}).status_code == 403
    assert alice.post('/api/support/requests',json={'message':'fake','owner_id':users[2]['id']}).status_code == 422
    rows = admin.get('/api/admin/support/requests').json()['items']
    assert rows[0]['owner_id'] == users[1]['id'] and rows[0]['username'] == 'alice'
    assert (settings.storage_dir/'private'/'support.db').is_file()


def test_admin_reply_persists_and_only_owner_can_see_it(support_app):
    settings, _, _, _, (admin, alice, bob) = support_app
    ident = alice.post('/api/support/requests',json={'message':'需要帮助'}).json()['item']['id']
    response = admin.patch('/api/admin/support/requests/'+ident,json={'status':'resolved','reply':'已经处理，请继续查询原任务。'})
    assert response.status_code == 200
    from app.support import SupportStore
    reopened = SupportStore(settings.storage_dir)
    own = alice.get('/api/support/requests').json()['items'][0]
    assert own['reply'] == '已经处理，请继续查询原任务。' and own['status'] == 'resolved'
    assert reopened.path == settings.storage_dir/'private'/'support.db'
    assert bob.get('/api/support/requests').json()['items'] == []
    assert admin.get('/api/admin/support/requests?status=open').json()['total'] == 0


def test_anonymous_csrf_and_revoked_sessions_cannot_submit(support_app):
    _, app, accounts, users, (_, alice, _) = support_app
    with TestClient(app,base_url='https://testserver') as guest:
        assert guest.post('/api/support/requests',json={'message':'anonymous'}).status_code == 401
        assert guest.get('/api/support/requests').status_code == 401
    saved = alice.headers.pop('x-csrf-token')
    assert alice.post('/api/support/requests',json={'message':'without csrf'}).status_code == 403
    alice.headers['x-csrf-token'] = saved
    assert alice.post('/api/support/requests',json={'message':'wrong origin'},headers={'Origin':'https://evil.test'}).status_code == 403
    accounts.update_user(users[1]['id'],users[0]['id'],enabled=False)
    assert alice.post('/api/support/requests',json={'message':'disabled'}).status_code == 401


@pytest.mark.parametrize('payload',[{'message':'   '},{'message':'x'*4001},{'message':'ok','request_id':'https://x/?token=bad'},{'message':'ok','task_name':'x'*161}])
def test_invalid_feedback_never_echoes_or_persists_raw_input(support_app,payload):
    *_, (_,alice,_) = support_app
    response = alice.post('/api/support/requests',json=payload)
    assert response.status_code == 422
    assert payload['message'] not in response.text
    assert alice.get('/api/support/requests').json()['total'] == 0


def test_credentials_are_redacted_before_storage_and_admin_replies(support_app):
    settings, _, _, _, (admin,alice,_) = support_app
    message = '故障 Bearer abc-secret; api_key=fixture-private-key; ark_session=secret-session; 链接 https://example.test/result?token=hidden'
    result = alice.post('/api/support/requests',json={'message':message,'task_name':'<script>alert(1)</script>'})
    assert result.status_code == 201
    ident = result.json()['item']['id']
    admin.patch('/api/admin/support/requests/'+ident,json={'status':'open','reply':'password=reply-secret'})
    with sqlite3.connect(settings.storage_dir/'private'/'support.db') as db:
        serialized = json.dumps(db.execute('SELECT message,reply FROM support_requests').fetchall())
    for secret in ('abc-secret','fixture-private-key','secret-session','hidden','reply-secret'):
        assert secret not in serialized
    assert '<script>' in result.json()['item']['task_name']  # JSON data, never executed HTML.


def test_pagination_and_open_record_bound_are_atomic(support_app,monkeypatch):
    *_, (admin,alice,_) = support_app
    from app import support
    monkeypatch.setattr(support,'MAX_OPEN_PER_OWNER',3)
    for n in range(3): assert alice.post('/api/support/requests',json={'message':f'problem {n}'}).status_code == 201
    assert alice.post('/api/support/requests',json={'message':'over limit'}).status_code == 429
    first = alice.get('/api/support/requests?page_size=2').json()
    second = alice.get('/api/support/requests?page_size=2&page=2').json()
    assert first['total']==3 and first['pages']==2 and len(second['items'])==1
    assert not ({x['id'] for x in first['items']} & {x['id'] for x in second['items']})
    admin.patch('/api/admin/support/requests/'+first['items'][0]['id'],json={'status':'resolved','reply':''})
    assert alice.post('/api/support/requests',json={'message':'after handling'}).status_code == 201


def test_auth_disabled_only_allows_same_origin_loopback_support(support_app,monkeypatch):
    _, app, _, _, _ = support_app
    monkeypatch.setenv('APP_AUTH_ENABLED','false')
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',50000)) as local:
        assert local.post('/api/support/requests',json={'message':'local help'}).status_code == 201
        assert local.post('/api/support/requests',json={'message':'forged'},headers={'Origin':'https://evil.test'}).status_code == 403
        assert local.post('/api/support/requests',json={'message':'proxy'},headers={'X-Forwarded-For':'203.0.113.5'}).status_code == 403
    with TestClient(app,base_url='http://service.test',client=('203.0.113.5',50000)) as public:
        assert public.post('/api/support/requests',json={'message':'public'}).status_code == 403
        assert public.get('/api/support/requests').status_code == 403


def test_error_request_id_is_shared_by_header_body_and_log(support_app,caplog):
    _, app, _, _, (_,alice,_) = support_app
    @app.get('/api/support/explode')
    def explode(): raise RuntimeError('fixture-private-key')
    # Only this diagnostic test route is exposed to an authenticated user.
    original = access_control.ordinary_allowed
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(access_control,'ordinary_allowed',lambda path,method:path=='/api/support/explode' or original(path,method))
        response = alice.get('/api/support/explode',headers={'X-Request-ID':'attacker-id'})
    assert response.status_code == 500
    ident = response.json()['request_id']
    assert response.headers['X-Request-ID'] == ident and ident != 'attacker-id'
    assert ident in caplog.text and 'fixture-private-key' not in caplog.text


def test_concurrent_submissions_cannot_overfill_the_last_open_slot(support_app,monkeypatch):
    *_, (_,alice,_) = support_app
    from app import support
    monkeypatch.setattr(support,'MAX_OPEN_PER_OWNER',1)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda n:alice.post('/api/support/requests',json={'message':f'parallel {n}'}).status_code,range(2)))
    assert sorted(results) == [201,429]
    assert alice.get('/api/support/requests').json()['total'] == 1


def test_total_bound_does_not_silently_delete_resolved_records(support_app,monkeypatch):
    *_, (admin,alice,bob) = support_app
    from app import support
    monkeypatch.setattr(support,'MAX_RECORDS',2)
    ident = alice.post('/api/support/requests',json={'message':'keep history'}).json()['item']['id']
    admin.patch('/api/admin/support/requests/'+ident,json={'status':'resolved','reply':'done'})
    assert bob.post('/api/support/requests',json={'message':'last slot'}).status_code == 201
    assert bob.post('/api/support/requests',json={'message':'full'}).status_code == 429
    rows = admin.get('/api/admin/support/requests').json()
    assert rows['total'] == 2 and any(r['id']==ident and r['reply']=='done' for r in rows['items'])


def test_oversized_chunked_body_is_rejected_before_it_becomes_a_record(support_app):
    *_, (_,alice,_) = support_app
    response=alice.post('/api/support/requests',content=iter([b'{"message":"',b'x'*40000,b'"}']),headers={'content-type':'application/json'})
    assert response.status_code == 413
    assert alice.get('/api/support/requests').json()['total'] == 0


def test_support_history_and_reply_are_in_the_complete_backup(support_app,monkeypatch):
    settings, _, _, _, (admin,alice,_) = support_app
    ident=alice.post('/api/support/requests',json={'message':'backup feedback'}).json()['item']['id']
    admin.patch('/api/admin/support/requests/'+ident,json={'status':'resolved','reply':'saved answer'})
    from app.backup import create_snapshot,restore_snapshot
    for name in ('WORKFLOW_DB','WORKFLOW_STORAGE'): monkeypatch.delenv(name,raising=False)
    root=settings.storage_dir
    snapshot=root.parent/(root.name+'-snapshot')
    destination=root.parent/(root.name+'-restored')
    manifest=create_snapshot(root,snapshot,database_url='sqlite:///'+(root/'private'/'support.db').as_posix(),quiesced=True)
    assert any(item['path']=='private/support.db' for item in manifest['databases'])
    restore_snapshot(snapshot,destination)
    with sqlite3.connect(destination/'private'/'support.db') as db:
        assert db.execute('SELECT message,status,reply FROM support_requests WHERE id=?',(ident,)).fetchone()==('backup feedback','resolved','saved answer')


def test_secret_value_cannot_be_stored_as_a_request_identifier(support_app):
    *_, (_,alice,_) = support_app
    response=alice.post('/api/support/requests',json={'message':'problem','request_id':'fixture-private-key'})
    assert response.status_code == 422 and 'fixture-private-key' not in response.text
    assert alice.get('/api/support/requests').json()['total'] == 0
