from urllib.parse import urlparse
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_prompt_privacy_delegation import users
from tests.test_production_api import complete_draft
from app import main, tenancy
from app.production_store import ProductionStore


def route_client(page, client):
    errors=[]; requests=[]
    page.on('pageerror', lambda error: errors.append(str(error)))
    def route(r):
        req=r.request; requests.append((req.method, urlparse(req.url).path))
        response=client.request(req.method,req.url,content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length','cookie')})
        r.fulfill(status=response.status_code,body=response.content,
            headers={k:v for k,v in response.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    return errors,requests


def test_ordinary_editor_saves_and_submits_without_prompt_preview(browser,users):
    _, (_,target,_), (_,alice,_) = users
    draft=complete_draft(alice)
    context=browser.new_context(viewport={'width':390,'height':1000})
    page=context.new_page(); errors,requests=route_client(page,alice)
    try:
        page.goto('https://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        assert page.locator('#generation-prompt').count()==0
        assert page.locator('#prompt-template-list').count()==0
        assert page.locator('#final-generation-prompt').count()==0
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        with page.expect_response(lambda r:r.request.method=='POST' and urlparse(r.url).path=='/api/production/runs') as submitted:
            page.locator('#studio-generate-submit').click()
        assert submitted.value.status==200,submitted.value.text()
        assert not any('prompt-preview' in path or 'prompt-templates' in path for _,path in requests)
        store=ProductionStore(tenancy.user_settings(main.settings,target).storage_dir)
        assert store.get_run(submitted.value.json()['id'])['snapshot']['prompt']
        assert errors==[]
    finally:context.close()


def test_delegated_editor_restores_target_and_saves_without_touching_admin(browser,users):
    _, (_,target,actor), (_,alice,operator)=users
    draft=complete_draft(alice)
    store=ProductionStore(tenancy.user_settings(main.settings,target).storage_dir)
    run=store.create_run(draft['id'],draft['revision'],'source-browser',{})
    response=operator.post('/api/admin/task-records/'+target['id']+'/'+run['id']+'/restore-draft',json={'idempotency_key':'browser-restore'})
    assert response.status_code==200,response.text
    restored=response.json()
    context=browser.new_context(viewport={'width':1440,'height':1000})
    page=context.new_page();errors,requests=route_client(page,operator)
    try:
        page.goto('https://testserver/?delegate_user='+target['id']+'&draft='+restored['id'])
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('#delegated-editor-banner')).to_contain_text('alice')
        expect(page.locator('#studio-generate-submit')).to_have_text('为该用户重新生成 →')
        page.locator('#draft-name-edit').click()
        page.locator('#draft-name-input').fill('管理员重新制作')
        page.locator('#draft-name-save').click()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.reload()
        expect(page.locator('#draft-task-name')).to_have_text('管理员重新制作')
        assert store.get_draft(restored['id'])['name']=='管理员重新制作'
        assert ProductionStore(tenancy.user_settings(main.settings,actor).storage_dir).list_drafts()==[]
        assert errors==[]
    finally:context.close()


def test_admin_operations_page_edits_prompts_but_cannot_change_system_config(browser,users):
    _, _, (_,_,operator)=users
    context=browser.new_context(viewport={'width':390,'height':1000})
    page=context.new_page();errors,_=route_client(page,operator)
    try:
        page.goto('https://testserver/admin/settings')
        expect(page.locator('#operations-template-name')).to_have_value('yoyo提示词')
        assert page.locator('#tos-form').count()==0
        page.locator('#operations-template-content').fill('运营管理员维护的默认提示词')
        page.locator('#operations-template-form button[type=submit]').click()
        expect(page.locator('#operations-template-status')).to_have_text('已保存')
        page.reload()
        expect(page.locator('#operations-template-content')).to_have_value('运营管理员维护的默认提示词')
        assert operator.put('/api/admin/scheduling',json={'global_concurrency':1}).status_code==403
        assert errors==[]
    finally:context.close()
