"""Offline UI boundaries: visible recovery guidance and usable support context."""
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import expect
from tests.test_account_frontend import browser
from tests.test_person_video import setup
from tests.test_support import support_app


@pytest.fixture
def support_page(browser, setup):
    context = browser.new_context(viewport={'width': 390, 'height': 900})
    page = context.new_page()
    context.add_init_script("window.copied=[];Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>window.copied.push(text)}})")
    state = {'role': 'user', 'auth_enabled':True, 'runs_error': None, 'requests': [], 'items': [], 'config': {
        'limits':{'message_max_chars':4000,'task_name_max_chars':160,'request_id_max_chars':64,'page_size_max':50},
        'input_requirements':{
            'source_video':{'text':'MP4 / MOV · 最大 123 MB','accept':'.mp4,.mov'},
            'image':{'text':'图片最大 17 MB','accept':'.png,.jpg'},
            'portrait_photo':{'text':'静态 PNG / JPEG / WebP · 最大 17 MB · 宽高 > 300 且 < 6000 像素','accept':'.png,.jpg,.jpeg,.webp'},
            'person_video':{'text':'MP4 / MOV · 2–30 秒 · 最大 49 MB · 24–60 fps','accept':'.mp4,.mov'},
            'prompt':{'text':'提示词正文最多 9000 字','max_length':9000}},
        'billing_notice':'未配置面向用户的收费规则；供应商服务可能产生费用，请先向管理员确认。',
        'support':{'auth_enabled':True,'login_required':True}}}
    pending = {'id': 'audit-task', 'name': '待确认任务', 'created_at': '2026-10-05T10:00:00+08:00',
        'status': 'needs_attention', 'error_kind': 'submission_uncertain', 'model': 'fixture',
        'message': '提交结果待确认，请核对服务商记录，避免重复提交',
        'error': '响应中断', 'request_id': 'provider-safe-id', 'can_resume': False, 'can_cancel': False,
        'snapshot': {'model': {}, 'assets': []}, 'timing': {'available': False}}
    def route(r):
        req = r.request
        path = urlparse(req.url).path
        state['requests'].append((req.method, path, req.post_data_json if req.post_data and 'application/json' in req.headers.get('content-type', '') else None))
        if path == '/api/auth/me':
            r.fulfill(json={'auth_enabled':state['auth_enabled'], 'user': {'id':'fixture-user','username':'示例用户','role':state['role']} if state['role'] else None, 'csrf_token':'test'}); return
        if path == '/api/support/config':
            r.fulfill(json=state['config']); return
        if path in ('/help','/admin/support'):
            root=Path(__file__).resolve().parents[1]/'app/templates'
            if (root/'help.html').exists():
                r.fulfill(content_type='text/html',body=Environment(loader=FileSystemLoader(root),autoescape=True).get_template('help.html').render(support_admin=path.startswith('/admin'),auth_enabled=True))
            else:r.fulfill(status=404,body='Missing help page')
            return
        if path in ('/api/support/requests','/api/admin/support/requests'):
            if req.method=='POST':
                item={'id':'feedback-1',**req.post_data_json,'status':'open','reply':'','created_at':'2026-10-05T10:30:00+08:00','updated_at':'2026-10-05T10:30:00+08:00'}
                state['items'].append(item);r.fulfill(status=201,json={'item':item})
            else:r.fulfill(json={'items':state['items'],'total':len(state['items']),'page':1,'pages':1,'page_size':10})
            return
        if path=='/api/admin/support/requests/feedback-1' and req.method=='PATCH':
            state['items'][0].update(req.post_data_json);r.fulfill(json={'item':state['items'][0]});return
        if path == '/api/production/runs' or path == '/api/production/runs/audit-task':
            if state['runs_error']:
                r.fulfill(status=500, json=state['runs_error'], headers={'X-Request-ID':'header-safe-id'}); return
            r.fulfill(json=pending if path.endswith('audit-task') else {'items':[pending],'total':1,'pages':1,'page':1,'active_count':0}); return
        if path.startswith('/static/') and path.endswith(('.mp4','.mov','.webm')):
            r.fulfill(status=404, body=''); return
        res = setup.request(req.method, req.url, content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=res.status_code,body=res.content,
            headers={k:v for k,v in res.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    context.route('**/*', route)
    yield page, state
    context.close()


def test_uncertain_task_explains_safe_next_step_and_prefills_feedback(support_page,tmp_path):
    # A hidden title/extra details menu cannot substitute for an actionable warning.
    page, _ = support_page
    page.goto('http://127.0.0.1:18759/#tasks')
    warning = page.locator('[data-run-id="audit-task"] [data-support-warning]')
    expect(warning).to_be_visible()
    expect(warning).to_contain_text('不要重复提交')
    link = warning.get_by_role('link', name='联系管理员')
    expect(link).to_be_visible()
    query = parse_qs(urlparse(link.get_attribute('href')).query)
    assert query['task_id'] == ['audit-task']
    assert query['task_name'] == ['待确认任务']
    assert query['problem_id'] == ['provider-safe-id']
    warning.get_by_role('button', name='复制问题编号').click()
    assert page.evaluate('window.copied') == ['provider-safe-id']
    page.screenshot(path=str(tmp_path/'support-uncertain-mobile.png'),full_page=True)


@pytest.mark.parametrize('problem', ['body-safe-id', 'https://internal.invalid/?api_key=secret'])
def test_http_problem_id_is_copyable_and_rejects_unsafe_values(support_page, problem):
    # The public error number must survive JSON/header parsing without copying URLs/secrets.
    page, state = support_page
    state['runs_error'] = {'detail':'服务暂时无法完成请求，请稍后重试。','request_id':problem}
    page.goto('http://127.0.0.1:18759/#tasks')
    notice = page.locator('[data-support-problem]')
    expect(notice).to_be_visible()
    expected = 'body-safe-id' if problem == 'body-safe-id' else 'header-safe-id'
    expect(notice).to_contain_text(expected)
    notice.get_by_role('button', name='复制问题编号').click()
    assert page.evaluate('window.copied') == [expected]
    assert 'internal.invalid' not in notice.inner_text()
    assert 'api_key' not in notice.inner_text()


@pytest.mark.parametrize('viewport', [{'width':390,'height':900}, {'width':844,'height':390}])
def test_problem_notice_preserves_mobile_account_navigation_and_copy(support_page, viewport):
    page,state=support_page
    page.set_viewport_size(viewport)
    state['runs_error']={'detail':'暂时无法读取任务','request_id':'navigation-safe-id'}
    page.goto('http://127.0.0.1:18759/#tasks')
    notice=page.locator('[data-support-problem]')
    expect(notice).to_contain_text('navigation-safe-id')
    page.locator('.workspace-toggle').click()
    page.locator('.workspace-account summary').click(timeout=2000)
    expect(page.get_by_role('link',name='修改密码',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='退出登录',exact=True)).to_be_visible()
    page.keyboard.press('Escape')
    notice.get_by_role('button',name='复制问题编号').click(timeout=2000)
    assert page.evaluate('window.copied')==['navigation-safe-id']
    expect(notice.get_by_role('link',name='联系管理员')).to_have_attribute('href',re.compile('problem_id=navigation-safe-id'))


@pytest.mark.parametrize('viewport', [{'width':390,'height':900}, {'width':844,'height':390}])
def test_problem_notice_does_not_cover_mobile_generation_controls(support_page, viewport):
    page,state=support_page
    page.set_viewport_size(viewport)
    state['runs_error']={'detail':'暂时无法读取任务','request_id':'generation-safe-id'}
    page.goto('http://127.0.0.1:18759/')
    notice=page.locator('[data-support-problem]')
    expect(notice).to_contain_text('generation-safe-id')
    dock=page.locator('.generation-dock')
    expect(dock).to_be_visible()
    assert notice.bounding_box()['y']+notice.bounding_box()['height'] <= dock.bounding_box()['y']
    page.locator('.generation-dock-summary').click(timeout=2000)
    notice.get_by_role('button',name='复制问题编号').click(timeout=2000)
    assert page.evaluate('window.copied')==['generation-safe-id']


@pytest.mark.parametrize('width', [390,1440])
def test_feedback_submission_admin_reply_and_user_readback(support_page,width,tmp_path):
    # Exercises the complete user/admin UI contract, including preserving task context.
    page,state=support_page
    page.set_viewport_size({'width':width,'height':1000})
    page.goto('http://127.0.0.1:18759/help?task_id=audit-task&task_name=待确认任务&problem_id=provider-safe-id&occurred_at=2026-10-05T10:00:00%2B08:00#feedback')
    expect(page.get_by_label('相关任务名称')).to_have_value('待确认任务')
    expect(page.get_by_label('问题编号')).to_have_value('provider-safe-id')
    page.get_by_label('问题描述').fill('任务已提交，结果一直待确认，请帮助核对原任务。')
    page.get_by_role('button',name='提交反馈',exact=True).click()
    expect(page.locator('#support-history')).to_contain_text('任务已提交')
    submitted=next(body for method,path,body in state['requests'] if method=='POST' and path=='/api/support/requests')
    assert submitted=={'message':'任务已提交，结果一直待确认，请帮助核对原任务。','task_name':'待确认任务','request_id':'provider-safe-id'}
    state['role']='admin'
    page.goto('http://127.0.0.1:18759/admin/support')
    expect(page.locator('#support-history')).to_contain_text('provider-safe-id')
    page.get_by_label('管理员回复').fill('已核对原任务，请在任务记录中继续查询，无需重新生成。')
    page.get_by_label('处理状态').select_option('resolved')
    page.get_by_role('button',name='保存回复',exact=True).click()
    expect(page.locator('#support-status')).to_contain_text('已保存')
    page.screenshot(path=str(tmp_path/f'support-admin-{width}.png'),full_page=True)
    state['role']='user'
    page.goto('http://127.0.0.1:18759/help#feedback')
    expect(page.locator('#support-history')).to_contain_text('无需重新生成')
    expect(page.locator('#support-history')).to_contain_text('已处理')
    assert not page.get_by_role('button',name='保存回复').count()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')


def test_public_contact_reaches_guide_without_login_loop(support_page):
    page,state=support_page
    state['role']=None
    page.goto('http://127.0.0.1:18759/login')
    contact=page.locator('.footer-links').get_by_role('link',name='联系',exact=True)
    assert contact.bounding_box()['height']>=44
    contact.click()
    expect(page).to_have_url('http://127.0.0.1:18759/help#feedback')
    expect(page.get_by_role('heading',name='帮助与反馈',exact=True)).to_be_visible()
    expect(page.get_by_role('link',name='登录后提交反馈',exact=True)).to_be_visible()
    assert not page.get_by_role('button',name='提交反馈',exact=True).is_visible()
    assert not any(path=='/api/support/requests' for _,path,_ in state['requests'])


def test_upload_requirements_follow_server_config_before_selecting_files(support_page):
    # Non-default limits catch client hardcoding and post-error-only instructions.
    page,_=support_page
    page.goto('http://127.0.0.1:18759/')
    expect(page.locator('#flow-stage-source [data-input-requirement]')).to_contain_text('123 MB')
    expect(page.locator('#clothing-picker').locator('..').locator('[data-input-requirement]')).to_contain_text('17 MB')
    expect(page.locator('#person-image-panel [data-input-requirement]')).to_contain_text('6000')
    expect(page.locator('#studio-face-image')).to_have_attribute('accept','.png,.jpg,.jpeg,.webp')
    page.locator('#toggle-video-url').click()
    expect(page.locator('#video-url-entry [data-billing-notice]')).to_contain_text('请先向管理员确认')
    page.locator('[data-person-media=video]').click()
    expect(page.locator('#person-video-panel [data-input-requirement]')).to_contain_text('49 MB')
    page.locator('#flow-stage-generation > summary').click()
    expect(page.locator('#flow-stage-generation [data-input-requirement]')).to_contain_text('9000')


@pytest.mark.parametrize('viewport', [{'width':390,'height':900}, {'width':844,'height':390}])
def test_login_server_failure_shows_copyable_problem_in_open_dialog(support_page, viewport):
    # Error feedback remains usable while the login modal is open.
    page,state=support_page;state['role']=None
    page.set_viewport_size(viewport)
    attempts=[]
    def fail_login(route):
        attempts.append(route.request.post_data_json)
        route.fulfill(status=500,json={'detail':'暂时无法登录','request_id':'login-safe-id'})
    page.route('**/api/auth/login',fail_login)
    page.goto('http://127.0.0.1:18759/login')
    page.locator('#open-login').click()
    page.locator('#login-username').fill('fixture')
    page.locator('#login-password').fill('password')
    page.locator('#login-submit').click()
    page.get_by_role('button',name='复制问题编号').click(timeout=2000)
    assert page.evaluate('window.copied')==['login-safe-id']
    assert not page.locator('#login-password').input_value()
    page.locator('#login-password').fill('password')
    page.locator('#login-submit').click(timeout=2000)
    expect(page.locator('#login-submit')).to_be_enabled()
    assert len(attempts)==2


def test_local_single_user_support_is_available_without_enabling_public_signup(support_page):
    page,state=support_page;state['role']=None;state['auth_enabled']=False
    state['config']['support']={'auth_enabled':False,'login_required':False}
    page.goto('http://127.0.0.1:18759/help#feedback')
    expect(page.get_by_role('button',name='提交反馈',exact=True)).to_be_visible()
    expect(page.locator('#support-local')).to_contain_text('仅限本机')


def test_real_support_api_persists_reply_and_redacts_secrets_in_browser(browser,support_app,tmp_path):
    # Integration: actual auth/CSRF, ownership, sanitization and persistence behind the real UI.
    _,_,_,_,(admin,alice,bob)=support_app
    state={'client':alice}
    context=browser.new_context(viewport={'width':390,'height':950})
    page=context.new_page()
    def route(r):
        req=r.request;path=urlparse(req.url).path
        if path.startswith('/static/'):
            file=Path(__file__).resolve().parents[1]/'app'/path.lstrip('/')
            r.fulfill(path=str(file));return
        response=state['client'].request(req.method,req.url,content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=response.status_code,body=response.content,
            headers={k:v for k,v in response.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    context.route('**/*',route)
    try:
        page.goto('https://testserver/help#feedback')
        page.get_by_label('问题描述').fill('任务异常 api_key=fixture-private-key，请协助核对。')
        page.get_by_role('button',name='提交反馈',exact=True).click()
        expect(page.locator('#support-history')).to_contain_text('任务异常')
        assert 'fixture-private-key' not in page.locator('#support-history').inner_text()
        state['client']=admin
        page.goto('https://testserver/admin/support')
        page.get_by_label('管理员回复').fill('已核对，请继续查询原任务。')
        page.get_by_label('处理状态').select_option('resolved')
        page.get_by_role('button',name='保存回复').click()
        expect(page.locator('#support-status')).to_contain_text('已保存')
        state['client']=alice
        page.goto('https://testserver/help#feedback')
        expect(page.locator('#support-history')).to_contain_text('已核对，请继续查询原任务。')
        page.screenshot(path=str(tmp_path/'support-user-reply-mobile.png'),full_page=True)
        state['client']=bob
        page.goto('https://testserver/help#feedback')
        page.reload()
        expect(page.locator('#support-history')).to_contain_text('暂无反馈')
        assert '任务异常' not in page.locator('#support-history').inner_text()
    finally:context.close()
