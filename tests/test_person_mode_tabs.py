"""Person image/video tabs retain the existing material and draft behavior."""
import pytest
from pathlib import Path
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_prompt_privacy_delegation import users
from tests.test_prompt_privacy_browser import route_client
from tests.media_fixtures import image_bytes, video_bytes as small_video
from tests.test_person_video import video_bytes


@pytest.mark.parametrize('width', [390, 844, 1440])
def test_person_tabs_switch_keep_assets_and_restore_mode(browser, users, width, tmp_path):
    _, _, (_, client, _) = users
    context=browser.new_context(viewport={'width':width,'height':1000})
    page=context.new_page()
    errors, calls=route_client(page,client)
    try:
        page.goto('https://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        pictures=page.get_by_role('tab',name='人物图片',exact=True)
        videos=page.get_by_role('tab',name='人物视频',exact=True)
        expect(pictures).to_have_attribute('aria-selected','true')
        expect(videos).to_be_visible()
        assert page.get_by_text('改用人物图片',exact=True).count()==0
        assert page.get_by_text('改用人物视频',exact=True).count()==0
        page.locator('#studio-face-image').set_input_files({'name':'person.png','mimeType':'image/png','buffer':image_bytes()})
        expect(page.locator('#face-reference-preview img')).to_be_visible()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        videos.click()
        expect(page.locator('#person-image-panel')).to_be_hidden()
        page.locator('#person-video-file').set_input_files({'name':'small.mp4','mimeType':'video/mp4','buffer':small_video()})
        expect(page.locator('#person-video-status')).to_contain_text('尺寸不符合')
        expect(page.locator('#person-video-status')).to_be_visible()
        page.locator('#person-video-file').set_input_files({'name':'person.mp4','mimeType':'video/mp4','buffer':video_bytes(tmp_path)})
        expect(page.locator('#person-video-panel .video-cover')).to_be_visible()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        videos.press('ArrowLeft')
        expect(pictures).to_be_focused()
        expect(pictures).to_have_attribute('aria-selected','true')
        expect(page.locator('#face-reference-preview img')).to_be_visible()
        pictures.press('ArrowRight')
        expect(videos).to_have_attribute('aria-selected','true')
        expect(page.locator('#person-video-panel .video-cover')).to_be_visible()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.reload()
        expect(videos).to_have_attribute('aria-selected','true')
        expect(page.locator('#person-video-panel .video-cover')).to_be_visible()
        page.locator('#person-picker > summary').click()
        expect(page.locator('#person-search')).to_be_visible()
        page.keyboard.press('Escape')
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        output=Path(__file__).resolve().parents[1]/'exports/person-tabs-preview'
        output.mkdir(parents=True,exist_ok=True)
        page.locator('.asset-grid').screenshot(path=str(output/f'person-tabs-{width}.png'))
        assert not errors,errors
    finally:
        context.close()
