"""A pending video import permits editing other materials without losing state."""
from pathlib import Path
import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_prompt_privacy_delegation import users
from tests.test_prompt_privacy_browser import route_client
from tests.test_production_api import complete_draft
from tests.media_fixtures import image_bytes, video_bytes


@pytest.mark.parametrize('width', [390, 1440])
def test_pending_import_local_lock_animation_editing_failure_and_navigation(browser, users, width):
    _, _, (_, client, _) = users
    draft=complete_draft(client)
    imported=client.post('/api/production/assets',data={'kind':'video'},
        files={'file':('imported.mp4',video_bytes(),'video/mp4')}).json()
    context=browser.new_context(viewport={'width':width,'height':1000})
    page=context.new_page();errors,calls=route_client(page,client)
    pending=[]; inspections=[]
    page.route('**/api/production/assets/import',lambda route:pending.append(route))
    page.route('**/api/video-link/inspect',lambda route:inspections.append(route))
    try:
        page.goto('https://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.get_by_role('tab',name='链接导入',exact=True).click()
        page.locator('#source-video-url').fill('https://v.douyin.com/example/')
        expect(page.locator('#video-link-status')).to_contain_text('正在识别链接')
        page.locator('#confirm-video-url').click()
        expect(page.locator('#studio-clothing-image')).to_be_enabled()
        expect(page.locator('#studio-face-image')).to_be_enabled()
        expect(page.get_by_role('tab',name='人物视频',exact=True)).to_be_enabled()
        expect(page.locator('#model-settings-form select').first).to_be_enabled()
        expect(page.locator('#video-import-loading')).to_be_visible()
        expect(page.locator('#video-import-loading')).to_contain_text('可继续准备其他素材')
        assert page.locator('.video-import-spinner').evaluate('(el)=>getComputedStyle(el).animationName')!='none'
        for selector in ('#source-video-url','#studio-source-video','#replace-source-video','#remove-source-video','#confirm-video-url','#source-local-tab','#studio-generate-submit'):
            expect(page.locator(selector)).to_be_disabled()
        assert len(pending)==1
        page.locator('#studio-generate-form').dispatch_event('submit')
        assert not any(method=='POST' and path=='/api/production/runs' for method,path in calls)
        picture={'name':'extra.png','mimeType':'image/png','buffer':image_bytes(color=(170,80,40))}
        page.locator('#studio-clothing-image').set_input_files(picture)
        expect(page.locator('#clothing-reference-preview img')).to_have_count(2)
        page.locator('#scene-description').fill('清晨的自然光')
        page.get_by_role('tab',name='人物视频',exact=True).click()
        page.get_by_role('tab',name='人物图片',exact=True).click()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        saved=client.get('/api/production/drafts/'+draft['id']).json()
        assert len(saved['clothing_asset_ids'])==2 and saved['scene_description']=='清晨的自然光'
        assert saved['source_asset_id']==draft['source_asset_id']
        dialogs=[]
        def dismiss(dialog):
            dialogs.append(dialog.type);dialog.dismiss()
        page.on('dialog',dismiss)
        page.evaluate("document.querySelector('.workspace-nav a[href=\"/videos\"]').click()")
        expect(page).to_have_url('https://testserver/')
        assert dialogs==['beforeunload']
        expect(page.locator('#video-import-loading')).to_be_visible()
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        output=Path(__file__).resolve().parents[1]/'exports/link-loading-preview';output.mkdir(parents=True,exist_ok=True)
        page.locator('#flow-stage-source').screenshot(path=str(output/f'loading-{width}.png'))
        pending.pop().fulfill(status=422,json={'detail':'导入失败，请重试。'})
        expect(page.locator('#video-import-loading')).to_be_hidden()
        expect(page.locator('#video-link-status')).to_contain_text('导入失败')
        assert len(inspections)==1
        inspections.pop().fulfill(status=200,json={'platform':'douyin','label':'抖音','configured':True})
        expect(page.locator('#video-link-status')).to_contain_text('导入失败')
        expect(page.locator('#source-video-url')).to_be_enabled()
        expect(page.locator('#source-video-url')).to_have_value('https://v.douyin.com/example/')
        assert client.get('/api/production/drafts/'+draft['id']).json()['source_asset_id']==draft['source_asset_id']
        page.locator('#confirm-video-url').click()
        expect(page.locator('#video-import-loading')).to_be_visible()
        assert len(pending)==1
        pending.pop().fulfill(status=200,json=imported)
        expect(page.locator('#video-import-loading')).to_be_hidden()
        expect(page.locator('#video-reference-preview .video-cover')).to_be_visible()
        expect(page.locator('#source-local-tab')).to_be_enabled()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        saved=client.get('/api/production/drafts/'+draft['id']).json()
        assert saved['source_asset_id']==imported['id']
        assert len(saved['clothing_asset_ids'])==2 and saved['scene_description']=='清晨的自然光'
        dialogs.clear();page.reload()
        expect(page.locator('#video-reference-preview .video-cover')).to_be_visible()
        assert not dialogs and not errors
    finally:
        for route in pending+inspections: route.abort()
        context.close()
