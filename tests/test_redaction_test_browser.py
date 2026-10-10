from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_person_video import setup
from tests.test_redaction_test import video


@pytest.mark.parametrize('width', [390, 1440])
def test_upload_unsaved_cloud_parameters_and_preview(browser, setup, video, width, tmp_path):
    context = browser.new_context(viewport={'width': width, 'height': 1000})
    page = context.new_page()
    calls, errors = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    def route(r):
        req, path = r.request, urlparse(r.request.url).path
        if path == '/api/auth/me':
            r.fulfill(json={'auth_enabled': False, 'user': None}); return
        if path == '/api/admin/redaction-tests':
            calls.append(req.post_data_buffer)
            r.fulfill(json={'id': 'a' * 32, 'status': 'queued'}); return
        if path == '/api/admin/redaction-tests/' + 'a' * 32:
            r.fulfill(json={'id': 'a' * 32, 'status': 'succeeded', 'profile': 'mediakit',
                           'values': {'mask_mode': 'blur', 'mask_strength': 'high', 'face_box_expand': 0.8, 'face_confidence': 0.35},
                           'message': '打码完成', 'output_url': '/test-result.mp4'}); return
        if path == '/test-result.mp4':
            r.fulfill(body=video, content_type='video/mp4'); return
        res = setup.request(req.method, req.url, content=req.post_data_buffer,
                            headers={k: v for k, v in req.headers.items() if k not in ('host', 'content-length')})
        r.fulfill(status=res.status_code, body=res.content,
                  headers={k: v for k, v in res.headers.items() if k not in ('content-length', 'content-encoding', 'transfer-encoding')})
    page.route('**/*', route)
    try:
        page.goto('http://127.0.0.1:18759/admin/settings#redaction')
        cloud = page.locator('#mediakit-form')
        service = page.locator('#redaction-service-form')
        expect(service.locator('fieldset')).to_be_enabled()
        service.locator('[name=mode]').select_option('http')
        service.locator('[name=endpoint]').fill('https://mediakit.cn-beijing.volces.com')
        cloud.locator('[name=mask_mode]').select_option('blur')
        cloud.locator('[name=mask_strength]').select_option('high')
        cloud.locator('[name=face_box_expand]').fill('0.8')
        page.locator('#redaction-test-video').set_input_files({'name': 'local.mp4', 'mimeType': 'video/mp4', 'buffer': video})
        page.locator('#start-redaction-test').click()
        expect(page.locator('#redaction-test-status')).to_contain_text('打码完成')
        expect(page.locator('#redaction-test-output')).to_have_attribute('src', '/test-result.mp4')
        expect(page.locator('#redaction-test-original')).to_have_attribute('src', __import__('re').compile('blob:'))
        assert b'"mask_mode":"blur"' in calls[0] and b'"face_box_expand":0.8' in calls[0]
        assert setup.get('/api/redaction-settings').json()['profiles']['mediakit']['mask_mode'] == 'mosaic'
        expect(page.locator('#redaction-test-summary')).to_contain_text('blur')
        page.wait_for_function("document.getElementById('redaction-test-output').readyState >= 2")
        assert page.locator('#redaction-test-output').evaluate('(video) => video.duration') > 0
        page.locator('#redaction-test-card').scroll_into_view_if_needed()
        (tmp_path / f'redaction-test-{width}.png').write_bytes(page.screenshot(full_page=True))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors
    finally:
        context.close()
