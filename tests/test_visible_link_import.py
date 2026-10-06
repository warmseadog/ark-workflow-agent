"""Visible link entry imports into a real user's draft without a paid downloader."""
from pathlib import Path

import pytest
from playwright.sync_api import expect

from app import media
from tests.media_fixtures import video_bytes
from tests.test_access_control import protected
from tests.test_account_frontend import browser
from tests.test_mira_integration import mira_browser


@pytest.mark.parametrize('width', [390, 844, 1440])
def test_source_tabs_preserve_video_until_replacement_succeeds(mira_browser, monkeypatch, width):
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
    source = page.locator('#source-video-url')
    button = page.get_by_role('button', name='导入视频', exact=True)
    local_tab = page.get_by_role('tab', name='本地上传', exact=True)
    link_tab = page.get_by_role('tab', name='链接导入', exact=True)
    expect(local_tab).to_have_attribute('aria-selected', 'true')
    expect(source).to_be_hidden()
    output = Path(__file__).resolve().parents[1] / 'exports/compact-home-preview'
    output.mkdir(parents=True, exist_ok=True)
    expect(page.locator('.production-header')).not_to_contain_text('制作流程')
    cards = page.locator('.asset-grid>.asset-entry,.optional-reference-group>.reference-module')
    assert cards.count() == 6
    if width > 720:
        assert len({round(card.bounding_box()['x']) for card in cards.all()}) == 3
        assert page.locator('#video-picker').bounding_box()['height'] <= 180
    page.screenshot(path=str(output / f'empty-{width}.png'), full_page=True)
    page.locator('#studio-source-video').set_input_files(
        {'name': 'original.mp4', 'mimeType': 'video/mp4', 'buffer': video_bytes()})
    expect(page.locator('#video-reference-preview .video-cover')).to_be_visible()
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    original = page.locator('#video-reference-preview video').get_attribute('data-media-source')
    link_tab.click()
    expect(source).to_be_visible()
    expect(button).to_be_visible()
    expect(button).to_be_disabled()
    expect(page.locator('#source-upload-requirement')).to_be_hidden()
    expect(page.locator('#video-picker')).to_be_hidden()
    assert page.locator('#link-settings').is_hidden()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.locator('#flow-stage-source').screenshot(path=str(output / f'link-import-{width}.png'))

    share = '复制打开 https://v.douyin.com/example/ 查看参考视频'
    source.fill(share)
    local_tab.click()
    expect(page.locator('#video-reference-preview video')).to_have_attribute('data-media-source', original)
    expect(page.locator('#video-reference-preview .video-cover')).to_be_visible()
    link_tab.click()
    expect(source).to_have_value(share)
    expect(button).to_be_enabled()
    expect(page.locator('#video-link-status')).to_contain_text('已识别')
    assert downloads == [], 'Typing only identifies the link; import requires confirmation.'
    button.click()
    expect(page.locator('#video-link-status')).to_contain_text('请重试')
    expect(source).to_have_value(share)
    expect(page.locator('#video-reference-preview video')).to_have_attribute('data-media-source', original)
    expect(button).to_be_enabled()
    failing = False
    source.press('Enter')
    expect(page.locator('#video-reference-preview video')).not_to_have_attribute('data-media-source', original)
    expect(source).to_have_value('')
    expect(source).to_be_hidden()
    expect(local_tab).to_have_attribute('aria-selected', 'true')
    expect(page.locator('#video-reference-preview .video-cover')).to_be_visible()
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    assert downloads == [share, share]
    assert any(method == 'POST' and path == '/api/production/assets/import' and status == 200
               for method, path, status in calls)
    page.reload()
    expect(page.locator('#video-reference-preview .video-cover')).to_be_visible()
    expect(source).to_be_hidden()
    page.locator('#remove-source-video').click()
    expect(source).to_be_hidden()
    page.locator('#studio-source-video').set_input_files(
        {'name': 'local-reference.mp4', 'mimeType': 'video/mp4', 'buffer': video_bytes()})
    expect(page.locator('#video-reference-preview .video-cover')).to_be_visible()
    expect(source).to_be_hidden()
    from io import BytesIO
    from PIL import Image
    picture = BytesIO()
    Image.new('RGB', (300, 1500), 'navy').save(picture, format='PNG')
    page.locator('#studio-clothing-image').set_input_files(
        {'name': 'tall.png', 'mimeType': 'image/png', 'buffer': picture.getvalue()})
    expect(page.locator('#clothing-reference-preview img')).to_be_visible()
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    assert page.locator('#clothing-picker').bounding_box()['height'] <= 180
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.screenshot(path=str(output / f'populated-{width}.png'), full_page=True)
    assert not errors
