"""The actual settings form manages shared templates for a non-legacy admin."""
import pytest
from playwright.sync_api import expect

from tests.test_account_frontend import browser
from tests.test_access_control import protected, accounts_clients
from tests.test_template_permissions import login


@pytest.mark.parametrize('width', [1440, 390])
def test_secondary_admin_shared_template_form(browser, accounts_clients, width):
    accounts, users, (legacy, alice, bob) = accounts_clients
    accounts.update_user(users[1]['id'], users[0]['id'], role='admin')
    login(alice, 'alice', 'changed-user-password')
    personal = alice.post('/api/prompt-templates', json={'name': 'Private', 'content': 'private content'}).json()
    context = browser.new_context(viewport={'width': width, 'height': 950})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def route(r):
        req = r.request
        response = alice.request(req.method, req.url, content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length','cookie')})
        r.fulfill(status=response.status_code, body=response.content,
            headers={k:v for k,v in response.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*', route)
    page.on('dialog', lambda dialog: dialog.accept())
    try:
        page.goto('http://testserver/admin/settings#prompts')
        form = page.locator('#prompt-form')
        expect(form.locator('fieldset')).to_be_enabled()
        expect(page.locator('#admin-template-select option[value="'+personal['id']+'"]')).to_have_count(0)
        form.locator('[name=name]').fill('Shared browser template')
        form.locator('[name=content]').fill('shared browser content')
        form.locator('button[type=submit]').click()
        expect(page.locator('#prompt-status')).to_contain_text('模板已保存')
        ident = page.locator('#admin-template-select').input_value()
        assert ident
        shared = next(x for x in bob.get('/api/prompt-templates').json()['items'] if x['id']=='system:'+ident)
        assert shared['read_only'] and shared['content']=='shared browser content'
        form.locator('[name=content]').fill('updated shared content')
        form.locator('button[type=submit]').click()
        expect(form.locator('fieldset')).to_be_enabled()
        assert next(x for x in bob.get('/api/prompt-templates').json()['items'] if x['id']=='system:'+ident)['content']=='updated shared content'
        page.locator('#delete-template').click()
        expect(page.locator('#prompt-status')).to_contain_text('模板已删除')
        assert all(x['id']!='system:'+ident for x in bob.get('/api/prompt-templates').json()['items'])
        assert any(x['id']==personal['id'] for x in alice.get('/api/prompt-templates').json()['items'])
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors, errors
    finally:
        context.close()
