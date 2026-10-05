"""Local browser hair preview/settings workflow; detector is deterministic, no cloud calls."""
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import numpy as np
from PIL import Image
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from app import main, hairstyle_mask, production_worker
ROOT=Path(__file__).resolve().parents[1]

def check(width):
    with TemporaryDirectory(prefix='hair-browser-',dir=ROOT/'storage') as tmp:
        settings=replace(main.settings,storage_dir=Path(tmp),seedance_mode='mock')
        frame=np.zeros((500,400,3),dtype=np.uint8)
        frame[:]=[170,145,125]
        frame[:160]=[65,48,42]
        frame[160:420,90:310]=np.random.default_rng(8).integers(90,240,(260,220,3),dtype=np.uint8)
        data=BytesIO();Image.fromarray(frame).save(data,format='PNG');picture=data.getvalue()
        calls=[]
        def detect(image,threshold):calls.append(threshold);return [[90,160,310,420,.9]]
        with patch.object(main,'settings',settings),patch.object(production_worker,'wake',lambda *_:None),patch.object(hairstyle_mask,'_detect_faces',detect):
            client=TestClient(main.app,base_url='http://127.0.0.1:18749')
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='msedge');page=browser.new_page(viewport={'width':width,'height':1100});errors=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                def route(r):
                    req=r.request
                    response=client.request(req.method,req.url,content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k.lower() not in {'host','content-length'}})
                    r.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-encoding','transfer-encoding'}})
                page.route('**/*',route);page.goto('http://127.0.0.1:18749/')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                expect(page.locator('#hairstyle-picker')).to_be_visible()
                page.locator('#studio-hairstyle-image').set_input_files({'name':'发型参考.png','mimeType':'image/png','buffer':picture})
                expect(page.locator('#hairstyle-mask-preview')).to_be_visible()
                expect(page.locator('#hairstyle-mask-status')).to_contain_text('已遮挡 1 张人脸')
                expect(page.locator('#hairstyle-mask-summary')).to_have_text('1.0 倍')
                first_url=page.locator('#hairstyle-mask-preview').get_attribute('src')
                page.locator('.hairstyle-mask-options > summary').click()
                expect(page.locator('#hairstyle-mask-scale')).to_have_value('1')
                expect(page.locator('[name=mask_scale]')).to_have_value('1.4')
                page.locator('#hairstyle-mask-scale').fill('1.05');page.locator('#hairstyle-mask-threshold').fill('0.15')
                expect(page.locator('#hairstyle-mask-status')).to_contain_text('已遮挡 1 张人脸')
                expect(page.locator('#hairstyle-mask-preview')).to_be_visible()
                assert first_url!=page.locator('#hairstyle-mask-preview').get_attribute('src')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                draft_id=page.evaluate("localStorage.getItem('production-current-draft-v1')")
                draft=client.get('/api/production/drafts/'+draft_id).json()
                assert draft['hairstyle_mask']=={'mask_scale':1.05,'threshold':.15}
                assert draft['mask']['mask_scale']==1.4
                page.reload();expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                expect(page.locator('#hairstyle-picker')).to_be_visible()
                expect(page.locator('#hairstyle-mask-preview')).to_be_visible()
                expect(page.locator('#hairstyle-mask-summary')).to_have_text('1.05 倍')
                page.locator('#hairstyle-references').screenshot(path=str(ROOT/'storage'/f'hairstyle-mask-{width}.png'))
                page.locator('#hairstyle-enabled').uncheck();expect(page.locator('#hairstyle-mask-panel')).to_be_hidden()
                page.locator('#hairstyle-enabled').check();expect(page.locator('#hairstyle-mask-preview')).to_be_visible()
                assert .15 in calls and .2 in calls
                assert not errors,errors
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                browser.close();print(f'PASS hair {width}: automatic preview, independent defaults, adjust, cache, restore, enabled state')
if __name__=='__main__':
    check(1440);check(390)
