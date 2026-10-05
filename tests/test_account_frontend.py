"""Account UI contracts and isolated browser regression tests (no live server)."""
import json
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest
from jinja2 import Environment, FileSystemLoader
from tests.browser_template_fixture import serve_editor_rules, EDITOR_RULES_PATH


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'app/templates'
STATIC = ROOT / 'app/static'
ACCOUNT = {'auth_enabled': True, 'user': {'id': 'alice-id', 'username': '小舟', 'role': 'user'}, 'csrf_token': 'test-csrf'}


@pytest.mark.parametrize('template', ['production.html', 'admin_settings.html', 'login.html', 'password.html', 'users.html'])
def test_account_script_is_first_and_synchronous(template):
    text = (TEMPLATES / template).read_text(encoding='utf-8')
    scripts = re.findall(r'<script\b[^>]*>', text)
    assert '/static/account.js?' in scripts[0]
    assert not re.search(r'\b(async|defer|type)\s*(=|>)', scripts[0])
    assert '/static/account.css?' in text


def test_account_scripts_do_not_render_html_or_persist_secrets():
    scripts = '\n'.join((STATIC / name).read_text(encoding='utf-8') for name in ('account.js', 'login.js', 'password.js', 'users.js'))
    assert 'innerHTML' not in scripts
    assert 'insertAdjacentHTML' not in scripts
    assert 'localStorage.clear' not in scripts
    assert 'sessionStorage' not in scripts
    assert 'document.cookie' not in scripts
    assert scripts.count('localStorage.setItem(') == 2
    assert 'localStorage.setItem(logoutKey(),' in scripts


def test_existing_application_scripts_remain_in_order():
    text = (TEMPLATES / 'production.html').read_text(encoding='utf-8')
    scripts = re.findall(r'<script src="/static/([^?]+)', text)
    assert scripts == ['account.js', 'delegated-editor.js', 'mira/mira-materials.js', 'production-shell.js', 'portrait-photos.js', 'portrait-people.js', 'video-comparison.js', 'production-runs.js', 'production.js', 'generation-options.js', 'link-templates.js']


@pytest.fixture(scope='module')
def browser():
    playwright = pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as pw:
        # Reuse a browser installed on the host; no downloads or live services.
        try:
            instance = pw.chromium.launch(headless=True)
        except playwright.Error:
            try:
                instance = pw.chromium.launch(channel='msedge', headless=True)
            except playwright.Error:
                pytest.skip('Browser unavailable; install Chromium or Edge to run UI checks')
        yield instance
        instance.close()


@pytest.fixture
def studio(browser):
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=True)
    state = {'account': json.loads(json.dumps(ACCOUNT)), 'calls': [], 'replies': {}, 'held': [], 'hold_me': False, 'headers': {}}
    env.globals['prompt_editor_visible'] = lambda request: not state['account']['auth_enabled'] or (state['account'].get('user') or {}).get('role') in {'admin','super_admin'}
    template_routes = {'/': 'production.html', '/login': 'login.html', '/account/password': 'password.html', '/admin/users': 'users.html', '/admin/settings': 'admin_settings.html'}

    def handle(route):
        request = route.request
        path = urlparse(request.url).path
        if path.startswith('/static/'):
            file = STATIC / path.removeprefix('/static/')
            route.fulfill(status=200, content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream', body=file.read_bytes())
        elif path == EDITOR_RULES_PATH:
            serve_editor_rules(route, ROOT, state['account'])
        elif path.startswith('/api/'):
            state['calls'].append({'path': path, 'method': request.method, 'url': request.url, 'headers': request.headers, 'body': request.post_data})
            if path == '/api/auth/me' and state['hold_me']:
                state['held'].append(route)
                return
            if path == '/api/admin/tasks' and state.get('hold_tasks'):
                state.setdefault('held_tasks', []).append(route)
                return
            default = state['account'] if path == '/api/auth/me' else {'items': [], 'stats': {}}
            status, data = state['replies'].get((request.method, path), (200, default))
            response_headers = {'Access-Control-Allow-Origin': '*', **state['headers'].get(path, {})}
            route.fulfill(status=status, content_type='application/json', body=json.dumps(data), headers=response_headers)
        else:
            template = template_routes.get(path)
            text = env.get_template(template).render() if template else '<p>Destination</p>'
            if path in ('/', '/admin/settings'):
                # Isolate account integration from existing media/network workflows.
                text = re.sub(r'<script src="/static/(?!account\.js)[^>]+></script>', '', text)
            route.fulfill(status=200, content_type='text/html', body=text)

    context.route('**/*', handle)
    yield page, state, context
    context.close()


def open_page(page, path='/'):
    page.goto('http://studio.test' + path)
    page.evaluate('window.accountReady.catch(() => null)')


def test_first_write_waits_for_identity_and_preserves_request_headers(studio):
    page, state, _ = studio
    state['hold_me'] = True
    page.goto('http://studio.test/', wait_until='domcontentloaded')
    page.evaluate("""() => { window.writeResult = fetch(new Request(location.origin + '/api/example', {
        method:'PATCH', headers:{'Content-Type':'application/json', 'X-Keep':'yes'}, body:'{"value":1}'
    })).then(response => response.json()); }""")
    assert [call['path'] for call in state['calls']] == ['/api/auth/me']
    assert len(state['held']) == 1
    state['held'][0].fulfill(status=200, content_type='application/json', body=json.dumps(state['account']))
    page.evaluate('window.writeResult')
    call = state['calls'][-1]
    assert call['method'] == 'PATCH'
    assert call['headers']['x-csrf-token'] == 'test-csrf'
    assert call['headers']['x-keep'] == 'yes'
    assert json.loads(call['body']) == {'value': 1}
    assert len([call for call in state['calls'] if call['path'] == '/api/auth/me']) == 1
    assert page.evaluate('window.currentAccount.id') == 'alice-id'


def test_normal_account_chrome_and_auth_disabled_compatibility(studio):
    page, state, _ = studio
    open_page(page)
    assert page.locator('[data-account-name]').inner_text() == '小舟'
    assert page.locator('.workspace-nav a[href="/people"]').is_visible()
    assert not page.locator('a[href="/admin/settings"]').is_visible()
    assert not page.locator('a[href="/admin/users"]').is_visible()
    state['account'] = {'auth_enabled': False, 'user': None, 'csrf_token': ''}
    open_page(page)
    page.evaluate("fetch('/api/example', {method:'POST', body:'legacy'}).then(r => r.json())")
    assert 'x-csrf-token' not in state['calls'][-1]['headers']
    assert page.locator('a[href="/admin/settings"]').is_visible()
    assert not page.locator('[data-account-logout]').is_visible()
    assert page.evaluate('window.currentAccount') is None


def test_cross_origin_and_non_api_requests_do_not_receive_csrf(studio):
    page, state, _ = studio
    open_page(page)
    page.evaluate("fetch('https://external.test/api/example', {method:'POST', body:'external'}).then(r => r.json())")
    assert 'x-csrf-token' not in state['calls'][-1]['headers']
    page.evaluate("fetch('/health', {method:'POST'}).then(r => r.text())")
    assert page.url == 'http://studio.test/'


@pytest.mark.parametrize('status,data,path', [(401, {}, '/login')])
def test_auth_failures_redirect_and_reject_response(studio, status, data, path):
    page, state, context = studio
    open_page(page)
    state['replies'][('POST', '/api/failure')] = (status, data)
    # Observe rejection before navigation destroys the original execution context.
    rejected = []
    page.expose_function('recordRejection', lambda name: rejected.append(name))
    if status == 401:
        state['account']['user'] = None
    else:
        state['account']['user']['must_change_password'] = True
    page.evaluate("() => { fetch('/api/failure', {method:'POST'}).then(() => recordRejection('resolved'), error => recordRejection(error.name)); }")
    page.wait_for_url('**' + path)
    assert rejected == ['AccountSessionError']


def test_logout_clears_only_known_caches_and_notifies_same_account_tabs(studio):
    page, state, context = studio
    open_page(page)
    other = context.new_page()
    open_page(other)
    page.evaluate("""() => {
      localStorage.setItem('production-current-draft-v1', 'legacy');
      localStorage.setItem('production-current-draft-v1:alice-id', 'alice');
      localStorage.setItem('production-pending-submit-v1:alice-id', 'pending');
      localStorage.setItem('production-current-draft-v1:bob-id', 'bob');
      localStorage.setItem('unrelated-setting', 'keep');
    }""")
    state['account']['user'] = None
    page.locator('.workspace-account summary').click()
    page.locator('[data-account-logout]').click()
    page.wait_for_url('**/login')
    other.wait_for_url('**/login')
    stored = page.evaluate('({...localStorage})')
    assert 'production-current-draft-v1' not in stored
    assert 'production-current-draft-v1:alice-id' not in stored
    assert 'production-pending-submit-v1:alice-id' not in stored
    assert stored['production-current-draft-v1:bob-id'] == 'bob'
    assert stored['unrelated-setting'] == 'keep'
    assert 'ark-account-logout:alice-id' in stored
    logout = next(call for call in state['calls'] if call['path'] == '/api/auth/logout')
    assert logout['method'] == 'POST' and logout['headers']['x-csrf-token'] == 'test-csrf'


def test_other_account_logout_event_does_not_redirect(studio):
    page, _, _ = studio
    open_page(page)
    page.evaluate("dispatchEvent(new StorageEvent('storage', {key:'ark-account-logout:bob-id', newValue:'1'}))")
    assert page.url.endswith('/')
    assert page.evaluate('window.currentAccount.id') == 'alice-id'


def test_login_error_then_direct_workspace_even_with_legacy_password_flag(studio):
    page, state, _ = studio
    state['account']['user'] = None
    state['replies'][('GET', '/api/auth/me')] = (401, {})
    state['replies'][('POST', '/api/auth/login')] = (401, {'detail': 'invalid credentials'})
    open_page(page, '/login')
    page.locator('#open-login').click()
    page.locator('#login-username').fill('alice')
    page.locator('#login-password').fill('wrong-password')
    page.locator('#login-submit').click()
    page.wait_for_function("!document.getElementById('login-error').hidden")
    assert '用户名或密码不正确' in page.locator('#login-error').inner_text()
    assert page.locator('#login-password').input_value() == ''
    state['account']['user'] = {**ACCOUNT['user'], 'must_change_password': True}
    state['replies'].pop(('GET', '/api/auth/me'))
    state['replies'][('POST', '/api/auth/login')] = (200, {'user': state['account']['user'], 'csrf_token': 'new-token'})
    page.locator('#login-password').fill('123456')
    page.locator('#login-submit').click()
    page.wait_for_url('http://studio.test/', timeout=5000)
    assert page.evaluate('window.currentAccount.id') == 'alice-id'
    stored = page.evaluate('({...localStorage})')
    assert set(stored) == {'ark-account-identity'}
    assert json.loads(stored['ark-account-identity'])['user_id'] == 'alice-id'
    assert '123456' not in str(stored) and 'new-token' not in str(stored)


def test_response_account_mismatch_rejects_payload_and_reloads_identity(studio):
    page, state, _ = studio
    open_page(page)
    state['headers']['/api/example'] = {'X-Account-ID': 'bob-id'}
    state['account']['user'] = {'id': 'bob-id', 'username': '新账号', 'role': 'user'}
    rejected = []
    page.expose_function('recordRejection', lambda name: rejected.append(name))
    page.evaluate("() => { fetch('/api/example').then(() => recordRejection('resolved'), error => recordRejection(error.name)); }")
    page.wait_for_function("window.currentAccount?.id === 'bob-id'")
    assert rejected == ['AccountSessionError']
    assert page.locator('[data-account-name]').inner_text() == '新账号'


def test_matching_account_header_returns_response(studio):
    page, state, _ = studio
    open_page(page)
    state['headers']['/api/example'] = {'X-Account-ID': 'alice-id'}
    assert page.evaluate("fetch('/api/example').then(response => response.ok)") is True


def test_login_broadcast_reloads_existing_account_tab(studio):
    page, state, context = studio
    open_page(page)
    old_page = page
    login = context.new_page()
    state['account']['user'] = None
    open_page(login, '/login')
    new_user = {'id': 'bob-id', 'username': '新账号', 'role': 'user'}
    state['replies'][('POST', '/api/auth/login')] = (200, {'user': new_user, 'csrf_token': 'new-token'})
    state['account']['user'] = new_user
    login.locator('#open-login').click()
    login.locator('#login-username').fill('bob')
    login.locator('#login-password').fill('new-password')
    login.locator('#login-submit').click()
    login.wait_for_url('http://studio.test/')
    old_page.wait_for_function("window.currentAccount?.id === 'bob-id'")
    assert old_page.locator('[data-account-name]').inner_text() == '新账号'
    login_call = next(call for call in state['calls'] if call['path'] == '/api/auth/login')
    assert login_call['headers']['origin'] == 'http://studio.test'


def test_tikhub_settings_and_nested_admin_links_are_hidden_until_admin_identity(studio):
    page, state, _ = studio
    state['hold_me'] = True
    page.goto('http://studio.test/', wait_until='domcontentloaded')
    page.evaluate("document.getElementById('video-url-entry').hidden = false")
    assert not page.locator('#link-settings').is_visible()
    assert page.locator('a[href^="/admin/"]:visible').count() == 0
    state['held'][0].fulfill(status=200, content_type='application/json', body=json.dumps(state['account']))
    page.evaluate('window.accountReady')
    assert not page.locator('#link-settings').is_visible()
    assert page.locator('.person-menu-actions a[href="/people"]').count() == 2
    state['hold_me'] = False
    state['account']['user']['role'] = 'admin'
    open_page(page)
    page.evaluate("document.getElementById('video-url-entry').hidden = false")
    assert page.locator('#link-settings').is_visible()


def test_password_confirmation_and_reauthentication(studio):
    page, state, _ = studio
    state['account']['user']['must_change_password'] = True
    state['replies'][('POST', '/api/auth/password')] = (200, {'needs_login': True})
    open_page(page, '/account/password')
    page.locator('#current-password').fill('old-password')
    page.locator('#new-password').fill('654321')
    assert page.locator('#new-password').evaluate('(node) => node.checkValidity()')
    page.locator('#confirm-password').fill('different-password')
    page.locator('#password-form button').click()
    assert '不一致' in page.locator('#password-error').inner_text()
    assert not any(call['path'] == '/api/auth/password' for call in state['calls'])
    state['account']['user'] = None
    page.locator('#confirm-password').fill('654321')
    page.locator('#password-form button').click()
    page.wait_for_url('**/login')
    call = next(call for call in state['calls'] if call['path'] == '/api/auth/password')
    assert json.loads(call['body']) == {'current_password': 'old-password', 'new_password': '654321'}
    assert call['headers']['x-csrf-token'] == 'test-csrf'


def test_admin_users_mutations_filters_stats_and_safe_rendering(studio):
    page, state, _ = studio
    state['account']['user']['role'] = 'super_admin'
    username = '<img src=x onerror=alert(1)>'
    state['replies'][('GET', '/api/admin/users')] = (200, {'items': [{'id': 'internal-id', 'username': username, 'role': 'user', 'enabled': True, 'max_concurrent': 2, 'max_queued': 5}]})
    state['replies'][('GET', '/api/admin/tasks')] = (200, {'items': [{'name': '<script>bad()</script>', 'username': username, 'status': 'succeeded', 'created_at': '2026-09-28T08:00:00Z', 'duration': 8, 'storage_bytes': 2048}], 'stats': {'submitted': 3, 'succeeded': 2, 'failed': 1, 'generated_seconds': 16, 'storage_bytes': 2048}})
    state['replies'][('GET', '/api/admin/audit')] = (200, {'items': [{'actor_username': username, 'action': 'user.create', 'created_at': '2026-09-28T08:00:00Z', 'password': 'must-not-render'}]})
    open_page(page, '/admin/users')
    page.wait_for_function("document.querySelector('[data-stat=submitted]').textContent === '3'")
    assert page.locator('#users-list tr').count() == 1
    assert username in page.locator('#users-list').inner_text()
    assert page.locator('#users-list img').count() == 0
    assert page.locator('#admin-tasks-list script').count() == 0
    assert 'internal-id' not in page.locator('body').inner_text()
    assert 'must-not-render' not in page.locator('body').inner_text()
    assert page.locator('[data-stat=storage_bytes]').inner_text() == '2 KB'
    page.locator('#create-username').fill('new-user')
    page.locator('#create-password').fill('123456')
    assert page.locator('#create-password').evaluate('(node) => node.checkValidity()')
    page.locator('#create-user-form button').click()
    page.wait_for_function("document.getElementById('create-user-status').dataset.state === 'success'")
    create = next(call for call in state['calls'] if call['path'] == '/api/admin/users' and call['method'] == 'POST')
    assert json.loads(create['body']) == {'username': 'new-user', 'password': '123456', 'role': 'user', 'max_concurrent': 1, 'max_queued': 10}
    assert page.locator('#create-password').input_value() == ''
    page.locator('#users-list input[type=checkbox]').uncheck()
    page.locator('#users-list select').select_option('admin')
    page.locator('#users-list .quota-input').first.fill('3')
    page.locator('#users-list button').first.click()
    page.wait_for_function("document.getElementById('users-status').dataset.state === 'success'")
    patch = next(call for call in state['calls'] if call['method'] == 'PATCH')
    assert patch['path'] == '/api/admin/users/internal-id'
    assert json.loads(patch['body']) == {'enabled': False, 'role': 'admin', 'max_concurrent': 3, 'max_queued': 5}
    page.locator('#users-list input[type=password]').fill('654321')
    assert page.locator('#users-list input[type=password]').evaluate('(node) => node.checkValidity()')
    page.locator('#users-list .row-actions button').click()
    page.wait_for_function("document.querySelector('#users-list input[type=password]').value === ''")
    reset = next(call for call in state['calls'] if call['path'].endswith('/reset-password'))
    assert json.loads(reset['body']) == {'password': '654321'}
    page.locator('#task-user').select_option('internal-id')
    page.locator('#task-status').select_option('failed')
    page.locator('#task-filter button').click()
    page.wait_for_function("!document.querySelector('#task-filter button').disabled")
    task = [call for call in state['calls'] if call['path'] == '/api/admin/tasks'][-1]
    assert parse_qs(urlparse(task['url']).query) == {'user_id': ['internal-id'], 'status': ['failed'], 'page': ['1'], 'page_size': ['10']}


def test_normal_user_does_not_fetch_admin_data(studio):
    page, state, _ = studio
    open_page(page, '/admin/users')
    assert not page.locator('#users-workspace').is_visible()
    assert page.locator('#users-access-error').is_visible()
    assert not any(call['path'].startswith('/api/admin/') for call in state['calls'])


def test_admin_can_delete_user_but_cannot_promote_or_restore(studio):
    page, state, _ = studio
    state['account']['user']['role'] = 'admin'
    state['replies'][('GET', '/api/admin/users')] = (200, {'items': [
        {'id':'person', 'username':'ordinary', 'role':'user', 'enabled':True},
        {'id':'deleted', 'username':'deleted', 'role':'user', 'enabled':False, 'deleted_at':123},
    ]})
    open_page(page, '/admin/users')
    page.wait_for_selector('#users-list tr')
    assert page.locator('#create-role option').all_text_contents() == ['普通用户']
    assert page.locator('#users-list select').first.is_disabled()
    assert page.get_by_role('button', name='恢复账号').is_disabled()
    assert '管理员' in page.locator('#users-authority').inner_text()
    page.on('dialog', lambda dialog: dialog.accept())
    page.get_by_role('button', name='删除账号').click()
    page.wait_for_function("document.querySelector('#users-status').dataset.state === 'success'")
    assert any(call['method']=='DELETE' and call['path']=='/api/admin/users/person' for call in state['calls'])


def test_super_can_restore_and_new_username_length_is_enforced(studio):
    page, state, _ = studio
    state['account']['user']['role'] = 'super_admin'
    state['replies'][('GET', '/api/admin/users')] = (200, {'items': [
        {'id':'deleted', 'username':'deleted', 'role':'admin', 'enabled':False, 'deleted_at':123},
    ]})
    open_page(page, '/admin/users')
    page.wait_for_selector('#users-list tr')
    assert page.locator('#create-role option').all_text_contents() == ['普通用户','管理员','超级管理员']
    assert page.locator('#users-list input[type=password]').is_disabled()
    page.get_by_role('button', name='恢复账号').click()
    page.wait_for_function("document.querySelector('#users-status').dataset.state === 'success'")
    assert any(call['method']=='POST' and call['path']=='/api/admin/users/deleted/restore' for call in state['calls'])
    page.locator('#create-username').fill('x')
    page.locator('#create-password').fill('123456')
    page.locator('#create-user-form button').click()
    assert not any(call['method']=='POST' and call['path']=='/api/admin/users' for call in state['calls'])


def test_voluntary_password_page_always_allows_return_and_six_digits(studio):
    page, state, _ = studio
    state['account']['user']['must_change_password'] = True
    open_page(page, '/account/password')
    assert page.locator('#password-back').is_visible()
    assert '首次登录' not in page.locator('#password-intro').inner_text()
    page.locator('#new-password').fill('12345')
    assert not page.locator('#new-password').evaluate('(node) => node.checkValidity()')
    page.locator('#new-password').fill('123456')
    assert page.locator('#new-password').evaluate('(node) => node.checkValidity()')
    page.locator('#password-back').click()
    page.wait_for_url('http://studio.test/', timeout=5000)


@pytest.mark.parametrize('role', ['user', 'admin', 'super_admin'])
def test_create_role_choice_and_numeric_password(studio, role):
    page, state, _ = studio
    state['account']['user']['role'] = 'super_admin'
    open_page(page, '/admin/users')
    assert page.locator('#create-role').count() == 1
    assert page.locator('#create-role').input_value() == 'user'
    page.locator('#create-role').select_option(role)
    page.locator('#create-username').fill('six-digit-user')
    page.locator('#create-password').fill('12345')
    assert not page.locator('#create-password').evaluate('(node) => node.checkValidity()')
    page.locator('#create-password').fill('123456')
    assert page.locator('#create-password').evaluate('(node) => node.checkValidity()')
    page.locator('#create-user-form button').click()
    page.wait_for_function("document.getElementById('create-user-status').dataset.state === 'success'")
    call = next(call for call in state['calls'] if call['path'] == '/api/admin/users' and call['method'] == 'POST')
    assert json.loads(call['body'])['role'] == role
    assert json.loads(call['body'])['password'] == '123456'
    assert page.locator('#create-role').input_value() == 'user'


@pytest.mark.parametrize('status', [200, 401])
def test_role_change_revoked_session_exits_admin_ui(studio, status):
    page, state, _ = studio
    state['account']['user']['role'] = 'super_admin'
    state['replies'][('GET', '/api/admin/users')] = (200, {'items': [dict(state['account']['user'])]})
    open_page(page, '/admin/users')
    assert page.locator('#users-list select').count() == 1
    page.locator('#users-list select').select_option('user')
    state['account']['user'] = None
    state['replies'][('PATCH', '/api/admin/users/alice-id')] = (status, {'detail': '请先登录。'} if status == 401 else {'user': {**ACCOUNT['user'], 'role': 'user'}})
    page.locator('#users-list button').first.click()
    page.wait_for_url('**/login', timeout=5000)
    assert page.locator('#open-login').is_visible()
    page.locator('#open-login').click()
    assert page.locator('#login-form').is_visible()


def test_legacy_forced_password_403_does_not_navigate(studio):
    page, state, _ = studio
    open_page(page)
    state['replies'][('POST', '/api/example')] = (403, {'detail': 'password_change_required'})
    result = page.evaluate("fetch('/api/example', {method:'POST'}).then(async r => ({status:r.status, data:await r.json()}))")
    assert result == {'status': 403, 'data': {'detail': 'password_change_required'}}
    assert page.url == 'http://studio.test/'


def test_admin_pagination_and_backend_audit_timestamps(studio):
    page, state, _ = studio
    state['account']['user']['role'] = 'admin'
    state['replies'][('GET', '/api/admin/users')] = (200, {'items': [state['account']['user']]})
    state['replies'][('GET', '/api/admin/tasks')] = (200, {'items': [], 'total': 120, 'page_size': 10, 'stats': {'duration_unknown': 2}})
    state['replies'][('GET', '/api/admin/audit')] = (200, {'items': [{'actor_id': 'alice-id', 'action': 'user.update', 'target': 'alice-id', 'created_at': 1790582400, 'details': {}}]})
    open_page(page, '/admin/users')
    page.wait_for_function("!document.getElementById('tasks-next').disabled")
    assert '共 120 条' in page.locator('#tasks-page').inner_text()
    assert '2 条' in page.locator('#tasks-duration-note').inner_text()
    assert '2026' in page.locator('#audit-list').inner_text()
    assert '修改账号设置' in page.locator('#audit-list').inner_text()
    assert page.locator('#audit-list td').nth(3).inner_text() == '小舟'
    # Filters apply immediately, reset to page one, and survive pagination.
    page.locator('#tasks-next').click()
    page.wait_for_function("document.querySelector('#tasks-page').textContent.includes('第 2 / 12')")
    page.locator('#task-status').select_option('failed')
    page.wait_for_function("!document.getElementById('tasks-next').disabled && document.querySelector('#tasks-page').textContent.includes('第 1 / 12')")
    query = parse_qs(urlparse(task_calls(state)[-1]['url']).query)
    assert query == {'status': ['failed'], 'page': ['1'], 'page_size': ['10']}
    page.locator('#tasks-next').click()
    page.wait_for_function("document.querySelector('#tasks-page').textContent.includes('第 2 / 12')")
    task = [call for call in state['calls'] if call['path'] == '/api/admin/tasks'][-1]
    query = parse_qs(urlparse(task['url']).query)
    assert query == {'status': ['failed'], 'page': ['2'], 'page_size': ['10']}


def task_poll_reply(status='running', seconds=10, name='轮询任务'):
    return {'items': [{'name': name, 'status': status, 'timing': {
        'available': True, 'is_live': status in ('queued', 'running'),
        'total_seconds': seconds, 'queue_seconds': 5, 'execution_seconds': seconds - 5,
    }}], 'stats': {}, 'total': 120, 'page_size': 10}


def task_calls(state):
    return [call for call in state['calls'] if call['path'] == '/api/admin/tasks']


def open_polling_admin(page, state, status='running'):
    page.clock.install()
    state['account']['user']['role'] = 'admin'
    state['replies'][('GET', '/api/admin/users')] = (200, {'items': [
        {'id': 'other-id', 'username': '另一个用户', 'enabled': True, 'max_concurrent': 2},
    ]})
    state['replies'][('GET', '/api/admin/tasks')] = (200, task_poll_reply(status))
    open_page(page, '/admin/users')
    page.wait_for_function("document.querySelector('#admin-tasks-list').textContent.includes('已耗时 10秒')")


@pytest.mark.parametrize('status', ['queued', 'running'])
def test_tasks_poll_server_timing_preserving_filter_page_and_user_edits(studio, status):
    page, state, _ = studio
    open_polling_admin(page, state, status)
    page.locator('#task-user').select_option('other-id')
    page.wait_for_function("!document.querySelector('#task-filter button').disabled")
    page.locator('#task-status').select_option(status)
    page.wait_for_function("!document.querySelector('#task-filter button').disabled")
    page.locator('#tasks-next').click()
    page.wait_for_function("document.querySelector('#tasks-page').textContent.includes('第 2 /')")
    page.locator('#users-list .quota-input').first.fill('7')  # Not saved.
    before = len(task_calls(state))
    state['replies'][('GET', '/api/admin/tasks')] = (200, task_poll_reply(status, 65))
    page.clock.fast_forward(10000)
    page.wait_for_function("document.querySelector('#admin-tasks-list').textContent.includes('已耗时 1分5秒')", timeout=2000)
    assert len(task_calls(state)) == before + 1
    assert parse_qs(urlparse(task_calls(state)[-1]['url']).query) == {
        'user_id': ['other-id'], 'status': [status], 'page': ['2'], 'page_size': ['10'],
    }
    assert page.locator('#users-list .quota-input').first.input_value() == '7'
    assert sum(call['path'] == '/api/admin/users' for call in state['calls']) == 1
    state['replies'][('GET', '/api/admin/tasks')] = (200, task_poll_reply('succeeded', 70))
    page.clock.fast_forward(10000)
    page.wait_for_function("document.querySelector('#admin-tasks-list').textContent.includes('已完成')")
    final_count = len(task_calls(state))
    page.clock.fast_forward(30000)
    assert len(task_calls(state)) == final_count
    assert page.locator('#admin-tasks-list td').nth(5).inner_text() == '1分10秒'


def set_page_hidden(page, hidden):
    # Headless tab activation does not reliably change visibility; dispatch the
    # browser lifecycle event with its matching DOM visibility property.
    page.evaluate("""hidden => {
        Object.defineProperty(document, 'hidden', {configurable:true, get:() => hidden});
        document.dispatchEvent(new Event('visibilitychange'));
    }""", hidden)


def test_task_poll_pauses_hidden_and_refreshes_on_return_even_after_terminal(studio):
    page, state, _ = studio
    open_polling_admin(page, state)
    set_page_hidden(page, True)
    before = len(task_calls(state))
    page.clock.fast_forward(30000)
    assert len(task_calls(state)) == before
    state['replies'][('GET', '/api/admin/tasks')] = (200, task_poll_reply('succeeded', 80))
    set_page_hidden(page, False)
    page.wait_for_function("document.querySelector('#admin-tasks-list').textContent.includes('1分20秒')", timeout=2000)
    assert len(task_calls(state)) == before + 1
    set_page_hidden(page, True)
    set_page_hidden(page, False)
    page.wait_for_function("!document.querySelector('#task-filter button').disabled")
    assert len(task_calls(state)) == before + 2


def test_slow_poll_cannot_overwrite_new_filter_or_restart_terminal_poll(studio):
    page, state, _ = studio
    open_polling_admin(page, state)
    state['hold_tasks'] = True
    page.clock.fast_forward(10000)
    page.wait_for_timeout(100)  # Pump the intercepted asynchronous request.
    assert len(state.get('held_tasks', [])) == 1
    # The background refresh must leave filtering available while in flight.
    assert page.locator('#task-filter button').is_enabled()
    page.clock.fast_forward(30000)
    assert len(state['held_tasks']) == 1  # No overlapping automatic requests.
    state['hold_tasks'] = False
    state['replies'][('GET', '/api/admin/tasks')] = (200, task_poll_reply('failed', 75, '新筛选结果'))
    page.locator('#task-status').select_option('failed')
    page.locator('#task-filter button').click()
    page.wait_for_function("document.querySelector('#admin-tasks-list').textContent.includes('新筛选结果')")
    state['held_tasks'][0].fulfill(status=200, content_type='application/json', body=json.dumps(task_poll_reply('running', 999, '旧响应')))
    page.wait_for_timeout(100)
    assert '旧响应' not in page.locator('#admin-tasks-list').inner_text()
    before = len(task_calls(state))
    page.clock.fast_forward(30000)
    assert len(task_calls(state)) == before


def test_task_poll_stops_on_session_rejection_even_if_navigation_is_blocked(studio):
    page, state, context = studio
    open_polling_admin(page, state)
    # Keep the old document alive to detect a retry loop after account.js rejects.
    context.route('**/login', lambda route: route.fulfill(status=204))
    page.evaluate("""() => {
        const originalFetch = window.fetch;
        window.taskFetchAttempts = 0;
        window.fetch = (...args) => {
            if (String(args[0]).startsWith('/api/admin/tasks')) window.taskFetchAttempts++;
            return originalFetch(...args);
        };
    }""")
    state['replies'][('GET', '/api/admin/tasks')] = (401, {'detail': 'session expired'})
    before = len(task_calls(state))
    page.clock.fast_forward(10000)
    page.wait_for_function("!document.querySelector('#tasks-status').hidden", timeout=2000)
    assert len(task_calls(state)) == before + 1
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.clock.fast_forward(30000)
    set_page_hidden(page, True)
    set_page_hidden(page, False)
    page.clock.fast_forward(30000)
    assert len(task_calls(state)) == before + 1
    assert not errors
    assert page.evaluate('window.taskFetchAttempts') == 1


def test_admin_failure_does_not_leave_stale_task_statistics(studio):
    page, state, _ = studio
    state['account']['user']['role'] = 'admin'
    state['replies'][('GET', '/api/admin/tasks')] = (200, {'items': [], 'stats': {'submitted': 7}})
    open_page(page, '/admin/users')
    page.wait_for_function("document.querySelector('[data-stat=submitted]').textContent === '7'")
    state['replies'][('GET', '/api/admin/tasks')] = (500, {'detail': '任务读取失败'})
    page.locator('#task-filter button').click()
    page.wait_for_function("!document.getElementById('tasks-status').hidden")
    assert page.locator('[data-stat=submitted]').inner_text() == '—'
    assert '任务读取失败' in page.locator('#tasks-status').inner_text()


@pytest.mark.parametrize('timing,elapsed,detail', [
    (None, '—', ''),
    ({'available': False, 'total_seconds': 999}, '—', ''),
    ({'available': True, 'total_seconds': None}, '—', ''),
    ({'available': True, 'total_seconds': 0, 'queue_seconds': 0, 'execution_seconds': 0}, '0秒', '排队 0秒'),
    ({'available': True, 'is_live': True, 'total_seconds': 65, 'queue_seconds': 5, 'execution_seconds': 60}, '已耗时 1分5秒', '执行 1分0秒'),
    ({'available': True, 'total_seconds': 3725, 'queue_seconds': None, 'execution_seconds': 3600, 'paused_seconds': 125}, '1小时2分5秒', '暂停 2分5秒'),
])
def test_task_elapsed_time_is_distinct_from_video_duration(studio, timing, elapsed, detail):
    page, state, _ = studio
    state['account']['user']['role'] = 'admin'
    state['replies'][('GET', '/api/admin/tasks')] = (200, {'items': [{'name': '任务', 'duration': 8, 'status': 'running', 'timing': timing}], 'stats': {}})
    open_page(page, '/admin/users')
    page.wait_for_function("document.querySelector('#admin-tasks-list tr')?.textContent.includes('任务')")
    assert page.get_by_role('columnheader', name='成片时长', exact=True).count() == 1
    assert page.get_by_role('columnheader', name='任务耗时', exact=True).count() == 1
    cells = page.locator('#admin-tasks-list tr').first.locator('td')
    assert cells.nth(4).inner_text() == '8 秒'
    assert cells.nth(5).inner_text() == elapsed
    if detail:
        assert detail in cells.nth(5).locator('[title]').get_attribute('title')
    if timing and timing.get('available') and timing.get('total_seconds') == 3725:
        assert '排队 —' in cells.nth(5).locator('[title]').get_attribute('title')


@pytest.mark.parametrize('width', [1440, 390])
def test_generation_controls_fit_desktop_and_mobile(studio, width):
    page, _, _ = studio
    page.set_viewport_size({'width': width, 'height': 1000})
    open_page(page)
    sizes = page.evaluate("""() => {
      const box = selector => { const node = document.querySelector(selector); const rect = node.getBoundingClientRect(); return {width:rect.width, height:rect.height, font:getComputedStyle(node).fontSize}; };
      return {model:box('#model-settings [name=model]'), resolution:box('#model-settings [name=resolution]'), ratio:box('#model-settings [name=ratio]'), duration:box('#generation-duration-slider'), generate:box('#studio-generate-submit'), overflow:document.documentElement.scrollWidth > innerWidth};
    }""")
    controls = ('model', 'resolution', 'ratio', 'duration', 'generate')
    for selector in ('#model-settings select', '#generation-duration-slider', '#studio-generate-submit'):
        for control in page.locator(selector).all():
            assert control.is_visible()
            box = control.bounding_box()
            assert box['width'] > 0 and 0 <= box['x'] < box['x'] + box['width'] <= width
    if width > 720:
        assert all(32 <= sizes[key]['height'] <= 40 for key in controls if key != 'duration')
        # Mira's duration control has a 44px interaction area on desktop too.
        assert sizes['duration']['height'] == 44
        assert all(sizes[key]['font'] == '12px' for key in ('model', 'resolution', 'ratio', 'generate'))
    else:
        assert all(44 <= sizes[key]['height'] <= 60 for key in controls)
        assert all(sizes[key]['font'] == '16px' for key in ('model', 'resolution', 'ratio'))
        button = page.locator('#studio-generate-submit').bounding_box()
        assert 0 <= button['y'] < button['y'] + button['height'] <= 1000
    assert sizes['overflow'] is False
    assert page.locator('#generation-duration-slider').get_attribute('type') == 'range'
    assert page.locator('#generation-duration-note').is_visible()
    assert page.locator('#generation-source-duration').is_visible()


@pytest.mark.parametrize('path', ['/login', '/account/password', '/admin/users'])
def test_account_pages_fit_mobile_viewport(studio, path):
    page, state, _ = studio
    state['account']['user']['role'] = 'admin'
    if path == '/login':
        state['account']['user'] = None
    page.set_viewport_size({'width': 390, 'height': 844})
    open_page(page, path)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
