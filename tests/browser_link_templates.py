"""Isolated real-server browser checks; no TikHub calls or user data writes."""
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
storage = Path(tempfile.mkdtemp(prefix='browser-links-', dir=ROOT / 'storage'))
with socket.socket() as sock:
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
base = f'http://127.0.0.1:{port}'
env = {**os.environ, 'STORAGE_DIR': str(storage), 'DATABASE_URL': f'sqlite:///{(storage / "studio.db").as_posix()}', 'TIKHUB_API_KEY': ''}
log = (storage / 'server.log').open('w', encoding='utf-8')
server = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
                          cwd=ROOT, env=env, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW)
try:
    for _ in range(100):
        try:
            if requests.get(base + '/healthz', timeout=1).ok:
                break
        except requests.RequestException:
            pass
        time.sleep(.1)
    else:
        raise RuntimeError('Test server failed to start')
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel='msedge')
        for width, height in [(1440, 1000), (390, 844)]:
            context = browser.new_context(viewport={'width': width, 'height': height})
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base)
            page.locator('#flow-stage-generation > summary').click()
            expect(page.locator('#prompt-template-list button')).to_have_count(4)
            page.locator('#prompt-template-name').fill('未保存草稿')
            page.locator('#generation-prompt').fill('不能静默丢失的内容')
            dialogs = []
            def accept_switch(dialog):
                dialogs.append(dialog.message)
                dialog.accept()
            page.once('dialog', accept_switch)
            page.locator('#prompt-template-list button').first.click()
            assert len(dialogs) == 1, 'Applying a template must warn before replacing a new draft'
            assert '保持' in page.locator('#generation-prompt').input_value()
            page.locator('#prompt-template-name').fill('测试模板 ' + str(width))
            page.locator('#generation-prompt').fill('测试保存内容 @Video1 @Image1 @Image2')
            page.locator('#template-copy').click()
            expect(page.locator('#prompt-template-list button')).to_have_count(5)
            page.reload()
            page.locator('#flow-stage-generation > summary').click()
            expect(page.locator('#prompt-template-name')).to_have_value('测试模板 ' + str(width))
            expect(page.locator('#generation-prompt')).to_have_value('测试保存内容 @Video1 @Image1 @Image2')
            page.locator('#prompt-template-name').fill('修改模板 ' + str(width))
            page.locator('#generation-prompt').fill('修改后内容')
            page.locator('#template-save').click()
            expect(page.locator('#prompt-template-status')).to_contain_text('已保存到本机')
            other = context.new_page()
            other.goto(base)
            other.locator('#flow-stage-generation > summary').click()
            other.get_by_role('button', name='修改模板 ' + str(width), exact=True).click()
            expect(other.locator('#generation-prompt')).to_have_value('修改后内容')
            other.close()
            page.locator('#template-delete').click()
            page.locator('#template-delete-cancel').click()
            expect(page.locator('#prompt-template-list button')).to_have_count(5)
            page.locator('#template-delete').click()
            page.locator('#template-delete-accept').click()
            expect(page.locator('#prompt-template-list button')).to_have_count(4)
            page.reload()
            page.locator('#flow-stage-generation > summary').click()
            expect(page.locator('#prompt-template-list button')).to_have_count(4)

            page.locator('#toggle-video-url').click()
            for url, label in [('https://v.douyin.com/abc/', '抖音'), ('https://xhslink.cn/abc', '小红书'),
                               ('https://v.kuaishou.com/abc', '快手'), ('https://b23.tv/abc', 'B 站')]:
                page.get_by_label('参考视频链接', exact=True).fill('复制打开，看看作品「' + url + '」。')
                expect(page.locator('#video-link-status')).to_contain_text('已识别' + label, timeout=5000)
            page.locator('#link-settings summary').click()
            page.locator('#tikhub-api-key').fill('fake-local-test-only')
            page.locator('#save-tikhub-key').click()
            expect(page.locator('#link-key-state')).to_have_text('已配置')
            expect(page.locator('#tikhub-api-key')).to_have_value('')
            assert 'fake-local-test-only' not in page.content()
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(storage / f'link-templates-{width}.png'), full_page=True)
            page.on('dialog', lambda dialog: dialog.accept())
            page.locator('#clear-tikhub-key').click()
            expect(page.locator('#link-key-state')).to_have_text('待配置')
            assert not errors, errors
            context.close()
        browser.close()
    print('PASS: desktop/mobile template CRUD, reload persistence, all four share links, key save/clear, no overflow or JS errors.')
    print('Screenshots:', storage)
finally:
    server.terminate()
    server.wait(timeout=15)
    log.close()
