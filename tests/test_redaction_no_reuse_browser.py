"""Real editor clicks must create a new preview after a completed preview."""
import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_prompt_privacy_delegation import users
from tests.test_prompt_privacy_browser import route_client
from tests.test_production_api import complete_draft
from app import production_worker


@pytest.mark.parametrize('width', [390,1440])
def test_repeated_preview_clicks_process_again(browser,users,monkeypatch,width):
    alice=users[2][1]
    complete_draft(alice)
    calls=[]
    def mask(src,dst,*args):
        calls.append(src);dst.write_bytes(b'masked')
    monkeypatch.setattr(production_worker,'run_deface',mask)
    context=browser.new_context(viewport={'width':width,'height':1000})
    page=context.new_page();errors,requests=route_client(page,alice)
    try:
        page.goto('https://testserver/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        # Compact home hides this retained compatibility control. Dispatch its
        # actual click handler without changing the product's visual layout.
        for count in (1,2):
            button=page.locator('#studio-preview-submit')
            expect(button).to_be_enabled()
            button.evaluate('(element)=>element.click()')
            expect(page.locator('#studio-preview-status')).to_contain_text('预览已就绪')
            assert len(calls)==count
        assert sum(method=='POST' and path=='/api/previews' for method,path in requests)==2
        assert not errors
    finally:
        context.close()
