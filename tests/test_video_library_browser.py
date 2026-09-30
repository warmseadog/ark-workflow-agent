"""Real browser + local API: covers must not request video bytes before a click."""
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_video_library import library


@pytest.mark.parametrize('width', [1440, 390])
def test_library_lazy_playback_and_workspace_navigation(browser, library, width):
    client, store, runs, root = library
    source = root / 'outputs' / (runs[0]['id'] + '.mp4')
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=s=180x320:d=2', '-y', str(source)], check=True)
    for run in runs[1:3]:
        shutil.copyfile(source, root / 'outputs' / (run['id'] + '.mp4'))
    draft = store.create_draft({'name': '竖屏视频'})
    for index in range(12):
        run = store.create_run(draft['id'], 1, 'gallery-' + str(index), {})
        store.update_run(run['id'], status='succeeded')
        shutil.copyfile(source, root / 'outputs' / (run['id'] + '.mp4'))
    context = browser.new_context(viewport={'width': width, 'height': 900})
    page = context.new_page()
    requests, errors = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))

    def route(r):
        req = r.request
        requests.append(req.url)
        if urlparse(req.url).path == '/api/auth/me':
            r.fulfill(content_type='application/json', body=json.dumps({'auth_enabled': True, 'user': {'id': 'preview', 'username': 'admin', 'role': 'admin'}, 'csrf_token': 'preview'}))
            return
        response = client.request(req.method, req.url, content=req.post_data_buffer,
                                  headers={k: v for k, v in req.headers.items() if k not in ('host', 'content-length')})
        r.fulfill(status=response.status_code, body=response.content,
                  headers={k: v for k, v in response.headers.items() if k not in ('content-length', 'content-encoding', 'transfer-encoding')})

    page.route('**/*', route)
    try:
        page.goto('http://testserver/videos')
        expect(page.locator('.library-card')).to_have_count(12)
        assert page.locator('video[src]').count() == 0
        assert not any('/download' in url or '/playback' in url for url in requests)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        shots = Path(__file__).resolve().parents[1] / 'storage' / 'workspace-preview'
        shots.mkdir(exist_ok=True)
        page.screenshot(path=str(shots / f'videos-{width}.png'))
        page.locator('.library-cover').first.click()
        expect(page.locator('#library-player')).to_be_visible()
        page.wait_for_function('document.getElementById("library-video").readyState >= 2')
        first_src = page.locator('#library-video').get_attribute('src')
        page.locator('#library-next').click()
        page.wait_for_function('(src)=>document.getElementById("library-video").getAttribute("src") !== src && document.getElementById("library-video").hasAttribute("src")', arg=first_src)
        assert page.locator('video[src]').count() == 1
        page.locator('#library-close').click()
        expect(page.locator('#library-video')).not_to_have_attribute('src')
        page.locator('#library-more').scroll_into_view_if_needed()
        expect(page.locator('.library-card')).to_have_count(15)
        expect(page.locator('#library-end')).to_be_visible()
        page.goto('http://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.screenshot(path=str(shots / f'production-{width}.png'))
        if width < 800:
            page.locator('.workspace-toggle').click()
        page.locator('[data-workspace-page=tasks]').click()
        expect(page.locator('.production-runs')).to_be_visible()
        expect(page.locator('#studio-generate-form')).not_to_be_visible()
        if width < 800:
            page.locator('.workspace-toggle').click()
        page.locator('.workspace-account summary').click()
        expect(page.get_by_role('link', name='修改密码')).to_be_visible()
        expect(page.get_by_role('button', name='退出登录')).to_be_visible()
        page.keyboard.press('Escape')
        if width < 800:
            page.locator('.workspace-toggle').click()
        page.locator('[data-workspace-page=create]').click()
        expect(page.locator('#studio-generate-form')).to_be_visible()
        assert not errors, errors
    finally:
        context.close()
