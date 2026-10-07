import json
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_production_api import client, complete_draft


@pytest.fixture
def assistant_page(browser, client):
    context = browser.new_context(viewport={'width': 1440, 'height': 1000})
    page = context.new_page()
    state = {'assist': [], 'held': [], 'random': [], 'runs': [], 'errors': []}
    page.on('pageerror', lambda e: state['errors'].append(str(e)))

    def route(r):
        request = r.request
        path = urlparse(request.url).path
        if path in ('/api/production/inspiration-assist', '/api/production/inspiration-random'):
            state['random' if path.endswith('inspiration-random') else 'assist'].append(json.loads(request.post_data))
            state['held'].append(r)
            return
        if path == '/api/production/runs' and request.method == 'POST':
            state['runs'].append(json.loads(request.post_data))
            r.fulfill(status=422, json={'detail': '测试提交保留输入'})
            return
        response = client.request(request.method, request.url, content=request.post_data_buffer,
            headers={k: v for k, v in request.headers.items() if k not in ('host', 'content-length')})
        r.fulfill(status=response.status_code, body=response.content,
            headers={k: v for k, v in response.headers.items() if k not in ('content-length', 'content-encoding', 'transfer-encoding')})

    page.route('**/*', route)
    yield page, state
    context.close()


def test_assist_without_assets_handles_success_error_and_inflight_edits(assistant_page):
    page, state = assistant_page
    page.goto('https://testserver/')
    editor, button = page.locator('#variation-inspiration'), page.locator('#inspiration-assist')
    expect(button).to_be_disabled()
    expect(button).to_have_text('AI 润色')
    editor.fill('  \n　')
    expect(button).to_be_disabled()
    editor.fill('先拍衣服，再拉远')
    expect(button).to_be_enabled()
    expect(page.locator('#studio-generate-submit')).to_be_disabled()
    button.click()
    expect(button).to_be_disabled()
    assert state['assist'] == [{'inspiration': '先拍衣服，再拉远'}]
    state['held'].pop().fulfill(json={'inspiration': '先拍衣服局部细节，再慢慢拉远至全身，人物保持自然放松。', 'request_id': 'one'})
    expect(editor).to_have_value('先拍衣服局部细节，再慢慢拉远至全身，人物保持自然放松。')
    button.click()
    expect(button).to_be_disabled()
    editor.fill('用户的新想法')
    state['held'].pop().fulfill(json={'inspiration': '过时的建议', 'request_id': 'two'})
    expect(page.locator('#inspiration-status')).to_contain_text('未覆盖')
    expect(editor).to_have_value('用户的新想法')
    button.click()
    expect(button).to_be_disabled()
    state['held'].pop().fulfill(status=504, json={'detail': '灵感生成超时，原内容已保留。'})
    expect(page.locator('#inspiration-status')).to_contain_text('超时')
    expect(editor).to_have_value('用户的新想法')
    page.locator('#variation-clear').click()
    expect(editor).to_have_value('')
    expect(button).to_be_disabled()
    assert not state['runs'] and not state['errors']


def test_top_submission_uses_confirmed_text_while_assist_pending(assistant_page, client):
    page, state = assistant_page
    draft = complete_draft(client)
    page.add_init_script('localStorage.setItem("production-current-draft-v1",' + json.dumps(draft['id']) + ');')
    page.goto('https://testserver/')
    page.locator('#variation-inspiration').fill('此处灵感不应进入普通任务')
    expect(page.locator('#inspiration-assist')).to_be_enabled()
    page.locator('#inspiration-assist').click()
    expect(page.locator('#inspiration-assist')).to_be_disabled()
    expect(page.locator('#studio-generate-submit')).to_be_enabled()
    page.locator('#studio-generate-submit').click()
    expect(page.locator('#production-status')).to_contain_text('测试提交保留输入')
    assert len(state['runs']) == 1 and state['runs'][0]['variation'] == {'inspiration':'此处灵感不应进入普通任务','creation_mode':'guided'}
    state['held'].pop().fulfill(json={'inspiration': '不应回填的旧响应', 'request_id': 'old'})
    expect(page.locator('#variation-inspiration')).to_have_value('此处灵感不应进入普通任务')
    assert not state['errors']


def test_clear_invalidates_pending_assist_and_allows_a_fresh_request(assistant_page):
    page, state = assistant_page
    page.goto('https://testserver/')
    editor = page.locator('#variation-inspiration')
    button = page.locator('#inspiration-assist')
    editor.fill('准备清空的灵感')
    expect(button).to_be_enabled()
    button.click()
    expect(button).to_be_disabled()
    old = state['held'].pop()
    page.locator('#variation-clear').click()
    expect(editor).to_have_value('')
    expect(button).to_be_disabled()
    editor.fill('新的想法')
    expect(button).to_be_enabled()
    button.click()
    expect(button).to_be_disabled()
    old.fulfill(json={'inspiration': '旧响应不能覆盖新请求', 'request_id': 'old'})
    expect(editor).to_have_value('新的想法')
    expect(button).to_be_disabled()
    state['held'].pop().fulfill(json={'inspiration': '以稳定的中景展示人物穿搭，人物轻轻调整重心，最后自然停住。', 'request_id': 'new'})
    expect(editor).to_have_value('以稳定的中景展示人物穿搭，人物轻轻调整重心，最后自然停住。')
    expect(button).to_be_enabled()
    assert state['assist'] == [{'inspiration': '准备清空的灵感'}, {'inspiration': '新的想法'}]
    assert not state['runs'] and not state['errors']


@pytest.mark.parametrize('width', [1440, 390, 320])
def test_dice_preview_edit_save_reload_and_confirm(assistant_page, client, width):
    page, state = assistant_page
    page.set_viewport_size({'width':width,'height':1000})
    draft = complete_draft(client)
    page.add_init_script('localStorage.setItem("production-current-draft-v1",' + json.dumps(draft['id']) + ');')
    page.goto('https://testserver/')
    editor = page.locator('#variation-inspiration')
    dice = page.locator('#inspiration-random')
    submit = page.locator('#variation-submit')
    expect(submit).to_be_disabled()
    expect(dice).to_be_enabled()
    with page.expect_request('**/api/production/inspiration-random'):
        dice.click()
    expect(dice).to_be_disabled()
    assert not state['runs'] and len(state['random']) == 1
    text = '以固定全身构图展示穿搭，人物自然站定并轻轻调整重心，用简洁的停顿表现整体轮廓。'
    state['held'].pop().fulfill(json={'inspiration':text,'style':'极简静态'})
    expect(editor).to_have_value(text)
    expect(dice).to_have_text('🎲 换一个')
    expect(submit).to_be_enabled()
    import os
    from pathlib import Path
    if os.environ.get('INSPIRATION_PREVIEW_DIR'):
        page.locator('#variation-editor').screenshot(path=str(Path(os.environ['INSPIRATION_PREVIEW_DIR'])/f'preview-{width}.png'))
    edited = text + '不要转身。'
    editor.fill(edited)
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    page.reload()
    expect(editor).to_have_value(edited)
    submit.click()
    expect(page.locator('#production-status')).to_contain_text('测试提交保留输入')
    assert state['runs'][0]['variation'] == {'inspiration':edited,'creation_mode':'guided'}
    assert not state['errors']
    for selector in ['#inspiration-random','#variation-submit']:
        box = page.locator(selector).bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= width


def test_dice_undo_and_stale_reply(assistant_page, client):
    page, state = assistant_page
    draft = complete_draft(client)
    page.add_init_script('localStorage.setItem("production-current-draft-v1",' + json.dumps(draft['id']) + ');')
    page.goto('https://testserver/')
    editor, dice = page.locator('#variation-inspiration'), page.locator('#inspiration-random')
    editor.fill('我的原始想法')
    with page.expect_request('**/api/production/inspiration-random'):
        dice.click()
    expect(dice).to_be_disabled()
    text = '人物自然向前走几步后停下，轻轻侧身展示穿搭，镜头保持中景，整体节奏舒缓自然。'
    state['held'].pop().fulfill(json={'inspiration':text,'style':'自然松弛'})
    expect(editor).to_have_value(text)
    page.locator('#inspiration-undo').click()
    expect(editor).to_have_value('我的原始想法')
    with page.expect_request('**/api/production/inspiration-random'):
        dice.click()
    expect(dice).to_be_disabled()
    old = state['held'].pop()
    page.locator('#variation-clear').click()
    old.fulfill(json={'inspiration':text,'style':'自然松弛'})
    expect(editor).to_have_value('')
    assert not state['runs'] and not state['errors']


def test_switch_start_invalidates_old_response_before_new_draft_arrives(assistant_page, client):
    page, state = assistant_page
    old_draft = complete_draft(client)
    new_draft = client.post('/api/production/drafts',json={}).json()
    page.add_init_script('''let factory;
      Object.defineProperty(window,'createProductionRuns', {
        get(){return factory}, set(fn){factory=(options)=>{window.testChangeDraft=options.changeDraft;return fn(options)}}
      });''')
    page.add_init_script('localStorage.setItem("production-current-draft-v1",' + json.dumps(old_draft['id']) + ');')
    page.goto('https://testserver/')
    editor = page.locator('#variation-inspiration')
    editor.fill('切换前的用户想法')
    with page.expect_request('**/api/production/inspiration-random'):
        page.locator('#inspiration-random').click()
    held = state['held'].pop()
    page.evaluate('window.testChangeDraft(() => new Promise(resolve => {window.finishDraftSwitch=resolve})); void 0')
    page.wait_for_function('typeof window.finishDraftSwitch === "function"')
    held.fulfill(json={'inspiration':'人物自然向前走几步后停下，轻轻侧身展示穿搭，镜头保持中景，整体节奏舒缓自然。'})
    expect(editor).to_have_value('切换前的用户想法')
    page.evaluate('draft => window.finishDraftSwitch(draft)',new_draft)
    expect(editor).to_have_value('')
    editor.fill('新草稿的拍摄想法')
    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
    assert client.get('/api/production/drafts/'+old_draft['id']).json()['inspiration'] == '切换前的用户想法'
    assert client.get('/api/production/drafts/'+new_draft['id']).json()['inspiration'] == '新草稿的拍摄想法'
    assert not state['errors']


def test_independent_assist_settings_roundtrip(browser, client):
    context = browser.new_context()
    page = context.new_page()

    def route(r):
        request = r.request
        response = client.request(request.method, request.url, content=request.post_data_buffer,
            headers={k: v for k, v in request.headers.items() if k not in ('host', 'content-length')})
        r.fulfill(status=response.status_code, body=response.content,
            headers={k: v for k, v in response.headers.items() if k not in ('content-length', 'content-encoding', 'transfer-encoding')})

    page.route('**/*', route)
    try:
        page.goto('https://testserver/admin/settings#variation')
        form = page.locator('#inspiration-settings-form')
        expect(form.locator('fieldset')).to_be_enabled()
        form.locator('[name=model]').fill('fast-text-model')
        form.locator('[name=timeout_seconds]').fill('12')
        form.locator('button[type=submit]').click()
        expect(page.locator('#inspiration-settings-status')).to_contain_text('已保存')
        page.reload()
        expect(form.locator('[name=model]')).to_have_value('fast-text-model')
        expect(form.locator('[name=timeout_seconds]')).to_have_value('12')
        assert client.get('/api/variation-settings').json()['config']['model'] != 'fast-text-model'
    finally:
        context.close()
