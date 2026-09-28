"""Browser + real local APIs; external asset checks replaced with deterministic fixtures."""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from io import BytesIO
import subprocess
import imageio_ffmpeg
from PIL import Image
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright,expect
from app import main,portrait_service,portrait_library,production_worker
ROOT=Path(__file__).resolve().parents[1]

def check(width):
    with TemporaryDirectory(prefix='person-video-browser-',dir=ROOT/'storage') as tmp:
        settings=replace(main.settings,storage_dir=Path(tmp),seedance_mode='mock')
        portrait_service.save_config(settings,{'access_key':'fixture','secret_key':'fixture'})
        clips={}
        for seconds,color in [(3,'#b0a0bc'),(7,'#9aaea0'),(13,'#bbac9b')]:
            path=Path(tmp)/f'clip-{seconds}.mp4'
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-f','lavfi','-i',f'color=c={color}:s=640x640:r=24','-t',str(seconds),'-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
            clips[seconds]=path.read_bytes()
        calls=[];remote_status={'value':'Active'}
        def official(self,action,payload):
            calls.append((action,payload))
            if action=='GetAssetGroup':return {'Id':payload['Id'],'GroupType':'LivenessFace','ProjectName':'default'}
            if action=='GetAsset':return {'Id':payload['Id'],'GroupId':'group-real','AssetType':'Video','ProjectName':'default','Status':remote_status['value'],'Name':'人物视频'}
            if action=='CreateAsset':return {'Id':'asset-person-video'}
            raise AssertionError(action)
        with patch.object(main,'settings',settings),patch.object(production_worker,'wake',lambda *_:None),patch.object(portrait_service.ArkPortraitClient,'_request',official),patch.object(portrait_library,'upload_photo',lambda *_:'https://test.invalid/video'):
            client=TestClient(main.app,base_url='http://127.0.0.1:18749')
            lib=portrait_library.PortraitLibrary(settings);person=lib.add_person('group-real','yoyo-真人头像');lib.add_person('group-other','另一人物')
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(channel='msedge')
                page=browser.new_page(viewport={'width':width,'height':1000});errors=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                def route(reqroute):
                    req=reqroute.request
                    response=client.request(req.method,req.url,content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k.lower() not in {'host','content-length'}})
                    reqroute.fulfill(status=response.status_code,body=response.content,headers={k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-encoding','transfer-encoding'}})
                page.route('**/*',route);page.goto('http://127.0.0.1:18749/')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                page.locator('[data-person-media=video]').click()
                expect(page.locator('#person-video-panel')).to_be_visible()
                expect(page.locator('#person-image-panel')).not_to_be_visible()
                page.locator('#person-video-choose').click()
                page.locator('[data-person-id="'+person['id']+'"]').click()
                dialog=page.locator('#person-photos-dialog');expect(dialog).to_contain_text('这个人还没有视频')
                dialog.locator('[data-photo-file]').set_input_files({'name':'yoyo-正面.mp4','mimeType':'video/mp4','buffer':clips[3]})
                expect(dialog.locator('.person-photo-card')).to_have_count(1)
                expect(dialog.locator('[data-photo-use]')).to_be_disabled()
                expect(dialog.locator('[data-photo-status]')).to_contain_text('视频已添加')
                dialog.locator('[data-photo-close]').click()
                assert page.evaluate('productionPortraits.currentPhoto') is None
                lib.process_one()
                with lib.store.connection() as db:db.execute('UPDATE portrait_photos SET next_check=0')
                lib.process_one()
                page.locator('#person-video-choose').click();page.locator('[data-person-id="'+person['id']+'"]').click()
                expect(dialog.locator('[data-photo-use]')).to_be_enabled()
                dialog.screenshot(path=str(ROOT/'storage'/f'person-video-gallery-{width}.png'))
                dialog.locator('[data-photo-use]').click();expect(dialog).not_to_be_visible()
                expect(page.locator('#person-current')).to_contain_text('yoyo')
                expect(page.locator('#person-video-preview')).to_be_visible()
                expect(page.locator('#person-photos-open')).to_have_text('选视频')
                page.wait_for_function('document.getElementById("person-video-preview").duration === 3')
                assert page.evaluate('productionPortraits.currentPhoto.kind')=='person_video'
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                page.reload();expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                expect(page.locator('[data-person-media=video]')).to_have_attribute('aria-pressed','true')
                assert page.evaluate('productionPortraits.currentPhoto.kind')=='person_video'
                page.locator('input[name=video]').set_input_files({'name':'动作.mp4','mimeType':'video/mp4','buffer':clips[7]})
                pic=BytesIO();Image.new('RGB',(400,400),'blue').save(pic,format='PNG')
                page.locator('#studio-clothing-image').set_input_files({'name':'衣服.png','mimeType':'image/png','buffer':pic.getvalue()})
                expect(page.locator('#person-video-duration')).to_contain_text('10.0 / 15')
                expect(page.locator('#studio-generate-submit')).to_be_enabled()
                assert page.locator('#generation-prompt').input_value().count('@Video2')>=1
                assert '@Image1人物参考图' not in page.locator('#generation-prompt').input_value()
                page.locator('#person-video-panel').scroll_into_view_if_needed()
                page.locator('#person-video-panel').locator('..').screenshot(path=str(ROOT/'storage'/f'person-video-panel-{width}.png'))
                page.locator('input[name=video]').set_input_files({'name':'过长动作.mp4','mimeType':'video/mp4','buffer':clips[13]})
                expect(page.locator('#person-video-duration')).to_contain_text('16.0 / 15')
                expect(page.locator('#studio-generate-submit')).to_be_disabled()
                # Switching mode keeps saved references, but inactive photos/video do not enable generation.
                page.locator('[data-person-media=image]').click()
                expect(page.locator('#person-image-panel')).to_be_visible()
                assert page.evaluate('productionPortraits.currentPhoto') is None
                expect(page.locator('#studio-generate-submit')).to_be_disabled()
                page.locator('[data-person-media=video]').click()
                assert page.evaluate('productionPortraits.currentPhoto.kind')=='person_video'
                # Cloud revocation leaves old choice intact and visibly rejects reuse.
                remote_status['value']='Failed'
                page.locator('#person-photos-open').click();dialog.locator('[data-photo-use]').click()
                expect(dialog.locator('[data-photo-status]')).to_contain_text('不可用')
                dialog.locator('[data-photo-close]').click();remote_status['value']='Active'
                page.locator('#person-video-remove').click();expect(page.locator('#person-video-preview')).not_to_be_visible()
                assert lib.person(person['id'])['video_count']==1
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert sum(action=='CreateAsset' for action,_ in calls)==1
                assert not errors,errors
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                browser.close();print(f'PASS person video {width}: upload/check/select, reuse, preview, restore, roles, duration gate, removal retains library')
if __name__=='__main__':check(1440);check(390)
