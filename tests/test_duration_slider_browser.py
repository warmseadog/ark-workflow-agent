"""Real UI and API integration; generation remains queued and no provider is called."""
import json
import subprocess
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import imageio_ffmpeg
import pytest
from PIL import Image
from playwright.sync_api import expect
from app import main, generation_settings, model_catalog
from tests.test_account_frontend import browser
from tests.test_person_video import setup


@pytest.mark.parametrize('width', [1440, 390, 320])
def test_duration_slider_restores_fractional_source_and_submits_continuation(browser, setup, tmp_path, width):
    from app.continuation_settings import save_config
    save_config(main.settings,{'api_key':'test-llm-key'})
    generation_settings.save_config(main.settings, {'model':model_catalog.SD25,'duration':8,'mode':'http','api_key':'test-video-key','public_base_url':'https://studio.example'})
    rows=model_catalog.catalog(main.settings)['items']
    for row in rows:
        if row['id'] in (model_catalog.SD25,model_catalog.SD20): row.update(enabled=True,verified=True)
    model_catalog.save_catalog(main.settings,{'items':rows})
    source=tmp_path/'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
                    'color=blue:s=320x240:r=30:d=7.7','-c:v','libx264',str(source)],check=True,capture_output=True)
    video=setup.post('/api/production/assets',data={'kind':'video'},files={'file':('source.mp4',source.read_bytes(),'video/mp4')}).json()
    picture=BytesIO();Image.new('RGB',(400,400),'blue').save(picture,format='PNG')
    photos={kind:setup.post('/api/production/assets',data={'kind':kind},files={'file':(kind+'.png',picture.getvalue(),'image/png')}).json() for kind in ('face','clothing')}
    draft=setup.post('/api/production/drafts',json={}).json()
    response=setup.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],
        'source_asset_id':video['id'],'face_asset_ids':[photos['face']['id']],'clothing_asset_ids':[photos['clothing']['id']]})
    assert response.status_code==200,response.text
    context=browser.new_context(viewport={'width':width,'height':1000})
    page=context.new_page();calls=[];errors=[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    def route(r):
        req=r.request;calls.append(urlparse(req.url).path)
        if urlparse(req.url).path=='/api/auth/me':
            r.fulfill(content_type='application/json',body=json.dumps({'auth_enabled':False,'user':None}));return
        res=setup.request(req.method,req.url,content=req.post_data_buffer,
            headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=res.status_code,body=res.content,headers={k:v for k,v in res.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    try:
        page.goto('http://127.0.0.1:18759/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('#generation-duration-note')).to_have_text('7.7 秒')
        assert page.locator('[name=source_duration_mode]').count()==0
        assert '/api/production/assets/'+video['id']+'/reference-status' in calls
        assert '/api/production/assets/'+video['id']+'/file' not in calls
        slider=page.locator('#generation-duration-slider')
        expect(slider).to_be_enabled()
        ratio=page.locator('[name=ratio]')
        ratio.select_option('16:9')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        page.reload();expect(ratio).to_have_value('16:9')
        expect(slider).to_be_enabled()
        # Desktop keeps its toolbar; mobile controls must fit without horizontal scrolling.
        controls=[page.locator(selector).bounding_box() for selector in (
            '#draft-task-name','[name=model]','[name=resolution]','[name=ratio]',
            '#generation-duration-slider','[name=generate_audio]','#studio-generate-submit')]
        centers=[box['y']+box['height']/2 for box in controls]
        if width > 800:
            assert max(centers)-min(centers)<3,controls
        else:
            assert all(box['x'] >= 0 and box['x']+box['width'] <= width for box in controls),controls
            button = controls[-1]
            assert button['y']+button['height'] <= 1000
            assert page.locator('.generation-action').evaluate('(el) => el.scrollWidth <= el.clientWidth')
        slider.focus();page.keyboard.press('ArrowLeft')
        expect(page.locator('#generation-duration-note')).to_have_text('7 秒')
        assert page.evaluate('generationOptions.sourceClip()')=={'start':0,'duration':7}
        page.keyboard.press('ArrowRight');expect(page.locator('#generation-duration-note')).to_have_text('7.7 秒')
        assert page.evaluate('generationOptions.sourceClip()') is None
        page.keyboard.press('ArrowRight');expect(page.locator('#generation-duration-note')).to_have_text('8 秒')
        page.keyboard.press('ArrowRight');expect(page.locator('#generation-duration-note')).to_have_text('9 秒')
        expect(page.locator('#generation-duration-help')).to_contain_text('续写')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        saved=setup.get('/api/production/drafts/'+draft['id']).json()
        assert saved['source_clip'] is None
        assert saved['target_duration']==9
        page.reload();expect(page.locator('#generation-duration-note')).to_have_text('9 秒')
        expect(slider).to_be_enabled()
        assert page.evaluate('generationOptions.sourceClip()') is None
        assert page.evaluate('generationOptions.targetDuration()')==9
        # Invalid tick is visible but cannot be submitted.
        slider.focus();page.keyboard.press('Home')
        expect(page.locator('#studio-generate-submit')).to_be_disabled()
        expect(page.locator('#generation-duration-help')).to_contain_text('2–30')
        page.keyboard.press('End');expect(page.locator('#generation-duration-note')).to_have_text('30 秒')
        # SD20 must never pretend a fractional output request was accepted.
        page.locator('[name=model]').select_option(model_catalog.SD20)
        expect(page.locator('#generation-duration-note')).to_have_text('7.7 秒')
        expect(page.locator('#generation-duration-help')).to_contain_text('模型自动时长')
        assert page.evaluate('generationOptions.get().duration')==-1
        slider.focus();page.keyboard.press('ArrowRight')
        assert page.evaluate('generationOptions.get().duration')==8
        page.locator('[name=model]').select_option(model_catalog.SD25)
        slider.focus();page.keyboard.press('ArrowRight');page.keyboard.press('ArrowRight')
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        audio=page.locator('[name=generate_audio]');audio.check()
        a=audio.bounding_box();b=page.locator('#studio-generate-submit').bounding_box()
        if width > 800:
            assert abs(a['y']+a['height']/2-b['y']-b['height']/2)<2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
        shots=Path(__file__).resolve().parents[1]/'storage/duration-browser';shots.mkdir(exist_ok=True)
        page.locator('.generation-action').screenshot(path=str(shots/f'controls-{width}.png'))
        page.locator('#studio-generate-submit').click()
        expect(page.locator('#production-run-list')).to_contain_text('排队')
        runs=setup.get('/api/production/runs').json()['items']
        run=setup.get('/api/production/runs/'+runs[0]['id']).json()
        assert run['snapshot']['source_clip'] is None
        assert run['snapshot']['target_duration']==9
        assert run['snapshot']['model']['duration']==-1
        assert run['snapshot']['model']['generate_audio'] is True
        assert run['snapshot']['model']['ratio']=='16:9'
        if width == 390:
            expect(page.locator('#generation-view-task')).to_be_visible()
            page.locator('#scene-references>summary').click()
            page.locator('#scene-enabled').check()
            page.locator('#scene-description').fill('下一版使用自然光')
            expect(page.locator('#generation-readiness')).to_have_text('素材已选齐，可以生成')
            expect(page.locator('#generation-view-task')).to_be_hidden()
            page.once('dialog', lambda dialog: dialog.dismiss())
            page.locator('#clear-reference-images').click()
            expect(page.locator('#generation-readiness')).to_have_text('素材已选齐，可以生成')
            page.once('dialog', lambda dialog: dialog.accept())
            page.locator('#clear-reference-images').click()
            expect(page.locator('#clear-reference-images')).to_be_hidden()
            expect(page.locator('#generation-readiness')).to_have_text('还缺服装参考')
        if width == 1440:
            # A saved short source segment and a longer target remain independent.
            page.evaluate("generationOptions.restore(generationOptions.get(), {start:1,duration:4}, 9)")
            assert page.evaluate('generationOptions.sourceClip()')=={'start':1,'duration':4}
            page.evaluate("window.dispatchEvent(new Event('model-settings-saved'))")
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            page.reload()
            expect(page.locator('#generation-duration-note')).to_have_text('9 秒')
            assert page.evaluate('generationOptions.sourceClip()')=={'start':1,'duration':4}
        assert not errors,errors
    finally:
        context.close()
