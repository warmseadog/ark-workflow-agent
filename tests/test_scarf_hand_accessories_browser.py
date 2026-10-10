"""Real editor upload/restore/disable and legacy-template coverage."""
import json
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_production_api import client
from tests.media_fixtures import image_bytes


@pytest.mark.parametrize('width', [1440, 390])
def test_scarf_hand_editor_upload_restore_and_legacy(browser, client, width):
    context = browser.new_context(viewport={'width': width, 'height': 1000})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(r):
        req = r.request
        if urlparse(req.url).path == '/api/auth/me':
            r.fulfill(content_type='application/json', body=json.dumps({'auth_enabled': False, 'user': None}))
            return
        response = client.request(req.method, req.url, content=req.post_data_buffer,
                                  headers={k: v for k, v in req.headers.items() if k not in ('host', 'content-length')})
        r.fulfill(status=response.status_code, body=response.content,
                  headers={k: v for k, v in response.headers.items() if k not in ('content-length', 'content-encoding', 'transfer-encoding')})

    page.route('**/*', route)
    try:
        page.goto('http://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.locator('#flow-stage-generation').evaluate('el=>el.open=true')
        expect(page.locator('#scarf-add')).to_be_visible()
        expect(page.locator('#hand_jewelry-add')).to_be_visible()
        picture = {'name': 'ref.png', 'mimeType': 'image/png', 'buffer': image_bytes()}
        page.locator('#studio-face-image').set_input_files(picture)
        page.locator('#studio-clothing-image').set_input_files(picture)
        final = page.locator('#final-generation-prompt')
        expect(final).to_have_value(re.compile('围巾来源：穿搭参考 @Image2'))
        for kind in ('scarf', 'hand_jewelry'):
            with page.expect_file_chooser() as chooser:
                page.locator('#'+kind+'-add').click()
            chooser.value.set_files(picture)
        expect(final).to_have_value(re.compile(r'围巾来源：@Image3[\s\S]*手饰来源：@Image4'))
        expect(page.locator('#hand_jewelry-references')).to_contain_text('手链、手镯、戒指')
        # The existing compact-home design hides optional switches. Exercise the
        # underlying change contract without altering unrelated presentation.
        page.locator('#scarf-enabled').evaluate("el=>{el.checked=false;el.dispatchEvent(new Event('change',{bubbles:true}));}")
        expect(final).to_have_value(re.compile(r'围巾来源：穿搭参考 @Image2[\s\S]*手饰来源：@Image3'))
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.reload()
        page.locator('#flow-stage-generation').evaluate('el=>el.open=true')
        expect(page.locator('#scarf-enabled')).not_to_be_checked()
        expect(page.locator('#hand_jewelry-enabled')).to_be_checked()
        expect(page.locator('#scarf-reference-preview img')).to_have_count(1)
        expect(final).to_have_value(re.compile('手饰来源：@Image3'))
        page.get_by_role('button', name='默认提示词2', exact=True).click()
        legacy = page.locator('#generation-prompt')
        expect(legacy).to_have_value(re.compile('围巾来源：衣服参考图及补充图'))
        expect(legacy).to_have_value(re.compile('@Image3 手饰参考：'))
        assert 'undefined' not in legacy.input_value()
        assert not errors, errors
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally:
        context.close()
