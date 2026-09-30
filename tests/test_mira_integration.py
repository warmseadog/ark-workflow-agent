"""Mira is a real authenticated workspace, not the standalone UI preview."""
from io import BytesIO
from pathlib import Path
import re
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import main
from app.accounts import Accounts
from tests.test_access_control import protected, accounts_clients
from tests.test_account_frontend import browser


def test_cover_has_real_login_without_preview_controls(protected):
    response = protected.get('/login')
    assert response.status_code == 200
    html = response.text
    assert 'id="login-dialog"' in html
    assert 'id="login-form"' in html and 'autocomplete="current-password"' in html
    assert '/static/login.js?' in html and '/static/mira/mira-cover.js?' in html
    assert 'mira-workspace-preview.js' not in html
    assert 'id="settings"' not in html and '查看工作台预览' not in html
    assert protected.get('/api/production/drafts').status_code == 401
    redirect = protected.get('/', follow_redirects=False)
    assert redirect.status_code == 303 and redirect.headers['location'] == '/login'


@pytest.mark.parametrize('path', ['/', '/people', '/videos', '/admin/settings', '/admin/users', '/account/password'])
def test_real_pages_have_mira_skin_and_keep_access_boundaries(accounts_clients, path):
    _, _, (admin, alice, _) = accounts_clients
    response = admin.get(path)
    assert response.status_code == 200
    assert '/static/mira/mira-system.css?' in response.text
    assert 'mira-workspace' in response.text
    assert 'mira-workspace-preview.js' not in response.text
    assert 'href="/"' in response.text
    if path.startswith('/admin/'):
        assert alice.get(path).status_code == 403


@pytest.fixture
def mira_browser(protected, browser):
    accounts = Accounts(main.settings.storage_dir)
    admin = accounts.init_admin('mira-admin', 'test-password-42')
    accounts.create_user('mira-user', 'test-password-42', admin['id'])
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, reduced_motion='reduce')
    page = context.new_page()
    transport = TestClient(main.app)
    errors, calls = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(r):
        req = r.request
        assert urlsplit(req.url).netloc == 'testserver', 'No external or paid requests'
        transport.cookies.clear()
        response = transport.request(req.method, req.url, content=req.post_data_buffer,
            headers={k:v for k,v in req.all_headers().items() if k.lower() not in {'host','content-length'}},
            follow_redirects=False)
        calls.append((req.method, urlsplit(req.url).path, response.status_code))
        r.fulfill(status=response.status_code, body=response.content,
            headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-encoding','transfer-encoding'}})

    context.route('**/*', route)
    yield page, errors, calls
    context.close()
    transport.close()


def test_mira_modal_real_login_upload_draft_and_logout(mira_browser, tmp_path):
    from playwright.sync_api import expect
    page, errors, calls = mira_browser
    page.goto('http://testserver/login')
    expect(page).to_have_url('http://testserver/login')
    expect(page.locator('#login-dialog')).not_to_be_visible()
    page.locator('#open-login').click()
    expect(page.locator('#login-username')).to_be_focused()
    page.locator('#login-username').fill('mira-user')
    page.locator('#login-password').fill('wrong-password')
    page.locator('#login-submit').click()
    expect(page.locator('#login-error')).to_contain_text('用户名或密码不正确')
    expect(page.locator('#login-password')).to_have_value('')
    page.locator('#login-password').fill('test-password-42')
    page.locator('#login-submit').click()
    expect(page).to_have_url('http://testserver/')
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    expect(page.locator('a[href="/admin/settings"]')).not_to_be_visible()
    image = BytesIO()
    Image.new('RGB', (400,400), 'navy').save(image, format='PNG')
    page.locator('#clothing-picker input[type=file]').set_input_files(
        {'name':'mira-shirt.png','mimeType':'image/png','buffer':image.getvalue()})
    expect(page.locator('#clothing-reference-preview img')).to_have_count(1)
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    assert any(method == 'POST' and path == '/api/production/assets' and code == 200 for method,path,code in calls)
    page.reload()
    expect(page.locator('#clothing-reference-preview img')).to_have_count(1)
    page.locator('.workspace-nav a[href="/videos"]').click()
    expect(page).to_have_url('http://testserver/videos')
    expect(page.locator('#library-empty')).to_be_visible()
    page.locator('.workspace-account summary').click()
    page.locator('[data-account-logout]').click()
    expect(page).to_have_url('http://testserver/login')
    assert not errors


@pytest.mark.parametrize('width', [320,390,768,1440])
def test_mira_cover_modal_navigation_and_mobile_fit(mira_browser, width):
    from playwright.sync_api import expect
    page, errors, _ = mira_browser
    page.set_viewport_size({'width':width,'height':900})
    page.goto('http://testserver/login')
    screenshots = Path(__file__).resolve().parents[1]/'.tmp-mira-visual'
    screenshots.mkdir(exist_ok=True)
    expect(page.locator('#current')).to_have_text('01')
    assert page.locator('#film').evaluate('(video) => video.paused')
    page.locator('#next').click()
    expect(page.locator('#current')).to_have_text('02')
    page.locator('#open-login').click()
    expect(page.locator('#login-dialog')).to_be_visible()
    if width in (390,1440):
        page.screenshot(path=str(screenshots/('login-'+str(width)+'.png')))
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    box = page.locator('#login-dialog').bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= width
    page.locator('#login-password').fill('not-retained')
    page.keyboard.press('Escape')
    expect(page.locator('#open-login')).to_be_focused()
    expect(page.locator('#login-password')).to_have_value('')
    page.locator('#open-login').click()
    page.mouse.click(2,2)
    expect(page.locator('#login-dialog')).not_to_be_visible()
    assert not errors


@pytest.mark.parametrize('width,height', [(320,900),(390,844),(768,1000),(844,390),(1440,1000)])
def test_mira_real_workspace_pages_and_navigation_fit(mira_browser, width, height):
    from playwright.sync_api import expect
    page, errors, _ = mira_browser
    page.set_viewport_size({'width':width,'height':height})
    page.goto('http://testserver/login')
    page.locator('#open-login').click()
    page.locator('#login-username').fill('mira-admin')
    page.locator('#login-password').fill('test-password-42')
    page.locator('#login-submit').click()
    expect(page).to_have_url('http://testserver/')
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    screenshots = Path(__file__).resolve().parents[1]/'.tmp-mira-visual'
    screenshots.mkdir(exist_ok=True)
    if width in (390,1440):
        page.screenshot(path=str(screenshots/('workspace-'+str(width)+'.png')),full_page=True)
    mobile = width <= 800 or (width > height and width <= 1100)
    if mobile:
        page.locator('.workspace-toggle').click()
    page.locator('[data-workspace-page="tasks"]').click()
    expect(page.locator('body')).to_have_attribute('data-workspace-view','tasks')
    expect(page.locator('[data-workspace-page="create"]')).not_to_have_attribute('aria-current','page')
    expect(page.locator('.production-runs')).to_be_visible()
    for path, view in [('/','create'),('/people','people'),('/videos','videos'),
                       ('/admin/settings','settings'),('/admin/users','users'),('/account/password','account')]:
        page.goto('http://testserver'+path)
        page.evaluate('window.accountReady')
        expect(page.locator('body')).to_have_attribute('data-workspace-view',view)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), (path,width,height)
        if view != 'account':
            assert page.locator('.workspace-nav [aria-current="page"]').count() == 1
            assert page.locator('.workspace-nav [aria-current="page"]').get_attribute('data-workspace-page') == view
    assert not errors


def test_mira_action_contrast_in_import_and_players(browser):
    """Exercise controls absent from an empty workspace, with the real CSS cascade."""
    static = Path(__file__).resolve().parents[1]/'app/static'
    styles = ['production.css','production-shell.css','account.css','video-library.css','workspace.css',
              'mira/mira-workspace-skin.css','mira/mira-system.css','mira/mira-production.css']
    context = browser.new_context()
    page = context.new_page()
    try:
        css = '\n'.join((static/name).read_text(encoding='utf-8') for name in styles)
        page.set_content('<style>'+css+'</style><body class="mira-workspace workspace-shell video-library-page">'
            '<main class="production-main"><button class="primary" id="confirm-video-url">确认并加载视频</button>'
            '<div class="run-player-tools"><button aria-pressed="true">原画</button></div></main>'
            '<dialog open class="library-player"><div class="library-player-bar"><button id="library-close">×</button></div>'
            '<div class="library-player-footer"><a id="library-download">下载原片</a></div></dialog></body>')
        def luminance(color):
            values = [int(v)/255 for v in re.findall(r'\d+',color)[:3]]
            linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in values]
            return sum(v*w for v,w in zip(linear,(.2126,.7152,.0722)))
        for selector in ('#confirm-video-url','.run-player-tools button','#library-close','#library-download'):
            colors = page.locator(selector).evaluate('(el) => { const s=getComputedStyle(el); return [s.color,s.backgroundColor]; }')
            light,dark = sorted(map(luminance,colors),reverse=True)
            assert (light+.05)/(dark+.05) >= 4.5, (selector,colors)
    finally:
        context.close()
