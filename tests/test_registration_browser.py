"""Self-registration through the live Mira templates and local application routes."""
import pytest

from tests.test_access_control import protected
from tests.test_account_frontend import browser
from tests.test_mira_integration import mira_browser


def fill_registration(page, username='new-mira-user', password='123456', confirmation=None):
    page.locator('#register-username').fill(username)
    page.locator('#register-password').fill(password)
    page.locator('#register-confirm-password').fill(password if confirmation is None else confirmation)


def test_registration_creates_session_and_enters_own_workspace(mira_browser):
    from playwright.sync_api import expect
    page, errors, calls = mira_browser
    page.goto('http://testserver/login')
    expect(page.locator('#open-register')).to_be_visible()
    page.locator('#open-register').click()
    expect(page.locator('#register-username')).to_be_focused()
    fill_registration(page)
    page.locator('#register-submit').click()
    expect(page).to_have_url('http://testserver/')
    expect(page.locator('[data-account-name]')).to_contain_text('new-mira-user')
    assert page.evaluate('window.currentAccount.role') == 'user'
    assert ('POST', '/api/auth/register', 201) in calls
    assert not any(path == '/api/auth/login' for _, path, _ in calls)
    cookies = page.context.cookies()
    assert any(cookie['name'] == 'ark_session' and cookie['httpOnly'] for cookie in cookies)
    stored = page.evaluate('JSON.stringify({...localStorage})')
    assert '123456' not in stored and 'csrf_token' not in stored
    assert not errors


def test_registration_validates_confirmation_and_recovers_from_duplicate(mira_browser):
    from playwright.sync_api import expect
    page, errors, calls = mira_browser
    page.goto('http://testserver/login')
    page.locator('#open-register').click()
    fill_registration(page, username='mira-user', confirmation='654321')
    page.locator('#register-submit').click()
    expect(page.locator('#register-error')).to_contain_text('不一致')
    assert not any(path == '/api/auth/register' for _, path, _ in calls)
    page.locator('#register-confirm-password').fill('123456')
    page.locator('#register-submit').click()
    expect(page.locator('#register-error')).to_contain_text('用户名')
    expect(page.locator('#register-submit')).to_be_enabled()
    expect(page.locator('#register-password')).to_have_value('')
    expect(page.locator('#register-confirm-password')).to_have_value('')
    assert ('POST', '/api/auth/register', 409) in calls
    fill_registration(page, username='available-user')
    page.locator('#register-submit').click()
    expect(page).to_have_url('http://testserver/')
    assert not errors


@pytest.mark.parametrize('width', [320, 390, 1440])
def test_direct_registration_is_accessible_and_switches_to_login(mira_browser, width, tmp_path):
    from playwright.sync_api import expect
    page, errors, _ = mira_browser
    page.set_viewport_size({'width': width, 'height': 844})
    page.goto('http://testserver/register')
    expect(page).to_have_url('http://testserver/register')
    expect(page.locator('#login-dialog')).to_be_visible()
    expect(page.locator('#register-username')).to_be_focused()
    page.screenshot(path=str(tmp_path / ('register-' + str(width) + '.png')))
    fill_registration(page)
    page.locator('#switch-login').click()
    expect(page.locator('#login-username')).to_be_focused()
    expect(page.locator('#register-password')).to_have_value('')
    expect(page.locator('#register-confirm-password')).to_have_value('')
    page.locator('#switch-register').click()
    expect(page.locator('#register-form')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    box = page.locator('#login-dialog').bounding_box()
    assert box['x'] >= 0 and box['x'] + box['width'] <= width
    page.keyboard.press('Escape')
    expect(page.locator('#login-dialog')).not_to_be_visible()
    page.locator('#language-trigger').click()
    page.locator('#language-list [data-language=en]').click()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#open-register').click()
    expect(page.locator('#register-submit')).to_have_text('Create account')
    expect(page.locator('#login-title')).to_contain_text('creative')
    page.locator('#register-password').fill('discard-on-close')
    page.keyboard.press('Escape')
    expect(page.locator('#register-password')).to_have_value('')
    expect(page.locator('#open-register')).to_be_focused()
    assert not errors
