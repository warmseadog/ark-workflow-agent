"""Offline portrait UI contract; every request is intercepted, including image files."""
import copy
from pathlib import Path
from urllib.parse import urlsplit

from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import expect, sync_playwright

from browser_asset_editor import png

ROOT = Path(__file__).resolve().parents[1]
CONFIG = dict(provider='ark', protocol='ark', mode='mock', base_url='https://example.test',
              public_base_url='', model='fixture', duration=5, fps=0, resolution='720p')


def check(width=1440):
    html = Environment(loader=FileSystemLoader(ROOT / 'app/templates')).get_template('production.html').render()
    portrait = dict(project_name='default', use_storage_credentials=False, has_credentials=False,
                    has_access_key=False, has_secret_key=False)
    draft = dict(id='d1', name='测试草稿', revision=0, source_asset_id=None, face_asset_ids=[],
                 clothing_asset_ids=[], prompt='', mask={}, model=CONFIG.copy(), assets=[])
    assets, uploads, puts, errors = {}, [], [], []
    switches = dict(download_failure=False, assets_failure=False, hold_import=False)
    pending_import = []
    official = dict(id='official', name='官方人物.png', kind='face', mime='image/png',
                    url='/api/production/assets/official/file',
                    portrait=dict(remote_asset_id='remote-1', group_id='group-1', project='default', status='Active'))
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        context = browser.new_context(viewport={'width': width, 'height': 844})
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        def handle(route):
            request, path = route.request, urlsplit(route.request.url).path
            body = request.post_data_json if request.method in ('POST', 'PUT') and 'application/json' in request.headers.get('content-type', '') else {}
            if path == '/': route.fulfill(content_type='text/html', body=html)
            elif path.startswith('/static/'): route.fulfill(path=str(ROOT / 'app' / path.lstrip('/')))
            elif path == '/api/model-settings': route.fulfill(json={'config': dict(CONFIG, has_api_key=False, status='demo'), 'presets': {'ark': {'models': []}}})
            elif path == '/api/prompt-templates': route.fulfill(json={'items': []})
            elif path == '/api/link-settings': route.fulfill(json={'has_api_key': False})
            elif path == '/api/production/runs':
                assert request.method == 'GET', 'Never submit generation in portrait tests'
                route.fulfill(json={'items': []})
            elif path == '/api/production/drafts': route.fulfill(json={'items': [draft]})
            elif path == '/api/production/drafts/d1':
                if request.method == 'PUT':
                    draft.update(body); draft['revision'] += 1
                    draft['assets'] = [assets[aid] for aid in draft['face_asset_ids']]
                route.fulfill(json=copy.deepcopy(draft))
            elif path == '/api/production/assets':
                aid = 'local-' + str(len(uploads))
                name = 'local.png' if not uploads else 'supplement.png'
                assets[aid] = dict(id=aid, name=name, kind='face', mime='image/png', url=f'/api/production/assets/{aid}/file')
                uploads.append(aid); route.fulfill(json=assets[aid])
            elif path.startswith('/api/production/assets/'):
                if 'official' in path and switches['download_failure']: route.abort()
                else: route.fulfill(body=png('fixture.png', (160, 100, 80))['buffer'], content_type='image/png')
            elif path == '/api/portrait/config':
                if request.method == 'PUT':
                    puts.append(body)
                    portrait.update(project_name=body['project_name'], use_storage_credentials=body['use_storage_credentials'])
                    portrait['has_credentials'] = not body.get('clear_credentials', False)
                    portrait['has_access_key'] = portrait['has_secret_key'] = portrait['has_credentials']
                route.fulfill(json=portrait)
            elif path == '/api/portrait/test': route.fulfill(json={'ok': True, 'message': '连接成功（只读查询）'})
            elif path == '/api/portrait/qr':
                if body['url'] != 'https://ark.volcengine.com/invitation?token=fixture-only':
                    route.fulfill(status=400, json={'detail': '请使用官方平台提供的邀约链接'}); return
                if width > 400:
                    route.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M0 0h64v64H0z" fill="white"/><path d="M8 8h48v48H8z"/></svg>', content_type='image/svg+xml')
                else: route.fulfill(body=png('qr.png', (50, 70, 90))['buffer'], content_type='image/png')
            elif path == '/api/portrait/assets':
                if switches['assets_failure']: route.fulfill(status=502, json={'detail': '权限不足，请检查项目和 AK/SK 权限'})
                else: route.fulfill(json={'items': [{'id': 'remote-1', 'name': '<img onerror=alert(1)>人物', 'group_id': 'group-1', 'status': 'Active', 'asset_type': 'Image'}]})
            elif path == '/api/portrait/import':
                assert body == {'remote_asset_id': 'remote-1'}
                assets['official'] = official
                if switches['hold_import']: pending_import.append(route)
                else: route.fulfill(json=official)
            else: route.fulfill(status=404, json={'detail': path})

        context.route('**/*', handle)
        page.goto('http://127.0.0.1:18747/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('.draft-toolbar')).to_have_count(0)
        expect(page.get_by_role('button', name='真人认证', exact=True)).to_be_visible()
        page.locator('#studio-face-image').set_input_files([png('local.png', (110, 120, 130)), png('supplement.png', (140, 150, 160))])
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('.portrait-badge')).to_have_count(0)
        page.get_by_role('button', name='真人认证', exact=True).click()
        expect(page.locator('#portrait-dialog')).to_be_visible()
        expect(page.locator('#portrait-dialog')).to_contain_text('请在火山官方平台完成本人认证和授权')
        expect(page.locator('#portrait-settings')).not_to_have_attribute('open', '')
        assert page.locator('#portrait-console').get_attribute('href').startswith('https://console.volcengine.com/ark/')
        page.locator('#portrait-invitation').fill('https://untrusted.example/invitation')
        page.locator('#portrait-qr-create').click()
        expect(page.locator('#portrait-invite-status')).to_contain_text('请使用官方平台')
        expect(page.locator('#portrait-qr-image')).not_to_be_visible()
        page.locator('#portrait-invitation').fill('https://ark.volcengine.com/invitation?token=fixture-only')
        page.locator('#portrait-qr-create').click()
        expect(page.locator('#portrait-qr-image')).to_be_visible()
        expect(page.locator('#portrait-qr-status')).to_contain_text('不代表认证完成')
        page.keyboard.press('Escape')
        expect(page.locator('#portrait-dialog')).not_to_be_visible()
        assert page.locator('#portrait-qr-image').get_attribute('src') is None
        assert 'fixture-only' not in page.evaluate('JSON.stringify(localStorage)')
        page.get_by_role('button', name='已授权人物', exact=True).click()
        expect(page.locator('#portrait-invitation')).to_have_value('')
        page.locator('#portrait-settings > summary').click()
        page.locator('#portrait-access-key').fill('fixture-ak')
        page.locator('#portrait-secret-key').fill('fixture-sk')
        page.locator('#portrait-save').click()
        expect(page.locator('#portrait-settings-status')).to_contain_text('已保存')
        expect(page.locator('#portrait-access-key')).to_have_value('')
        expect(page.locator('#portrait-secret-key')).to_have_value('')
        assert puts[-1]['access_key'] == 'fixture-ak'
        page.locator('#portrait-save').click()
        expect(page.locator('#portrait-save')).to_be_enabled()
        assert puts[-1]['secret_key'] == '' and not puts[-1].get('clear_credentials')
        page.locator('#portrait-test').click()
        expect(page.locator('#portrait-settings-status')).to_contain_text('连接成功')
        page.locator('#portrait-reuse-storage').check()
        page.locator('#portrait-save').click()
        expect(page.locator('#portrait-save')).to_be_enabled()
        assert puts[-1]['use_storage_credentials'] is True
        page.locator('#portrait-settings > summary').click()
        page.locator('#portrait-refresh').click()
        expect(page.locator('[data-portrait-import]')).to_have_count(1)
        expect(page.locator('#portrait-assets img')).to_have_count(0)
        switches['hold_import'] = True
        page.locator('[data-portrait-import]').click()
        expect(page.locator('#portrait-assets-status')).to_contain_text('正在导入')
        page.locator('#portrait-close').click()
        assert pending_import
        pending_import.pop().fulfill(json=official)
        page.get_by_role('button', name='已授权人物', exact=True).click()
        expect(page.locator('[data-portrait-import]')).to_have_count(1)
        assert page.locator('#studio-face-image').evaluate('(el)=>[...el.files].map(f=>f.name)') == ['local.png', 'supplement.png']
        switches['hold_import'] = False
        switches['download_failure'] = True
        page.locator('[data-portrait-import]').click()
        expect(page.locator('#portrait-assets-status')).to_contain_text('原人物图片已保留')
        assert page.locator('#studio-face-image').evaluate('(el)=>[...el.files].map(f=>f.name)') == ['local.png', 'supplement.png']
        switches['download_failure'] = False
        page.locator('[data-portrait-import]').click()
        expect(page.locator('#portrait-dialog')).not_to_be_visible()
        expect(page.locator('.portrait-badge')).to_have_count(1)
        assert draft['face_asset_ids'] == ['official', 'local-1']
        assert len(uploads) == 2, 'Official imported file must not be uploaded again'
        page.reload()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('.portrait-badge')).to_have_count(1)
        assert page.locator('#studio-face-image').evaluate('(el)=>[...el.files].map(f=>f.name)') == ['官方人物.png', 'supplement.png']
        page.get_by_role('button', name='已授权人物', exact=True).click()
        switches['assets_failure'] = True
        page.locator('#portrait-refresh').click()
        expect(page.locator('#portrait-assets-status')).to_contain_text('权限不足')
        expect(page.locator('[data-portrait-import]')).to_have_count(0)
        bounds = page.locator('#portrait-dialog').bounding_box()
        assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width + 1
        assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= 845
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        page.locator('#portrait-settings > summary').click()
        page.locator('#portrait-clear').click()
        expect(page.locator('#portrait-settings-status')).to_contain_text('真人凭据已清除')
        assert puts[-1]['clear_credentials'] is True
        assert puts[-1]['use_storage_credentials'] is False
        page.locator('#portrait-refresh').click()
        expect(page.locator('#portrait-assets-status')).to_contain_text('请先展开')
        assert not errors, errors
        browser.close()
    print(f'PASS portrait {width}px: QR privacy, config, safe import, metadata persistence, failure preservation, mobile bounds')


if __name__ == '__main__':
    check()
    check(390)
