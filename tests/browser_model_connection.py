"""Check connection settings with intercepted responses; never call a provider."""
from playwright.sync_api import sync_playwright, expect


def check(width, height):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        page = browser.new_page(viewport={'width': width, 'height': height})
        errors, calls, pending = [], [], []
        page.on('pageerror', lambda error: errors.append(str(error)))
        config = {'provider': 'ark', 'protocol': 'ark', 'mode': 'http',
                  'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
                  'model': 'saved-model', 'duration': 5, 'fps': 0,
                  'resolution': '720p', 'has_api_key': True, 'status': 'configured'}

        def handle(route):
            request = route.request
            path = request.url.split('8000')[-1].split('?')[0]
            calls.append((path, request.method))
            if path == '/api/model-settings' and request.method == 'GET':
                route.fulfill(json={'config': config, 'presets': {'ark': {'models': []}}})
            elif path == '/api/model-settings/test' and request.method == 'POST':
                pending.append(route)
            elif path == '/api/model-settings' and request.method == 'PUT':
                submitted = request.post_data_json
                assert submitted['public_base_url'] == 'https://studio.example.com'
                route.fulfill(json={'config': {**config, **{key: value for key, value in submitted.items() if key not in {'api_key', 'clear_api_key'}}}})
            else:
                route.abort()

        page.route('**/api/**', handle)
        page.goto('http://127.0.0.1:8000/v1')
        page.locator('#model-settings-toggle').click()
        button = page.locator('#test-model-connection')
        expect(button).to_be_visible()
        form = page.locator('#model-settings-form')
        result = page.locator('#model-connection-result')
        expect(result).to_be_hidden()

        form.locator('.model-advanced > summary').click()
        public_base = form.locator('[name=public_base_url]')
        expect(public_base).to_be_visible()
        expect(public_base).to_have_value('')
        public_base.fill('invalid-url')
        form.locator('.model-advanced > summary').click()
        button.click()
        expect(public_base).to_be_visible()
        expect(public_base).to_be_focused()
        assert not pending
        public_base.fill('https://studio.example.com')
        form.locator('[name=protocol]').select_option('toapis')
        expect(public_base).to_be_hidden()
        form.locator('[name=protocol]').select_option('ark')
        expect(public_base).to_be_visible()
        form.locator('.model-advanced > summary').click()

        form.locator('[name=model]').fill('unsaved-model')
        form.locator('[name=duration]').fill('8')
        form.locator('[name=api_key]').fill('fixture-only-key')
        button.click()
        expect(button).to_have_text('测试中…')
        expect(button).to_be_disabled()
        expect(form.locator('[name=model]')).to_be_disabled()
        expect(page.locator('#save-model-settings')).to_be_disabled()
        route = pending.pop()
        payload = route.request.post_data_json
        assert payload['model'] == 'unsaved-model'
        assert payload['duration'] == 8 and payload['api_key'] == 'fixture-only-key'
        assert payload['public_base_url'] == 'https://studio.example.com'
        route.fulfill(json={'ok': True, 'status': 'connected', 'message': '接口和模型均可访问。', 'latency_ms': 123})
        expect(result).to_be_visible()
        expect(result).to_contain_text('接口和模型均可访问。')
        expect(result).to_contain_text('123 ms')
        expect(result).to_have_attribute('data-state', 'success')
        expect(button).to_be_enabled()
        expect(form.locator('[name=model]')).to_have_value('unsaved-model')

        form.locator('[name=api_key]').fill('')
        expect(result).to_be_hidden()
        button.click()
        route = pending.pop()
        assert route.request.post_data_json['api_key'] == ''
        route.fulfill(json={'ok': False, 'status': 'authentication_failed', 'message': 'API Key 无效或已过期，请更换。', 'latency_ms': 45})
        expect(result).to_have_attribute('data-state', 'error')
        expect(result).to_contain_text('API Key 无效或已过期，请更换。')
        form.locator('[name=resolution]').select_option('1080p')
        expect(result).to_be_hidden()
        button.click()
        pending.pop().fulfill(status=422, json={'detail': '请填写接口地址、Key 和模型 ID。'})
        expect(result).to_contain_text('请填写接口地址、Key 和模型 ID。')
        expect(button).to_be_enabled()

        button.click()
        pending.pop().abort()
        expect(result).to_contain_text('连接测试失败，请检查网络或接口地址后重试。')
        expect(button).to_be_enabled()
        expect(page.locator('#save-model-settings')).to_be_enabled()
        box = result.bounding_box()
        assert box and box['x'] >= 0 and box['x'] + box['width'] <= width
        page.clock.install()
        button.click()
        route = pending.pop()
        page.clock.fast_forward(30001)
        expect(result).to_contain_text('连接测试超时，请检查接口地址或稍后重试。')
        expect(button).to_be_enabled()
        route.abort()
        assert len([call for call in calls if call == ('/api/model-settings/test', 'POST')]) == 5
        assert not any(method == 'PUT' or '/api/jobs' in path for path, method in calls)
        page.screenshot(path=f'storage/model-connection-{width}.png', full_page=True)
        page.locator('#save-model-settings').click()
        expect(page.locator('#model-settings-status')).to_have_text('配置已保存 · 可测试连接')
        expect(result).to_be_hidden()
        assert not errors, errors
        browser.close()
        print(f'PASS model connection UI {width}x{height}')


if __name__ == '__main__':
    check(1440, 1000)
    check(390, 844)
