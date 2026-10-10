"""Scheduling controls call the real router against isolated persistent settings."""
from pathlib import Path
from playwright.sync_api import expect
import pytest

from tests.test_account_frontend import browser
from tests.test_scheduling_api import api
from app import scheduling_settings


@pytest.mark.parametrize('width', [1440, 390])
def test_super_admin_save_and_admin_readonly_use_real_api(browser, api, width):
    client, cfg = api
    context = browser.new_context(viewport={'width': width, 'height': 900})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    role = ['super_admin']
    def route(request):
        req = request.request
        response = client.request(req.method, req.url, content=req.post_data_buffer,
                                  headers={'X-Test-Role': role[0], 'Content-Type': 'application/json'})
        request.fulfill(status=response.status_code, body=response.content,
                        content_type='application/json')
    page.route('**/api/admin/scheduling', route)
    try:
        page.route('http://testserver/', lambda route: route.fulfill(
            content_type='text/html', body='<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body><main><div class="content"><section id="section-overview"></section></div></main></body></html>'))
        def mount():
            page.goto('http://testserver/')
            page.add_style_tag(path=str(Path('app/static/admin-settings.css').resolve()))
            page.add_script_tag(path=str(Path('app/static/scheduling-settings.js').resolve()))
        mount()
        control = page.locator('#scheduling-limit')
        expect(control).to_be_enabled()
        expect(control).to_have_value('4')
        control.fill('2')
        page.locator('#scheduling-settings button[type=submit]').click()
        expect(page.locator('[data-scheduling-note]')).to_contain_text('已保存')
        assert scheduling_settings.load_config(cfg)['global_concurrency'] == 2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        mount()
        expect(control).to_have_value('2')
        role[0] = 'admin'
        mount()
        expect(control).to_be_disabled()
        expect(page.locator('[data-scheduling-capacity]')).to_contain_text('仅超级管理员')
        expect(page.locator('[data-scheduling-usage]')).to_contain_text('当前可用名额 2')
        assert not errors, errors
    finally:
        context.close()
