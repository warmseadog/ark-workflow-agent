"""Photo batches use the real browser UI with isolated upload responses."""
import json
from pathlib import Path
from urllib.parse import urlparse

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import expect
from tests.test_account_frontend import browser

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def library(browser):
    context = browser.new_context(viewport={'width': 1440, 'height': 900})
    page = context.new_page()
    state = {'uploads': [], 'jobs': [], 'fail_at': None, 'errors': [], 'hold_at': None, 'held': []}
    page.on('pageerror', lambda error: state['errors'].append(str(error)))
    env = Environment(loader=FileSystemLoader(ROOT/'app/templates'), autoescape=True)
    people = [{'id': kind, 'person_type': kind, 'name': '人物' + 'LongName' * 12,
               'can_manage': True, 'photo_count': 0, 'photo_counts': {}}
              for kind in ('AIGC', 'LivenessFace')]

    def route(r):
        req = r.request
        path = urlparse(req.url).path
        if path.startswith('/static/'):
            file = ROOT/'app/static'/Path(path).name
            r.fulfill(content_type='text/css' if file.suffix == '.css' else 'application/javascript',
                      body=file.read_text(encoding='utf-8'))
            return
        if path in ('/people', '/admin/settings'):
            template = 'people.html' if path == '/people' else 'admin_settings.html'
            r.fulfill(content_type='text/html', body=env.get_template(template).render())
            return
        data = {}
        if path == '/api/auth/me':
            data = {'auth_enabled': True, 'user': {'id': 'me', 'username': '管理员', 'role': 'admin'}, 'csrf_token': 'test'}
        elif path == '/api/portrait/people':
            data = {'items': [] if 'removed=true' in req.url else people}
        elif path == '/api/portrait/virtual/status':
            data = {'ready': True, 'project_name': 'default', 'requests': []}
        elif path == '/api/portrait/config':
            data = {'project_name': 'default', 'use_storage_credentials': True}
        elif path == '/api/production/assets':
            state['uploads'].append(req.post_data_buffer)
            number = len(state['uploads'])
            if number == state['fail_at']:
                r.fulfill(status=422, content_type='application/json', body=json.dumps({'detail': '无法识别图片'}))
                return
            data = {'id': f'asset-{number}', 'kind': 'face'}
            if number == state['hold_at']:
                state['held'].append((r, data))
                return
        elif path == '/api/portrait/photos' and req.method == 'POST':
            values = req.post_data_json
            data = {**values, 'id': 'job-' + values['asset_id'], 'name': values['asset_id'],
                    'status': 'queued', 'kind': 'face', 'can_manage': True}
            state['jobs'].append(data)
        elif path.endswith('/photos') or path == '/api/portrait/photos':
            data = {'items': state['jobs']}
        r.fulfill(content_type='application/json', body=json.dumps(data))

    page.route('**/*', route)
    yield page, state
    context.close()


def open_library(page, kind='AIGC', detail=False, path='/admin/settings#people'):
    page.goto('http://library.test' + path)
    page.locator('#manage-people').click()
    page.locator(f'[data-library-type={kind}]').click()
    holder = page.locator(f'[data-person-id={kind}]')
    expect(holder.get_by_role('button', name='添加照片', exact=True)).to_be_enabled()
    if detail:
        holder.click()
        holder = page.locator('[data-person-detail]')
    return holder


FILES = [{'name': f'photo-{i}.png', 'mimeType': 'image/png', 'buffer': b'fixture'} for i in range(3)]


@pytest.mark.parametrize('kind,detail,path', [
    ('AIGC', False, '/admin/settings#people'),
    ('LivenessFace', True, '/admin/settings#people'),
    ('AIGC', True, '/people'),
    ('LivenessFace', False, '/people'),
])
def test_batch_uploads_every_photo_to_selected_person(library, kind, detail, path):
    page, state = library
    holder = open_library(page, kind, detail, path)
    with page.expect_file_chooser() as chooser:
        holder.get_by_role('button', name='添加照片', exact=True).click()
    assert chooser.value.is_multiple()
    chooser.value.set_files(FILES)
    expect(page.locator('.virtual-library-dialog > [data-status]')).to_contain_text('已添加 3 张')
    assert len(state['uploads']) == 3
    assert [job['person_id'] for job in state['jobs']] == [kind] * 3
    assert len({job['asset_id'] for job in state['jobs']}) == 3
    assert not holder.locator('[data-video-upload]').evaluate('el => el.multiple')
    assert not state['errors']


def test_failed_photo_does_not_stop_batch_and_same_file_can_retry(library):
    page, state = library
    state['fail_at'] = 2
    holder = open_library(page)
    holder.locator('[data-photo-upload]').set_input_files(FILES)
    status = page.locator('.virtual-library-dialog > [data-status]')
    expect(status).to_contain_text('失败 1 张')
    expect(status).to_contain_text('photo-1.png')
    expect(status).to_contain_text('无法识别图片')
    assert len(state['uploads']) == 3
    assert len(state['jobs']) == 2
    expect(holder.get_by_role('button', name='添加照片', exact=True)).to_be_enabled()
    holder.locator('[data-photo-upload]').set_input_files(FILES[1])
    expect(status).to_contain_text('照片已添加')
    assert len(state['jobs']) == 3


def test_batch_progress_disables_duplicate_submission(library):
    page, state = library
    state['hold_at'] = 2
    holder = open_library(page)
    holder.locator('[data-photo-upload]').set_input_files(FILES)
    status = page.locator('.virtual-library-dialog > [data-status]')
    expect(status).to_contain_text('2/3')
    expect(holder.get_by_role('button', name='添加照片', exact=True)).to_be_disabled()
    assert len(state['jobs']) == 1
    route, data = state['held'].pop()
    route.fulfill(content_type='application/json', body=json.dumps(data))
    expect(status).to_contain_text('已添加 3 张')
    expect(holder.get_by_role('button', name='添加照片', exact=True)).to_be_enabled()
    assert len(state['uploads']) == 3


@pytest.mark.parametrize('width', [320, 390, 430, 768])
def test_mobile_library_controls_fit_and_have_touch_targets(library, width):
    page, state = library
    page.set_viewport_size({'width': width, 'height': 740})
    holder = open_library(page)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    dialog = page.locator('.virtual-library-dialog')
    assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
    for button in holder.locator('.virtual-person-actions button').all():
        assert button.bounding_box()['height'] >= 44
    shots = ROOT/'storage/library-batch-preview'
    shots.mkdir(exist_ok=True)
    page.screenshot(path=str(shots/f'list-{width}.png'))
    holder.click()
    page.locator('[data-person-detail] [data-photo-upload]').set_input_files(FILES)
    expect(page.locator('.virtual-library-dialog > [data-status]')).to_contain_text('已添加 3 张')
    expect(page.locator('[data-detail-photos] article')).to_have_count(3)
    assert dialog.evaluate('el => el.scrollWidth <= el.clientWidth + 1')
    page.screenshot(path=str(shots/f'detail-{width}.png'))
    assert not state['errors']
