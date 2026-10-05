"""Offline browser coverage: real editor scripts, no production or provider traffic."""
import copy
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from tests.browser_template_fixture import template_environment, serve_editor_rules, LOCAL_ACCOUNT, EDITOR_RULES_PATH
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('width', [1440, 390])
def test_audio_choice_restore_and_one_click_retry(width, tmp_path):
    model = {'model':'doubao-seedance-2-0-260128','duration':8,'resolution':'720p','generate_audio':False}
    draft = {'id':'draft1','revision':1,'name':'声音设置测试','source_asset_id':None,'face_asset_ids':[],
             'clothing_asset_ids':[],'prompt':'保留动作','mask':{},'model':model.copy(),'assets':[]}
    old = {'id':'failed1','name':'音频版权失败任务','status':'failed','stage':'generating','message':'生成声音未通过版权检查。',
           'error_kind':'audio_copyright','error':'OutputAudioSensitiveContentDetected.PolicyViolation request-fixture',
           'can_retry_without_audio':True,'generate_audio':True,'model':model['model'],'duration':8,
           'created_at':'2026-09-29T08:56:43Z','can_delete':True,'snapshot':{**copy.deepcopy(draft),'model':{**model,'generate_audio':True}}}
    drafts = {'draft1':draft}; calls=[]; held=[]; errors=[]
    html = template_environment(ROOT, LOCAL_ACCOUNT).get_template('production.html').render()
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge',headless=True)
        page=browser.new_page(viewport={'width':width,'height':1000})
        page.on('pageerror',lambda err:errors.append(str(err)))
        def route_handler(route):
            req=route.request;path=urlsplit(req.url).path
            body=req.post_data_json if req.method in ('PUT','POST') else {}
            if path=='/':route.fulfill(content_type='text/html',body=html)
            elif path==EDITOR_RULES_PATH:serve_editor_rules(route, ROOT, LOCAL_ACCOUNT)
            elif path.startswith('/static/'):route.fulfill(path=str(ROOT/'app'/path.lstrip('/')))
            elif path=='/api/auth/me':route.fulfill(json={'auth_enabled':False,'user':None})
            elif path=='/api/production/model-options':route.fulfill(json={'defaults':model,'items':[{'id':model['model'],'label':'Seedance 2.0','audio_control':True,'resolutions':['720p'],'max_duration':15,'max_images':9,'max_video_seconds':15}]})
            elif path=='/api/production/drafts':route.fulfill(json={'items':list(drafts.values())})
            elif path.startswith('/api/production/drafts/'):
                item=drafts[path.rsplit('/',1)[-1]]
                if req.method=='PUT':item.update(body);item['revision']+=1
                route.fulfill(json=copy.deepcopy(item))
            elif path=='/api/production/runs':route.fulfill(json={'items':[old],'page':1,'pages':1,'total':1,'active_count':0})
            elif path=='/api/production/runs/failed1':route.fulfill(json=old)
            elif path.endswith('/retry-without-audio'):
                calls.append(path);held.append(route)
            elif path=='/api/prompt-templates':route.fulfill(json={'items':[]})
            elif path=='/api/portrait/people':route.fulfill(json={'items':[]})
            elif path=='/api/link-settings':route.fulfill(json={'has_api_key':False})
            else:route.fulfill(status=404,json={'detail':path})
        page.route('**/*',route_handler)
        page.goto('http://127.0.0.1:18769/')
        toggle=page.get_by_role('checkbox',name='生成声音',exact=True)
        expect(toggle).to_be_enabled()
        expect(toggle).not_to_be_checked()
        toggle.check()
        page.wait_for_function('window.productionDraftModel?.generate_audio === true')
        page.wait_for_timeout(650)
        page.reload()
        expect(toggle).to_be_checked()
        def navigate(section):
            if width < 800: page.locator('.workspace-toggle').click()
            page.locator('[data-workspace-page='+section+']').click()
        navigate('tasks')
        expect(page.get_by_text('生成声音未通过版权检查',exact=True)).to_be_visible()
        retry=page.get_by_role('button',name='关闭声音并重试',exact=True)
        retry.click()
        expect(page.locator('[data-run-action="retry-without-audio"]')).to_be_disabled()
        page.wait_for_timeout(150)
        assert len(calls)==1
        silent=copy.deepcopy(old['snapshot']);silent.update(id='draft2',revision=1);silent['model']['generate_audio']=False
        drafts['draft2']=silent
        held.pop().fulfill(json={'draft':silent,'run':{'id':'retry1','status':'queued'}})
        expect(toggle).not_to_be_checked()
        page.wait_for_function("localStorage.getItem('production-current-draft-v1') === 'draft2'")
        page.reload()
        expect(toggle).not_to_be_checked()
        navigate('tasks')
        page.get_by_role('button',name='查看错误详情',exact=True).click()
        expect(page.locator('.run-error-detail')).to_contain_text('request-fixture')
        assert not errors, errors
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
        navigate('create')
        page.locator('.generation-action').screenshot(path=str(tmp_path/f'audio-controls-{width}.png'))
        navigate('tasks')
        page.locator('#production-run-list').screenshot(path=str(tmp_path/f'audio-error-{width}.png'))
        browser.close()
