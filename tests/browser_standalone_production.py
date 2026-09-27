"""Verify the standalone production page without sending media or creating jobs."""
import re
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright, expect


CONFIG = {
    'provider': 'ark', 'protocol': 'ark', 'mode': 'mock',
    'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
    'public_base_url': '', 'model': 'fixture-model', 'duration': 5,
    'fps': 0, 'resolution': '720p', 'has_api_key': False, 'status': 'demo',
}


def assert_within_viewport(locator, width, height):
    box = locator.bounding_box()
    assert box, f'Missing visible element: {locator}'
    assert box['x'] >= 0 and box['x'] + box['width'] <= width + 1, box
    assert box['y'] >= 0 and box['y'] + box['height'] <= height + 1, box


def check(browser, path, width, height):
    context = browser.new_context(viewport={'width': width, 'height': height})
    page = context.new_page()
    errors, requests = [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('request', lambda request: requests.append((urlsplit(request.url).path, request.method)))

    def handle(route):
        request = route.request
        if urlsplit(request.url).path == '/api/model-settings' and request.method == 'GET':
            route.fulfill(json={'config': CONFIG, 'presets': {'ark': {'models': []}}})
        elif urlsplit(request.url).path == '/api/prompt-templates' and request.method == 'GET':
            route.fulfill(json={'items': [{'id': str(i), 'name': name, 'content': '保留动作与场景'} for i, name in enumerate(['动作保留', '自然换装', '电商展示', '稳定一致'])]})
        elif urlsplit(request.url).path == '/api/link-settings' and request.method == 'GET':
            route.fulfill(json={'has_api_key': False})
        else:
            # All other API calls are forbidden in this test, including generation.
            route.abort()

    page.route('**/api/**', handle)
    response = page.goto(f'http://127.0.0.1:8000{path}')
    assert response and response.ok, path
    expect(page).to_have_title(re.compile('制作流程'))
    expect(page.get_by_role('heading', name='制作流程', exact=True)).to_be_visible()
    expect(page.locator('#create-view')).to_be_visible()
    expect(page.locator('.asset-entry')).to_have_count(3)
    for name in ['上传参考视频', '上传衣服参考图', '上传人物参考图']:
        expect(page.get_by_label(name, exact=True)).to_be_attached()
    expect(page.locator('#studio-generate-submit')).to_be_disabled()

    expect(page.locator('aside, [role=complementary], .rail, nav, [role=navigation]')).to_have_count(0)
    expect(page.locator('#discover-view, #cases-view, #queue-view, #search-form, #case-form, #import-modal')).to_have_count(0)
    body_text = page.locator('body').inner_text()
    assert '素材发现' not in body_text and '成功案例' not in body_text and '制作队列' not in body_text
    for attribute, selector in [('src', 'script[src]'), ('href', 'link[rel=stylesheet]')]:
        urls = page.locator(selector).evaluate_all('(elements, attribute) => elements.map(el => el.getAttribute(attribute))', attribute)
        assert all(urlsplit(url).path not in {'/static/studio.js', '/static/studio.css'} for url in urls), urls

    dock = page.locator('.corner-settings')
    assert dock.evaluate('(element) => getComputedStyle(element).position') == 'fixed'
    assert_within_viewport(dock, width, height)
    dock_box = dock.bounding_box()
    assert width - dock_box['x'] - dock_box['width'] <= 40
    assert dock_box['y'] > height / 2
    for selector in ['#redaction-settings > summary', '#model-settings-toggle']:
        expect(page.locator(selector)).to_be_visible()
        assert_within_viewport(page.locator(selector), width, height)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), f'Horizontal overflow at {width}'
    if path == '/v1' and width in {1440, 390}:
        page.screenshot(path=f'storage/standalone-production-{width}.png', full_page=True)

    page.locator('#model-settings-toggle').click()
    expect(page.locator('#model-provider')).to_be_visible()
    expect(page.locator('#test-model-connection')).to_be_enabled()
    assert_within_viewport(page.locator('#model-settings-panel'), width, height)
    if path == '/v1' and width in {1440, 390}:
        page.screenshot(path=f'storage/standalone-settings-{width}.png')
    page.locator('#close-model-settings').click()
    page.locator('#redaction-settings > summary').click()
    expect(page.locator('#studio-preview-submit')).to_be_visible()
    expect(page.locator('#studio-preview-submit')).to_be_disabled()
    assert_within_viewport(page.locator('#redaction-settings-panel'), width, height)
    page.get_by_role('button', name='关闭打码设置', exact=True).click()
    page.locator('#flow-stage-generation > summary').click()
    expect(page.locator('#generation-prompt')).to_be_visible()
    expect(page.locator('[data-prompt-template]')).to_have_count(4)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')

    api_requests = [(url, method) for url, method in requests if url.startswith('/api/')]
    assert api_requests and all(url in {'/api/model-settings', '/api/prompt-templates', '/api/link-settings'} and method == 'GET' for url, method in api_requests), api_requests
    assert not any(url.startswith('/api/discovery') for url, _ in requests), requests
    assert not any(url in {'/static/studio.js', '/static/studio.css'} for url, _ in requests), requests
    assert not errors, errors
    context.close()
    print(f'PASS standalone production {path} {width}x{height}')


if __name__ == '__main__':
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge')
        for path in ['/', '/v1', '/studio']:
            for width, height in [(1440, 1000), (390, 844), (320, 740)]:
                check(browser, path, width, height)
        browser.close()
