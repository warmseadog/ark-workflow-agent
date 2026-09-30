"""Real task UI backed by isolated authenticated tenant APIs."""
from pathlib import Path
import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_access_control import protected, accounts_clients
from tests.test_admin_task_records import seed, isolate_legacy_jobs


@pytest.mark.parametrize('width', [1440,390])
def test_admin_user_filter_details_and_own_actions(browser, accounts_clients, width):
    _, users, (admin, _, _) = accounts_clients
    seed(users[0])
    seed(users[1],12)
    seed(users[2])
    context = browser.new_context(viewport={'width':width,'height':950})
    page = context.new_page()
    errors = []
    page.on('pageerror',lambda error:errors.append(str(error)))
    def route(r):
        req = r.request
        response = admin.request(req.method,req.url,content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length','cookie')})
        r.fulfill(status=response.status_code,body=response.content,
            headers={k:v for k,v in response.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    try:
        page.goto('http://testserver/#tasks')
        expect(page.locator('#runs-user-controls')).to_be_visible()
        expect(page.locator('#runs-status')).to_contain_text('共 14 个任务')
        expect(page.locator('.production-run')).to_have_count(10)
        page.locator('#runs-next').click()
        expect(page.locator('#runs-page')).to_have_text('2 / 2')
        page.locator('#runs-user-filter').select_option(users[1]['id'])
        expect(page.locator('#runs-status')).to_contain_text('共 12 个任务')
        expect(page.locator('#runs-page')).to_have_text('1 / 2')
        expect(page.locator('.run-owner')).to_have_text(['alice']*10)
        expect(page.locator('[data-run-action=copy], [data-run-action=delete], .production-run .run-rename')).to_have_count(0)
        row=page.locator('.production-run').first
        row.locator('.run-menu summary').click()
        row.locator('[data-run-action=details]').click()
        expect(row.locator('.run-timing-details')).to_be_visible()
        page.locator('#runs-refresh').click()
        expect(page.locator('#runs-user-filter')).to_have_value(users[1]['id'])
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        shots=Path('storage/admin-records-review');shots.mkdir(parents=True,exist_ok=True)
        page.screenshot(path=str(shots/f'admin-{width}.png'),full_page=True)
        page.locator('#runs-user-filter').select_option(users[0]['id'])
        expect(page.locator('#runs-status')).to_contain_text('共 1 个任务')
        expect(page.locator('.run-owner')).to_have_text('admin')
        expect(page.locator('[data-run-action=copy]')).to_have_count(1)
        expect(page.locator('.production-run .run-rename')).to_have_count(1)
        page.locator('#runs-user-filter').select_option('')
        expect(page.locator('#runs-status')).to_contain_text('共 14 个任务')
        assert not errors,errors
    finally:
        context.close()


def test_ordinary_user_has_no_filter_or_other_records(browser,accounts_clients):
    _, users, (_,alice,_) = accounts_clients
    seed(users[1]);seed(users[2])
    context=browser.new_context()
    page=context.new_page()
    def route(r):
        req=r.request
        response=alice.request(req.method,req.url,content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length','cookie')})
        r.fulfill(status=response.status_code,body=response.content,
            headers={k:v for k,v in response.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    try:
        page.goto('http://testserver/#tasks')
        expect(page.locator('#runs-status')).to_contain_text('共 1 个任务')
        expect(page.locator('#runs-user-controls')).to_be_hidden()
        expect(page.locator('.run-name-text')).to_have_text('alice0')
        expect(page.locator('[data-run-action=copy]')).to_have_count(1)
    finally:
        context.close()
