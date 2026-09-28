"""Account UI contracts and isolated browser regression tests (no live server)."""
import json
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import pytest
from jinja2 import Environment, FileSystemLoader


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
    assert scripts == ['account.js', 'production-shell.js', 'portrait-photos.js', 'portrait-people.js', 'production-runs.js', 'production.js', 'generation-options.js', 'link-templates.js']


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
    template_routes = {'/': 'production.html', '/login': 'login.html', '/account/password': 'password.html', '/admin/users': 'users.html', '/admin/settings': 'admin_settings.html'}

    def handle(route):
        request = route.request
        path = urlparse(request.url).path
        if path.startswith('/static/'):
            file = STATIC / Path(path).name
            route.fulfill(status=200, content_type='text/css' if file.suffix == '.css' else 'application/javascript', body=file.read_text(encoding='utf-8'))
        elif path.startswith('/api/'):
            state['calls'].append({'path': path, 'method': request.method, 'url': request.url, 'headers': request.headers, 'body': request.post_data})
            if path == '/api/auth/me' and state['hold_me']:
                state['held'].append(route)
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
    assert page.locator('.account-chrome a[href="/people"]').is_visible()
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


@pytest.mark.parametrize('status,data,path', [(401, {}, '/login'), (403, {'detail': {'code': 'password_change_required'}}, '/account/password')])
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


def test_login_error_then_force_password_change(studio):
    page, state, _ = studio
    state['account']['user'] = None
    state['replies'][('GET', '/api/auth/me')] = (401, {})
    state['replies'][('POST', '/api/auth/login')] = (401, {'detail': 'invalid credentials'})
    open_page(page, '/login')
    page.locator('#login-username').fill('alice')
    page.locator('#login-password').fill('wrong-password')
    page.locator('#login-submit').click()
    page.wait_for_function("!document.getElementById('login-error').hidden")
    assert '用户名或密码不正确' in page.locator('#login-error').inner_text()
    assert page.locator('#login-password').input_value() == ''
    state['account']['user'] = {**ACCOUNT['user'], 'must_change_password': True}
    state['replies'].pop(('GET', '/api/auth/me'))
    state['replies'][('POST', '/api/auth/login')] = (200, {'user': state['account']['user'], 'csrf_token': 'new-token'})
    page.locator('#login-password').fill('correct-password')
    page.locator('#login-submit').click()
    page.wait_for_url('**/account/password')
    page.wait_for_function("!document.getElementById('password-fields').disabled")
    assert not page.locator('#password-back').is_visible()
    assert '首次登录' in page.locator('#password-intro').inner_text()
    stored = page.evaluate('({...localStorage})')
    assert set(stored) == {'ark-account-identity'}
    assert json.loads(stored['ark-account-identity'])['user_id'] == 'alice-id'
    assert 'correct-password' not in str(stored) and 'new-token' not in str(stored)


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
    page.locator('#new-password').fill('new-password')
    page.locator('#confirm-password').fill('different-password')
    page.locator('#password-form button').click()
    assert '不一致' in page.locator('#password-error').inner_text()
    assert not any(call['path'] == '/api/auth/password' for call in state['calls'])
    state['account']['user'] = None
    page.locator('#confirm-password').fill('new-password')
    page.locator('#password-form button').click()
    page.wait_for_url('**/login')
    call = next(call for call in state['calls'] if call['path'] == '/api/auth/password')
    assert json.loads(call['body']) == {'current_password': 'old-password', 'new_password': 'new-password'}
    assert call['headers']['x-csrf-token'] == 'test-csrf'


def test_admin_users_mutations_filters_stats_and_safe_rendering(studio):
    page, state, _ = studio
    state['account']['user']['role'] = 'admin'
    username = '<img src=x onerror=alert(1)>'
    state['replies'][('GET', '/api/admin/users')] = (200, {'items': [{'id': 'internal-id', 'username': username, 'enabled': True, 'max_concurrent': 2, 'max_queued': 5}]})
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
    page.locator('#create-password').fill('initial-password')
    page.locator('#create-user-form button').click()
    page.wait_for_function("document.getElementById('create-user-status').dataset.state === 'success'")
    create = next(call for call in state['calls'] if call['path'] == '/api/admin/users' and call['method'] == 'POST')
    assert json.loads(create['body']) == {'username': 'new-user', 'password': 'initial-password', 'max_concurrent': 1, 'max_queued': 10}
    assert page.locator('#create-password').input_value() == ''
    page.locator('#users-list input[type=checkbox]').uncheck()
    page.locator('#users-list .quota-input').first.fill('3')
    page.locator('#users-list button').first.click()
    page.wait_for_function("document.getElementById('users-status').dataset.state === 'success'")
    patch = next(call for call in state['calls'] if call['method'] == 'PATCH')
    assert patch['path'] == '/api/admin/users/internal-id'
    assert json.loads(patch['body']) == {'enabled': False, 'max_concurrent': 3, 'max_queued': 5}
    page.locator('#users-list input[type=password]').fill('reset-password')
    page.locator('#users-list .row-actions button').click()
    page.wait_for_function("document.querySelector('#users-list input[type=password]').value === ''")
    reset = next(call for call in state['calls'] if call['path'].endswith('/reset-password'))
    assert json.loads(reset['body']) == {'password': 'reset-password'}
    page.locator('#task-user').select_option('internal-id')
    page.locator('#task-status').select_option('failed')
    page.locator('#task-filter button').click()
    page.wait_for_function("!document.querySelector('#task-filter button').disabled")
    task = [call for call in state['calls'] if call['path'] == '/api/admin/tasks'][-1]
    assert parse_qs(urlparse(task['url']).query) == {'user_id': ['internal-id'], 'status': ['failed'], 'page': ['1'], 'page_size': ['50']}


def test_normal_user_does_not_fetch_admin_data(studio):
    page, state, _ = studio
    open_page(page, '/admin/users')
    assert not page.locator('#users-workspace').is_visible()
    assert page.locator('#users-access-error').is_visible()
    assert not any(call['path'].startswith('/api/admin/') for call in state['calls'])


def test_admin_pagination_and_backend_audit_timestamps(studio):
    page, state, _ = studio
    state['account']['user']['role'] = 'admin'
    state['replies'][('GET', '/api/admin/users')] = (200, {'items': [state['account']['user']]})
    state['replies'][('GET', '/api/admin/tasks')] = (200, {'items': [], 'total': 120, 'page': 1, 'page_size': 50, 'stats': {'duration_unknown': 2}})
    state['replies'][('GET', '/api/admin/audit')] = (200, {'items': [{'actor_id': 'alice-id', 'action': 'user.update', 'target': 'alice-id', 'created_at': 1790582400, 'details': {}}]})
    open_page(page, '/admin/users')
    page.wait_for_function("!document.getElementById('tasks-next').disabled")
    assert '共 120 条' in page.locator('#tasks-page').inner_text()
    assert '2 条' in page.locator('#tasks-duration-note').inner_text()
    assert '2026' in page.locator('#audit-list').inner_text()
    assert '修改账号设置' in page.locator('#audit-list').inner_text()
    assert page.locator('#audit-list td').nth(3).inner_text() == '小舟'
    # Changing a filter without applying it must not silently alter page navigation.
    page.locator('#task-status').select_option('failed')
    page.locator('#tasks-next').click()
    page.wait_for_function("!document.getElementById('tasks-next').disabled")
    task = [call for call in state['calls'] if call['path'] == '/api/admin/tasks'][-1]
    query = parse_qs(urlparse(task['url']).query)
    assert query['page'] == ['2'] and 'status' not in query


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


@pytest.mark.parametrize('width,height', [(1440, 34), (390, 40)])
def test_generation_controls_have_compact_measured_dimensions(studio, width, height):
    page, _, _ = studio
    page.set_viewport_size({'width': width, 'height': 1000})
    open_page(page)
    sizes = page.evaluate("""() => {
      const box = selector => { const node = document.querySelector(selector); const rect = node.getBoundingClientRect(); return {width:rect.width, height:rect.height, font:getComputedStyle(node).fontSize}; };
      return {model:box('#model-settings [name=model]'), resolution:box('#model-settings [name=resolution]'), duration:box('#model-settings [name=duration]'), generate:box('#studio-generate-submit'), overflow:document.documentElement.scrollWidth > innerWidth};
    }""")
    assert all(sizes[key]['height'] == height for key in ('model', 'resolution', 'duration', 'generate'))
    assert all(sizes[key]['font'] == '12px' for key in ('model', 'resolution', 'duration', 'generate'))
    if width > 720:
        assert [sizes[key]['width'] for key in ('model', 'resolution', 'duration', 'generate')] == [180, 90, 80, 116]
    assert sizes['overflow'] is False
    page.evaluate("document.getElementById('generation-duration-field').hidden = true; document.getElementById('generation-follow-source').hidden = false")
    follow = page.locator('#generation-duration-note').bounding_box()
    assert follow['height'] == height
    if width > 720:
        assert follow['width'] == 140


@pytest.mark.parametrize('path', ['/login', '/account/password', '/admin/users'])
def test_account_pages_fit_mobile_viewport(studio, path):
    page, state, _ = studio
    state['account']['user']['role'] = 'admin'
    if path == '/login':
        state['account']['user'] = None
    page.set_viewport_size({'width': 390, 'height': 844})
    open_page(page, path)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
