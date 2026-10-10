"""Render the actual refreshed run returned by the API in the task-list UI."""
from pathlib import Path
import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_redaction_refresh import client, seeded, submit

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('width', [390, 1440])
def test_refreshed_mask_is_labeled_in_summary_and_details(browser, client, seeded, width):
    _, original, _, _ = seeded
    draft = client.post('/api/production/runs/'+original['id']+'/copy').json()
    run = submit(client, draft, 'browser-refresh')
    full = client.get('/api/production/runs/'+run['id']).json()
    page = browser.new_page(viewport={'width': width, 'height': 950})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        panel = (ROOT/'app/templates/production_panel.html').read_text(encoding='utf-8')
        page.route('http://redaction-refresh.test/', lambda route: route.fulfill(
            content_type='text/html', body=panel[panel.index('  <section class="production-runs"'):]))
        page.goto('http://redaction-refresh.test/')
        page.add_script_tag(path=str(ROOT/'app/static/production-runs.js'))
        page.evaluate("""item => {
          window.accountReady=Promise.resolve();
          window.runs=createProductionRuns({
            api:async path=>path.includes('?')?{items:[item],total:1,page:1,pages:1}:item,
            changeDraft:async()=>{},accessoryLabels:{},
            media:{releaseVideo:()=>{},lazyVideo:()=>{},thumbnailFor:asset=>asset.url}
          });runs.refresh();
        }""", full)
        expect(page.locator('.run-phase-summary')).to_contain_text('打码')
        expect(page.locator('.run-phase-summary')).not_to_contain_text('复用缓存')
        page.locator('.run-menu>summary').click()
        page.locator('[data-run-action=details]').click()
        expect(page.locator('.run-phase-details')).to_contain_text('打码：')
        expect(page.locator('[data-run-action=preview-redacted]')).to_be_visible()
        assert not errors, errors
    finally:
        page.close()
