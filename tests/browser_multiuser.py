"""Real multi-user browser verification; run with .venv/Scripts/python.exe.

Edge requests are fulfilled by real FastAPI TestClients, one per context.
Auth responses are never stubbed. Accounts/data are temporary, workers disabled.
Only the four requested screenshots persist. Diagnostics exclude credentials.
"""
from contextlib import ExitStack
from dataclasses import replace
import json
import os
from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory
import time
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from playwright.sync_api import expect, sync_playwright
from app import main, production_worker
from app.accounts import Accounts
from app.production_store import ProductionStore
from app.tenancy import user_settings

ORIGIN = 'http://127.0.0.1:18759'
OUTPUT = ROOT/'storage'
TIMEOUT = 12000
# Observe native fetch dispatch/response ordering without changing response data.
FETCH_TIMELINE = """(() => {
  const original = window.fetch.bind(window);
  window.__multiuserFetch = [];
  window.fetch = (input, options) => {
    const path = new URL(input instanceof Request ? input.url : input, location.href).pathname;
    const method = options?.method || (input instanceof Request ? input.method : 'GET');
    window.__multiuserFetch.push({event:'request', path, method});
    return original(input, options).then(response => {
      window.__multiuserFetch.push({event:'response', path, method, status:response.status});
      return response;
    });
  };
})();"""


class Report:
    def __init__(self):
        self.secrets, self.checks, self.requests = [], [], []
        self.js_errors, self.console_errors, self.transport_errors = [], [], []
        self.screenshots, self.measurements = [], {}

    def clean(self, value):
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, '[redacted]')
        return text[:2500]

    def password(self):
        value = secrets.token_urlsafe(24)
        self.secrets.append(value)
        return value

    def case(self, name, action):
        try:
            action()
            self.checks.append({'check': name, 'result': 'PASS'})
            print('PASS '+name, flush=True)
            return True
        except Exception as exc:
            detail = self.clean(f'{type(exc).__name__}: {exc}')
            self.checks.append({'check': name, 'result': 'FAIL', 'detail': detail})
            print('FAIL '+name+': '+detail, flush=True)
            return False

    def screenshot(self, page, name):
        path = OUTPUT/name
        page.screenshot(path=str(path), full_page=True,
                        mask=[field for field in page.locator('input[type=password]').all()
                              if field.input_value()])
        self.screenshots.append(str(path))

    def finish(self):
        failures = [item for item in self.requests if item['status'] >= 400 and not item['expected']]
        unexpected_console = [item for item in self.console_errors
                              if not (item['path'] == '/login' and item['error'].startswith(
                                  'Failed to load resource: the server responded with a status of 401'))]
        ok = (all(item['result'] == 'PASS' for item in self.checks)
              and not failures and not self.js_errors and not self.transport_errors
              and not unexpected_console)
        print(json.dumps({
            'result': 'PASS' if ok else 'FAIL', 'checks': self.checks,
            'measurements': self.measurements, 'screenshots': self.screenshots,
            'js_errors': self.js_errors, 'console_errors': self.console_errors,
            'unexpected_console_errors': unexpected_console,
            'failing_requests': failures,
            'expected_denials': [item for item in self.requests if item['expected']],
            'transport_errors': self.transport_errors, 'http_requests': len(self.requests),
        }, ensure_ascii=True, indent=2), flush=True)
        return 0 if ok else 1


class Session:
    def __init__(self, name, browser, stack, report):
        self.name, self.report = name, report
        self.client = stack.enter_context(TestClient(main.app, base_url=ORIGIN,
            follow_redirects=False, raise_server_exceptions=False))
        self.context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        stack.callback(self.context.close)
        self.context.add_init_script(FETCH_TIMELINE)
        self.context.route('**/*', self.route)
        self.page = self.context.new_page()
        self.page.set_default_timeout(TIMEOUT)
        self.page.set_default_navigation_timeout(TIMEOUT)
        self.page.on('pageerror', lambda error: report.js_errors.append(
            {'session': name, 'path': urlsplit(self.page.url).path, 'error': report.clean(error)}))
        self.page.on('console', lambda message: report.console_errors.append(
            {'session': name, 'path': urlsplit(self.page.url).path, 'error': report.clean(message.text)})
            if message.type == 'error' else None)
        self.page.on('requestfailed', lambda request: report.transport_errors.append(
            {'session': name, 'method': request.method, 'path': urlsplit(request.url).path,
             'error': report.clean(request.failure)}))

    def record(self, method, path, response, expected=False):
        item = {'session': self.name, 'method': method, 'path': path,
                'status': response.status_code, 'expected': expected}
        if response.status_code >= 400:
            try:
                detail = response.json().get('detail')
            except (ValueError, AttributeError):
                detail = None
            # Never echo validation input dictionaries or raw request/response bodies.
            if isinstance(detail, str):
                item['detail'] = self.report.clean(detail)
        self.report.requests.append(item)

    def route(self, route):
        request, parsed = route.request, urlsplit(route.request.url)
        if f'{parsed.scheme}://{parsed.netloc}' != ORIGIN:
            self.report.transport_errors.append({'session': self.name,
                'error': 'external request blocked', 'path': parsed.path})
            route.abort()
            return
        try:
            headers = {key: value for key, value in request.all_headers().items()
                       if key.lower() not in {'host', 'content-length'}}
            # The browser cookie is authoritative. A stale httpx cookie must not
            # hide a Set-Cookie/redirect regression in the actual browser.
            self.client.cookies.clear()
            response = self.client.request(request.method, request.url,
                content=request.post_data_buffer, headers=headers, follow_redirects=False)
            expected = (parsed.path == '/api/auth/me' and response.status_code == 401
                        and urlsplit(request.frame.url).path == '/login')
            self.record(request.method, parsed.path, response, expected)
            if parsed.path == '/api/auth/me' and response.status_code == 200:
                time.sleep(0.25)  # Delay the real response to exercise accountReady.
            route.fulfill(status=response.status_code, body=response.content,
                headers={key: value for key, value in response.headers.items()
                         if key.lower() not in {'content-length', 'content-encoding', 'transfer-encoding'}})
        except Exception as exc:
            self.report.transport_errors.append({'session': self.name, 'method': request.method,
                'path': parsed.path, 'error': self.report.clean(exc)})
            route.fulfill(status=500, content_type='application/json',
                          body='{"detail":"browser harness request failed"}')

    def get(self, path, expected_status=200):
        self.client.cookies.clear()
        self.client.cookies.update({item['name']: item['value'] for item in self.context.cookies(ORIGIN)})
        response = self.client.get(path)
        self.record('GET', urlsplit(path).path, response,
                    expected=expected_status >= 400 and response.status_code == expected_status)
        assert response.status_code == expected_status, f'GET {path}: {response.status_code}'
        return response.json()


def login(session, username, password):
    page = session.page
    expect(page.locator('#login-form')).to_be_visible(timeout=TIMEOUT)
    page.locator('#login-username').fill(username)
    page.locator('#login-password').fill(password)
    page.locator('#login-submit').click()


def first_login(session, user, initial, changed, report):
    page = session.page
    page.goto(ORIGIN+'/login')
    if user['username'] == 'alice':
        report.screenshot(page, 'multiuser-login.png')
    login(session, user['username'], initial)
    if user['must_change_password']:
        page.wait_for_url(ORIGIN+'/account/password')
        expect(page.locator('#password-fields')).to_be_enabled(timeout=TIMEOUT)
        expect(page.locator('#password-back')).to_be_hidden()
        page.locator('#current-password').fill(initial)
        page.locator('#new-password').fill(changed)
        page.locator('#confirm-password').fill(changed)
        page.locator('#password-form [type=submit]').click()
        page.wait_for_url(ORIGIN+'/login')
        login(session, user['username'], changed)
    page.wait_for_url(ORIGIN+'/')
    account = session.get('/api/auth/me')['user']
    assert account['id'] == user['id'] and not account['must_change_password']
    assert any(cookie['name'] == 'ark_session' and cookie['httpOnly']
               for cookie in session.context.cookies(ORIGIN))


def initial_draft(session):
    expect(session.page.locator('#draft-save-status')).to_contain_text('已保存', timeout=TIMEOUT)
    assert len(session.get('/api/production/drafts')['items']) == 1
    events = session.page.evaluate('window.__multiuserFetch')
    identity = next(i for i, event in enumerate(events) if event['event'] == 'response'
                    and event['path'] == '/api/auth/me' and event['status'] == 200)
    requests = [(i, event) for i, event in enumerate(events) if event['event'] == 'request'
                and event['path'].startswith('/api/') and event['path'] != '/api/auth/me']
    assert requests and all(i > identity for i, _ in requests), events
    assert any(event['path'] == '/api/production/drafts' and event['method'] == 'POST'
               for _, event in requests), 'initial own draft was never created'


def account_chrome(session):
    page = session.page
    expect(page.locator('.account-chrome [data-account-name]')).to_have_text(session.name)
    for selector in ('[data-account-logout]', 'a[href="/account/password"]', 'a[href="/people"]'):
        expect(page.locator('.account-chrome '+selector)).to_be_visible()
    assert page.locator('a[href^="/admin/"]:visible, [data-admin-only]:visible').count() == 0
    assert page.locator('#link-settings:visible').count() == 0


def isolation(alice, bob, cfg, users):
    drafts = {}
    for session in (alice, bob):
        marker = session.name+' private browser draft'
        session.page.locator('#draft-name-edit').click()
        session.page.locator('#draft-name-input').fill(marker)
        with session.page.expect_response(lambda response: response.request.method == 'PUT'
                and '/api/production/drafts/' in response.url) as saved:
            session.page.locator('#draft-name-save').click()
        assert saved.value.status == 200
        expect(session.page.locator('#draft-save-status')).to_contain_text('已保存')
        items = session.get('/api/production/drafts')['items']
        assert len(items) == 1 and items[0]['name'] == marker
        drafts[session.name] = items[0]
    assert drafts['alice']['id'] != drafts['bob']['id']
    alice.get('/api/production/drafts/'+drafts['bob']['id'], 404)
    bob.get('/api/production/drafts/'+drafts['alice']['id'], 404)
    for session in (alice, bob):
        session.page.reload()
        expect(session.page.locator('#draft-save-status')).to_contain_text('已保存', timeout=TIMEOUT)
        expect(session.page.locator('#draft-task-name')).to_have_text(
            session.name+' private browser draft')
        store = ProductionStore(user_settings(cfg, users[session.name]).storage_dir)
        assert [draft['id'] for draft in store.list_drafts()] == [drafts[session.name]['id']]
    assert ProductionStore(cfg.storage_dir).list_drafts() == []
    tokens = [{cookie['value'] for cookie in session.context.cookies(ORIGIN)
               if cookie['name'] == 'ark_session'} for session in (alice, bob)]
    assert len(tokens[0]) == len(tokens[1]) == 1 and tokens[0].isdisjoint(tokens[1])


def workspace_size(session, width, report):
    page = session.page
    page.set_viewport_size({'width': width, 'height': 1000})
    if width in (1440, 390):
        report.screenshot(page, f'multiuser-workspace-{width}.png')
    dimensions = page.evaluate('''() => ({width:innerWidth,
        scroll:document.documentElement.scrollWidth, body:document.body.scrollWidth})''')
    report.measurements[str(width)] = dimensions
    assert dimensions['scroll'] <= width+1 and dimensions['body'] <= width+1, dimensions
    if width == 1440:
        duration = ('#generation-duration-note' if page.locator('#generation-duration-note').is_visible()
                    else '#model-settings-form [name=duration]')
        selectors = ['#model-settings-form [name=model]', '#model-settings-form [name=resolution]',
                     duration, '#studio-generate-submit']
        boxes = {selector: page.locator(selector).bounding_box() for selector in selectors}
        report.measurements['generation_controls'] = boxes
        assert all(box and abs(box['height']-34) <= 1 for box in boxes.values()), boxes
        bottoms = [box['y']+box['height'] for box in boxes.values()]
        assert max(bottoms)-min(bottoms) < 4, boxes


def people(session):
    page = session.page
    page.goto(ORIGIN+'/people')
    expect(page.locator('[data-personal-library=true]')).to_be_visible()
    expect(page.locator('[data-library-counts]')).to_contain_text('真人 0 位', timeout=TIMEOUT)
    assert session.get('/api/portrait/people')['items'] == []
    assert page.locator('[data-config], input[name=access_key], input[name=secret_key]').count() == 0
    page.locator('#manage-people').click()
    expect(page.locator('.virtual-library-dialog')).to_be_visible()
    for selector in ('[data-sync]', '[data-show-import]', '[data-more]', '[data-assets]'):
        assert page.locator(selector+':visible').count() == 0, selector+' exposed in personal library'
    assert not any(item['session'] == session.name and item['path'] in {
        '/api/portrait/people/sync', '/api/portrait/people/resolve'} for item in session.report.requests)
    page.get_by_role('button', name='关闭人物与照片', exact=True).click()


def admin_users(session, report, accounts):
    page = session.page
    page.goto(ORIGIN+'/admin/users')
    expect(page.locator('#users-workspace')).to_be_visible(timeout=TIMEOUT)
    expect(page.locator('#users-list tr')).to_have_count(3)
    expect(page.locator('.account-chrome a[href="/admin/settings"]')).to_be_visible()
    form = page.locator('#create-user-form')
    form.locator('[name=username]').fill('charlie')
    form.locator('[name=password]').fill(report.password())
    form.locator('[type=submit]').click()
    expect(page.locator('#create-user-status')).to_contain_text('账号已创建', timeout=TIMEOUT)
    row = page.locator('#users-list tr').filter(has=page.get_by_role('cell', name='charlie', exact=True))
    expect(row).to_have_count(1)
    row.get_by_role('checkbox', name='启用账号 charlie', exact=True).uncheck()
    row.get_by_role('button', name='保存', exact=True).click()
    expect(page.locator('#users-status')).to_contain_text('已保存 charlie', timeout=TIMEOUT)
    user = next(user for user in session.get('/api/admin/users')['items'] if user['username'] == 'charlie')
    assert not user['enabled'] and user['must_change_password']
    assert user['max_concurrent'] == 1 and user['max_queued'] == 10
    assert accounts.get_user(user['id'])['enabled'] is False
    page.reload()
    expect(page.get_by_role('checkbox', name='启用账号 charlie', exact=True)).not_to_be_checked()
    report.screenshot(page, 'multiuser-users.png')


def no_generation(report):
    assert not any(item['method'] == 'POST' and item['path'] == '/api/production/runs'
                   for item in report.requests)


def check():
    report = Report()
    OUTPUT.mkdir(exist_ok=True)
    try:
        with TemporaryDirectory(prefix='browser-multiuser-') as tmp, ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'APP_AUTH_ENABLED': 'true',
                'APP_COOKIE_SECURE': 'false', 'APP_PUBLIC_ORIGIN': ORIGIN}))
            cfg = replace(main.settings, storage_dir=Path(tmp), config_root=None,
                          user_id='', seedance_mode='mock')
            stack.enter_context(patch.object(main, 'settings', cfg))
            stack.enter_context(patch.object(production_worker, 'wake', lambda *_: None))
            accounts = Accounts(cfg.storage_dir)
            initial = {name: report.password() for name in ('admin', 'alice', 'bob')}
            changed = {name: report.password() for name in initial}
            users = {'admin': accounts.init_admin('admin', initial['admin'])}
            for name in ('alice', 'bob'):
                users[name] = accounts.create_user(name, initial[name], users['admin']['id'])
                assert users[name]['must_change_password'] is True
            playwright = stack.enter_context(sync_playwright())
            browser = playwright.chromium.launch(channel='msedge', headless=True)
            stack.callback(browser.close)
            sessions = {name: Session(name, browser, stack, report) for name in users}
            ready, saved = {}, {}
            # Admin logs in later: the root store must first be empty of user drafts.
            for name in ('alice', 'bob'):
                session = sessions[name]
                ready[name] = report.case(name+' real login / forced password change / relogin',
                    lambda s=session, n=name: first_login(s, users[n], initial[n], changed[n], report))
                if ready[name]:
                    saved[name] = report.case(name+' accountReady and initial own draft',
                                             lambda s=session: initial_draft(s))
                    report.case(name+' ordinary account chrome', lambda s=session: account_chrome(s))
            if all(saved.get(name, False) for name in ('alice', 'bob')):
                report.case('separate cookies, persisted drafts and cross-tenant 404s',
                            lambda: isolation(sessions['alice'], sessions['bob'], cfg, users))
            if ready['alice']:
                for width in (1440, 390, 320):
                    report.case(f'workspace layout {width}',
                                lambda w=width: workspace_size(sessions['alice'], w, report))
                report.case('ordinary user cannot read admin APIs',
                            lambda: sessions['alice'].get('/api/admin/users', 403))
                sessions['alice'].page.set_viewport_size({'width': 1440, 'height': 1000})
                report.case('personal library without global sync or connection controls',
                            lambda: people(sessions['alice']))
            if report.case('admin real UI login', lambda: first_login(sessions['admin'], users['admin'],
                           initial['admin'], changed['admin'], report)):
                report.case('admin UI creates and disables account',
                            lambda: admin_users(sessions['admin'], report, accounts))
            report.case('no generation submitted', lambda: no_generation(report))
    except Exception as exc:
        report.checks.append({'check': 'harness setup / teardown', 'result': 'FAIL',
                              'detail': report.clean(f'{type(exc).__name__}: {exc}')})
    return report.finish()


if __name__ == '__main__':
    raise SystemExit(check())
