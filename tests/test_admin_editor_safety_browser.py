"""Protect template edits and keep delegated task actions in the shown tenant."""
from pathlib import Path
import re

from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_prompt_privacy_delegation import users
from tests.test_prompt_privacy_browser import route_client
from tests.test_production_api import complete_draft
from app import main, tenancy
from app.production_store import ProductionStore

ROOT = Path(__file__).resolve().parents[1]


def template_page(browser):
    page = browser.new_page()
    markup = (ROOT / 'app/templates/admin_operations.html').read_text(encoding='utf-8')
    page.set_content(re.sub(r'<script[^>]*>.*?</script>', '', markup, flags=re.S))
    page.evaluate("""() => {
      window.accountReady=Promise.resolve({}); window.accountUI={readJSON:r=>r.json()};
      window.items=[{id:'first',name:'First',content:'Original',rule_version:'exclusive-v2'},
                    {id:'second',name:'Second',content:'Other',rule_version:'legacy-v1'}];
      window.fetch=async(url,opts)=> {
        if(opts.method==='PUT') {
          window.sent=JSON.parse(opts.body);
          return await new Promise(resolve=>window.releaseSave=()=>{
            Object.assign(items.find(item=>url.endsWith('/'+item.id)),window.sent);
            resolve({json:async()=>items.find(item=>url.endsWith('/'+item.id))});
          });
        }
        return {json:async()=>({items})};
      };
    }""")
    page.add_script_tag(path=str(ROOT / 'app/static/admin-operations.js'))
    expect(page.locator('#operations-template-content')).to_have_value('Original')
    return page


def test_template_inputs_lock_until_save_response_applied(browser):
    page = template_page(browser)
    try:
        page.locator('#operations-template-content').fill('First edit')
        page.locator('button[type=submit]').click()
        page.wait_for_function('Boolean(window.releaseSave)')
        expect(page.locator('#operations-template-content')).to_be_disabled()
        expect(page.locator('#operations-template-name')).to_be_disabled()
        expect(page.locator('#operations-template')).to_be_disabled()
        page.evaluate('window.releaseSave()')
        expect(page.locator('#operations-template-status')).to_have_text('已保存')
        expect(page.locator('#operations-template-content')).to_be_enabled()
        expect(page.locator('#operations-template-content')).to_have_value('First edit')
        page.locator('#operations-template-content').fill('Next edit')
        expect(page.locator('#operations-template-content')).to_have_value('Next edit')
    finally:
        page.close()


def test_template_switch_and_new_require_discard_of_unsaved_content(browser):
    page = template_page(browser)
    dialogs = []
    accept = [False]
    def answer(dialog):
        dialogs.append(dialog.message)
        dialog.accept() if accept[0] else dialog.dismiss()
    page.on('dialog', answer)
    try:
        page.locator('#operations-template-content').fill('Unsaved edit')
        page.locator('#operations-template').select_option('second')
        expect(page.locator('#operations-template-content')).to_have_value('Unsaved edit')
        expect(page.locator('#operations-template')).to_have_value('first')
        page.locator('#operations-template-new').click()
        expect(page.locator('#operations-template-content')).to_have_value('Unsaved edit')
        accept[0] = True
        page.locator('#operations-template').select_option('second')
        expect(page.locator('#operations-template-content')).to_have_value('Other')
        page.locator('#operations-template-name').fill('Unsaved name')
        page.locator('#operations-template-new').click()
        expect(page.locator('#operations-template-name')).to_have_value('')
        expect(page.locator('#operations-template-content')).to_have_value('')
        assert len(dialogs) == 4
    finally:
        page.close()


def test_delegated_task_list_only_shows_target_and_supported_actions(browser, users):
    _, (_, target, actor), (_, alice, operator) = users
    own_draft = complete_draft(operator)
    own_store = ProductionStore(tenancy.user_settings(main.settings, actor).storage_dir)
    own = own_store.create_run(own_draft['id'], own_draft['revision'], 'admin-own', {})
    draft = complete_draft(alice)
    store = ProductionStore(tenancy.user_settings(main.settings, target).storage_dir)
    run = store.create_run(draft['id'], draft['revision'], 'target-original', {})
    store.update_run(run['id'], status='succeeded')
    output = store.storage / 'outputs' / (run['id']+'.mp4')
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b'isolated-playback-fixture')
    restored = operator.post('/api/admin/task-records/'+target['id']+'/'+run['id']+'/restore-draft',
                             json={'idempotency_key':'scoped-list'}).json()
    context = browser.new_context(viewport={'width':1440,'height':1000})
    page = context.new_page()
    errors, requests = route_client(page, operator)
    try:
        page.goto('https://testserver/?delegate_user='+target['id']+'&draft='+restored['id']+'#tasks')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('#runs-status')).to_contain_text('共 1 个任务')
        expect(page.locator('.production-run')).to_have_count(1)
        expect(page.locator('.run-owner')).to_have_text('alice')
        expect(page.locator('#runs-user-controls')).to_be_hidden()
        expect(page.locator('[data-run-action=copy], [data-run-action=cancel], [data-run-action=resume], [data-run-action=delete], .production-run .run-rename')).to_have_count(0)
        expect(page.locator('[data-run-action=play]')).to_have_count(1)
        expect(page.locator('[data-run-action=comparison]')).to_have_count(1)
        expect(page.locator('[data-run-action=restore]')).to_have_count(1)
        page.locator('.run-menu summary').click()
        page.locator('[data-run-action=details]').click()
        expect(page.locator('.run-timing-details')).to_be_visible()
        assert not page.locator('[data-run-id="'+own['id']+'"]').count()
        assert errors == []
    finally:
        context.close()
