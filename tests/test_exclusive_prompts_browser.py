"""Real editor and API through isolated TestClient; no remote model calls."""
import json
import re
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_production_api import client, asset
from tests.test_production_api import complete_draft
from tests.media_fixtures import image_bytes


@pytest.mark.parametrize('width',[1440,390])
def test_exclusive_default_reference_changes_restore_and_legacy_switch(browser,client,width):
    context=browser.new_context(viewport={'width':width,'height':1000})
    page=context.new_page(); errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    page.on('dialog',lambda dialog:dialog.accept())
    def route(r):
        req=r.request
        if urlparse(req.url).path=='/api/auth/me':
            r.fulfill(content_type='application/json',body=json.dumps({'auth_enabled':False,'user':None}));return
        response=client.request(req.method,req.url,content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=response.status_code,body=response.content,
            headers={k:v for k,v in response.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    try:
        page.goto('http://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.locator('#flow-stage-generation').evaluate('el=>el.open=true')
        expect(page.get_by_role('button',name='yoyo提示词',exact=True)).to_have_attribute('aria-pressed','true')
        expect(page.locator('#exclusive-prompt-status')).to_contain_text('已按本次素材更新')
        picture={'name':'ref.png','mimeType':'image/png','buffer':image_bytes()}
        page.locator('#studio-face-image').set_input_files(picture)
        page.locator('#studio-clothing-image').set_input_files(picture)
        page.locator('#studio-bag-image').set_input_files(picture)
        page.locator('#studio-shoes-image').set_input_files(picture)
        final=page.locator('#final-generation-prompt')
        expect(final).to_have_value(re.compile(r'发型来源：@Image1[\s\S]*包包来源：@Image3[\s\S]*鞋子来源：@Image4'))
        assert '不保留或叠加旧包' in final.input_value()
        page.locator('#studio-hairstyle-image').set_input_files(picture)
        expect(final).to_have_value(re.compile(r'发型来源：@Image3[\s\S]*包包来源：@Image4[\s\S]*鞋子来源：@Image5'))
        page.locator('#accessory-references').evaluate('el=>el.open=true')
        page.locator('#bag-enabled').uncheck()
        expect(final).to_have_value(re.compile(r'鞋子来源：@Image4'))
        expect(final).to_have_value(re.compile(r'包包来源：穿搭参考 @Image2'))
        page.locator('#generation-prompt').fill('保持动作自然，这是我的自定义正文。')
        expect(final).to_have_value(re.compile(r'^保持动作自然，这是我的自定义正文。'))
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.reload()
        page.locator('#flow-stage-generation').evaluate('el=>el.open=true')
        expect(page.get_by_role('button',name='yoyo提示词',exact=True)).to_have_attribute('aria-pressed','true')
        expect(page.locator('#generation-prompt')).to_have_value('保持动作自然，这是我的自定义正文。')
        expect(final).to_have_value(re.compile(r'鞋子来源：@Image4'))
        page.get_by_role('button',name='默认提示词',exact=True).click()
        expect(final).to_have_value(re.compile(r'独立参考优先（exclusive-v2）'))
        assert '包包来源：' not in final.input_value()
        page.get_by_role('button',name='默认提示词2',exact=True).click()
        expect(page.locator('#exclusive-prompt-preview')).to_be_hidden()
        expect(page.locator('#generation-prompt')).to_have_value(re.compile(r'以@Video1为动作与运镜参考'))
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.reload()
        page.locator('#flow-stage-generation').evaluate('el=>el.open=true')
        expect(page.get_by_role('button',name='默认提示词2',exact=True)).to_have_attribute('aria-pressed','true')
        assert not errors,errors
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    finally:
        context.close()


def test_stale_preview_cannot_replace_current_and_failure_blocks_submit(browser,client):
    draft=complete_draft(client)
    existing_runs = {run['id'] for run in client.get('/api/production/runs').json()['items']}
    context=browser.new_context(viewport={'width':1440,'height':1000})
    page=context.new_page();held=[];runs=[]
    state={'fail':False}
    def route(r):
        req=r.request;path=urlparse(req.url).path
        if path=='/api/auth/me':
            r.fulfill(content_type='application/json',body=json.dumps({'auth_enabled':False,'user':None}));return
        if path=='/api/production/prompt-preview':
            payload=json.loads(req.post_data)
            if payload['prompt']=='旧预览': held.append(r);return
            if state['fail']:
                r.fulfill(status=503,content_type='application/json',body=json.dumps({'detail':'测试预览失败'}));return
        if path=='/api/production/runs' and req.method=='POST':runs.append(req.post_data)
        response=client.request(req.method,req.url,content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=response.status_code,body=response.content,
            headers={k:v for k,v in response.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    try:
        page.goto('http://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.locator('#flow-stage-generation').evaluate('el=>el.open=true')
        expect(page.locator('#exclusive-prompt-status')).to_contain_text('已按本次素材更新')
        with page.expect_request('**/prompt-preview'):
            page.locator('#generation-prompt').fill('旧预览')
        page.wait_for_timeout(100)
        assert held
        page.locator('#generation-prompt').fill('新预览')
        expect(page.locator('#final-generation-prompt')).to_have_value(re.compile('^新预览'))
        held.pop().fulfill(content_type='application/json',body=json.dumps({'prompt':'旧预览回复'}))
        page.wait_for_timeout(100)
        expect(page.locator('#final-generation-prompt')).to_have_value(re.compile('^新预览'))
        state['fail']=True
        page.locator('#generation-prompt').fill('预览不可用时不得提交')
        expect(page.locator('#exclusive-prompt-status')).to_contain_text('提示词预览失败')
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        page.locator('#studio-generate-submit').click()
        expect(page.locator('#production-status')).to_contain_text('测试预览失败')
        assert not runs
        assert {run['id'] for run in client.get('/api/production/runs').json()['items']} == existing_runs
    finally:
        context.close()
