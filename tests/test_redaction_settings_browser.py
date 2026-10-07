"""Save the remaining masking controls through the actual admin page."""
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect

from tests.test_account_frontend import browser
from tests.test_person_video import setup


@pytest.mark.parametrize('width', [390, 1440])
def test_face_only_settings_save_and_reload(browser, setup, width, tmp_path):
    context = browser.new_context(viewport={'width': width, 'height': 1000})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(r):
        req = r.request
        if urlparse(req.url).path == '/api/auth/me':
            r.fulfill(json={'auth_enabled': False, 'user': None})
            return
        res = setup.request(req.method, req.url, content=req.post_data_buffer,
                            headers={k: v for k, v in req.headers.items() if k not in ('host', 'content-length')})
        r.fulfill(status=res.status_code, body=res.content,
                  headers={k: v for k, v in res.headers.items()
                           if k not in ('content-length', 'content-encoding', 'transfer-encoding')})

    page.route('**/*', route)
    try:
        page.goto('http://127.0.0.1:18759/admin/settings#redaction')
        form = page.locator('#redaction-form')
        expect(form.locator('fieldset')).to_be_enabled()
        expect(form.get_by_text('遮挡目标', exact=False)).to_have_count(0)
        expect(form.locator('select[name=mask_mode]')).to_have_count(0)
        form.locator('[name=mask_scale]').fill('1.8')
        form.locator('[name=blur_style]').select_option('blur')
        form.locator('button[type=submit]').click()
        expect(page.locator('#redaction-status')).to_contain_text('已保存')
        saved = setup.get('/api/redaction-settings').json()['config']
        assert saved['mask_mode'] == 'face'
        assert saved['mask_scale'] == 1.8 and saved['blur_style'] == 'blur'
        page.reload()
        expect(form.locator('[name=mask_scale]')).to_have_value('1.8')
        expect(form.locator('[name=blur_style]')).to_have_value('blur')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        (tmp_path/f'redaction-{width}.png').write_bytes(page.screenshot(full_page=True))
        assert not errors, errors
    finally:
        context.close()
