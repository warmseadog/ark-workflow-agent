"""Offline browser regression: durable drafts, snapshot queue and uncertain submit recovery.
All requests are intercepted; this never touches live sessions or paid providers.
"""
import copy
import io
from PIL import Image
import json
from pathlib import Path
from urllib.parse import urlsplit
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parents[1]
CONFIG = dict(provider='ark', protocol='ark', mode='mock', base_url='https://example.test',
              public_base_url='', model='fixture-model', duration=5, fps=0, resolution='720p')


def check(width=1440, portrait_people=False, extra_references=False, prompt_sync=False, accessories=False):
    fixture=io.BytesIO(); Image.new('RGB',(64,80),'#c8ad92').save(fixture,format='PNG'); image_bytes=fixture.getvalue()
    drafts, assets, runs, submissions = {}, {}, {}, []
    uploads = []
    photos = {}
    held_uploads = []
    people = [dict(id="p1",name="小李",photo_count=0,verified=True),dict(id="p2",name="小张",photo_count=0,verified=True)]
    switches = {'lose_response':False, 'conflict':False}
    def new_draft(source=None):
        item = dict(id=f'd{len(drafts)+1}', name=f'草稿 {len(drafts)+1}', revision=0,
                    source_asset_id=None, face_asset_ids=[], clothing_asset_ids=[], prompt='',
                    mask={}, model=CONFIG.copy(), updated_at='2026-09-27T00:00:00', assets=[])
        if source:
            item.update({k: copy.deepcopy(v) for k, v in source.items() if k not in ('id','revision','name')})
        drafts[item['id']] = item
        return copy.deepcopy(item)
    new_draft()
    drafts['d1']['prompt'] = '服务器保存的提示词'
    html = Environment(loader=FileSystemLoader(ROOT / 'app/templates')).get_template('production.html').render()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        context = browser.new_context(viewport={'width': width, 'height': 1000})
        context.add_init_script("localStorage.setItem('production-prompt-draft-v1', JSON.stringify({name:'old',content:'过期的浏览器提示词'}))")
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('dialog', lambda dialog: dialog.accept())
        def handle(route):
            req, path = route.request, urlsplit(route.request.url).path
            body = req.post_data_json if req.method in ('POST','PUT') and 'application/json' in req.headers.get('content-type','') else {}
            if path.startswith('/static/'):
                route.fulfill(path=str(ROOT / 'app' / path.lstrip('/')))
            elif path == '/': route.fulfill(content_type='text/html', body=html)
            elif path == '/api/portrait/people': route.fulfill(json={'items':people if portrait_people else []})
            elif path.startswith('/api/portrait/people/') and req.method == 'PUT':
                person=next(x for x in people if x['id']==path.rsplit('/',1)[-1]);person['name']=body['name'];route.fulfill(json=person)
            elif path == '/api/portrait/photos':
                if req.method == 'POST':
                    key=body['person_id']+':'+body['asset_id']
                    photos.setdefault(key,dict(id='job'+str(len(photos)+1),person_id=body['person_id'],asset_id=body['asset_id'],status='processing',message='校验中'))
                    route.fulfill(json=photos[key])
                else: route.fulfill(json={'items':list(photos.values())})
            elif path == '/api/model-settings': route.fulfill(json={'config': dict(CONFIG, has_api_key=False, status='demo'), 'presets': {'ark': {'models': []}}})
            elif path == '/api/prompt-templates': route.fulfill(json={'items': [{'id':'t1','name':'应用模板','content':'模板新提示词'}]})
            elif path == '/api/link-settings': route.fulfill(json={'has_api_key':False})
            elif path == '/api/production/drafts':
                route.fulfill(json=new_draft(drafts.get(body.get('copy_from'))) if req.method == 'POST' else {'items':list(drafts.values())})
            elif path.startswith('/api/production/drafts/'):
                d = drafts[path.rsplit('/',1)[-1]]
                if req.method == 'PUT':
                    if switches['conflict']:
                        route.fulfill(status=409, json={'detail':'版本冲突'}); return
                    assert body['revision'] == d['revision']
                    assert 'api_key' not in body.get('model', {})
                    d.update(body); d['revision'] += 1
                    d['assets'] = [assets[a] for a in [d['source_asset_id'], *d['face_asset_ids'], *d['clothing_asset_ids'], *d.get('hairstyle_asset_ids',[]), *d.get('scene_asset_ids',[]), *[a for k in ('bag','hat','watch','shoes','necklace','glasses') for a in d.get(k+'_asset_ids',[])]] if a]
                route.fulfill(json=copy.deepcopy(d))
            elif path == '/api/video-link/inspect': route.fulfill(json={'configured':True,'platform':'douyin','label':'抖音','url':'https://example.test/video'})
            elif path == '/api/production/assets/import':
                aid=f'a{len(assets)+1}'
                assets[aid]=dict(id=aid,kind='video',name='imported.mp4',size=7,mime='video/mp4',url=f'/api/production/assets/{aid}/file')
                route.fulfill(json=assets[aid])
            elif path == '/api/jobs' and req.method=='POST':
                assert b'name="video"' in req.post_data_buffer
                route.fulfill(json={'id':'preview','status':'defaced','progress':60,'defaced_url':'/api/jobs/preview/defaced'})
            elif path == '/api/jobs/preview/defaced': route.fulfill(body=b'fixture',content_type='video/mp4')
            elif path == '/api/production/assets':
                data = req.post_data_buffer
                kind = next(k for k in ('video','face','clothing','hairstyle','scene','bag','hat','watch','shoes','necklace','glasses') if ('\r\n\r\n'+k+'\r\n').encode() in data)
                aid = f'a{len(assets)+1}'
                assets[aid] = dict(id=aid, kind=kind, name=f'{kind}.bin', size=8, mime='video/mp4' if kind=='video' else 'image/png', url=f'/api/production/assets/{aid}/file')
                uploads.append(kind)
                if switches.get('hold_face') and kind == 'face': held_uploads.append((route,assets[aid])); return
                route.fulfill(json=assets[aid])
            elif path.startswith('/api/production/assets/'):
                route.fulfill(body=image_bytes if extra_references or accessories else b'fixture', content_type='image/png' if extra_references or accessories else 'application/octet-stream')
            elif path == '/api/production/runs':
                if req.method == 'POST':
                    submissions.append(body)
                    existing = next((r for r in runs.values() if r['key']==body['idempotency_key']), None)
                    if not existing:
                        d = drafts[body['draft_id']]
                        rid = f'r{len(runs)+1}'
                        existing = dict(id=rid, draft_id=d['id'], name=d['name'], status='queued', stage='preprocess', message='等待处理', progress=0, snapshot=copy.deepcopy(d), key=body['idempotency_key'], created_at=d['updated_at'], legacy=False,defaced_url=f'/api/production/runs/{rid}/defaced')
                        runs[rid] = existing
                    if switches['lose_response']:
                        switches['lose_response']=False; route.abort(); return
                    route.fulfill(json=existing)
                else: route.fulfill(json={'items':list(runs.values())})
            elif path.startswith('/api/production/runs/') and req.method == 'DELETE':
                rid = path.rsplit('/',1)[-1]; runs.pop(rid); route.fulfill(json={'id':rid,'deleted':True})
            elif path.endswith('/cancel'):
                r = runs[path.split('/')[-2]]; r['status']='cancelled'; route.fulfill(json=r)
            elif path.endswith('/copy'):
                route.fulfill(json=new_draft(runs[path.split('/')[-2]]['snapshot']))
            else: route.fulfill(status=404, json={'detail':path})
        page.route('**/*', handle)
        page.goto('http://127.0.0.1:18743/')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('#generation-prompt')).to_have_value('服务器保存的提示词')
        if portrait_people and not drafts['d1'].get('person_id'):
            page.locator('#person-picker > summary').click()
            expect(page.locator('[data-person-id=p1]')).to_contain_text('上传第一张照片')
            page.locator('[data-person-id=p1]').click()
        page.locator('#flow-stage-generation > summary').click()
        page.locator('#generation-prompt').fill('任务 A 的提示词')
        for name, kind in [('video','video'),('face_image','face'),('clothing_image','clothing')]:
            page.locator(f'[name={name}]').set_input_files({'name':f'{kind}.mp4' if kind=='video' else f'{kind}.png', 'mimeType':'video/mp4' if kind=='video' else 'image/png', 'buffer':b'fixture'})
        if accessories:
            expect(page.locator('#scene-picker')).to_be_visible()
            assert page.locator('#scene-references').evaluate('(el)=>el.tagName')=='ARTICLE'
            page.locator('#accessory-references > summary').click()
            kinds={'bag':'包包','hat':'帽子','watch':'手表','shoes':'鞋子','necklace':'项链','glasses':'眼镜'}
            for kind,label in kinds.items():
                with page.expect_file_chooser() as chooser:
                    page.locator('#'+kind+'-add').click()
                chooser.value.set_files({'name':kind+'.png','mimeType':'image/png','buffer':image_bytes})
                expect(page.locator('#'+kind+'-references')).to_be_visible()
                expect(page.locator('#'+kind+'-enabled')).to_be_checked()
            expect(page.locator('#reference-count')).to_contain_text('8 / 9')
            assert '@Image8 眼镜参考' in page.locator('#generation-prompt').input_value()
            page.locator('#hairstyle-references > summary').click()
            for kind in ['hairstyle','scene']:
                page.locator('[name='+kind+'_image]').set_input_files({'name':kind+'.png','mimeType':'image/png','buffer':image_bytes})
            expect(page.locator('#reference-count')).to_contain_text('10 / 9')
            expect(page.locator('#studio-generate-submit')).to_be_disabled()
            page.locator('#hat-enabled').uncheck()
            expect(page.locator('#reference-count')).to_contain_text('9 / 9')
            page.locator('#studio-generate-submit').click()
            expect(page.locator('[data-run-id]')).to_have_count(1)
            assert runs['r1']['snapshot']['hat_enabled'] is False
            assert len(runs['r1']['snapshot']['assets'])==11
            page.reload()
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            expect(page.locator('#scene-picker')).to_be_visible()
            expect(page.locator('#hat-enabled')).not_to_be_checked()
            expect(page.locator('#hat-reference-preview img')).to_have_count(1)
            expect(page.locator('#reference-count')).to_contain_text('9 / 9')
            page.locator('#accessory-references > summary').click()
            expect(page.locator('#reference-count')).to_contain_text('9 / 9')
            page.locator('#accessory-references > summary').click()
            page.locator('#glasses-reference-preview button[aria-label="删除第 1 张图片"]').click()
            expect(page.locator('#glasses-references')).to_be_hidden()
            expect(page.locator('#reference-count')).to_contain_text('8 / 9')
            assert '眼镜参考：' not in page.locator('#generation-prompt').input_value()
            page.locator('#scene-reference-preview button[aria-label="删除第 1 张图片"]').click()
            page.locator('#scene-description').fill('夜晚的海边')
            assert '按文字描述替换场景：夜晚的海边' in page.locator('#generation-prompt').input_value()
            page.locator('#scene-enabled').uncheck()
            assert '夜晚的海边' not in page.locator('#generation-prompt').input_value()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert not errors,errors
            page.screenshot(path=str(ROOT/'storage'/f'accessories-{width}.png'),full_page=True)
            browser.close()
            print(f'PASS accessories {width}: 6 categories, optional scene, count, prompt, snapshot, reload')
            return
        if extra_references:
            for kind in ('scene','hairstyle'):
                if kind=='hairstyle': page.locator(f'#{kind}-references > summary').click()
                page.locator(f'[name={kind}_image]').set_input_files({'name':kind+'.png','mimeType':'image/png','buffer':image_bytes})
                expect(page.locator(f'#{kind}-enabled')).to_be_checked()
            page.locator('#scene-description').fill('暖色室内')
            if prompt_sync:
                text=page.locator('#generation-prompt').input_value()
                assert '@Image3 发型参考' in text and '@Image4 场景参考' in text,text
                assert '任务 A 的提示词' in text and '暖色室内' in text
                prompt=page.locator('#generation-prompt')
                prompt.fill('hello ')
                prompt.evaluate('(el)=>el.setSelectionRange(6,6)')
                page.wait_for_timeout(750)
                assert prompt.input_value()=='hello ' and prompt.evaluate('(el)=>el.selectionStart')==6,'Autosave moved editing caret'
                prompt.press('End');prompt.press_sequentially('world')
                assert prompt.input_value()=='hello world'
                prompt.fill('请不要改变动作、镜头和场景')
                page.locator('#scene-enabled').uncheck();page.locator('#scene-enabled').check()
                assert prompt.input_value().startswith('请不要改变动作、镜头和场景'),'Sync must preserve user body'

                page.locator('[data-prompt-template=t1]').click()
                text=page.locator('#generation-prompt').input_value()
                assert text.startswith('模板新提示词') and '@Image3 发型参考' in text
                page.locator('[name=face_image]').set_input_files({'name':'second-face.png','mimeType':'image/png','buffer':image_bytes})
                text=page.locator('#generation-prompt').input_value()
                assert '@Image4 发型参考' in text and '@Image5 场景参考' in text
                page.locator('#face-reference-preview button[aria-label="删除第 2 张图片"]').click()
                assert '@Image3 发型参考' in page.locator('#generation-prompt').input_value()
                page.locator('#hairstyle-enabled').uncheck()
                text=page.locator('#generation-prompt').input_value()
                assert '发型沿用主人物参考图' in text and '@Image3 发型参考' not in text and '@Image3 场景参考' in text
                page.locator('#hairstyle-enabled').check()
                assert page.locator('#generation-prompt').input_value().count('【素材联动】')==1

            expect(page.locator('#scene-picker')).to_be_visible()
            page.locator('#hairstyle-references > summary').click()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.locator('#studio-generate-submit').click()
            expect(page.locator('[data-run-id]')).to_have_count(1)
            assert runs['r1']['snapshot']['scene_description']=='暖色室内'
            assert len(runs['r1']['snapshot']['assets'])==5
            page.reload()
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            for kind in ('scene','hairstyle'):
                if kind=='hairstyle': page.locator(f'#{kind}-references > summary').click()
                expect(page.locator(f'#{kind}-reference-preview img')).to_have_count(1)
                expect(page.locator(f'#{kind}-enabled')).to_be_checked()
                assert page.locator(f'#{kind}-reference-preview img').evaluate('(el)=>el.complete && el.naturalWidth>0')
            expect(page.locator('#scene-description')).to_have_value('暖色室内')
            page.locator('#scene-enabled').uncheck()
            page.locator('#studio-generate-submit').click()
            expect(page.locator('[data-run-id]')).to_have_count(2)
            assert runs['r1']['snapshot']['scene_enabled'] is True
            assert runs['r2']['snapshot']['scene_enabled'] is False
            page.locator('[data-run-id=r2] [data-run-action=details]').click()
            expect(page.locator('[data-run-id=r2] .run-detail-assets figure')).to_have_count(5)
            expect(page.locator('[data-run-id=r2] [data-source-kind=scene]')).to_contain_text('未使用')
            if prompt_sync:
                text=page.locator('#generation-prompt').input_value()
                assert '@Image3 发型参考' in text and '保留原视频场景' in text and '暖色室内' not in text
                page.locator('#hairstyle-reference-preview button[aria-label="删除第 1 张图片"]').click()
                text=page.locator('#generation-prompt').input_value()
                assert '发型沿用主人物参考图' in text and '@Image3 发型参考' not in text
                page.locator('#flow-stage-generation > summary').click()
                page.locator('#generation-prompt').fill('我的额外要求：自然光')
                page.locator('#hairstyle-enabled').check()
                # Scene is still present (disabled): updating roles preserves manually edited base text.
                assert '我的额外要求：自然光' in page.locator('#generation-prompt').input_value()
            assert len(uploads)==(6 if prompt_sync else 5)
            if prompt_sync:
                # Copy an old snapshot that predates visible rules; immediate submission must freeze visible text.
                runs['r1']['snapshot']['prompt']='old legacy prompt'
                page.locator('[data-run-id=r1] [data-run-action=copy]').click()
                expect(page.locator('#studio-generate-submit')).to_be_enabled()
                visible=page.locator('#generation-prompt').input_value()
                assert 'old legacy prompt' in visible and '@Image3 发型参考' in visible
                page.locator('#studio-generate-submit').click()
                expect(page.locator('[data-run-id]')).to_have_count(3)
                assert runs['r3']['snapshot']['prompt']==visible,'Copied snapshot lost displayed role block'
                runs['r1']['snapshot'].update(hairstyle_asset_ids=[],scene_asset_ids=[],scene_description='',scene_enabled=False,prompt='plain old draft')
                page.locator('[data-run-id=r1] [data-run-action=copy]').click()
                expect(page.locator('#generation-prompt')).to_have_value('plain old draft')
                expect(page.locator('#prompt-reference-status')).to_have_text('可选，展开编辑')

            page.screenshot(path=str(ROOT/'storage'/f'scene-hairstyle-{width}.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert not errors,errors
            browser.close()
            print(f'PASS scene hairstyle {width}: upload, collapse, restore, disable, immutable snapshots')
            return
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        page.locator('#studio-generate-submit').click()
        expect(page.locator('[data-run-id]')).to_have_count(1)
        expect(page.locator('#generation-prompt')).to_be_enabled()
        first_card=page.locator('[data-run-id=r1]')
        first_card.locator('[data-run-action=details]').click()
        expect(first_card.locator('.run-detail-assets figure')).to_have_count(3)
        expect(first_card.locator('.run-detail-assets figcaption')).to_have_text(['参考视频','人物参考图','衣服参考图'])
        detail_text=first_card.locator('details').inner_text()
        assert not any(x in detail_text for x in ['任务编号','服务商任务编号','Request ID','模型参数','打码参数','base_url','provider'])
        expect(first_card.locator('details pre')).to_have_count(0)
        preview_toggle=first_card.locator('[data-run-action=preview-redacted]')
        expect(preview_toggle).to_have_text('预览打码效果')
        expect(first_card.locator('[data-redacted-preview]')).to_be_hidden()
        assert first_card.locator('[data-redacted-preview] video').get_attribute('src') is None
        preview_toggle.click()
        expect(first_card.locator('[data-redacted-preview]')).to_be_visible()
        expect(preview_toggle).to_have_attribute('aria-expanded','true')
        assert first_card.locator('[data-redacted-preview] video').get_attribute('src')==runs['r1']['defaced_url']
        preview_toggle.click()
        expect(first_card.locator('[data-redacted-preview]')).to_be_hidden()
        assert first_card.locator('[data-redacted-preview] video').evaluate('(el)=>el.paused')
        first_card.locator('[data-run-action=details]').click()
        assert runs['r1']['snapshot']['prompt'] == '任务 A 的提示词'
        assert isinstance(runs['r1']['snapshot']['mask']['mask_scale'], (int,float))
        expect(page.locator('.draft-toolbar')).to_have_count(0)
        if portrait_people:
            assert runs['r1']['snapshot']['person_id']=='p1'
            page.locator('#person-picker > summary').click()
            page.locator('[data-person-id=p2]').click()
            expect(page.locator('#person-current')).to_contain_text('小张')
        page.locator('#generation-prompt').fill('任务 B 的提示词')
        page.locator('#studio-generate-submit').click()
        expect(page.locator('[data-run-id]')).to_have_count(2)
        assert runs['r1']['snapshot']['prompt'] == '任务 A 的提示词'
        assert runs['r2']['snapshot']['prompt'] == '任务 B 的提示词'
        if portrait_people:
            assert runs['r2']['snapshot']['person_id']=='p2'
            assert runs['r1']['snapshot']['person_id']=='p1'
            assert len(photos)==2,photos

        assert len(uploads)==3, uploads
        page.reload()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('#generation-prompt')).to_have_value('任务 B 的提示词')
        expect(page.locator('#face-reference-preview img')).to_have_count(1)
        if portrait_people:
            expect(page.locator('#person-current')).to_contain_text('小张')
            page.locator('#person-picker > summary').click()
            page.locator('#person-rename-toggle').click()
            page.locator('#person-name').fill('模特小张')
            page.locator('#person-name-save').click()
            expect(page.locator('#person-current')).to_contain_text('模特小张')
            page.locator('#person-picker > summary').click()

        expect(page.locator('[data-run-id]')).to_have_count(2)
        page.locator('#flow-stage-generation > summary').click()
        page.locator('#generation-prompt').fill('任务 A 的提示词')
        expect(page.locator('#generation-prompt')).to_have_value('任务 A 的提示词')
        assert len(uploads)==3
        # Losing an accepted POST response must retain the exact request key across reload.
        switches['lose_response'] = True
        page.locator('#studio-generate-submit').click()
        expect(page.locator('#studio-generate-submit')).to_contain_text('确认上次提交结果')
        before = len(runs)
        lost_key = submissions[-1]['idempotency_key']
        page.reload()
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        page.locator('#studio-generate-submit').click()
        expect(page.locator('#production-status')).to_contain_text('已加入队列')
        assert submissions[-1]['idempotency_key'] == lost_key
        assert len(runs) == before
        # Explicit server conflicts preserve the editor and offer lossless recovery.
        page.locator('#flow-stage-generation > summary').click()
        switches['conflict'] = True
        page.locator('#generation-prompt').fill('冲突后仍保留的内容')
        expect(page.locator('#draft-recover')).to_be_visible()
        expect(page.locator('#generation-prompt')).to_have_value('冲突后仍保留的内容')
        switches['conflict'] = False
        page.locator('#draft-recover').click()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        assert drafts['d2']['prompt'] == '冲突后仍保留的内容'
        assert len(uploads)==3
        page.locator('#redaction-settings > summary').click()
        page.locator('[name=mask_scale]').fill('1.6')
        page.locator('#studio-preview-submit').click()
        expect(page.locator('#studio-preview-status')).to_contain_text('预览已就绪')
        page.locator('[data-redaction-save]').click()
        page.locator('#model-settings-toggle').click()
        page.locator('#model-settings-form [name=duration]').fill('6')
        page.locator('#close-model-settings').click()
        page.locator('[data-prompt-template=t1]').click()
        expect(page.locator('#generation-prompt')).to_have_value('模板新提示词')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        assert drafts['d2']['model']['duration']==6
        assert drafts['d2']['mask']['mask_scale']==1.6
        page.locator('#toggle-video-url').click()
        page.locator('[name=video_url]').fill('https://example.test/video')
        page.locator('#confirm-video-url').click()
        expect(page.locator('#source-file-name')).to_contain_text('imported.mp4')
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        assert drafts['d2']['source_asset_id']=='a4'
        assert len(uploads)==3
        runs['r2'].update(status='failed', error_kind='material_rejected', error='sensitive content', request_id='req-fixture')
        page.locator('#runs-refresh').click()
        expect(page.locator('[data-run-id=r2]')).to_contain_text('sensitive content')
        expect(page.locator('[data-run-id=r2] [data-run-action=resume]')).to_have_count(0)
        page.locator('[data-run-id=r2] [data-run-action=details]').click()
        expect(page.locator('[data-run-id=r2] .run-error-detail')).to_contain_text('req-fixture')
        page.locator('[data-run-id=r1] [data-run-action=cancel]').click()
        expect(page.locator('[data-run-id=r1]')).to_contain_text('已取消')
        expect(page.locator('[data-run-id=r2] > details')).to_have_attribute('open','')
        page.locator('#runs-refresh').click()
        expect(page.locator('[data-run-id=r2] > details')).to_have_attribute('open','')
        page.locator('[data-run-id=r2] [data-run-action=delete]').click()
        expect(page.locator('[data-run-id=r2]')).to_have_count(0)
        page.reload()
        expect(page.locator('#draft-save-status')).to_contain_text('已保存')
        expect(page.locator('[data-run-id=r2]')).to_have_count(0)
        expect(page.locator('#face-reference-preview img')).to_have_count(1)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        if portrait_people:
            page.locator('#person-picker > summary').click()
            page.locator('[data-person-id=p1]').click()
            switches['hold_face']=True
            page.locator('[name=face_image]').set_input_files({'name':'slow.png','mimeType':'image/png','buffer':b'slow upload'})
            expect(page.locator('#face-reference-preview img')).to_have_count(2)
            for _ in range(30):
                if held_uploads:break
                page.wait_for_timeout(100)
            assert held_uploads
            count=len(photos)
            page.locator('#face-reference-preview button[aria-label="删除第 2 张图片"]').click()
            page.locator('#person-picker > summary').click();page.locator('[data-person-id=p2]').click()
            for route,asset in held_uploads:route.fulfill(json=asset)
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            assert len(photos)==count,'Removed photo must not be submitted for the newly selected person'
            page.locator('#person-picker > summary').click()
            page.get_by_role('button',name='普通参考图（不使用真人授权）',exact=True).click()
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            assert drafts[page.evaluate("localStorage.getItem('production-current-draft-v1')")]['person_id'] is None
            page.reload();expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            expect(page.locator('#person-current')).to_have_text('选择人物')
            page.locator('#person-picker > summary').click();page.locator('[data-person-id=p2]').click()
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            page.screenshot(path=str(ROOT/'storage'/f'portrait-people-{width}.png'),full_page=True)
        assert not errors, errors
        browser.close()
    print(f'PASS durable production session {width}: restore, upload once, independent snapshots, editor unlock, cancel')




def integration():
    """Real app/SQLite/worker/mock provider; only face detection is a local copy fixture."""
    import os
    import socket
    import sys
    import subprocess
    import tempfile
    import time
    import urllib.request
    import cv2
    import numpy as np
    with tempfile.TemporaryDirectory(prefix='production-browser-') as temporary:
        root = Path(temporary)
        video = root / 'source.mp4'
        writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*'mp4v'), 12, (160, 160))
        for index in range(24):
            frame = np.full((160, 160, 3), 210, dtype=np.uint8)
            cv2.rectangle(frame, (index + 10, 40), (index + 40, 90), (80, 100, 170), -1)
            writer.write(frame)
        writer.release()
        reference = root / 'ref.png'
        cv2.imwrite(str(reference), np.full((100,100,3),150,dtype=np.uint8))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        env = dict(os.environ, STORAGE_DIR=str(root / 'storage'), DATABASE_URL='sqlite:///'+str(root / 'db.sqlite'),
                   SEEDANCE_MODE='mock', SEEDANCE_API_URL='', SEEDANCE_API_KEY='',
                   DEFACE_BIN=str(ROOT / '.venv/Scripts/deface.exe'), PYTHONPATH=str(ROOT / '.venv/Lib/site-packages') + os.pathsep + str(ROOT))
        with (root / 'server.log').open('w',encoding='utf-8') as log:
            bootstrap = "import shutil, uvicorn; from app import production_worker; production_worker.run_deface = lambda source, target, *args: shutil.copyfile(source, target); uvicorn.run('app.main:app', host='127.0.0.1', port="+str(port)+")"
            process = subprocess.Popen([sys._base_executable,'-c',bootstrap],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                url=f'http://127.0.0.1:{port}'
                for _ in range(120):
                    try:
                        urllib.request.urlopen(url, timeout=1); break
                    except Exception:
                        if process.poll() is not None: raise RuntimeError((root / 'server.log').read_text())
                        time.sleep(.25)
                else: raise RuntimeError('Isolated server did not start')
                with sync_playwright() as p:
                    browser=p.chromium.launch(channel='msedge')
                    page=browser.new_page(viewport={'width':1440,'height':1000})
                    errors=[]; page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(url)
                    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                    page.locator('#flow-stage-generation > summary').click()
                    page.locator('#generation-prompt').fill('真实本地测试 A')
                    page.locator('[name=video]').set_input_files(str(video))
                    for field in ['face_image','clothing_image']: page.locator(f'[name={field}]').set_input_files(str(reference))
                    page.locator('#studio-generate-submit').click()
                    expect(page.locator('[data-run-id]')).to_have_count(1)
                    expect(page.locator('#generation-prompt')).to_be_enabled()
                    page.locator('[data-run-action=copy]').first.click()
                    page.locator('#generation-prompt').fill('真实本地测试 B')
                    page.locator('#studio-generate-submit').click()
                    expect(page.locator('[data-run-id]')).to_have_count(2)
                    selected=page.evaluate("localStorage.getItem('production-current-draft-v1')")
                    page.reload()
                    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                    assert page.evaluate("localStorage.getItem('production-current-draft-v1')")==selected
                    expect(page.locator('#generation-prompt')).to_have_value('真实本地测试 B')
                    expect(page.locator('#face-reference-preview img')).to_have_count(1)
                    expect(page.locator('[data-run-id]')).to_have_count(2)
                    expect(page.locator('[data-state=succeeded]')).to_have_count(2,timeout=120000)
                    expect(page.locator('.run-actions a[download]')).to_have_count(2)
                    result=page.request.get(url+'/api/production/runs').json()['items']
                    assert {r['snapshot']['prompt'] for r in result}=={'真实本地测试 A','真实本地测试 B'}
                    assert all(r['snapshot']['model']['mode']=='mock' for r in result)
                    assert not errors,errors
                    page.screenshot(path=str(ROOT/'storage/durable-production-desktop.png'),full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    page.screenshot(path=str(ROOT/'storage/durable-production-mobile.png'),full_page=True)
                    card=page.locator('[data-state=succeeded]').first
                    removed=card.get_attribute('data-run-id')
                    card.locator('[data-run-action=details]').click()
                    expect(card.locator('.run-detail-assets figure')).to_have_count(3)
                    page.once('dialog', lambda dialog: dialog.dismiss())
                    card.locator('[data-run-action=delete]').click()
                    expect(page.locator('[data-run-id]')).to_have_count(2)
                    page.once('dialog', lambda dialog: dialog.accept())
                    card.locator('[data-run-action=delete]').click()
                    expect(page.locator('[data-run-id]')).to_have_count(1)
                    page.reload()
                    expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                    expect(page.locator('[data-run-id]')).to_have_count(1)
                    assert page.request.get(url+'/api/production/runs/'+removed).status==404
                    expect(page.locator('#face-reference-preview img')).to_have_count(1)
                    page.screenshot(path=str(ROOT/'storage/task-actions-mobile.png'),full_page=True)
                    browser.close()
                print('PASS real isolated server: persistent media, independent A/B runs, refresh, mock pipeline success and downloads')
            finally:
                process.terminate()
                try: process.wait(timeout=15)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
                if process.returncode and process.returncode not in (-15,1): print((root / 'server.log').read_text(encoding='utf-8',errors='replace')[-5000:])


if __name__ == '__main__':
    import sys
    if '--integration' in sys.argv: integration()
    else:
        check()
        check(390)
