"""Browser coverage for admin configuration without altering saved user settings."""
import json
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright, expect

BASE = 'http://127.0.0.1:8000'
OUT = Path('storage/admin-qa')
OUT.mkdir(exist_ok=True)

def check(width, height):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        page = browser.new_page(viewport={'width': width, 'height': height}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE + '/admin/settings')
        expect(page.locator('#overview-tikhub')).to_have_text('密钥已保存')
        expect(page.locator('#overview-storage')).not_to_have_text('读取中…')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.screenshot(path=str(OUT / f'overview-{width}.png'), full_page=True)
        page.locator('nav a[href="#storage"]').click()
        form = page.locator('#tos-form')
        expect(form.locator('[name=region]')).to_be_enabled()
        form.locator('[name=region]').fill('cn-shanghai')
        form.locator('[name=bucket]').click()
        expect(form.locator('[name=endpoint]')).to_have_value('https://tos-cn-shanghai.volces.com')
        captured = []
        def handle_storage(route):
            req = route.request
            if req.method == 'GET':
                route.continue_()
                return
            payload = req.post_data_json
            captured.append(payload)
            if req.url.endswith('/test'):
                route.fulfill(json={'ok':False,'message':'测试：请检查 Bucket 权限'})
            else:
                config = {k:v for k,v in payload.items() if k not in {'access_key','secret_key','clear_credentials'}}
                route.fulfill(json={'config':{**config, 'has_access_key':True, 'has_secret_key':True,'ready':True,'message':''}})
        page.route('**/api/storage-settings**',handle_storage)
        form.locator('[name=bucket]').fill('ui-fixture-bucket')
        form.locator('[name=access_key]').fill('ui-fixture-ak')
        form.locator('[name=secret_key]').fill('ui-fixture-sk')
        form.locator('[name=enabled]').check()
        form.locator('button[type=submit]').click()
        expect(page.locator('#tos-status')).to_contain_text('已保存并启用')
        assert captured[0]['bucket'] == 'ui-fixture-bucket' and captured[0]['enabled'] is True
        expect(form.locator('[name=secret_key]')).to_have_value('')
        page.locator('#test-tos').click()
        expect(page.locator('#tos-status')).to_contain_text('测试：请检查')
        page.screenshot(path=str(OUT / f'storage-{width}.png'), full_page=True)
        page.locator('nav a[href="#tikhub"]').click()
        expect(page.locator('#tikhub-key-status')).to_have_text('已保存')
        page.route('**/api/link-settings/test',lambda route:route.fulfill(json={'ok':True,'message':'测试：账户鉴权通过'}))
        page.locator('#test-tikhub').click()
        expect(page.locator('#tikhub-status')).to_contain_text('账户鉴权通过')
        page.locator('nav a[href="#prompts"]').click()
        expect(page.locator('#prompt-form fieldset')).to_be_enabled()
        options = page.locator('#admin-template-select option')
        if options.count() > 1:
            page.locator('#admin-template-select').select_option(index=1)
            expect(page.locator('#prompt-form [name=content]')).not_to_have_value('')
        page.locator('nav a[href="#model"]').click()
        expect(page.locator('#model-settings-fields')).to_be_enabled()
        expect(page.locator('#model-settings [name=model]')).to_be_visible()
        page.screenshot(path=str(OUT / f'model-{width}.png'),full_page=True)
        page.locator('nav a[href="#redaction"]').click()
        expect(page.locator('#redaction-form fieldset')).to_be_enabled()
        expect(page.locator('#redaction-service-form fieldset')).to_be_enabled()
        page.locator('nav a[href="#system"]').click()
        expect(page.locator('#system-info')).to_contain_text('存储目录')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.goto(BASE + '/')
        expect(page.locator('a.admin-entry')).to_be_visible()
        expect(page.locator('#video-url-entry')).to_be_visible()
        page.locator('#link-settings > summary').click()
        expect(page.locator('#test-tikhub-key')).to_be_visible()
        page.locator('#test-tikhub-key').click()
        expect(page.locator('#link-settings-status')).to_contain_text('账户鉴权通过')
        page.screenshot(path=str(OUT / f'production-{width}.png'),full_page=True)
        assert not errors, errors
        print(json.dumps({'viewport':width,'page_errors':errors,'ok':True}))
        browser.close()

if __name__ == '__main__':
    check(1440, 1000)
    check(390, 844)
