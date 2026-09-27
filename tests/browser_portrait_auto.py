"""Automatic QR UI regression with an isolated, mocked official API."""
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1]

def check(width):
    sessions={}; errors=[]; calls={'new':0,'cancel':0}; status={'value':'pending'}
    html='<html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/portrait.css"></head><body><button data-portrait-open="auth">真人认证</button>'+ (ROOT/'app/templates/portrait_panel.html').read_text(encoding='utf-8')+'<script src="/static/portrait.js"></script></body></html>'
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='msedge')
        page=browser.new_page(viewport={'width':width,'height':850})
        page.on('pageerror',lambda e:errors.append(str(e)))
        def handle(route):
            req=route.request; path=urlsplit(req.url).path
            if path=='/': return route.fulfill(body=html,content_type='text/html')
            if path.startswith('/static/'): return route.fulfill(path=str(ROOT/'app'/path.lstrip('/')))
            if path=='/api/portrait/config': return route.fulfill(json={'mode':'automatic','project_name':'default','has_credentials':True,'has_access_key':True,'has_secret_key':True,'use_storage_credentials':True})
            if path=='/api/portrait/sessions':
                key=req.post_data_json['request_id']
                if key not in sessions:
                    calls['new']+=1; sessions[key]='session-'+str(calls['new'])
                return route.fulfill(json={'id':sessions[key],'status':'pending','qr_url':'/api/portrait/sessions/'+sessions[key]+'/qr','message':'请本人扫码'})
            if path.endswith('/qr'): return route.fulfill(body='<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="black"/></svg>',content_type='image/svg+xml')
            if path.startswith('/api/portrait/sessions/'):
                if req.method=='DELETE': calls['cancel']+=1; return route.fulfill(json={'status':'cancelled'})
                return route.fulfill(json={'status':status['value'],'message':'官方真人认证已通过' if status['value']=='verified' else '等待认证'})
            if path=='/api/portrait/assets': return route.fulfill(json={'items':[]})
            return route.fulfill(status=404,json={})
        page.route('**/*',handle)
        page.goto('http://127.0.0.1:18749/')
        for _ in range(12):
            page.get_by_role('button',name='真人认证',exact=True).click()
            expect(page.locator('#portrait-qr-image')).to_be_visible()
            expect(page.locator('#portrait-manual')).to_be_hidden()
            page.locator('#portrait-close').click()
        assert calls['new']==1,calls
        page.reload()
        page.get_by_role('button',name='真人认证',exact=True).click()
        expect(page.locator('#portrait-qr-image')).to_be_visible()
        assert calls['new']==1,calls
        page.locator('#portrait-auto-retry').click()
        expect(page.locator('#portrait-qr-image')).to_be_visible()
        expect(page.locator('#portrait-auto-retry')).to_be_enabled()
        assert calls=={'new':2,'cancel':1},calls
        assert page.locator('#portrait-dialog').evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
        status['value']='verified'
        expect(page.locator('#portrait-invite-status')).to_contain_text('已通过',timeout=8000)
        expect(page.locator('#portrait-qr')).to_be_hidden()
        assert not errors,errors
        browser.close()
    print('PASS automatic QR',width,'open/reopen/reload/replace/status/mobile')

if __name__=='__main__':
    check(1440); check(390)
