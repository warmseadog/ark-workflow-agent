"""Exercise the real app through TestClient; no external services or paid jobs."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from app import main, generation_settings, model_catalog


def check(width):
    with TemporaryDirectory(prefix='model-selection-',dir=ROOT/'storage') as tmp:
        settings=replace(main.settings,storage_dir=Path(tmp),seedance_mode='mock')
        generation_settings.save_config(settings,{'duration':8})
        rows=model_catalog.catalog(settings)['items']
        for row in rows:
            if row['id'] in {model_catalog.SD25,'doubao-seedance-2-0-fast-260128'}:
                row.update(enabled=True,verified=True)
        model_catalog.save_catalog(settings,{'items':rows})
        with patch.object(main,'settings',settings), sync_playwright() as p:
            client=TestClient(main.app,base_url='http://127.0.0.1:18759')
            browser=p.chromium.launch(channel='msedge')
            page=browser.new_page(viewport={'width':width,'height':1000})
            errors,calls=[],[]
            page.on('pageerror',lambda err:errors.append(str(err)))
            def route(r):
                req=r.request; calls.append((req.method,req.url))
                res=client.request(req.method,req.url,content=req.post_data_buffer,
                    headers={k:v for k,v in req.headers.items() if k.lower() not in {'host','content-length'}})
                r.fulfill(status=res.status_code,body=res.content,
                    headers={k:v for k,v in res.headers.items() if k.lower() not in {'content-length','content-encoding','transfer-encoding'}})
            page.route('**/*',route)
            page.goto('http://127.0.0.1:18759/')
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            form=page.locator('#model-settings-form')
            assert form.locator('[name=api_key]').count()==0
            assert form.locator('[name=base_url]').count()==0
            selector=form.locator('[name=model]')
            selector.select_option(model_catalog.SD25)
            expect(page.locator('#generation-duration-note')).to_contain_text('跟随动作视频')
            assert '4k' not in form.locator('[name=resolution] option').evaluate_all('(nodes)=>nodes.map(n=>n.value)')
            form.locator('[name=resolution]').select_option('1080p')
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            page.reload(); expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            expect(selector).to_have_value(model_catalog.SD25)
            expect(form.locator('[name=resolution]')).to_have_value('1080p')
            assert generation_settings.load_config(settings).model==model_catalog.SD20
            assert client.get('/api/production/drafts').json()['items'][0]['model']=={
                'model':model_catalog.SD25,'duration':-1,'resolution':'1080p'}
            page.screenshot(path=str(ROOT/f'storage/model-selection-{width}.png'),full_page=True)
            page.locator('.generation-action').screenshot(path=str(ROOT/f'storage/inline-generation-{width}.png'))
            assert page.locator('#redaction-settings, .corner-settings').count() == 0
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            if width >= 720:
                boxes=[form.locator('[name='+name+']').bounding_box() for name in ['model','resolution']]
                boxes += [page.locator('#generation-duration-note').bounding_box(),page.locator('#studio-generate-submit').bounding_box()]
                assert max(b['y']+b['height'] for b in boxes)-min(b['y']+b['height'] for b in boxes)<4,boxes
            selector.select_option('doubao-seedance-2-0-fast-260128')
            expect(form.locator('[name=resolution]')).to_have_value('720p')
            assert form.locator('[name=resolution] option').evaluate_all('(nodes)=>nodes.map(n=>n.value)')==['480p','720p']
            expect(form.locator('[name=duration]')).to_be_visible()
            form.locator('[name=duration]').fill('6')
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            current=client.get('/api/production/drafts').json()['items'][0]
            assert current['model']['duration']==6
            form.locator('[name=duration]').fill('2')
            expect(page.locator('#model-settings-error')).to_be_visible()
            assert page.evaluate('window.generationOptions.available()') is False
            form.locator('[name=duration]').fill('7')
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            page.reload();expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            expect(form.locator('[name=duration]')).to_have_value('7')
            assert not any('/api/model-settings' in url for method,url in calls)
            page.goto('http://127.0.0.1:18759/admin/settings#redaction')
            mask=page.locator('#redaction-form')
            expect(mask.locator('fieldset')).to_be_enabled()
            mask.locator('[name=mask_scale]').fill('1.8')
            mask.locator('[name=keep_audio]').uncheck()
            mask.locator('[name=mask_mode]').select_option('face_hair_primary')
            expect(mask.locator('[name=blur_style]')).to_have_value('mosaic')
            mask.locator('[type=submit]').click()
            expect(page.locator('#redaction-status')).to_contain_text('已保存')
            page.reload()
            expect(mask.locator('[name=mask_scale]')).to_have_value('1.8')
            expect(mask.locator('[name=keep_audio]')).not_to_be_checked()
            assert client.get('/api/production/drafts/'+current['id']).json()['mask']['mask_scale']==1.4
            assert client.post('/api/production/drafts',json={}).json()['mask']['mask_scale']==1.8
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(ROOT/f'storage/inline-admin-redaction-{width}.png'),full_page=True)
            page.goto('http://127.0.0.1:18759/admin/settings#model')
            expect(page.locator('#model-api-key')).to_be_visible()
            rows=page.locator('[data-catalog-id]')
            expect(rows).to_have_count(4)
            sd25=page.locator('[data-catalog-id="'+model_catalog.SD25+'"]')
            sd25.locator('[data-field=enabled]').uncheck()
            page.locator('#save-model-catalog').click()
            expect(page.locator('#model-catalog-status')).to_contain_text('已保存')
            assert model_catalog.SD25 not in [x['id'] for x in model_catalog.editor_options(settings)['items']]
            assert not errors,errors
            browser.close()
    print(f'PASS model selection {width}',flush=True)

if __name__=='__main__':
    check(1440)
    check(390)
    check(320)
