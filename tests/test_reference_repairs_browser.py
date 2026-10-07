import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_production_api import client, complete_draft
from tests.test_reference_repairs import video


@pytest.mark.parametrize('width',[1440,390])
def test_long_source_disabled_even_when_output_duration_is_short(browser,client,width):
    from app import main
    from app.production_store import ProductionStore
    draft=complete_draft(client)
    path=Path(ProductionStore(main.settings.storage_dir).get_asset(draft['source_asset_id'],private=True)['path'])
    video(path,(64,64),673)
    context=browser.new_context(viewport={'width':width,'height':900});page=context.new_page()
    submissions=[];errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    def route(r):
        req=r.request;url=urlparse(req.url).path
        if url=='/api/production/runs' and req.method=='POST':submissions.append(req.post_data)
        response=client.request(req.method,req.url,content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        if url=='/api/production/model-options':
            from app.model_catalog import capabilities,SD25
            body=response.json();body['items'].append({'id':SD25,'label':'Seedance 2.5',**capabilities(SD25)})
            r.fulfill(content_type='application/json',body=json.dumps(body));return
        r.fulfill(status=response.status_code,body=response.content,
            headers={k:v for k,v in response.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    page.add_init_script('localStorage.setItem("production-current-draft-v1",'+json.dumps(draft['id'])+');')
    try:
        page.goto('https://testserver/')
        expect(page.locator('#generation-source-duration')).to_contain_text('22.4')
        # Output seconds must not stand in for the actual input length.
        page.locator('#generation-duration-slider').evaluate("el => {el.value='7';el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}")
        expect(page.locator('#studio-generate-submit')).to_be_disabled()
        expect(page.locator('#variation-submit')).to_be_disabled()
        expect(page.locator('#source-model-hint')).to_contain_text('22.43')
        page.locator('#studio-generate-submit').evaluate("el=>el.form.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))")
        assert submissions==[]
        page.reload()
        expect(page.locator('#generation-source-duration')).to_contain_text('22.4')
        expect(page.locator('#studio-generate-submit')).to_be_disabled()
        page.locator('#model-settings-form [name=model]').select_option('doubao-seedance-2-5-260628')
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        page.locator('#model-settings-form [name=model]').select_option('doubao-seedance-2-0-260128')
        expect(page.locator('#studio-generate-submit')).to_be_disabled()
        assert not errors
    finally:context.close()


def test_custom_adapter_long_input_is_not_limited_by_seedance_rules(browser,client):
    from app import main
    from app.generation_settings import save_config
    from app.production_store import ProductionStore
    save_config(main.settings,{'provider':'custom','protocol':'adapter','mode':'mock','model':'custom-model','duration':8,'base_url':'https://example.test'})
    draft=complete_draft(client)
    path=Path(ProductionStore(main.settings.storage_dir).get_asset(draft['source_asset_id'],private=True)['path'])
    video(path,(64,64),673)
    context=browser.new_context();page=context.new_page()
    def route(r):
        req=r.request
        res=client.request(req.method,req.url,content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=res.status_code,body=res.content,headers={k:v for k,v in res.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    page.add_init_script('localStorage.setItem("production-current-draft-v1",'+json.dumps(draft['id'])+');')
    try:
        page.goto('https://testserver/')
        expect(page.locator('#generation-source-duration')).to_contain_text('22.4')
        page.locator('#generation-duration-slider').evaluate("el=>{el.value='7';el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}));}")
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
    finally:context.close()
