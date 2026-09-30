import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import expect
from tests.test_account_frontend import browser

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def library_page(browser):
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    state = {'manage': True, 'status': None, 'calls': [], 'fail': False, 'errors': []}
    page.on('pageerror', lambda error: state['errors'].append(str(error)))
    env = Environment(loader=FileSystemLoader(ROOT/'app/templates'), autoescape=True)

    def route(r):
        req = r.request
        path = urlparse(req.url).path
        if path.startswith('/static/'):
            file = ROOT/'app'/path.lstrip('/')
            r.fulfill(content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream', body=file.read_bytes())
            return
        if path == '/people':
            r.fulfill(content_type='text/html', body=env.get_template('people.html').render())
            return
        state['calls'].append((path, req.method, req.post_data_buffer))
        active = state['status'] == 'active'
        person = {'id': 'real-1', 'name': '已授权模特', 'person_type': 'LivenessFace', 'can_manage': state['manage'],
                  'shared': True, 'photo_count': 0, 'video_count': int(active), 'photo_counts': {state['status']: 1} if state['status'] else {}}
        job = {'id': 'job-video', 'person_id': 'real-1', 'asset_id': 'video-1', 'kind': 'person_video',
               'name': '模特.mp4', 'status': state['status'], 'message': '人物视频检查中', 'can_manage': state['manage'],
               'remote_asset_id': 'asset-cloud' if active else None, 'url': '/media/test.mp4'}
        data = {'items': []}
        if path == '/api/auth/me':
            data = {'auth_enabled': True, 'user': {'id': 'me', 'username': '测试管理员', 'role': 'admin' if state['manage'] else 'user'}, 'csrf_token': 'test'}
        elif path == '/api/portrait/people':
            data = {'items': [] if 'removed=true' in req.url else [person]}
        elif path == '/api/portrait/virtual/status':
            data = {'ready': True, 'project_name': 'default', 'requests': []}
        elif path == '/api/production/assets':
            if state['fail']:
                r.fulfill(status=422, content_type='application/json', body=json.dumps({'detail': '人物视频时长需在 2–30 秒之间。'}))
                return
            data = {'id': 'video-1', 'kind': 'person_video'}
        elif path == '/api/portrait/photos' and req.method == 'POST':
            state['status'] = 'queued'
            data = {**job, 'status': 'queued'}
        elif path == '/api/portrait/photos':
            data = {'items': [job] if state['status'] else []}
        elif path == '/api/portrait/people/real-1/photos':
            data = {'items': [job] if state['status'] else []}
        r.fulfill(content_type='application/json', body=json.dumps(data))

    page.route('**/*', route)
    yield page, state
    context.close()


def open_real(page):
    page.goto('http://studio.test/people')
    page.locator('#manage-people').click()
    page.locator('[data-library-type=LivenessFace]').click()
    expect(page.locator('[data-person-id=real-1]')).to_be_visible()


@pytest.mark.parametrize('width,detail', [(1440, False), (390, True)])
def test_real_video_upload_polling_and_preview(library_page, width, detail):
    page, state = library_page
    page.set_viewport_size({'width': width, 'height': 950})
    page.clock.install()
    open_real(page)
    holder = page.locator('[data-person-id=real-1]')
    if detail:
        holder.click()
        holder = page.locator('[data-person-detail]')
    expect(holder.get_by_role('button', name='添加视频', exact=True)).to_be_visible(timeout=1500)
    expect(holder.locator('[data-video-upload]')).to_have_attribute('accept', '.mp4,.mov')
    holder.locator('[data-video-upload]').set_input_files({'name': '模特.mp4', 'mimeType': 'video/mp4', 'buffer': b'fixture'})
    expect(page.locator('.virtual-library-dialog > [data-status]')).to_contain_text('视频已添加')
    upload = next(body for path,method,body in state['calls'] if path == '/api/production/assets')
    assert b'person_video' in upload
    posted = [json.loads(body) for path,method,body in state['calls'] if path == '/api/portrait/photos' and method == 'POST']
    assert posted == [{'person_id': 'real-1', 'asset_id': 'video-1'}]
    state['status'] = 'active'
    page.clock.fast_forward(6000)
    expect(page.locator('.virtual-library-dialog > [data-status]')).to_contain_text('视频已通过检查')
    if not detail:
        expect(page.locator('[data-person-id=real-1]')).to_contain_text('1 段视频可用')
        page.locator('[data-person-id=real-1]').click()
    expect(page.locator('[data-detail-photos]')).to_contain_text('视频可用')
    assert page.locator('video[src]').count() == 0
    page.get_by_role('button', name='打开视频', exact=True).click()
    expect(page.locator('[data-library-preview] video')).to_be_visible()
    page.locator('[data-preview-close]').click()
    expect(page.locator('video[src]')).to_have_count(0)
    assert page.locator('.virtual-library-dialog').evaluate('(el)=>el.scrollWidth <= el.clientWidth + 1')
    shots = ROOT/'storage/real-video-preview'
    shots.mkdir(exist_ok=True)
    page.screenshot(path=str(shots/f'people-{width}.png'))
    assert not state['errors']


def test_shared_person_readonly_hides_upload(library_page):
    page, state = library_page
    state['manage'] = False
    open_real(page)
    expect(page.get_by_role('button', name='添加视频', exact=True)).to_have_count(0)
    page.locator('[data-person-id=real-1]').click()
    expect(page.locator('[data-person-detail]')).to_be_visible()
    expect(page.get_by_role('button', name='添加视频', exact=True)).to_have_count(0)


def test_invalid_video_error_does_not_enqueue(library_page):
    page, state = library_page
    state['fail'] = True
    open_real(page)
    upload = page.locator('[data-person-id=real-1] [data-video-upload]')
    expect(upload).to_have_count(1, timeout=1500)
    upload.set_input_files({'name': 'short.mp4', 'mimeType': 'video/mp4', 'buffer': b'invalid'})
    expect(page.locator('.virtual-library-dialog > [data-status]')).to_contain_text('2–30')
    assert not any(path == '/api/portrait/photos' and method == 'POST' for path,method,_ in state['calls'])
    expect(page.get_by_role('button', name='添加视频', exact=True)).to_be_enabled()
