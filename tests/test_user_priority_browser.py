import re
from playwright.sync_api import expect
from tests.test_inspiration_browser import assistant_page, browser, client


def test_motion_profile_can_be_saved_reloaded_and_switched_back(assistant_page, client):
    page, state = assistant_page
    page.goto('https://testserver/admin/settings#variation')
    form = page.locator('#variation-form')
    expect(form.locator('fieldset')).to_be_enabled()
    original = client.get('/api/variation-settings').json()['config']
    form.locator('[name=prompt_mode]').select_option('motion')
    expect(form.locator('[name=motion_skill]')).to_be_visible()
    expect(form.locator('[name=skill]')).to_be_disabled()
    form.locator('button[type=submit]').click()
    expect(page.locator('#variation-status')).to_contain_text('已保存')
    expect(page.locator('#inspiration-config-info')).to_contain_text('丰富动作')
    page.reload()
    expect(form.locator('[name=prompt_mode]')).to_have_value('motion')
    expect(form.locator('[name=motion_skill]')).to_have_value(re.compile('相邻镜头应在人物动作'))
    current = client.get('/api/variation-settings').json()['config']
    assert current['skill'] == original['skill']
    assert current['template'] == original['template']
    form.locator('[name=prompt_mode]').select_option('strict')
    form.locator('button[type=submit]').click()
    expect(page.locator('#variation-status')).to_contain_text('已保存')
    assert client.get('/api/variation-settings').json()['config']['prompt_mode'] == 'strict'
    assert not state['errors']


def test_profile_switch_preserves_two_independent_editors(assistant_page, client):
    original = client.put('/api/variation-settings', json={'template': '原版模板不可覆盖', 'skill': '原版Skill不可覆盖'}).json()['config']
    page, state = assistant_page
    page.goto('https://testserver/admin/settings#variation')
    form = page.locator('#variation-form')
    expect(form.locator('fieldset')).to_be_enabled()
    expect(form.locator('[name=prompt_mode]')).to_have_value('strict')
    expect(form.locator('[name=template]')).to_have_value(original['template'])
    assist = page.locator('#inspiration-settings-form')
    expect(assist.locator('fieldset')).to_be_enabled()
    assist.locator('[name=inherit_provider]').uncheck()
    assist.locator('[name=model]').fill('unsaved-fast-model')
    assist.locator('[name=api_key]').fill('unsaved-local-test-key')
    form.locator('[name=prompt_mode]').select_option('user_priority')
    expect(form.locator('[name=template]')).to_be_hidden()
    expect(form.locator('[name=user_priority_template]')).to_be_visible()
    form.locator('[name=user_priority_template]').fill('用户明确指定的CCD质感优先')
    form.locator('[name=user_priority_skill]').fill('落实正常速度与微虚化')
    form.locator('button[type=submit]').click()
    expect(page.locator('#variation-status')).to_contain_text('已保存')
    expect(page.locator('#inspiration-config-info')).to_contain_text('用户意图优先')
    expect(assist.locator('[name=model]')).to_have_value('unsaved-fast-model')
    expect(assist.locator('[name=api_key]')).to_have_value('unsaved-local-test-key')
    expect(assist.locator('[name=inherit_provider]')).not_to_be_checked()
    current = client.get('/api/variation-settings').json()['config']
    assert current['template'] == original['template'] and current['skill'] == original['skill']
    page.reload()
    expect(form.locator('[name=prompt_mode]')).to_have_value('user_priority')
    expect(form.locator('[name=user_priority_template]')).to_have_value('用户明确指定的CCD质感优先')
    form.locator('[name=prompt_mode]').select_option('strict')
    expect(form.locator('[name=template]')).to_have_value(original['template'])
    form.locator('button[type=submit]').click()
    expect(page.locator('#variation-status')).to_contain_text('已保存')
    current = client.get('/api/variation-settings').json()['config']
    assert current['prompt_mode'] == 'strict'
    assert current['user_priority_skill'] == '落实正常速度与微虚化'
    assert current['template'] == original['template'] and current['skill'] == original['skill']
    assert not state['errors']
