"""Exercise the real admin forms and library after removing redundant UI."""
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_person_video import setup


@pytest.mark.parametrize('width,height', [(1440,1000), (390,844)])
def test_settings_forms_and_library(browser, setup, width, height):
    context = browser.new_context(viewport={'width':width,'height':height})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def route(r):
        req = r.request
        if urlparse(req.url).path == '/api/auth/me':
            r.fulfill(json={'auth_enabled':False,'user':None})
            return
        res = setup.request(req.method, req.url, content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=res.status_code, body=res.content,
            headers={k:v for k,v in res.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*', route)
    try:
        page.goto('http://127.0.0.1:18759/admin/settings#redaction')
        form = page.locator('#redaction-service-form')
        expect(form.locator('fieldset')).to_be_enabled()
        expect(form.locator('[name=mode]')).to_have_value('local')
        expect(page.locator('#redaction-api-fields')).to_be_hidden()
        form.locator('[name=mode]').select_option('http')
        form.locator('[name=endpoint]').fill('https://mask.example/process')
        form.locator('[name=api_key]').fill('ui-test-secret')
        form.locator('button[type=submit]').click()
        expect(page.locator('#redaction-service-status')).to_contain_text('已使用外部 API')
        expect(form.locator('[name=api_key]')).to_have_value('')
        expect(page.locator('#redaction-key-status')).to_have_text('已保存')
        page.reload()
        expect(form.locator('[name=mode]')).to_have_value('http')
        expect(form.locator('[name=endpoint]')).to_have_value('https://mask.example/process')
        expect(page.locator('#redaction-defaults')).to_have_count(0)
        shots = Path('storage/settings-review')
        shots.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(shots/f'redaction-{width}.png'), full_page=True)
        form.locator('[name=mode]').select_option('local')
        form.locator('button[type=submit]').click()
        expect(page.locator('#redaction-service-status')).to_contain_text('已使用本地打码')
        for section in ('overview','model','tikhub','storage','prompts','system','people'):
            page.evaluate('(section) => location.hash = section', section)
            expect(page.locator('#section-'+section)).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), section
            if section == 'model':
                expect(page.locator('#model-settings-fields')).to_be_enabled()
                expect(page.locator('#model-settings [name=model]')).to_be_visible()
                expect(page.locator('#model-settings .model-advanced')).not_to_have_attribute('open','')
                expect(page.locator('#model-catalog-form')).to_be_hidden()
                expect(page.locator('#admin-intro')).to_have_count(0)
                page.screenshot(path=str(shots/f'model-{width}.png'), full_page=True)
        page.locator('#manage-people').click()
        dialog = page.locator('.virtual-library-dialog')
        expect(dialog).to_be_visible()
        expect(dialog.locator('[data-show-import], [data-more], [data-import]')).to_have_count(0)
        dialog.locator('[data-library-type=LivenessFace]').click()
        expect(dialog.locator('[data-new]')).to_be_hidden()
        dialog.locator('[data-library-type=AIGC]').click()
        expect(dialog.locator('[data-new]')).to_be_visible()
        dialog.locator('[data-new]').click()
        expect(dialog.locator('[data-create]')).to_be_visible()
        page.screenshot(path=str(shots/f'library-{width}.png'), full_page=True)
        assert not errors, errors
    finally:
        context.close()
