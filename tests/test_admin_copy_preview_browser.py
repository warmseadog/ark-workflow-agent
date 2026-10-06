from urllib.parse import urlparse

from playwright.sync_api import expect
import pytest

from tests.test_account_frontend import browser
from tests.test_prompt_privacy_delegation import users
from tests.test_prompt_privacy_browser import route_client
from tests.test_admin_task_copy import setup_copy
from tests.test_tenant_portrait_selection import portraits
from tests.test_production_api import complete_draft


@pytest.mark.parametrize('delegated', [False, True])
def test_admin_copies_from_task_list_and_generates_in_own_session(browser, users, delegated):
    source, destination, run, _ = setup_copy(users)
    _, (_, target, actor), (_, alice, operator) = users
    context = browser.new_context(viewport={'width':1440, 'height':1000})
    page = context.new_page()
    errors, requests = route_client(page, operator)
    try:
        url = 'https://testserver/#tasks'
        if delegated:
            restored = operator.post('/api/admin/task-records/'+target['id']+'/'+run['id']+'/restore-draft',
                                     json={'idempotency_key':'old-editor'}).json()
            url = 'https://testserver/?delegate_user='+target['id']+'&draft='+restored['id']+'#tasks'
        page.goto(url)
        row = page.locator('[data-run-id="'+run['id']+'"]')
        row.locator('.run-menu summary').click()
        row.get_by_role('button', name='复制到我的草稿', exact=True).click()
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        expect(page.locator('body')).to_have_attribute('data-workspace-view', 'create')
        assert 'delegate_user=' not in page.url
        assert page.evaluate('window.currentAccount.id') == actor['id']
        assert page.locator('#delegated-editor-banner').count() == 0
        with page.expect_response(lambda r:r.request.method=='POST' and urlparse(r.url).path=='/api/production/runs') as submitted:
            page.locator('#studio-generate-submit').click()
        assert submitted.value.status == 200, submitted.value.text()
        assert destination.get_run(submitted.value.json()['id'])['status'] == 'queued'
        assert len(source.list_runs()) == 1
        assert alice.get('/api/auth/me').json()['user']['id'] == target['id']
        assert not any(path in {'/api/auth/login','/api/auth/logout'} for _,path in requests)
        assert errors == []
    finally:
        context.close()


@pytest.mark.parametrize('width', [390, 1440])
def test_editor_images_show_original_and_hide_file_names(browser, users, width, tmp_path):
    _, _, run, _ = setup_copy(users)
    context = browser.new_context(viewport={'width':width, 'height':844})
    page = context.new_page()
    errors, _ = route_client(page, users[2][1])
    try:
        page.goto('https://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        for filename in ('source.mp4', 'face.png', 'dress.png'):
            expect(page.get_by_text(filename, exact=True)).to_have_count(0)
        picture = page.locator('#clothing-reference-preview img')
        picture.click()
        viewer = page.get_by_role('dialog', name='查看图片')
        expect(viewer).to_be_visible()
        expect(viewer.locator('img')).to_have_attribute('src', '/api/production/assets/'+run['snapshot']['clothing_asset_ids'][0]+'/file')
        expect(viewer.locator('[role=status]')).to_have_text('')
        assert viewer.locator('img').evaluate('(image) => image.naturalWidth > 0')
        # Return bytes from the browser before writing so the artifact inherits
        # the test directory permissions instead of browser process ACLs.
        (tmp_path/'preview.png').write_bytes(page.screenshot())
        page.keyboard.press('Escape')
        expect(viewer).not_to_be_visible()
        expect(picture).to_be_focused()
        picture.press('Enter')
        expect(viewer).to_be_visible()
        viewer.get_by_role('button', name='关闭图片').click()
        expect(viewer).not_to_be_visible()
        expect(page.locator('#clothing-reference-preview').get_by_role('button', name='替换第 1 张图片')).to_be_enabled()
        assert errors == []
    finally:
        context.close()


def test_admin_task_detail_image_preview_uses_authorized_original(browser, users):
    _, _, run, _ = setup_copy(users)
    target = users[1][1]
    context = browser.new_context()
    page = context.new_page()
    errors, _ = route_client(page, users[2][2])
    try:
        page.goto('https://testserver/#tasks')
        row = page.locator('[data-run-id="'+run['id']+'"]')
        row.locator('.run-menu summary').click()
        row.locator('[data-run-action=details]').click()
        row.locator('[data-source-kind=face] img').click()
        viewer = page.get_by_role('dialog', name='查看图片')
        expect(viewer).to_be_visible()
        url = '/api/admin/task-records/'+target['id']+'/'+run['id']+'/assets/'+run['snapshot']['face_asset_ids'][0]+'/file'
        expect(viewer.locator('img')).to_have_attribute('src', url)
        expect(viewer.locator('[role=status]')).to_have_text('')
        assert errors == []
    finally:
        context.close()


def test_person_photo_dialog_zoom_does_not_select_or_block_use(browser, portraits):
    owner = portraits.owners['bob']
    complete_draft(owner.client)
    context = browser.new_context(viewport={'width':390, 'height':844})
    page = context.new_page()
    errors, requests = route_client(page, owner.client)
    try:
        page.goto('https://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.locator('#person-picker summary').click()
        page.locator('.person-option[data-person-id="'+owner.person['id']+'"]').click()
        card = page.locator('.person-photo-card').first
        card.locator('img').click()
        viewer = page.get_by_role('dialog', name='查看图片')
        expect(viewer).to_be_visible()
        assert not any(method=='POST' and path.endswith('/use') for method,path in requests)
        viewer.get_by_role('button', name='关闭图片').click()
        expect(page.locator('#person-photos-dialog')).to_be_visible()
        card.locator('[data-photo-use]').click()
        expect(page.locator('#person-photos-dialog')).not_to_be_visible()
        expect(page.locator('#face-reference-preview img')).to_be_visible()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        assert errors == []
    finally:
        context.close()
