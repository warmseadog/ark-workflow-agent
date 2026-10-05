"""Visible link entry imports into a real user's draft without a paid downloader."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from app import media
from tests.media_fixtures import video_bytes
from tests.test_access_control import protected
from tests.test_account_frontend import browser
from tests.test_mira_integration import mira_browser


@pytest.mark.parametrize('width', [390, 1440])
def test_link_entry_is_visible_and_imports_without_an_extra_toggle(mira_browser, monkeypatch, width):
    page, errors, calls = mira_browser
    downloads = []
    failing = True

    def download(text, destination, settings):
        downloads.append(text)
        if failing:
            raise ValueError('视频暂时无法导入，请重试。')
        destination.write_bytes(video_bytes())
        return destination

    monkeypatch.setattr(media, 'download_video', download)
    page.set_viewport_size({'width': width, 'height': 1000})
    page.goto('http://testserver/login')
    page.locator('#open-login').click()
    page.locator('#login-username').fill('mira-user')
    page.locator('#login-password').fill('test-password-42')
    page.locator('#login-submit').click()
    expect(page).to_have_url('http://testserver/')
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    source = page.get_by_role('textbox', name='参考视频链接', exact=True)
    button = page.get_by_role('button', name='导入视频', exact=True)
    expect(source).to_be_visible()
    expect(button).to_be_visible()
    expect(button).to_be_disabled()
    assert source.bounding_box()['y'] < page.locator('#source-upload-requirement').bounding_box()['y']
    assert page.locator('#link-settings').is_hidden()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    output = Path(__file__).resolve().parents[1] / 'exports/link-import-preview'
    output.mkdir(parents=True, exist_ok=True)
    page.locator('#flow-stage-source').screenshot(path=str(output / f'link-import-{width}.png'))

    share = '复制打开 https://v.douyin.com/example/ 查看参考视频'
    source.fill(share)
    expect(button).to_be_enabled()
    expect(page.locator('#video-link-status')).to_contain_text('已识别')
    assert downloads == [], 'Typing only identifies the link; import requires confirmation.'
    button.click()
    expect(page.locator('#video-link-status')).to_contain_text('请重试')
    expect(source).to_have_value(share)
    expect(button).to_be_enabled()
    failing = False
    source.press('Enter')
    expect(page.locator('#source-file-name')).to_contain_text('链接视频')
    expect(source).to_have_value('')
    expect(source).to_be_visible()
    expect(page.locator('#video-reference-preview .video-cover')).to_be_visible()
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    assert downloads == [share, share]
    assert any(method == 'POST' and path == '/api/production/assets/import' and status == 200
               for method, path, status in calls)
    page.reload()
    expect(page.locator('#video-reference-preview .video-cover')).to_be_visible()
    expect(source).to_be_visible()
    page.locator('#remove-source-video').click()
    expect(source).to_be_visible()
    page.locator('#studio-source-video').set_input_files(
        {'name': 'local-reference.mp4', 'mimeType': 'video/mp4', 'buffer': video_bytes()})
    expect(page.locator('#source-file-name')).to_contain_text('local-reference.mp4')
    expect(source).to_be_visible()
    assert not errors
