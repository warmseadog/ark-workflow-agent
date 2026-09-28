"""Real local routes + browser. Official image services are replaced with fixtures."""
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
    with TemporaryDirectory(prefix='photos-browser-',dir=ROOT/'storage') as tmp:
        settings=replace(main.settings,storage_dir=Path(tmp),seedance_mode='mock')
        portrait_service.save_config(settings,{'access_key':'fixture','secret_key':'fixture'})
        pictures={}
        for aid,color in [('asset-one','#aaa1b8'),('asset-two','#b3c1c6'),('asset-new','#a6bc96'),('asset-virtual','#d7b4a2')]:
            output=BytesIO();Image.new('RGB',(400,500),color).save(output,format='PNG');pictures[aid]=output.getvalue()
        calls=[];fail={'id':None}
        def official(self,action,payload):
            calls.append((action,payload))
            if action=='GetAssetGroup':return {'Id':payload['Id'],'GroupType':'AIGC' if payload['Id']=='group-virtual' else 'LivenessFace','ProjectName':'default'}
            if action=='GetAsset':
                aid=payload['Id']
                if fail['id']==aid:raise portrait_service.PortraitError('这张照片暂不可用，请选择其他照片。')
                return {'Id':aid,'GroupId':'group-virtual' if aid=='asset-virtual' else 'group-real','AssetType':'Image','ProjectName':'default','Status':'Active','Name':{'asset-one':'正面照片','asset-two':'侧面照片','asset-new':'新照片','asset-virtual':'虚拟照片'}[aid],'URL':'https://ark-asset.cn-beijing.volcengine.com/'+aid+'.png'}
            if action=='CreateAsset':return {'Id':'asset-new'}
            raise AssertionError(action)
        with patch.object(main,'settings',settings),patch.object(production_worker,'wake',lambda *_:None),patch.object(portrait_service.ArkPortraitClient,'_request',official),patch.object(portrait_service,'download_image',lambda remote,path:path.write_bytes(pictures[remote['remote_asset_id']])),patch.object(portrait_library,'upload_photo',lambda *_:'https://test.invalid/photo'):
            client=TestClient(main.app,base_url='http://127.0.0.1:18749')
            for aid in ['asset-one','asset-two','asset-virtual']:
                response=client.post('/api/portrait/import',json={'remote_asset_id':aid,'person_type':'AIGC' if aid=='asset-virtual' else 'LivenessFace'})
                assert response.status_code==200,response.text
            lib=portrait_library.PortraitLibrary(settings)
            real=next(p for p in lib.people() if p['person_type']=='LivenessFace');lib.rename(real['id'],'yoyo-真人头像')
            virtual=next(p for p in lib.people() if p['person_type']=='AIGC');lib.rename(virtual['id'],'虚拟模特')
            jobs={p['remote_asset_id']:p['id'] for p in lib.photos_for_person(real['id'])}
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
                page.locator('#person-picker summary').click()
                page.locator('[data-person-type=LivenessFace]').click()
                page.locator('[data-person-id="'+real['id']+'"]').click()
                dialog=page.locator('#person-photos-dialog')
                expect(dialog).to_be_visible();expect(dialog.locator('.person-photo-card')).to_have_count(2)
                expect(page.locator('#person-current')).not_to_be_visible()
                assert dialog.evaluate('(el)=>el.scrollWidth<=el.clientWidth+1')
                dialog.screenshot(path=str(ROOT/'storage'/f'person-photos-{width}.png'))
                def use(aid):page.locator('[data-photo-id="'+jobs[aid]+'"] [data-photo-use]').click()
                use('asset-two')
                expect(dialog).not_to_be_visible();expect(page.locator('#person-current')).to_contain_text('yoyo-真人头像')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert page.evaluate('productionPortraits.currentPhoto.portrait.remote_asset_id')=='asset-two'
                page.locator('#person-photos-open').click(); page.locator('[data-person-id="'+real['id']+'"]').click()
                expect(dialog).to_contain_text('当前主参考')
                page.locator('[data-photo-close]').click()
                assert page.evaluate('productionPortraits.currentPhoto.portrait.remote_asset_id')=='asset-two'
                page.locator('#person-photos-open').click(); page.locator('[data-person-id="'+real['id']+'"]').click();use('asset-one')
                expect(dialog).not_to_be_visible()
                assert page.evaluate('productionPortraits.currentPhoto.portrait.remote_asset_id')=='asset-one'
                page.reload();expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert page.evaluate('productionPortraits.currentPhoto.portrait.remote_asset_id')=='asset-one'
                # An unavailable cloud asset cannot overwrite the current reference.
                fail['id']='asset-two';page.locator('#person-photos-open').click(); page.locator('[data-person-id="'+real['id']+'"]').click();use('asset-two')
                expect(dialog.locator('[data-photo-status]')).to_contain_text('暂不可用')
                assert page.evaluate('productionPortraits.currentPhoto.portrait.remote_asset_id')=='asset-one'
                fail['id']=None
                # Adding to an existing person belongs to the backend, never the production picker.
                expect(dialog.locator('[data-photo-file]')).to_have_count(0)
                uploaded=client.post('/api/production/assets',data={'kind':'face'},files={'file':('新角度.png',pictures['asset-new'],'image/png')}).json()
                added=client.post('/api/portrait/photos',json={'person_id':real['id'],'asset_id':uploaded['id']})
                assert added.status_code==200,added.text
                dialog.locator('[data-photo-refresh]').click()
                expect(dialog.locator('.person-photo-card')).to_have_count(3)
                new_card=dialog.locator('.person-photo-card').filter(has_text='新角度.png')
                expect(new_card).to_contain_text('入库检查中')
                expect(new_card.locator('[data-photo-use]')).to_be_disabled()
                page.locator('[data-photo-close]').click()
                assert page.evaluate('productionPortraits.currentPhoto.portrait.remote_asset_id')=='asset-one'
                lib.process_one()
                with lib.store.connection() as db:db.execute('UPDATE portrait_photos SET next_check=0')
                lib.process_one()
                page.locator('#person-photos-open').click(); page.locator('[data-person-id="'+real['id']+'"]').click()
                expect(new_card.locator('[data-photo-use]')).to_be_enabled()
                new_card.locator('[data-photo-use]').click();expect(dialog).not_to_be_visible()
                assert page.evaluate('productionPortraits.currentPhoto.portrait.remote_asset_id')=='asset-new'
                # Reuploading the same bytes reuses the verified photo without another cloud creation.
                page.locator('#person-photos-open').click(); page.locator('[data-person-id="'+real['id']+'"]').click()
                duplicate=client.post('/api/production/assets',data={'kind':'face'},files={'file':('重复.png',pictures['asset-new'],'image/png')}).json()
                reused=client.post('/api/portrait/photos',json={'person_id':real['id'],'asset_id':duplicate['id']}).json()
                assert reused['id']==added.json()['id']
                dialog.locator('[data-photo-refresh]').click()
                expect(dialog.locator('.person-photo-card')).to_have_count(3)
                page.locator('[data-photo-close]').click()
                assert sum(action=='CreateAsset' for action,_ in calls)==1
                # Keep another face reference, then prove changing people replaces the old set.
                page.locator('#studio-face-image').set_input_files({'name':'附加.png','mimeType':'image/png','buffer':pictures['asset-one']})
                expect(page.locator('#face-reference-preview img')).to_have_count(1)
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                page.locator('#person-picker summary').click();page.locator('[data-person-type=AIGC]').click()
                page.locator('[data-person-id="'+virtual['id']+'"]').click()
                expect(dialog.locator('.person-photo-card')).to_have_count(1)
                page.locator('[data-photo-close]').click()
                expect(page.locator('#person-current')).not_to_be_visible()
                assert page.evaluate('portraitPeople.selected') is None
                expect(page.locator('#face-reference-preview img')).to_have_count(1)
                page.locator('#person-picker summary').click();page.locator('[data-person-type=AIGC]').click()
                page.locator('[data-person-id="'+virtual['id']+'"]').click()
                dialog.locator('[data-photo-use]').click();expect(dialog).not_to_be_visible()
                expect(page.locator('#face-reference-preview img')).to_have_count(1)
                expect(page.locator('#person-current')).to_contain_text('虚拟模特')
                assert page.evaluate('productionPortraits.currentPhoto.portrait.remote_asset_id')=='asset-virtual'
                assert not errors,errors
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                browser.close()
                print(f'PASS photos {width}: choose either photo, cancel, reuse, reload, failure retains reference, upload/check/reopen, dedup, switch person clears old references')
if __name__=='__main__':
    check(1440);check(390)
