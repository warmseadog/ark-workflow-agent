from urllib.parse import urlparse
import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_person_video import setup


@pytest.mark.parametrize('width', [390, 1440])
def test_switching_profiles_preserves_values_and_enforces_cloud_limits(browser, setup, width, tmp_path):
    context = browser.new_context(viewport={'width': width, 'height': 1000})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def route(r):
        req = r.request
        if urlparse(req.url).path == '/api/auth/me':
            r.fulfill(json={'auth_enabled': False, 'user': None}); return
        res = setup.request(req.method, req.url, content=req.post_data_buffer,
                            headers={k: v for k, v in req.headers.items() if k not in ('host', 'content-length')})
        r.fulfill(status=res.status_code, body=res.content,
                  headers={k: v for k, v in res.headers.items() if k not in ('content-length', 'content-encoding', 'transfer-encoding')})
    page.route('**/*', route)
    try:
        page.goto('http://127.0.0.1:18759/admin/settings#redaction')
        local = page.locator('#redaction-form')
        cloud = page.locator('#mediakit-form')
        service = page.locator('#redaction-service-form')
        expect(local.locator('fieldset')).to_be_enabled()
        local.locator('[name=mask_scale]').fill('3')
        local.locator('[name=mosaic_size]').fill('60')
        local.locator('button').click()
        expect(page.locator('#redaction-status')).to_contain_text('已保存')
        service.locator('[name=mode]').select_option('http')
        service.locator('[name=endpoint]').fill('https://mediakit.cn-beijing.volces.com')
        expect(cloud).to_be_visible()
        expect(local).to_be_hidden()
        cloud.locator('[name=face_box_expand]').fill('1.1')
        assert not cloud.evaluate('(form) => form.checkValidity()')
        cloud.locator('[name=face_box_expand]').fill('0')
        assert not cloud.evaluate('(form) => form.checkValidity()')
        cloud.locator('[name=face_box_expand]').fill('1')
        cloud.locator('[name=mask_strength]').select_option('high')
        cloud.locator('button').click()
        expect(page.locator('#mediakit-status')).to_contain_text('已保存')
        service.locator('button[type=submit]').click()
        expect(page.locator('#redaction-service-status')).to_contain_text('已设置')
        page.reload()
        expect(cloud).to_be_visible()
        expect(cloud.locator('[name=face_box_expand]')).to_have_value('1')
        expect(cloud.locator('[name=mask_strength]')).to_have_value('high')
        (tmp_path / f'mediakit-{width}.png').write_bytes(page.screenshot(full_page=True))
        service.locator('[name=mode]').select_option('local')
        expect(local).to_be_visible()
        expect(local.locator('[name=mask_scale]')).to_have_value('3')
        expect(local.locator('[name=mosaic_size]')).to_have_value('60')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors
    finally:
        context.close()


@pytest.mark.parametrize('width', [390, 1440])
def test_parameter_ranges_report_immediately_and_block_requests(browser, setup, width, tmp_path):
    context = browser.new_context(viewport={'width': width, 'height': 1000})
    page = context.new_page()
    writes, errors = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def route(r):
        req = r.request
        path = urlparse(req.url).path
        if path == '/api/auth/me':
            r.fulfill(json={'auth_enabled': False, 'user': None}); return
        if req.method in ('PUT', 'POST') and path in ('/api/redaction-settings', '/api/admin/redaction-tests'):
            writes.append(path)
        res = setup.request(req.method, req.url, content=req.post_data_buffer,
                            headers={k: v for k, v in req.headers.items() if k not in ('host', 'content-length')})
        r.fulfill(status=res.status_code, body=res.content,
                  headers={k: v for k, v in res.headers.items() if k not in ('content-length', 'content-encoding', 'transfer-encoding')})
    page.route('**/*', route)
    try:
        page.goto('http://127.0.0.1:18759/admin/settings#redaction')
        local = page.locator('#redaction-form')
        expect(local.locator('fieldset')).to_be_enabled()
        local.locator('summary').click()
        cases = [
            ('mask_scale', ['3.1', '0', '-1', ''], '3', '最大 3'),
            ('mosaic_size', ['101', '3', '4.5', ''], '100', '4～100'),
            ('threshold', ['2', '0', '-0.1', ''], '1', '最大 1'),
        ]
        for name, invalid, valid, message in cases:
            field = local.locator(f'[name={name}]')
            for value in invalid:
                field.fill(value)
                expect(field).to_have_attribute('aria-invalid', 'true')
                expect(page.locator('#' + field.get_attribute('aria-describedby'))).to_contain_text(message)
                local.locator('button[type=submit]').click()
                assert not writes
            field.fill(valid)
            expect(field).not_to_have_attribute('aria-invalid', 'true')
        local.locator('button[type=submit]').click()
        expect(page.locator('#redaction-status')).to_contain_text('已保存')
        assert writes == ['/api/redaction-settings']
        writes.clear()
        service = page.locator('#redaction-service-form')
        service.locator('[name=mode]').select_option('http')
        service.locator('[name=endpoint]').fill('https://mediakit.cn-beijing.volces.com')
        cloud = page.locator('#mediakit-form')
        for name, invalid, valid, message in [
            ('face_box_expand', ['1.1', '0', '-0.1', '1e-20', ''], '1', '最大 1'),
            ('face_confidence', ['2', '0.09', ''], '0.1', '0.1～1'),
        ]:
            field = cloud.locator(f'[name={name}]')
            for value in invalid:
                field.fill(value)
                expect(field).to_have_attribute('aria-invalid', 'true')
                expect(page.locator('#' + field.get_attribute('aria-describedby'))).to_contain_text(message)
                cloud.locator('button[type=submit]').click()
                assert not writes
            field.fill(valid)
            expect(field).not_to_have_attribute('aria-invalid', 'true')
        cloud.locator('[name=face_confidence]').fill('2')
        page.locator('#redaction-test-video').set_input_files(
            {'name': 'sample.mp4', 'mimeType': 'video/mp4', 'buffer': b'unused-invalid-parameters'})
        page.locator('#start-redaction-test').click()
        assert not writes
        cloud.scroll_into_view_if_needed()
        (tmp_path / f'parameter-range-{width}.png').write_bytes(page.screenshot(full_page=True))
        cloud.locator('[name=face_confidence]').fill('1')
        cloud.locator('button[type=submit]').click()
        expect(page.locator('#mediakit-status')).to_contain_text('已保存')
        assert writes == ['/api/redaction-settings']
        assert setup.get('/api/redaction-settings').json()['profiles']['mediakit']['face_confidence'] == 1
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors
    finally:
        context.close()
