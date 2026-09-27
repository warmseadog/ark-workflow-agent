"""Browser regression: confirmation imports playable video; no paid API calls."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import imageio_ffmpeg
import requests
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
storage = Path(tempfile.mkdtemp(prefix='browser-import-', dir=ROOT / 'storage'))
fixture = storage / 'reference.mp4'
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-v', 'error', '-f', 'lavfi',
                '-i', 'color=c=blue:s=160x120:d=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-movflags', '+faststart', str(fixture)], check=True)
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
            errors, imports, jobs = [], [], []
            page.on('pageerror', lambda error: errors.append(str(error)))
            def import_video(route):
                imports.append(route.request.post_data_json['text'])
                route.fulfill(status=200, content_type='video/mp4', body=fixture.read_bytes(),
                              headers={'Content-Disposition': 'attachment; filename="reference.mp4"'})
            page.route('**/api/video-link/import', import_video)
            def prepare_video(route):
                jobs.append(route.request.post_data_buffer)
                route.fulfill(status=200, content_type='application/json', body=json.dumps({
                    'id': 'test-preview', 'status': 'defaced', 'defaced_url': '/fixture.mp4'}))
            page.route('**/api/jobs', prepare_video)
            page.route('**/fixture.mp4', lambda route: route.fulfill(content_type='video/mp4', body=fixture.read_bytes()))
            page.goto(base)
            page.locator('#toggle-video-url').click()
            source = page.get_by_label('参考视频链接', exact=True)
            button = page.get_by_role('button', name='确认并加载视频', exact=True)
            expect(button).to_be_disabled()
            share = '复制打开 https://v.douyin.com/example/ 看视频'
            source.fill(share)
            expect(page.locator('#video-link-status')).to_contain_text('已识别')
            assert not imports, 'Typing must not trigger a paid download'
            button.click()
            video = page.locator('#video-reference-preview video')
            expect(video).to_be_visible()
            page.wait_for_function('document.querySelector("#video-reference-preview video")?.readyState >= 2')
            assert imports == [share]
            assert page.locator('#studio-source-video').evaluate('(el) => el.files[0].size') == fixture.stat().st_size
            expect(source).to_have_value('')
            expect(page.locator('#video-link-status')).to_contain_text('已加载')
            page.locator('#redaction-settings > summary').click()
            page.locator('#studio-preview-submit').click()
            expect(page.locator('#studio-preview-status')).to_contain_text('预览已就绪')
            assert len(jobs) == 1 and b'filename="reference.mp4"' in jobs[0]
            assert share.encode() not in jobs[0], 'Generation must reuse the imported file, not download the link again'
            page.locator('#redaction-settings > summary').click()
            page.locator('#remove-source-video').click()
            expect(video).to_have_count(0)
            page.locator('#toggle-video-url').click()
            source.fill(share)
            page.unroute('**/api/video-link/import')
            page.route('**/api/video-link/import', lambda route: route.fulfill(status=422, json={'detail': 'TikHub 余额不足'}))
            button.click()
            expect(page.locator('#video-link-status')).to_contain_text('余额不足')
            # Wait past the 450 ms identification debounce: it must not erase an import error.
            page.wait_for_timeout(700)
            expect(page.locator('#video-link-status')).to_contain_text('余额不足')
            expect(button).to_be_enabled()
            expect(source).to_have_value(share)
            expect(video).to_have_count(0)
            page.unroute('**/api/video-link/import')
            page.route('**/api/video-link/import', import_video)
            button.click()
            expect(video).to_be_visible()
            page.wait_for_function('document.querySelector("#video-reference-preview video")?.readyState >= 2')
            assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
            page.screenshot(path=str(storage / f'video-import-{width}.png'), full_page=True)
            assert not errors, errors
            context.close()
        browser.close()
    print('PASS: desktop/mobile confirmation, playable video, upload reuse, remove, failure, retry, no JS errors/overflow.')
    print('Screenshots:', storage)
finally:
    server.terminate()
    server.wait(timeout=15)
    log.close()
