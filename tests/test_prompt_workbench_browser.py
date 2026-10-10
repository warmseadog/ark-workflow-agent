import pytest
import re
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_production_api import client
from tests.test_inspiration_browser import assistant_page


@pytest.mark.parametrize('width',[1440,390])
def test_admin_can_inspect_edit_and_preview_accessory_rules(assistant_page,client,width,tmp_path):
    page,state=assistant_page
    page.set_viewport_size({'width':width,'height':1100})
    page.goto('https://testserver/admin/settings#prompts')
    expect(page.locator('#pw-status')).to_contain_text('已读取')
    page.locator('#pw-role-bag').check()
    expect(page.locator('#pw-diff')).to_contain_text('包包来源：@Image3')
    page.locator('#pw-group').select_option('更多搭配')
    card=page.locator('[data-prompt-id="accessory.bag"]')
    card.locator('summary').first.click()
    editor=card.locator('textarea').first
    editor.fill('测试用短肩带，包包自然贴合肩部。')
    expect(page.locator('#pw-final')).to_have_value(re.compile('测试用短肩带'))
    card.get_by_role('button',name='保存本段').click()
    expect(page.locator('#pw-status')).to_contain_text('已保存')
    page.reload()
    expect(page.locator('#pw-status')).to_contain_text('已读取')
    page.locator('#pw-group').select_option('更多搭配')
    card=page.locator('[data-prompt-id="accessory.bag"]')
    card.locator('summary').first.click()
    expect(card.locator('textarea').first).to_have_value('测试用短肩带，包包自然贴合肩部。')
    page.locator('#pw-role-bag').check()
    expect(page.locator('#pw-final')).to_have_value(re.compile('测试用短肩带'))
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path=str(tmp_path/f'prompt-workbench-{width}.png'),full_page=True)
    assert not state['errors']


@pytest.mark.parametrize('section,key,form_id,status_id',[('variation','variation.skill','variation-form','variation-status'),('continuation','continuation.skill','continuation-form','continuation-status')])
def test_model_settings_save_cannot_overwrite_new_prompt_from_workbench(assistant_page,client,section,key,form_id,status_id):
    page,state=assistant_page
    page.goto('https://testserver/admin/settings#'+section)
    form=page.locator('#'+form_id)
    expect(form.locator('[name=skill]')).to_be_enabled()
    data=client.get('/api/admin/prompt-workbench').json()
    response=client.put('/api/admin/prompt-workbench',json={'revision':data['revision'],'changes':{key:'统一页面保存的新正文'}})
    assert response.status_code==200,response.text
    form.locator('[name=timeout_seconds]').fill('120')
    form.locator('button[type=submit]').click()
    expect(page.locator('#'+status_id)).to_contain_text('已保存')
    after=client.get('/api/admin/prompt-workbench').json()
    assert next(x for x in after['items'] if x['id']==key)['content']=='统一页面保存的新正文'
    assert not state['errors']
