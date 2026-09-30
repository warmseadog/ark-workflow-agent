import json
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import pytest
from PIL import Image
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_person_video import setup, video_bytes
from tests.test_asset_preview import removed_video


@pytest.mark.parametrize('width', [1440, 390])
def test_removed_upload_plays_but_cannot_submit_and_replacement_recovers(browser, setup, tmp_path, width):
    removed_video(setup, tmp_path)
    content = (tmp_path/'fixture.mp4').read_bytes()
    context = browser.new_context(viewport={'width':width, 'height':1000})
    page = context.new_page()
    calls, errors = [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    def route(r):
        req = r.request; calls.append(urlparse(req.url).path)
        if urlparse(req.url).path == '/api/auth/me':
            r.fulfill(content_type='application/json', body=json.dumps({'auth_enabled':False, 'user':None}))
            return
        response = setup.request(req.method, req.url, content=req.post_data_buffer,
                                 headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=response.status_code, body=response.content,
                  headers={k:v for k,v in response.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*', route)
    try:
        page.goto('http://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.locator('[data-person-media=video]').click()
        page.locator('#person-video-file').set_input_files({'name':'removed.mp4', 'mimeType':'video/mp4', 'buffer':content})
        expect(page.locator('#person-video-status')).to_contain_text('不可用于生成')
        assert not any(p.endswith('/preview') for p in calls)
        cover = page.locator('#person-video-panel [data-video-cover]')
        expect(cover.locator('img')).to_be_visible()
        cover.click()
        expect(page.locator('#reference-video-dialog')).to_be_visible()
        page.wait_for_function('document.getElementById("reference-video-player").readyState >= 2')
        assert '/preview' in page.locator('#reference-video-player').get_attribute('src')
        page.locator('#reference-video-close').click()
        expect(page.locator('#reference-video-player')).not_to_have_attribute('src')
        expect(page.locator('#studio-generate-submit')).to_be_disabled()
        page.locator('#person-video-choose').click()
        assert page.locator('#person-picker').evaluate('e=>e.open')
        page.locator('#person-picker > summary').click()
        # A different, not-yet-registered virtual input can proceed to normal generation checks.
        replacement = video_bytes(tmp_path, seconds=4)
        page.locator('#person-video-file').set_input_files({'name':'new.mp4','mimeType':'video/mp4','buffer':replacement})
        expect(page.locator('#person-video-status')).to_contain_text('尚未入库检查')
        expect(page.locator('#person-video-choose')).to_be_hidden()
        source = video_bytes(tmp_path, seconds=7)
        page.locator('input[name=video]').set_input_files({'name':'source.mp4','mimeType':'video/mp4','buffer':source})
        picture=BytesIO(); Image.new('RGB',(400,400),'blue').save(picture,format='PNG')
        page.locator('#studio-clothing-image').set_input_files({'name':'clothes.png','mimeType':'image/png','buffer':picture.getvalue()})
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        assert not errors, errors
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        shots=Path(__file__).resolve().parents[1]/'storage/asset-preview-browser'; shots.mkdir(exist_ok=True)
        page.screenshot(path=str(shots/f'preview-{width}.png'))
    finally:
        context.close()
