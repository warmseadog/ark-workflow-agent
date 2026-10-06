import json
from urllib.parse import urlparse
import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_production_api import client, complete_draft


@pytest.mark.parametrize('width',[1440,390])
def test_inline_editor_optional_inspiration_and_single_submission(browser,client,width,tmp_path):
    draft=complete_draft(client)
    ctx=browser.new_context(viewport={'width':width,'height':1000})
    page=ctx.new_page();submitted=[];errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    def route(r):
        req=r.request;path=urlparse(req.url).path
        if path=='/api/auth/me':
            r.fulfill(content_type='application/json',body=json.dumps({'auth_enabled':False,'user':None}));return
        if path=='/api/production/runs' and req.method=='POST':
            submitted.append(json.loads(req.post_data))
            r.fulfill(status=422,content_type='application/json',body=json.dumps({'detail':'测试提交保留输入'}));return
        res=client.request(req.method,req.url,content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=res.status_code,body=res.content,headers={k:v for k,v in res.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    page.add_init_script('localStorage.setItem("production-current-draft-v1",'+json.dumps(draft['id'])+');')
    try:
        page.goto('https://testserver/')
        expect(page.locator('#variation-toggle')).to_be_visible()
        page.locator('#variation-toggle').click()
        expect(page.locator('#variation-editor')).to_be_visible()
        page.screenshot(path=str(tmp_path/f'variation-{width}.png'),full_page=True)
        assert submitted==[]
        expect(page.locator('#variation-submit')).to_be_enabled()
        page.locator('#variation-submit').click()
        expect(page.locator('#production-status')).to_contain_text('测试提交保留输入')
        assert len(submitted)==1 and submitted[0]['variation']=={'inspiration':''}
        page.locator('#variation-inspiration').fill('先拍袖口，再拉远')
        page.locator('#variation-submit').click()
        expect(page.locator('#production-status')).to_contain_text('测试提交保留输入')
        assert len(submitted)==2 and submitted[1]['variation']['inspiration']=='先拍袖口，再拉远'
        expect(page.locator('#variation-inspiration')).to_have_value('先拍袖口，再拉远')
        page.locator('#variation-cancel').click()
        expect(page.locator('#variation-editor')).to_be_hidden()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        assert not errors
    finally:ctx.close()


def test_admin_configuration_controls(browser,client):
    ctx=browser.new_context();page=ctx.new_page()
    def route(r):
        req=r.request
        res=client.request(req.method,req.url,content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=res.status_code,body=res.content,headers={k:v for k,v in res.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    try:
        page.goto('https://testserver/admin/settings#variation')
        form=page.locator('#variation-form');expect(form).to_be_visible()
        expect(form.locator('fieldset')).to_be_enabled()
        form.locator('[name=model]').fill('custom-multimodal-model')
        form.locator('[name=api_key]').fill('ui-private-key')
        form.locator('[name=template]').fill('展示衣服细节，保持人物与场景')
        form.locator('button[type=submit]').click()
        expect(page.locator('#variation-status')).to_contain_text('已保存')
        expect(form.locator('[name=api_key]')).to_have_value('')
        page.reload()
        expect(form.locator('[name=model]')).to_have_value('custom-multimodal-model')
    finally:ctx.close()
