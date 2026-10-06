from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_prompt_privacy_delegation import users
from tests.test_prompt_privacy_browser import route_client
from tests.test_production_api import complete_draft
from app import main, tenancy
from app.production_store import ProductionStore


def test_tail_retry_clearly_requires_paid_generation_confirmation(browser,users):
    _, (_,user,_), (_,client,_) = users
    draft=complete_draft(client)
    store=ProductionStore(tenancy.user_settings(main.settings,user).storage_dir)
    run=store.create_run(draft['id'],draft['revision'],'tail-retry-ui',{})
    store.update_continuation(run['id'],base_ready=True,quality_rejected=True,provider_task_id='rejected-tail')
    store.update_run(run['id'],status='needs_attention',error_kind='continuation_seam_mismatch',message='接续变化过大')
    context=browser.new_context();page=context.new_page();errors,_=route_client(page,client)
    posts=[];dialogs=[];accept=[False]
    def resume(route):
        posts.append(route.request.method);route.fulfill(json={})
    page.route('**/api/production/runs/'+run['id']+'/resume',resume)
    def confirm(dialog):
        dialogs.append(dialog.message)
        dialog.accept() if accept[0] else dialog.dismiss()
    page.on('dialog',confirm)
    try:
        page.goto('https://testserver/#tasks')
        row=page.locator('[data-run-id="'+run['id']+'"]')
        row.locator('.run-menu summary').click()
        row.get_by_role('button',name='重新生成尾段',exact=True).click()
        assert not posts and '费用' in dialogs[-1] and '基础片' in dialogs[-1]
        accept[0]=True
        row.locator('.run-menu summary').click()
        with page.expect_response(lambda response:response.url.endswith('/resume')):
            row.get_by_role('button',name='重新生成尾段',exact=True).click()
        assert posts==['POST'] and not errors
    finally:context.close()
