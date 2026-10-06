"""Assistant is text-only, isolated from video submission and private settings."""
import json

import httpx
import pytest

from app import main, variation_settings
from app.production_store import ProductionStore
from tests.test_production_api import client
from tests.test_access_control import protected, accounts_clients


def mock_provider(monkeypatch, *, text='先以近景展示，再缓慢拉远至全身。', reason='stop', status=200):
    calls = []

    async def send(self, request, **kwargs):
        calls.append(json.loads(request.content))
        return httpx.Response(status, request=request, json={
            'choices': [{'finish_reason': reason, 'message': {'content': text}}]})

    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    return calls


@pytest.mark.parametrize('inspiration', ['', '  \n ', '先拍衣服，再拉远'])
def test_assist_works_without_assets_and_never_creates_video(client, monkeypatch, inspiration):
    variation_settings.save_config(main.settings, {
        'api_key': 'private-key', 'thinking_enabled': True,
        'max_completion_tokens': 32768, 'timeout_seconds': 300})
    calls = mock_provider(monkeypatch)
    response = client.post('/api/production/inspiration-assist', json={'inspiration': inspiration})
    assert response.status_code == 200, response.text
    assert response.json()['inspiration'] == '先以近景展示，再缓慢拉远至全身。'
    assert response.json()['request_id']
    assert len(calls) == 1
    body = calls[0]
    assert [m['role'] for m in body['messages']] == ['system', 'user']
    assert all(isinstance(m['content'], str) for m in body['messages'])
    assert body['max_tokens'] == 512 and body['thinking'] == {'type': 'disabled'}
    assert 'private-key' not in json.dumps(body) + response.text
    if inspiration.strip():
        assert inspiration.strip() in body['messages'][1]['content']
    assert not ProductionStore(main.settings.storage_dir).list_runs()
    original = variation_settings.load_config(main.settings)
    assert original.thinking_enabled and original.timeout_seconds == 300


@pytest.mark.parametrize('payload', [{}, {'inspiration': None}, {'inspiration': 'x' * 2001},
                                     {'inspiration': 'x\x00'}, {'inspiration': '', 'assets': []}])
def test_invalid_assist_input_rejected_before_llm(client, monkeypatch, payload):
    calls = mock_provider(monkeypatch)
    assert client.post('/api/production/inspiration-assist', json=payload).status_code == 422
    assert not calls


@pytest.mark.parametrize('text,reason,status', [('', 'stop', 200), ('x' * 501, 'stop', 200),
    ('```json\n{}\n```', 'stop', 200), ('部分回答', 'length', 200), ('private-provider-message', 'stop', 401)])
def test_invalid_outputs_never_return_as_inspiration(client, monkeypatch, text, reason, status):
    variation_settings.save_config(main.settings, {'api_key': 'secret-key'})
    calls = mock_provider(monkeypatch, text=text, reason=reason, status=status)
    response = client.post('/api/production/inspiration-assist', json={'inspiration': '保留原想法'})
    assert response.status_code == 502
    assert 'inspiration' not in response.json()
    assert 'private-provider-message' not in response.text
    assert len(calls) == 1


def test_unconfigured_assist_does_not_call_llm(client, monkeypatch):
    calls = mock_provider(monkeypatch)
    assert client.post('/api/production/inspiration-assist', json={'inspiration': ''}).status_code == 503
    assert not calls


def test_config_is_independent_and_private(accounts_clients, monkeypatch):
    _, _, (admin, alice, bob) = accounts_clients
    admin.put('/api/variation-settings', json={'api_key': 'old-planner-key', 'model': 'old-model'})
    updated = admin.put('/api/inspiration-settings', json={
        'inherit_provider': False, 'base_url': 'https://example.com/v1',
        'model': 'fast-text', 'api_key': 'assist-only-key', 'timeout_seconds': 12})
    assert updated.status_code == 200, updated.text
    assert 'assist-only-key' not in updated.text and 'old-planner-key' not in updated.text
    assert updated.json()['config']['has_api_key']
    assert admin.get('/api/variation-settings').json()['config']['model'] == 'old-model'
    for user in (alice, bob):
        assert user.get('/api/inspiration-settings').status_code == 403
        assert user.put('/api/inspiration-settings', json={'enabled': False}).status_code == 403
    calls = mock_provider(monkeypatch)
    result = alice.post('/api/production/inspiration-assist', json={'inspiration': ''})
    assert result.status_code == 200, result.text
    assert calls[0]['model'] == 'fast-text'
    changed = admin.put('/api/inspiration-settings', json={'base_url': 'https://other.example/v1'})
    assert changed.status_code == 200 and not changed.json()['config']['has_api_key']
    assert alice.post('/api/production/inspiration-assist', json={'inspiration': ''}).status_code == 503


def test_config_validates_types_and_budget(client):
    for values in ({'enabled': 'yes'}, {'inherit_provider': 1}, {'timeout_seconds': 0},
                   {'max_tokens': True}, {'max_tokens': 4000}, {'api_key': 'bad\nkey'}, {'unknown': 1}):
        assert client.put('/api/inspiration-settings', json=values).status_code == 422


def test_assist_requires_login_and_csrf(protected):
    response = protected.post('/api/production/inspiration-assist', json={'inspiration': ''})
    assert response.status_code in (401, 403)


def test_concurrent_requests_rejected_and_cancelled_request_releases_lease(client, monkeypatch):
    import asyncio
    from app.inspiration_assist import generate
    from fastapi import HTTPException
    variation_settings.save_config(main.settings, {'api_key': 'private-key'})

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def send(self, request, **kwargs):
            entered.set()
            await release.wait()
            return httpx.Response(200, request=request, json={
                'choices': [{'finish_reason': 'stop', 'message': {'content': '固定中景，自然展示。'}}]})

        monkeypatch.setattr(httpx.AsyncClient, 'send', send)
        first = asyncio.create_task(generate(main.settings, {'inspiration': ''}))
        done, _ = await asyncio.wait([first, asyncio.create_task(entered.wait())],
                                     timeout=5, return_when=asyncio.FIRST_COMPLETED)
        if first in done:
            await first
        assert entered.is_set(), 'provider request never started'
        try:
            with pytest.raises(HTTPException) as error:
                await generate(main.settings, {'inspiration': '第二次'})
            assert error.value.status_code == 429
        finally:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
        release.set()
        assert (await generate(main.settings, {'inspiration': ''}))['inspiration'] == '固定中景，自然展示。'

    asyncio.run(scenario())


def test_whole_request_deadline_and_transport_errors_are_sanitized(client, monkeypatch):
    import asyncio
    variation_settings.save_config(main.settings, {'api_key': 'private-key'})
    client.put('/api/inspiration-settings', json={'timeout_seconds': 5})

    async def delayed(self, request, **kwargs):
        await asyncio.sleep(10)
        raise AssertionError('deadline did not cancel')

    monkeypatch.setattr(httpx.AsyncClient, 'send', delayed)
    result = client.post('/api/production/inspiration-assist', json={'inspiration': ''})
    assert result.status_code == 504

    async def failed(self, request, **kwargs):
        raise httpx.ConnectError('private-key internal provider detail')

    monkeypatch.setattr(httpx.AsyncClient, 'send', failed)
    result = client.post('/api/production/inspiration-assist', json={'inspiration': ''})
    assert result.status_code == 502 and 'private-key' not in result.text


def test_independent_key_is_covered_by_global_redaction(client):
    from app.security import configured_secrets
    client.put('/api/inspiration-settings', json={'inherit_provider': False,
        'model': 'test', 'api_key': 'independent-secret'})
    assert 'independent-secret' in configured_secrets(main.settings)


def test_operational_admin_can_configure_assistant(accounts_clients):
    accounts, users, (_, operator, _) = accounts_clients
    accounts.update_user(users[1]['id'], users[0]['id'], role='admin')
    login = operator.post('/api/auth/login', headers={'Origin': 'http://testserver'},
                          json={'username': 'alice', 'password': 'changed-user-password'})
    assert login.status_code == 200
    operator.headers['X-CSRF-Token'] = login.json()['csrf_token']
    assert operator.get('/api/inspiration-settings').status_code == 200
    assert operator.put('/api/inspiration-settings', json={'model': 'quick-model'}).status_code == 200


def test_delegated_assist_uses_actor_permissions_and_requires_csrf(accounts_clients, monkeypatch):
    _, users, (admin, alice, bob) = accounts_clients
    admin.put('/api/variation-settings', json={'api_key': 'private-key'})
    calls = mock_provider(monkeypatch)
    path = '/api/admin/delegated/' + users[1]['id'] + '/production/inspiration-assist'
    response = admin.post(path, json={'inspiration': '近景'})
    assert response.status_code == 200, response.text
    assert len(calls) == 1
    assert bob.post(path, json={'inspiration': ''}).status_code == 403
    assert admin.post('/api/admin/delegated/' + 'f' * 32 + '/production/inspiration-assist',
                      json={'inspiration': ''}).status_code == 404
    assert alice.post('/api/production/inspiration-assist', json={'inspiration': ''},
                      headers={'X-CSRF-Token': ''}).status_code == 403
    assert len(calls) == 1


def test_site_limit_is_shared_across_actors_and_released(client):
    from contextlib import ExitStack
    from dataclasses import replace
    from fastapi import HTTPException
    from app.inspiration_assist import admission
    with ExitStack() as stack:
        for index in range(4):
            stack.enter_context(admission(replace(main.settings, user_id=str(index))))
        with pytest.raises(HTTPException) as error:
            with admission(replace(main.settings, user_id='fifth')):
                pytest.fail('site limit bypassed')
        assert error.value.status_code == 429
    with admission(replace(main.settings, user_id='fifth')):
        pass
