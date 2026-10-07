import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_production_api import client
from tests.test_inspiration_browser import assistant_page


@pytest.mark.parametrize('width', [1440, 390])
def test_admin_explains_current_mechanism_and_saves_without_applying(assistant_page, client, width, tmp_path):
    page, state = assistant_page
    page.set_viewport_size({'width': width, 'height': 1000})
    before = client.get('/api/admin/prompt-templates').json()['items']
    current = next(x for x in before if x['is_default'])
    page.goto('https://testserver/admin/settings#prompts')
    expect(page.locator('#prompt-current-name')).to_have_text(current['name'])
    expect(page.locator('#prompt-current-body')).to_have_value(current['content'])
    expect(page.locator('#prompt-current-body')).to_have_attribute('readonly', '')
    expect(page.locator('#prompt-current-rule')).to_contain_text('其余配饰跟随穿搭图')
    expect(page.locator('#prompt-mechanism')).to_contain_text('已提交任务')
    expect(page.locator('#standby-source')).to_have_value(current['id'])
    page.locator('#standby-name').fill('待用方案 <test>')
    page.locator('#standby-content').fill('仅保存，暂不应用的新正文')
    page.locator('#standby-note').fill('保留当前线上效果')
    page.locator('#standby-save').click()
    expect(page.locator('#standby-status')).to_contain_text('已保存为待用版本，当前默认提示词未改变')
    expect(page.locator('#standby-records')).to_contain_text('待用方案 <test>')
    assert client.get('/api/admin/prompt-templates').json()['items'] == before
    page.reload()
    expect(page.locator('#standby-records')).to_contain_text('待用方案 <test>')
    page.locator('#standby-records summary').first.click()
    expect(page.locator('#standby-records textarea').first).to_have_value('仅保存，暂不应用的新正文')
    expect(page.locator('#prompt-current-body')).to_have_value(current['content'])
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.locator('#section-prompts').screenshot(path=str(tmp_path / f'prompt-standby-{width}.png'))
    assert not state['errors']


def test_standby_save_failure_retains_edits_and_can_retry(assistant_page):
    page, state = assistant_page
    page.goto('https://testserver/admin/settings#prompts')
    expect(page.locator('#standby-save')).to_be_enabled()
    page.locator('#standby-name').fill('保留修改')
    page.locator('#standby-content').fill('未保存的正文')
    def reject(route):
        if route.request.method == 'POST':
            route.fulfill(status=503, json={'detail': '暂时不可用'})
        else:
            route.fallback()
    page.route('**/api/admin/prompt-standby', reject)
    page.locator('#standby-save').click()
    expect(page.locator('#standby-status')).to_contain_text('暂时不可用')
    expect(page.locator('#standby-content')).to_have_value('未保存的正文')
    expect(page.locator('#standby-save')).to_be_enabled()
    page.unroute('**/api/admin/prompt-standby', reject)
    page.locator('#standby-save').click()
    expect(page.locator('#standby-status')).to_contain_text('已保存为待用版本')
    assert not state['errors']
