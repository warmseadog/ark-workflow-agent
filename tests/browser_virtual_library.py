"""Actual local API + browser, fake official upstream. Never calls paid services."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from io import BytesIO
from PIL import Image
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from app import main, portrait_service, portrait_library, production_worker

ROOT=Path(__file__).resolve().parents[1]

def check(width):
    with TemporaryDirectory(prefix='virtual-browser-',dir=ROOT/'storage') as tmp:
        settings=replace(main.settings,storage_dir=Path(tmp),seedance_mode='mock')
        portrait_service.save_config(settings,{'access_key':'test-ak','secret_key':'test-sk'})
        image=BytesIO();Image.new('RGB',(400,500),'#c6b4a1').save(image,format='PNG');picture=image.getvalue()
        calls=[]
        group={'Id':'group-virtual','Name':'虚拟模特','GroupType':'AIGC','ProjectName':'default'}
        remote={'Id':'asset-virtual','GroupId':group['Id'],'AssetType':'Image','ProjectName':'default','Status':'Active','Name':'虚拟模特参考图','URL':'https://ark-asset.cn-beijing.volcengine.com/test.png'}
        def official(self,action,payload):
            calls.append((action,payload))
            if action=='CreateAssetGroup':return {'Id':group['Id']}
            if action=='GetAssetGroup':return {**group,'Id':'group-real','GroupType':'LivenessFace'} if payload.get('Id')=='group-real' else group
            if action=='ListAssetGroups':return {'Items':[group]}
            if action=='ListAssets':return {'Items':[remote]}
            if action=='GetAsset':return {**remote,'Id':'asset-real','GroupId':'group-real'} if payload.get('Id')=='asset-real' else remote
            if action=='CreateAsset':return {'Id':remote['Id']}
            raise AssertionError(action)
        with patch.object(main,'settings',settings),patch.object(production_worker,'wake',lambda *_:None),patch.object(portrait_service.ArkPortraitClient,'_request',official),patch.object(portrait_service,'download_image',lambda remote,path:path.write_bytes(picture)),patch.object(portrait_library,'upload_photo',lambda *_:'https://test.invalid/photo'):
            client=TestClient(main.app,base_url='http://127.0.0.1:18749')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(channel='msedge')
                page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                def route(reqroute):
                    req=reqroute.request
                    response=client.request(req.method,req.url,content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k.lower() not in {'host','content-length'}})
                    reqroute.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-encoding','transfer-encoding'}})
                page.route('**/*',route)
                page.goto('http://127.0.0.1:18749/admin/settings#people')
                page.locator('#manage-people').click()
                expect(page.locator('.virtual-library-dialog')).to_be_visible()
                expect(page.locator('#virtual-asset-id')).not_to_be_visible()
                page.locator('[data-new]').click()
                page.locator('#virtual-person-name').fill('虚拟模特')
                page.locator('[data-create] button[type=submit]').click()
                row=page.locator('.virtual-person')
                expect(row).to_have_count(1)
                row.locator('[data-action=rename]').click()
                row.locator('input[name=name]').fill('短发女模特')
                row.locator('[data-action=save-name]').click()
                expect(row.locator('strong')).to_have_text('短发女模特')
                row.locator('[data-photo-upload]').set_input_files({'name':'face.png','mimeType':'image/png','buffer':picture})
                expect(row).to_contain_text('正在检查')
                lib=portrait_library.PortraitLibrary(settings)
                assert len(lib.people())==1
                lib.process_one()
                with lib.store.connection() as db:db.execute('UPDATE portrait_photos SET next_check=0')
                lib.process_one()
                expect(row).to_contain_text('1 张照片可用',timeout=10000)
                assert page.locator('.virtual-library-dialog').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
                page.locator('.virtual-library-dialog').screenshot(path=str(ROOT/'storage'/f'virtual-management-{width}.png'))
                page.locator('[data-close]').click()
                page.goto('http://127.0.0.1:18749/')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert page.locator('#portrait-dialog').count() == 0
                assert page.locator('#person-sync').count() == 0
                page.locator('#person-picker summary').click()
                page.locator('[data-person-type=AIGC]').click()
                page.locator('[data-person-id]').click()
                page.locator('[data-photo-use]').click()
                expect(page.locator('#person-current')).to_contain_text('短发女模特')
                expect(page.locator('#production-status')).to_contain_text('官方虚拟人物')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                page.reload()
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                expect(page.locator('#person-current')).to_contain_text('短发女模特')
                page.goto('http://127.0.0.1:18749/admin/settings#people')
                page.locator('#manage-people').click()
                row.locator('[data-action=remove]').click()
                row.locator('[data-action=cancel-remove]').click()
                expect(row).to_have_count(1)
                row.locator('[data-action=remove]').click()
                row.locator('[data-action=confirm-remove]').click()
                expect(row).to_have_count(0)
                expect(page.locator('[data-more], [data-sync], [data-show-import]')).to_have_count(0)
                page.locator('[data-recycle] > summary').click()
                page.locator('[data-action=restore]').click()
                expect(row.locator('strong')).to_have_text('短发女模特')
                expect(row).to_contain_text('1 张照片可用')
                expect(page.locator('[data-assets], [data-import]')).to_have_count(0)
                assert page.locator('.virtual-library-dialog').evaluate('(el) => el.scrollWidth <= el.clientWidth + 1')
                assert len([c for c in calls if c[0]=='CreateAssetGroup'])==1
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                page.locator('[data-library-type=LivenessFace]').click()
                expect(row).to_have_count(0)
                assert len([c for c in calls if c[0]=='CreateAssetGroup']) == 1
                page.locator('[data-close]').click()
                page.screenshot(path=str(ROOT/'storage'/f'backend-people-{width}.png'))
                page.goto('http://127.0.0.1:18749/')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                page.locator('#person-picker summary').click()
                page.locator('[data-person-type=AIGC]').click()
                page.locator('#person-search').fill('asset-real')
                expect(page.locator('.person-empty')).to_contain_text('请按人物名称搜索')
                page.locator('#person-search').fill('短发女模特')
                page.locator('[data-person-id]').click()
                page.locator('[data-photo-use]').click()
                expect(page.locator('#person-current')).to_contain_text('短发女模特')
                expect(page.locator('#production-status')).to_contain_text('官方虚拟人物')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert not errors,errors
                browser.close()
                print(f'PASS virtual library {width}: create, rename, direct upload, Active, use, reload, remove/cancel, restore, no cloud import, backend, clean frontend')

if __name__=='__main__':
    check(1440);check(390)
