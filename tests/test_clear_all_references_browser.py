import json

import pytest
from playwright.sync_api import expect
from tests.test_inspiration_browser import assistant_page, browser, client
from tests.test_production_api import complete_draft
from tests.test_person_video import video_bytes


@pytest.mark.parametrize('references', ['mixed', 'source_only', 'person_only'])
def test_clear_all_references_persists_and_keeps_library(assistant_page, client, tmp_path, references):
    draft = complete_draft(client)
    uploaded = client.post('/api/production/assets', data={'kind': 'person_video'},
        files={'file': ('person.mp4', video_bytes(tmp_path), 'video/mp4')})
    assert uploaded.status_code == 200, uploaded.text
    person = uploaded.json()
    original_assets = list(draft['assets']) + [person]
    update = {'revision': draft['revision'], 'person_input_policy': 'auto_virtual',
        'person_reference_mode': 'video', 'person_video_asset_id': person['id'],
        'scene_description': '清空的场景描述'}
    if references != 'mixed':
        update.update(face_asset_ids=[], clothing_asset_ids=[])
    if references == 'source_only':
        update['person_video_asset_id'] = None
    if references == 'person_only':
        update['source_asset_id'] = None
    response = client.put('/api/production/drafts/' + draft['id'], json=update)
    assert response.status_code == 200, response.text
    page, state = assistant_page
    page.add_init_script('localStorage.setItem("production-current-draft-v1",' + json.dumps(draft['id']) + ');')
    page.goto('https://testserver/')
    button = page.locator('#clear-reference-images')
    expect(button).to_be_visible()
    expect(button).to_be_enabled()
    prompt = page.locator('#generation-prompt').input_value()
    page.locator('#variation-inspiration').fill('保留我的拍摄灵感')
    page.once('dialog', lambda dialog: dialog.dismiss())
    button.click()
    expect(button).to_be_visible()
    saved = client.get('/api/production/drafts/' + draft['id']).json()
    assert saved['source_asset_id'] == response.json()['source_asset_id']
    assert saved['person_video_asset_id'] == response.json()['person_video_asset_id']
    page.once('dialog', lambda dialog: dialog.accept())
    button.click()
    expect(button).to_be_hidden()
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    expect(page.locator('#person-video-preview')).not_to_be_visible()
    expect(page.locator('#video-reference-preview video')).to_have_count(0)
    expect(page.locator('#generation-prompt')).to_have_value(prompt)
    expect(page.locator('#variation-inspiration')).to_have_value('保留我的拍摄灵感')
    saved = client.get('/api/production/drafts/' + draft['id']).json()
    assert saved['source_asset_id'] is None and saved['person_video_asset_id'] is None
    assert saved['person_id'] is None and saved['source_clip'] is None
    assert not saved['face_asset_ids'] and not saved['clothing_asset_ids']
    assert saved['scene_description'] == ''
    for item in original_assets:
        assert client.get(item['url']).status_code == 200
    page.reload()
    expect(button).to_be_hidden()
    expect(page.locator('#generation-readiness')).to_have_text('还缺参考视频')
    assert not state['errors']
