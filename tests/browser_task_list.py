"""Task list UI backed by real local routes, SQLite and FFmpeg derivatives."""
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
from unittest.mock import patch
import subprocess,shutil,json
import imageio_ffmpeg
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright,expect
from app import main,production_worker,jobs
from app.production_store import ProductionStore
from app.playback import process_one
ROOT=Path(__file__).resolve().parents[1]

def check(width):
    with TemporaryDirectory(prefix='tasks-browser-',dir=ROOT/'storage') as tmp:
        cfg=replace(main.settings,storage_dir=Path(tmp),seedance_mode='mock');store=ProductionStore(cfg.storage_dir)
        draft=store.create_draft({'name':'视频-0928-120000'});created=[]
        for i in range(25):
            run=store.create_run(draft['id'],1,str(i),{});store.update_run(run['id'],status='failed',message='测试记录');created.append(run)
        ident=created[-1]['id'];store.rename_run(ident,'yoyo－白色外套');store.update_run(ident,status='succeeded')
        active=created[-2]['id'];store.update_run(active,status='running',progress=25)
        output=cfg.storage_dir/'outputs'/(ident+'.mp4');output.parent.mkdir()
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-f','lavfi','-i','testsrc2=s=640x360:r=24','-t','8','-c:v','libx264','-pix_fmt','yuv420p',str(output)],check=True,capture_output=True)
        process_one(cfg)
        with patch.object(main,'settings',cfg),patch.object(production_worker,'wake',lambda *_:None),patch.object(jobs,'store',jobs.JobStore()):
            client=TestClient(main.app,base_url='http://127.0.0.1:18751')
            with sync_playwright() as pw:
                browser=pw.chromium.launch(channel='msedge');page=browser.new_page(viewport={'width':width,'height':1000});errors=[];requests=[]
                page.on('pageerror',lambda e:errors.append(str(e)))
                hold={'enabled':False,'pending':[]}
                def route(r):
                    req=r.request;requests.append(req.url)
                    response=client.request(req.method,req.url,content=req.post_data_buffer,headers={k:v for k,v in req.headers.items() if k.lower() not in {'host','content-length'}})
                    reply={'status':response.status_code,'body':response.content,'headers':{k:v for k,v in response.headers.items() if k.lower() not in {'content-length','content-encoding','transfer-encoding'}}}
                    if hold['enabled'] and req.method=='PUT' and '/drafts/' in req.url:hold['pending'].append((r,reply));return
                    r.fulfill(**reply)
                page.route('**/*',route);page.goto('http://127.0.0.1:18751/')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                rows=page.locator('#production-run-list .run-row');expect(rows).to_have_count(10)
                expect(page.locator('#runs-page')).to_have_text('1 / 3')
                assert page.locator('#production-run-list video').count()==0
                first_ids=rows.evaluate_all('(rows)=>rows.map(x=>x.dataset.runId)')
                page.locator('#runs-next').click();expect(page.locator('#runs-page')).to_have_text('2 / 3');expect(rows).to_have_count(10)
                assert not set(first_ids)&set(rows.evaluate_all('(rows)=>rows.map(x=>x.dataset.runId)'))
                page.locator('#runs-next').click();expect(page.locator('#runs-page')).to_have_text('3 / 3');expect(rows).to_have_count(5)
                page.locator('#runs-previous').click();page.locator('#runs-previous').click();expect(page.locator('#runs-page')).to_have_text('1 / 3')
                row=page.locator('[data-run-id="'+ident+'"]')
                row.locator('.run-rename').click();edit=row.locator('input[aria-label="任务名称"]');edit.fill('秋季穿搭测试')
                page.locator('#runs-refresh').click();expect(edit).to_have_value('秋季穿搭测试');edit.press('Enter')
                expect(row.locator('.run-name-text')).to_have_text('秋季穿搭测试')
                page.reload();expect(page.locator('#draft-save-status')).to_contain_text('已保存');expect(row.locator('.run-name-text')).to_have_text('秋季穿搭测试')
                hold['enabled']=True
                page.locator('#flow-stage-generation > summary').click();page.locator('#generation-prompt').fill('触发一笔延迟自动保存')
                for _ in range(30):
                    if hold['pending']:break
                    page.wait_for_timeout(100)
                assert hold['pending'],'Draft PUT was not captured'
                page.locator('#draft-name-edit').click();page.locator('#draft-name-input').fill('下一条穿搭');page.locator('#draft-name-input').press('Enter')
                hold['enabled']=False
                for pending,reply in hold['pending']:pending.fulfill(**reply)
                expect(page.locator('#draft-task-name')).to_have_text('下一条穿搭');expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                current_id=page.evaluate("localStorage.getItem('production-current-draft-v1')")
                assert store.get_draft(current_id)['name']=='下一条穿搭','Old automatic save overwrote the newer name'
                tail_id=rows.last.get_attribute('data-run-id');tail=page.locator('[data-run-id="'+tail_id+'"]')
                tail.locator('.run-rename').click();tail.locator('input').fill('正在编辑的末行')
                inserted=store.create_run(draft['id'],store.get_draft(draft['id'])['revision'],'new-from-another-page',{});store.update_run(inserted['id'],status='failed')
                page.locator('#runs-refresh').click();expect(tail.locator('input')).to_have_value('正在编辑的末行')
                tail.locator('input').press('Escape');page.locator('#runs-refresh').click()
                row.locator('.run-rename').click();edit.fill('取消改名');edit.press('Escape');expect(row.locator('.run-name-text')).to_have_text('秋季穿搭测试')
                page.locator('.production-runs').screenshot(path=str(ROOT/'storage'/f'task-list-{width}.png'))
                row.locator('[data-run-action=play]').click();expect(page.locator('#run-player-dialog')).to_be_visible()
                page.wait_for_function('document.getElementById("run-player-video").readyState>=2')
                page.evaluate('document.getElementById("run-player-video").play()');page.wait_for_function('document.getElementById("run-player-video").currentTime>0.3')
                t=page.locator('#run-player-video').evaluate('(v)=>v.currentTime')
                store.update_run(active,progress=65)
                page.evaluate('document.getElementById("runs-refresh").click()')
                expect(page.locator('[data-run-id="'+active+'"] .run-state')).to_contain_text('65%')
                page.wait_for_timeout(600)
                assert page.locator('#run-player-video').evaluate('(v)=>v.currentTime')>t
                assert not page.locator('#run-player-video').evaluate('(v)=>v.paused')
                page.locator('[data-play-quality=original]').click();page.wait_for_function('document.getElementById("run-player-video").readyState>=2')
                assert '/playback/original' in page.locator('#run-player-video').get_attribute('src')
                page.locator('#run-player-dialog').screenshot(path=str(ROOT/'storage'/f'task-player-{width}.png'))
                page.locator('#run-player-close').click();expect(page.locator('#run-player-dialog')).not_to_be_visible()
                page.wait_for_function('!document.getElementById("run-player-video").getAttribute("src")')
                row.locator('.run-menu summary').click();row.locator('[data-run-action=details]').click();expect(row.locator('.run-detail-panel')).to_be_visible()
                # Full task detail is fetched only on request.
                assert any(url.endswith('/runs/'+ident) for url in requests)
                assert any('page=2&page_size=10' in url for url in requests)
                assert not errors,errors
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                browser.close();print(f'PASS task list {width}: paging, rename/reload/cancel, draft name, on-demand playback, stable progress refresh, quality switch, details')
if __name__=='__main__':check(1440);check(390)
