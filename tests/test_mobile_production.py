"""Mobile flow regressions through the real templates and isolated local API."""
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from app import main, generation_settings, model_catalog
from tests.test_account_frontend import browser
from tests.test_person_video import setup


@pytest.mark.parametrize('width,height', [(320,844), (375,844), (390,844), (430,844), (768,844), (667,375), (740,360), (844,390), (932,430), (1024,768)])
def test_mobile_flow_fits_and_preserves_optional_scene(browser, setup, width, height):
    generation_settings.save_config(main.settings, {'model': model_catalog.SD25, 'duration': 8})
    rows = model_catalog.catalog(main.settings)['items']
    for row in rows:
        if row['id'] == model_catalog.SD25:
            row.update(enabled=True, verified=True)
    model_catalog.save_catalog(main.settings, {'items': rows})
    context = browser.new_context(viewport={'width': width, 'height': height}, is_mobile=True, has_touch=True)
    page = context.new_page()
    page.emulate_media(reduced_motion='reduce')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(r):
        req = r.request
        if urlparse(req.url).path == '/api/auth/me':
            r.fulfill(content_type='application/json', body=json.dumps({'auth_enabled': False, 'user': None}))
            return
        res = setup.request(req.method, req.url, content=req.post_data_buffer,
                            headers={k: v for k, v in req.headers.items() if k not in ('host', 'content-length')})
        r.fulfill(status=res.status_code, body=res.content,
                  headers={k: v for k, v in res.headers.items() if k not in ('content-length', 'content-encoding', 'transfer-encoding')})

    page.route('**/*', route)
    try:
        page.goto('http://127.0.0.1:18759/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('#generation-readiness')).to_have_text('还缺参考视频')
        expect(page.locator('#material-progress')).to_have_count(0)
        expect(page.locator('#clear-reference-images')).to_be_hidden()
        expect(page.locator('#studio-generate-submit')).to_be_disabled()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        for selector in ('#video-picker', '#clothing-picker', '#face-picker'):
            assert page.locator(selector).bounding_box()['height'] <= 120, selector
        assert page.locator('#person-picker>summary').bounding_box()['height'] >= 44
        button = page.locator('#studio-generate-submit').bounding_box()
        assert 0 <= button['x'] and button['x'] + button['width'] <= width
        assert button['y'] + button['height'] <= height
        if width > height:
            assert page.locator('.generation-dock').bounding_box()['height'] <= 96
            expect(page.locator('.workspace-toggle')).to_be_visible()
            assert page.locator('.workspace-sidebar').evaluate('(el) => el.inert')
        shots = Path(__file__).resolve().parents[1] / 'storage/mobile-review'
        shots.mkdir(exist_ok=True)
        page.screenshot(path=str(shots / f'overview-{width}.png'))
        if width == 390:
            page.screenshot(path=str(shots / 'full-page-390.png'), full_page=True)
        page.locator('#generation-fix').click()
        expect(page.locator('#flow-stage-source')).to_be_focused()
        scene = page.locator('#scene-references')
        assert scene.evaluate('(el)=>el.tagName') == 'ARTICLE'
        expect(page.locator('.asset-grid>.asset-entry, .optional-reference-group>.reference-module')).to_have_count(6)
        expect(page.locator('.optional-reference-group>details')).to_have_count(0)
        expect(page.locator('#scene-custom-fields')).to_be_visible()
        page.locator('#scene-enabled').check()
        page.locator('#scene-description').fill('自然光，浅色背景')
        expect(page.locator('#scene-reference-state')).to_contain_text('文字描述')
        page.locator('#scene-enabled').uncheck()
        expect(page.locator('#scene-reference-state')).to_have_text('保留原视频场景')
        expect(page.locator('#scene-custom-fields')).to_be_visible()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.reload()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('#scene-custom-fields')).to_be_visible()
        page.locator('#scene-enabled').check()
        expect(page.locator('#scene-description')).to_have_value('自然光，浅色背景')
        page.locator('#generation-dock-summary').click()
        expect(page.locator('#generation-settings')).to_be_focused()
        page.locator('#generation-settings').scroll_into_view_if_needed()
        assert page.locator('.generation-action').evaluate('(el) => el.scrollWidth <= el.clientWidth')
        expect(page.locator('.generation-dock')).to_be_visible()
        button = page.locator('#studio-generate-submit').bounding_box()
        assert button['y'] + button['height'] <= height
        page.screenshot(path=str(shots / f'settings-{width}.png'))
        page.locator('.workspace-toggle').click()
        expect(page.locator('.generation-dock')).to_be_hidden()
        page.keyboard.press('Escape')
        expect(page.locator('.generation-dock')).to_be_visible()
        page.locator('.production-tasks-shortcut').click()
        expect(page.locator('.production-runs')).to_be_visible()
        expect(page.locator('.generation-dock')).to_be_hidden()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if width > height:
            page.locator('.workspace-toggle').click()
            page.locator('[data-workspace-page=create]').click()
            page.set_viewport_size({'width': height, 'height': width})
            expect(page.locator('#studio-generate-form')).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            expect(page.locator('#scene-picker')).to_be_visible()
            expect(page.locator('#scene-description')).to_have_value('自然光，浅色背景')
            page.set_viewport_size({'width': width, 'height': height})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            expect(page.locator('.workspace-toggle')).to_be_visible()
            expect(page.locator('.generation-dock')).to_be_visible()
        assert not errors, errors
    finally:
        context.close()
