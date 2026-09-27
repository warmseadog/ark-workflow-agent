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
            if action=='GetAssetGroup':return group
            if action=='ListAssetGroups':return {'Items':[group]}
            if action=='ListAssets':return {'Items':[remote]}
            if action=='GetAsset':return remote
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
                page.goto('http://127.0.0.1:18749/')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                page.locator('.virtual-library-open').click()
                expect(page.locator('.virtual-library-dialog')).to_be_visible()
                page.locator('#virtual-person-name').fill('虚拟模特')
                page.locator('[data-create] button').click()
                expect(page.locator('.virtual-person')).to_have_count(1)
                page.locator('.virtual-person button').click()
                expect(page.locator('#person-current')).to_contain_text('虚拟人物')
                assert '已认证' not in page.locator('#person-current').inner_text()
                page.locator('[name=face_image]').set_input_files({'name':'face.png','mimeType':'image/png','buffer':picture})
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                expect(page.locator('#person-photo-status')).to_contain_text('校验')
                lib=portrait_library.PortraitLibrary(settings)
                lib.process_one()
                with lib.store.connection() as db:db.execute('UPDATE portrait_photos SET next_check=0')
                lib.process_one()
                page.reload()
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                expect(page.locator('#person-current')).to_contain_text('虚拟人物')
                page.locator('.virtual-library-open').click()
                page.locator('[data-assets]').click()
                expect(page.locator('[data-asset-list] button')).to_have_count(1)
                page.locator('[data-asset-list] button').click()
                expect(page.locator('.virtual-library-dialog')).not_to_be_visible()
                expect(page.locator('#production-status')).to_contain_text('官方虚拟人物')
                assert [c for c in calls if c[0]=='CreateAssetGroup'] and len([c for c in calls if c[0]=='CreateAssetGroup'])==1
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
                page.screenshot(path=str(ROOT/'storage'/f'virtual-library-{width}.png'),full_page=True)
                page.goto('http://127.0.0.1:18749/admin/settings')
                page.locator('[data-test]').click()
                expect(page.locator('[data-status]')).to_contain_text('只读查询成功')
                assert not errors,errors
                browser.close()
                print(f'PASS virtual library {width}: create, select, upload, Active, import, restore, admin test')

if __name__=='__main__':
    check(1440);check(390)
