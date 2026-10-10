"""Run manually against the local app; media/job requests are intercepted fixtures."""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect


def check(width=1440, height=1000):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        page = browser.new_page(viewport={'width': width, 'height': height})
        errors, calls, jobs = [], [], {}
        page.on('pageerror', lambda error: errors.append(str(error)))

        def handle(route):
            request = route.request
            url = request.url.split('8000')[-1].split('?')[0]
            if url == '/api/model-settings':
                route.fulfill(json={'config': {'provider': 'ark', 'protocol': 'ark', 'mode': 'mock', 'base_url': 'https://ark.cn-beijing.volces.com/api/v3', 'model': 'test-model', 'duration': 5, 'fps': 0, 'resolution': '720p', 'has_api_key': False, 'status': 'demo'}, 'presets': {'ark': {'protocol': 'ark', 'base_url': 'https://ark.cn-beijing.volces.com/api/v3', 'model': 'test-model', 'models': []}}})
            elif url == '/api/jobs' and request.method == 'POST':
                assert b'fixture-video' in request.post_data_buffer or b'new-video' in request.post_data_buffer
                assert b'name="mask_scale"' in request.post_data_buffer
                calls.append('redact')
                job_id = str(len(jobs)+1)
                job = {'id': job_id, 'status': 'defaced', 'progress': 60, 'defaced_url': f'/api/jobs/{job_id}/defaced', 'logs': []}
                jobs[job_id] = job
                route.fulfill(json=job)
            elif url.endswith('/generate'):
                assert b'name="face_image"' in request.post_data_buffer
                assert b'name="clothing_image"' in request.post_data_buffer
                calls.append('generate')
                job = jobs[url.split('/')[3]]
                job.update(status='succeeded', progress=100, provider='mock', download_url='/fixture-result.mp4')
                route.fulfill(json=job)
            elif url.endswith('/defaced'):
                route.fulfill(status=200, content_type='video/mp4', body=b'')
            elif url.startswith('/api/jobs/'):
                route.fulfill(json=jobs[url.split('/')[3]])
            else:
                route.continue_()

        page.route('**/api/**', handle)
        page.goto('http://127.0.0.1:8000/v1')
        expect(page.locator('.asset-entry')).to_have_count(3)
        expect(page.get_by_role('button', name='开始打码', exact=True)).to_have_count(0)
        expect(page.locator('#studio-preview-submit')).not_to_be_visible()
        expect(page.locator('#generation-prompt')).not_to_be_visible()
        expect(page.locator('#studio-generate-submit')).to_be_disabled()
        page.screenshot(path=str(Path('storage') / f'compact-production-initial-{width}.png'), full_page=True)
        page.locator('[name=video]').set_input_files({'name': 'source.mp4', 'mimeType': 'video/mp4', 'buffer': b'fixture-video'})
        page.locator('#redaction-settings > summary').click()
        page.locator('#studio-preview-submit').click()
        expect(page.locator('#studio-defaced-video')).to_be_visible()
        expect(page.locator('#studio-preview-status')).to_contain_text('预览已就绪')
        assert calls == ['redact']
        page.locator('#redaction-settings [data-redaction-save]').click()
        for name in ['clothing_image', 'face_image']:
            page.locator(f'[name={name}]').set_input_files({'name': 'ref.png', 'mimeType': 'image/png', 'buffer': b'fixture-image'})
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        page.locator('#studio-generate-submit').click()
        expect(page.locator('#generation-message')).to_contain_text('演示')
        assert calls == ['redact', 'generate']
        page.locator('#redaction-settings > summary').click()
        page.locator('[name=mask_scale]').fill('1.6')
        page.locator('#studio-preview-submit').click()
        expect(page.locator('#studio-preview-status')).to_contain_text('预览已就绪')
        assert calls == ['redact', 'generate', 'redact']
        page.locator('#redaction-settings [data-redaction-save]').click()
        page.locator('#model-settings-toggle').click()
        expect(page.locator('#model-provider')).to_be_visible()
        bounds = page.locator('#model-settings-panel').bounding_box()
        assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width + 1
        assert bounds['y'] >= 0 and bounds['y'] + bounds['height'] <= height + 1
        page.locator('#close-model-settings').click()
        page.locator('[name=video]').set_input_files({'name': 'another.mp4', 'mimeType': 'video/mp4', 'buffer': b'new-video'})
        page.locator('#studio-generate-submit').click()
        expect(page.locator('#studio-final-video')).to_be_visible()
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        expect(page.locator('#generation-message')).to_contain_text('演示')
        assert calls[-2:] == ['redact', 'generate']
        # Backend failures must remain visible and allow a retry.
        page.route('**/api/jobs', lambda route: route.fulfill(status=500, json={'detail': '测试：预处理失败'}) if route.request.method == 'POST' else route.continue_())
        page.locator('[name=video]').set_input_files({'name': 'failure.mp4', 'mimeType': 'video/mp4', 'buffer': b'bad-video'})
        page.locator('#studio-generate-submit').click()
        expect(page.locator('#production-status')).to_contain_text('测试：预处理失败')
        expect(page.locator('#generation-result')).to_be_visible()
        expect(page.locator('#generation-result')).to_have_attribute('role', 'alert')
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        # Incomplete real-provider configurations explain the missing field before processing media.
        before = len(calls)
        page.route('**/api/model-settings', lambda route: route.fulfill(json={'config': {'mode': 'http', 'message': '请补齐模型配置'}}))
        page.locator('#studio-generate-submit').click()
        expect(page.locator('#production-status')).to_contain_text('请补齐模型配置')
        expect(page.locator('#generation-result')).to_be_visible()
        expect(page.locator('#generation-message')).to_contain_text('请补齐模型配置')
        assert len(calls) == before
        # A configured real provider now reaches generation submission.
        page.unroute('**/api/jobs')
        page.route('**/api/model-settings', lambda route: route.fulfill(json={'config': {'mode': 'http', 'message': '', 'generation_message': ''}}))
        page.locator('[name=video]').set_input_files({'name': 'real-mode.mp4', 'mimeType': 'video/mp4', 'buffer': b'new-video'})
        page.locator('#studio-generate-submit').click()
        expect(page.locator('#studio-final-video')).to_be_visible()
        assert calls[-2:] == ['redact', 'generate']
        assert not errors, errors
        print(f'PASS {width}x{height}: 3 inputs; preview only; automatic sequence; reuse and invalidation; failure and retry; real-provider guard; panel bounds')
        browser.close()


if __name__ == '__main__':
    check()
    check(390, 844)
