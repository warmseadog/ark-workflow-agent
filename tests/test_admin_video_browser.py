"""Browser behavior for duration reset and task-scoped synchronized comparison."""
import base64
import json
from pathlib import Path
import subprocess
from urllib.parse import urlparse

import imageio_ffmpeg
import pytest
from playwright.sync_api import expect

from tests.test_account_frontend import browser
from tests.test_person_video import setup

ROOT = Path(__file__).resolve().parents[1]


def duration_page(browser):
    page = browser.new_page()
    panel = (ROOT/'app/templates/generation_options_panel.html').read_text(encoding='utf-8')
    source = (ROOT/'app/templates/production_panel.html').read_text(encoding='utf-8')
    clips = '\n'.join(line for line in source.splitlines() if 'id="source-clip-controls"' in line or 'id="source-clip-dialog"' in line)
    page.set_content(panel + clips)
    page.evaluate("""() => {
      window.source = {id:'source',duration:7.7,url:'/source.mp4'};
      window.productionSource = () => source;
      window.saved = [];
      window.addEventListener('model-settings-saved', () => saved.push({clip:generationOptions.sourceClip(),target:generationOptions.targetDuration(),model:generationOptions.get()}));
      window.fetch = async () => ({ok:true,json:async()=>({
        defaults:{model:'edit',resolution:'720p',ratio:'adaptive',duration:-1,generate_audio:true},
        items:[{id:'edit',label:'Edit',follow_source:true,max_duration:30,resolutions:['720p'],ratios:['adaptive'],audio_control:true},
          {id:'generate',label:'Generate',max_duration:15,resolutions:['720p'],ratios:['adaptive'],auto_duration:true},
          {id:'integer',label:'Integer',max_duration:15,resolutions:['720p'],ratios:['adaptive'],auto_duration:false}]
      })});
    }""")
    page.add_script_tag(path=str(ROOT/'app/static/generation-options.js'))
    page.evaluate('generationOptions.setLocked(false)')
    return page


def test_reset_clears_clip_and_continuation_and_emits_saved_values(browser):
    page = duration_page(browser)
    try:
        page.evaluate('generationOptions.restore(generationOptions.get(),{start:2,duration:4},12)')
        expect(page.get_by_role('button',name='恢复原片时长')).to_be_visible()
        page.get_by_role('button',name='恢复原片时长').click()
        expect(page.locator('#generation-duration-note')).to_have_text('7.7 秒')
        assert page.evaluate('saved.at(-1)') == {'clip':None,'target':None,'model':{'model':'edit','resolution':'720p','ratio':'adaptive','duration':-1,'generate_audio':True}}
        assert page.locator('#source-clip-start').input_value() == '0'
        assert page.locator('#source-clip-duration').input_value() == ''
        page.locator('[name=model]').select_option('generate')
        page.get_by_role('button',name='恢复原片时长').click()
        assert page.evaluate('generationOptions.get().duration') == -1
        expect(page.locator('#generation-duration-help')).to_contain_text('模型自动时长')
        page.locator('[name=model]').select_option('integer')
        page.get_by_role('button',name='恢复原片时长').click()
        assert not page.evaluate('generationOptions.available()')
        expect(page.locator('#model-settings-error')).to_contain_text('整数秒')
        page.evaluate('generationOptions.setLocked(true)')
        expect(page.get_by_role('button',name='恢复原片时长')).to_be_disabled()
        page.evaluate('source.duration=null;generationOptions.setLocked(false)')
        expect(page.get_by_role('button',name='恢复原片时长')).to_be_disabled()
    finally:
        page.close()


@pytest.fixture(scope='module')
def comparison_media(tmp_path_factory):
    folder = tmp_path_factory.mktemp('comparison')
    result = {}
    for name,duration in [('source',8),('result',12)]:
        path = folder/(name+'.mp4')
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',
            f'color=blue:s=160x120:r=10:d={duration}','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
        result[name] = 'data:video/mp4;base64,'+base64.b64encode(path.read_bytes()).decode()
    return result


@pytest.mark.parametrize('width',[1440,390])
def test_comparison_sync_clip_continuation_single_audio_and_cleanup(browser,comparison_media,width):
    page = browser.new_page(viewport={'width':width,'height':900})
    try:
        page.set_content('<button id="outside">Task list</button>')
        assert (ROOT/'app/static/video-comparison.js').is_file(), 'Comparison player is not implemented'
        page.add_style_tag(path=str(ROOT/'app/static/video-comparison.css'))
        page.add_script_tag(path=str(ROOT/'app/static/video-comparison.js'))
        page.evaluate("""media => {
          window.full={id:'run',name:'Test task',download_url:media.result,snapshot:{source_asset_id:'action',source_clip:{start:2,duration:4},target_duration:12,
            assets:[{id:'wrong',kind:'person_video',url:'/must-not-load'},{id:'action',kind:'video',url:media.source}]}};
          window.comparison=createVideoComparison({readRun:async item=>{window.readId=item.id;return full;},onError:error=>{throw error;}});
          comparison.open({id:'run',name:'Test task'});
        }""",comparison_media)
        left = page.locator('#comparison-source'); right = page.locator('#comparison-result')
        expect(page.locator('#video-comparison-dialog')).to_be_visible()
        page.wait_for_function('document.querySelector("#comparison-source").readyState >= 2 && document.querySelector("#comparison-result").readyState >= 2')
        page.wait_for_function('Math.abs(document.querySelector("#comparison-source").currentTime-2)<.1')
        assert page.evaluate('readId') == 'run'
        right.evaluate('el=>el.currentTime=3')
        page.wait_for_function('Math.abs(document.querySelector("#comparison-source").currentTime-5)<.15')
        right.evaluate('el=>el.playbackRate=1.5')
        assert left.evaluate('el=>el.playbackRate') == pytest.approx(1.5)
        assert left.evaluate('el=>el.muted') and not right.evaluate('el=>el.muted')
        page.locator('#comparison-audio').select_option('source')
        assert not left.evaluate('el=>el.muted') and right.evaluate('el=>el.muted')
        page.locator('#comparison-audio').select_option('mute')
        assert left.evaluate('el=>el.muted') and right.evaluate('el=>el.muted')
        right.evaluate('el=>el.currentTime=7')
        page.wait_for_function('document.querySelector("#comparison-source").currentTime>5.8 && document.querySelector("#comparison-source").currentTime<=6')
        assert left.evaluate('el=>el.paused')
        expect(page.locator('#comparison-status')).to_contain_text('原片片段已结束')
        right.evaluate('el=>el.currentTime=0')
        page.locator('#comparison-play').click()
        page.wait_for_function('!document.querySelector("#comparison-source").paused && !document.querySelector("#comparison-result").paused')
        right.evaluate("el=>el.dispatchEvent(new Event('waiting'))")
        assert left.evaluate('el=>el.paused')
        right.evaluate("el=>el.dispatchEvent(new Event('playing'))")
        page.wait_for_function('!document.querySelector("#comparison-source").paused')
        page.locator('#comparison-play').click()
        assert left.evaluate('el=>el.paused') and right.evaluate('el=>el.paused')
        boxes=[locator.bounding_box() for locator in (left,right)]
        assert boxes[0]['x'] < boxes[1]['x'] and abs(boxes[0]['y']-boxes[1]['y']) < 2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#comparison-close').click()
        expect(page.locator('#video-comparison-dialog')).not_to_be_visible()
        assert left.get_attribute('src') is None and right.get_attribute('src') is None
    finally:
        page.close()


@pytest.mark.parametrize('width', [1440, 390])
def test_comparison_download_button_exports_selected_audio_and_recovers(browser, comparison_media, width):
    page = browser.new_page(viewport={'width': width, 'height': 900}, accept_downloads=True)
    requests = []
    def route(req):
        if '/comparison?' in req.request.url:
            requests.append(req.request.url)
            if len(requests) == 1:
                req.fulfill(status=503, json={'detail': '导出繁忙，请重试'})
            else:
                req.fulfill(content_type='video/mp4', body=base64.b64decode(comparison_media['result'].split(',')[1]))
        else:
            req.fulfill(content_type='text/html', body='<html><body></body></html>')
    page.route('http://comparison.test/**', route)
    try:
        page.goto('http://comparison.test/')
        page.add_style_tag(path=str(ROOT/'app/static/video-comparison.css'))
        page.add_script_tag(path=str(ROOT/'app/static/video-comparison.js'))
        page.evaluate("""media => {
          window.comparison=createVideoComparison({readRun:async()=>({
            name:'测试作品',download_url:media.result,comparison_url:'/api/production/runs/one/comparison',
            snapshot:{source_asset_id:'s',assets:[{id:'s',kind:'video',url:media.source}]}
          })}); comparison.open({id:'one'});
        }""", comparison_media)
        button = page.get_by_role('button', name='下载对比视频', exact=True)
        expect(button).to_be_enabled()
        page.locator('#comparison-audio').select_option('source')
        button.click()
        expect(page.locator('#comparison-export-status')).to_contain_text('导出繁忙')
        expect(button).to_be_enabled()
        with page.expect_download() as download:
            button.click()
        assert download.value.suggested_filename == '测试作品-对比.mp4'
        assert requests == ['http://comparison.test/api/production/runs/one/comparison?audio=source'] * 2
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#comparison-close').click()
        expect(page.locator('#comparison-download')).to_be_disabled()
    finally:
        page.close()


def test_comparison_discards_closed_read_and_maps_legacy_slow_clip(browser,comparison_media):
    page = browser.new_page()
    try:
        page.set_content('<p>Tasks</p>')
        page.add_script_tag(path=str(ROOT/'app/static/video-comparison.js'))
        page.evaluate("""() => {
          window.comparison=createVideoComparison({readRun:()=>new Promise(resolve=>window.deliver=resolve)});
          comparison.open({id:'first'});comparison.close();
        }""")
        page.evaluate('media=>deliver({download_url:media.result,snapshot:{source_asset_id:"source",assets:[{id:"source",kind:"video",url:media.source}]}})',comparison_media)
        assert page.locator('#comparison-source').get_attribute('src') is None
        page.evaluate("void comparison.open({id:'second'});")
        page.evaluate('media=>deliver({download_url:media.result,snapshot:{source_asset_id:"source",source_clip:{start:2,duration:12,retime:"slow"},assets:[{id:"source",kind:"video",url:media.source}]}})',comparison_media)
        page.wait_for_function('document.querySelector("#comparison-source").readyState>=2 && document.querySelector("#comparison-result").readyState>=2')
        page.locator('#comparison-result').evaluate('el=>el.currentTime=4')
        page.wait_for_function('Math.abs(document.querySelector("#comparison-source").currentTime-4)<.15')
        assert page.locator('#comparison-source').evaluate('el=>el.playbackRate') == pytest.approx(.5)
        page.keyboard.press('Escape')
        page.wait_for_function('!document.querySelector("#comparison-source").hasAttribute("src")')
    finally:
        page.close()


@pytest.mark.parametrize('width',[1440,390,320])
def test_duration_reset_persists_real_draft_and_fits_toolbar(browser,setup,comparison_media,tmp_path,width):
    from app import main, generation_settings, model_catalog
    generation_settings.save_config(main.settings,{'model':model_catalog.SD25,'duration':8,'mode':'http','api_key':'test','public_base_url':'https://studio.example'})
    rows=model_catalog.catalog(main.settings)['items']
    for row in rows:
        if row['id']==model_catalog.SD25:row.update(enabled=True,verified=True)
    model_catalog.save_catalog(main.settings,{'items':rows})
    raw=base64.b64decode(comparison_media['source'].split(',',1)[1])
    asset=setup.post('/api/production/assets',data={'kind':'video'},files={'file':('source.mp4',raw,'video/mp4')}).json()
    draft=setup.post('/api/production/drafts',json={}).json()
    response=setup.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'source_asset_id':asset['id'],'source_clip':{'start':2,'duration':4},'target_duration':12})
    assert response.status_code==200,response.text
    page=browser.new_page(viewport={'width':width,'height':1000})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    def route(r):
        req=r.request
        if urlparse(req.url).path=='/api/auth/me':
            r.fulfill(content_type='application/json',body=json.dumps({'auth_enabled':False,'user':None}));return
        result=setup.request(req.method,req.url,content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k not in ('host','content-length')})
        r.fulfill(status=result.status_code,body=result.content,headers={k:v for k,v in result.headers.items() if k not in ('content-length','content-encoding','transfer-encoding')})
    page.route('**/*',route)
    try:
        page.goto('http://127.0.0.1:18759/')
        # Parent integration owns the final page asset tags.
        page.add_style_tag(path=str(ROOT/'app/static/video-comparison.css'))
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('#generation-duration-note')).to_have_text('12 秒')
        page.get_by_role('button',name='恢复原片时长').click()
        expect(page.locator('#generation-duration-note')).to_have_text('8 秒')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        saved=setup.get('/api/production/drafts/'+draft['id']).json()
        assert saved['source_clip'] is None and saved['target_duration'] is None
        assert saved['model']['duration']==-1
        page.reload()
        page.add_style_tag(path=str(ROOT/'app/static/video-comparison.css'))
        expect(page.locator('#generation-duration-note')).to_have_text('8 秒')
        assert page.evaluate('generationOptions.targetDuration()') is None
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        box=page.locator('#generation-duration-reset').bounding_box()
        assert box['x']>=0 and box['x']+box['width']<=width
        page.locator('.generation-action').screenshot(path=str(tmp_path/f'duration-reset-{width}.png'))
        assert not errors,errors
    finally:
        page.close()


def test_super_admin_scoped_comparison_restore_retry_and_phase_details(browser,comparison_media):
    page = browser.new_page()
    try:
        source = (ROOT/'app/templates/production_panel.html').read_text(encoding='utf-8')
        page.route('http://127.0.0.1:18759/',lambda route:route.fulfill(content_type='text/html',body=source[source.index('  <section class="production-runs"'):]))
        page.goto('http://127.0.0.1:18759/')
        page.add_script_tag(path=str(ROOT/'app/static/video-comparison.js'))
        page.add_script_tag(path=str(ROOT/'app/static/production-runs.js'))
        page.evaluate("""media => {
          window.currentAccount={id:'admin',role:'super_admin'};window.accountReady=Promise.resolve();
          window.requests=[];window.restores=[];
          const item={id:'run',user_id:'owner',username:'Owner',name:'Task',status:'succeeded',created_at:'2026-10-05T00:00:00Z',read_only:true,
            download_url:media.result,can_restore_draft:true,copy_url:'/api/admin/delegated/owner/runs/run/restore',
            timing:{available:true,total_seconds:20,queue_seconds:1,execution_seconds:19,paused_seconds:0,
              phases:{waiting:{seconds:1,status:'complete'},masking:{seconds:0,status:'complete',cached:true},upload:{seconds:null,status:'unknown'},model:{seconds:16,status:'complete'},other:{seconds:3,status:'complete'}}}};
          const full={...item,snapshot:{source_asset_id:'action',assets:[{id:'action',kind:'video',url:media.source}]}};
          window.fetch=async(url,options)=>{
            restores.push({url,body:JSON.parse(options.body)});
            return restores.length===1?{ok:false,status:503,json:async()=>({detail:'稍后重试'})}:{ok:true,status:200,json:async()=>({id:'restored',delegated_user:{id:'owner',username:'Owner'}})};
          };
          window.runs=createProductionRuns({
            api:async(path,method,body,options)=>{requests.push({path,options});return path.includes('?')?{items:[item],total:1,page:1,pages:1,users:[{id:'owner',username:'Owner',enabled:true}]}:full;},
            changeDraft:async action=>{window.restored=await action();},accessoryLabels:{},
            media:{releaseVideo:video=>{video.pause();video.removeAttribute('src');video.load();},lazyVideo:()=>{},thumbnailFor:asset=>asset.url}
          });runs.refresh();
        }""",comparison_media)
        expect(page.locator('#runs-user-controls')).to_be_visible()
        expect(page.locator('[data-run-action=comparison]')).to_be_visible()
        page.locator('[data-run-action=comparison]').click()
        expect(page.locator('#video-comparison-dialog')).to_be_visible()
        page.wait_for_function('document.querySelector("#comparison-source").readyState>=2')
        assert page.evaluate('requests.at(-1).path') == '/owner/run'
        assert page.evaluate('requests.at(-1).options.adminRecords')
        page.locator('#comparison-close').click()
        page.locator('.run-menu>summary').click()
        page.locator('[data-run-action=restore]').click()
        expect(page.locator('#runs-status')).to_contain_text('稍后重试')
        page.locator('.run-menu>summary').click()
        page.locator('[data-run-action=restore]').click()
        page.wait_for_function('window.restored?.id === "restored"')
        keys=page.evaluate('restores.map(item=>item.body.idempotency_key)')
        assert len(keys)==2 and keys[0] and keys[0]==keys[1]
        page.locator('.run-menu>summary').click()
        page.locator('[data-run-action=details]').click()
        expect(page.locator('.run-phase-details')).to_contain_text('打码：复用缓存')
        expect(page.locator('.run-phase-details')).to_contain_text('上传：未记录')
        expect(page.locator('.run-phase-details')).to_contain_text('模型生成：16 秒')
    finally:
        page.close()
