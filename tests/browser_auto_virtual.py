"""Offline UI contract: automatic uploads, durable policies and isolated late replies.

Real frontend runs in Edge; every HTTP request is intercepted. Removing explicit
draft policy, enabling sole-person selection, or queueing portraits before submit
must break these checks. No production API or paid provider is contacted.
"""
import copy
import io
import re
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

import imageio_ffmpeg
from jinja2 import Environment, FileSystemLoader
from PIL import Image
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
CONFIG = dict(provider='ark', protocol='ark', mode='mock', base_url='https://example.test',
              public_base_url='', model='fixture-model', duration=5, fps=0, resolution='720p')


def check(width=1440, sole_real=False, legacy=False, bound=False):
    drafts, assets, runs, calls, held, photo_queries = {}, {}, {}, [], [], []
    switches = {'hold_video': False, 'hold_face': False, 'hold_photo_query': False, 'fail_people_once': False}
    person = dict(id='real-1', name='已有真人', person_type='LivenessFace', verified=True, photo_count=1)
    library_people = [person] if sole_real else []
    image = io.BytesIO()
    Image.new('RGB', (400, 400), '#bab2a9').save(image, format='PNG')

    def new_draft(body):
        item = dict(id=f'd{len(drafts)+1}', name='视频测试', revision=0, source_asset_id=None,
                    face_asset_ids=[], clothing_asset_ids=[], person_id=None, person_reference_mode='image',
                    person_video_asset_id=None, prompt='参考动作', mask={}, model=CONFIG.copy(),
                    updated_at='2026-09-28T00:00:00', assets=[])
        item.update(body)
        drafts[item['id']] = item
        return copy.deepcopy(item)

    if legacy:
        if bound:
            assets['bound'] = dict(id='bound', kind='face', name='bound.png', mime='image/png',
                                   url='/api/production/assets/bound/file', sha256='bound',
                                   portrait={'status': 'Active', 'remote_asset_id': 'asset-bound', 'group_id': 'group-real'})
        new_draft({'face_asset_ids': ['bound'], 'assets': [assets['bound']]} if bound else {})
    html = Environment(loader=FileSystemLoader(ROOT / 'app/templates')).get_template('production.html').render()
    with TemporaryDirectory(prefix='auto-virtual-browser-') as tmp:
        clip = Path(tmp) / 'person.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i',
                        'color=c=#b0a0bc:s=640x640:r=24', '-t', '3', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', str(clip)], check=True, capture_output=True)
        video_bytes = clip.read_bytes()
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='msedge')
            page = browser.new_page(viewport={'width': width, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def handle(route):
                req, path = route.request, urlsplit(route.request.url).path
                body = req.post_data_json if 'application/json' in req.headers.get('content-type', '') else {}
                calls.append((req.method, path, body))
                if path == '/':
                    route.fulfill(content_type='text/html', body=html)
                elif path.startswith('/static/'):
                    route.fulfill(path=str(ROOT / 'app' / path.lstrip('/')))
                elif path == '/api/portrait/people':
                    if switches['fail_people_once']:
                        switches['fail_people_once'] = False
                        route.fulfill(status=503, json={'detail': '人物目录暂时不可用'})
                    else:
                        route.fulfill(json={'items': library_people})
                elif path == '/api/portrait/people/real-1/photos':
                    assets['official-real'] = dict(id='official-real', kind='face', name='real.png', size=100,
                                                   mime='image/png', sha256='real-image', url='/api/production/assets/official-real/file',
                                                   person_type='LivenessFace', portrait={'status': 'Active', 'remote_asset_id': 'asset-real', 'group_id': 'group-real'})
                    route.fulfill(json={'items': [dict(id='photo-real', asset_id='official-real', name='real.png',
                                                      url=assets['official-real']['url'], status='active', remote_asset_id='asset-real')]})
                elif path == '/api/portrait/import':
                    route.fulfill(json=assets['official-real'])
                elif path == '/api/portrait/people/resolve':
                    route.fulfill(json=person)
                elif path == '/api/model-settings':
                    route.fulfill(json={'config': dict(CONFIG, has_api_key=False, status='demo'), 'presets': {'ark': {'models': []}}})
                elif path == '/api/prompt-templates':
                    route.fulfill(json={'items': []})
                elif path == '/api/link-settings':
                    route.fulfill(json={'has_api_key': False})
                elif path == '/api/production/drafts':
                    route.fulfill(json=new_draft(body) if req.method == 'POST' else {'items': list(drafts.values())})
                elif path.startswith('/api/production/drafts/'):
                    draft = drafts[path.rsplit('/', 1)[-1]]
                    if req.method == 'PUT':
                        assert body['revision'] == draft['revision']
                        draft.update(body)
                        draft['revision'] += 1
                        ids = [draft['source_asset_id'], draft.get('person_video_asset_id'), *draft['face_asset_ids'], *draft['clothing_asset_ids']]
                        draft['assets'] = [assets[aid] for aid in ids if aid]
                    route.fulfill(json=copy.deepcopy(draft))
                elif path == '/api/production/assets':
                    data = req.post_data_buffer
                    kind = next(k for k in ('person_video', 'video', 'face', 'clothing') if ('\r\n\r\n'+k+'\r\n').encode() in data)
                    aid = f'a{len(assets)+1}'
                    asset = dict(id=aid, kind=kind, name=f'{aid}.mp4' if 'video' in kind else f'{aid}.png',
                                 size=100, mime='video/mp4' if 'video' in kind else 'image/png', sha256=aid,
                                 url=f'/api/production/assets/{aid}/file')
                    assets[aid] = asset
                    if switches['hold_video'] and kind == 'person_video' or switches['hold_face'] and kind == 'face':
                        held.append((route, asset))
                    else:
                        route.fulfill(json=asset)
                elif path.startswith('/api/production/assets/'):
                    asset = assets.get(path.split('/')[-2], {})
                    route.fulfill(body=video_bytes if 'video' in asset.get('kind', '') else image.getvalue(),
                                  content_type=asset.get('mime', 'image/png'))
                elif path == '/api/production/runs':
                    if req.method == 'POST':
                        draft = drafts[body['draft_id']]
                        assert body['revision'] == draft['revision']
                        rid = f'r{len(runs)+1}'
                        runs[rid] = dict(id=rid, draft_id=draft['id'], name=draft['name'], status='queued',
                                         stage='authorizing', progress=0, message='正在准备虚拟人物',
                                         person_preparation={'state': 'waiting', 'person_id': None, 'message': '人物素材处理中'},
                                         snapshot=copy.deepcopy(draft), created_at=draft['updated_at'], can_cancel=True)
                        route.fulfill(json=runs[rid])
                    else:
                        route.fulfill(json={'items': list(runs.values())})
                elif path.endswith('/person-preparation/retry'):
                    run = runs[path.split('/')[-3]]
                    run.update(status='queued', can_retry_preparation=False,
                               person_preparation={'state': 'waiting', 'person_id': 'virtual-1', 'message': '继续检查原人物素材'})
                    route.fulfill(json=run)
                elif path.startswith('/api/production/runs/') and path.endswith('/copy'):
                    snapshot = runs[path.split('/')[-2]]['snapshot']
                    route.fulfill(json=new_draft({k: copy.deepcopy(v) for k, v in snapshot.items() if k not in {'id', 'revision', 'name'}}))
                elif path.startswith('/api/production/runs/'):
                    route.fulfill(json=runs[path.rsplit('/', 1)[-1]])
                elif path == '/api/portrait/photos':
                    if req.method == 'GET' and switches['hold_photo_query']:
                        photo_queries.append(route)
                    else:
                        route.fulfill(json={'id': 'photo-1', 'status': 'processing', 'message': '检查中'} if req.method == 'POST' else {'items': []})
                elif path == '/favicon.ico':
                    route.fulfill(status=204)
                else:
                    raise AssertionError(f'Unexpected HTTP request: {req.method} {path}')

            page.route('**/*', handle)
            page.goto('http://127.0.0.1:18758/')
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            if legacy:
                assert not [c for c in calls if c[:2] == ('POST', '/api/production/drafts')]
                assert page.evaluate('productionPortraits.inputPolicy') == ('existing_person' if bound else 'legacy_raw')
            else:
                created = next(body for method, path, body in calls if method == 'POST' and path == '/api/production/drafts')
                assert created.get('person_input_policy') == 'auto_virtual', created
            assert page.evaluate('portraitPeople.selected') is None, 'A new draft must not select the sole real person'
            for selector, name, mime, content in [
                ('#studio-source-video', 'action.mp4', 'video/mp4', video_bytes),
                ('#studio-face-image', 'virtual.png', 'image/png', image.getvalue()),
                ('#studio-clothing-image', 'clothes.png', 'image/png', image.getvalue()),
            ]:
                page.locator(selector).set_input_files({'name': name, 'mimeType': mime, 'buffer': content})
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            assert drafts['d1'].get('person_input_policy') == ('existing_person' if bound else 'legacy_raw' if legacy else 'auto_virtual')
            assert not [c for c in calls if c[1] == '/api/portrait/photos'], 'Local upload must not enqueue portraits'
            page.locator('#studio-generate-submit').click()
            expect(page.locator('[data-run-id]')).to_have_count(1)
            assert runs['r1']['snapshot']['person_id'] is None
            if legacy:
                browser.close()
                if bound:
                    assert runs['r1']['snapshot']['assets'][1]['portrait']['remote_asset_id'] == 'asset-bound'
                print(f'PASS legacy draft {width}: bound={bound}, original path preserved, sole real not selected')
                return
            expect(page.locator('[data-run-id=r1]')).to_contain_text('人物素材处理中')
            page.reload()
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            assert page.evaluate('productionPortraits.inputPolicy') == 'auto_virtual'
            page.locator('[data-person-media=video]').click()
            page.locator('#person-video-file').set_input_files(str(clip))
            expect(page.locator('#person-video-preview')).to_be_visible()
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            page.wait_for_function('document.getElementById("person-video-preview").duration === 3')
            assert assets[drafts['d1']['person_video_asset_id']]['kind'] == 'person_video'
            expect(page.locator('#studio-generate-submit')).to_be_enabled()
            page.locator('#studio-generate-submit').click()
            expect(page.locator('[data-run-id]')).to_have_count(2)
            assert runs['r2']['snapshot']['person_reference_mode'] == 'video'
            assert not [c for c in calls if c[1] == '/api/portrait/photos']
            # A response arriving after leaving video mode must not replace the current reference.
            page.locator('#person-video-remove').click()
            switches['hold_video'] = True
            page.locator('#person-video-file').set_input_files(str(clip))
            page.wait_for_function('document.getElementById("person-video-status").textContent.includes("保存")')
            page.locator('[data-person-media=image]').click()
            while not held:
                page.wait_for_timeout(30)
            route, asset = held.pop()
            route.fulfill(json=asset)
            page.wait_for_timeout(700)
            page.locator('[data-person-media=video]').click()
            assert page.evaluate('productionPortraits.currentPhoto') is None
            page.locator('[data-person-media=image]').click()
            current_reference = page.evaluate('productionPortraits.currentPhoto.id')
            people_reads = sum(method == 'GET' and path == '/api/portrait/people' for method, path, _ in calls)
            library_people.append(dict(id='virtual-1', name='自动创建的虚拟人物', person_type='AIGC', verified=False, photo_count=1))
            switches['fail_people_once'] = True
            runs['r1'].update(status='needs_attention', can_retry_preparation=True,
                              person_preparation={'state': 'uncertain', 'person_id': 'virtual-1', 'message': '入库结果待确认'})
            page.locator('#runs-refresh').click()
            row = page.locator('[data-run-id=r1]')
            expect(row).to_contain_text('入库结果待确认')
            page.wait_for_timeout(200)
            assert not page.evaluate('portraitPeople.items.some(person => person.id === "virtual-1")')
            assert sum(method == 'GET' and path == '/api/portrait/people' for method, path, _ in calls) == people_reads + 1, 'A failed directory fetch must not trigger an immediate retry loop'
            assert page.evaluate('portraitPeople.selected') is None
            assert page.evaluate('productionPortraits.currentPhoto.id') == current_reference
            assert page.evaluate('productionPortraits.inputPolicy') == 'auto_virtual'
            page.locator('#runs-refresh').click()
            page.locator('#person-picker > summary').click()
            page.locator('[data-person-type=AIGC]').click()
            expect(page.locator('[data-person-id=virtual-1]')).to_be_visible()
            assert page.evaluate('portraitPeople.selected') is None
            assert page.evaluate('productionPortraits.currentPhoto.id') == current_reference
            assert page.evaluate('productionPortraits.inputPolicy') == 'auto_virtual'
            assert sum(method == 'GET' and path == '/api/portrait/people' for method, path, _ in calls) == people_reads + 2
            page.locator('[data-person-type=LivenessFace]').click()
            page.locator('#person-menu-close').click()
            page.locator('#runs-refresh').click()
            expect(row).to_contain_text('入库结果待确认')
            page.wait_for_timeout(100)
            assert sum(method == 'GET' and path == '/api/portrait/people' for method, path, _ in calls) == people_reads + 2
            row.locator('.run-menu > summary').click()
            row.locator('[data-run-action=retry-preparation]').click()
            expect(row).to_contain_text('继续检查原人物素材')
            assert sum(path.endswith('/person-preparation/retry') for _, path, _ in calls) == 1
            if sole_real:
                # A real library choice retains its server type; a late photo query cannot
                # overwrite the automatic-source status after the user switches back.
                page.locator('[data-person-media=image]').click()
                switches['hold_photo_query'] = True
                page.locator('#person-picker > summary').click()
                page.locator('[data-person-id=real-1]').click()
                page.locator('[data-photo-use]').click()
                expect(page.locator('#person-photos-dialog')).not_to_be_visible()
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert drafts['d1']['person_input_policy'] == 'existing_person'
                assert drafts['d1']['person_id'] == 'real-1'
                assert 'person_type' not in drafts['d1']
                for _ in range(60):
                    if photo_queries:
                        break
                    page.wait_for_timeout(100)
                assert photo_queries
                page.locator('#person-auto-virtual').click()
                photo_queries.pop().fulfill(status=503, json={'detail': 'late query error'})
                page.wait_for_timeout(100)
                expect(page.locator('#person-photo-status')).to_contain_text('虚拟人物')
                # The same guard applies to a local image upload that finishes after switching.
                switches['hold_photo_query'] = False
                page.locator('#person-picker > summary').click()
                page.locator('[data-person-id=real-1]').click()
                page.locator('[data-photo-use]').click()
                expect(page.locator('#person-photos-dialog')).not_to_be_visible()
                switches['hold_face'] = True
                page.locator('#studio-face-image').set_input_files({'name': 'late.png', 'mimeType': 'image/png', 'buffer': image.getvalue()})
                for _ in range(30):
                    if held:
                        break
                    page.wait_for_timeout(50)
                assert held
                count = sum(method == 'POST' and path == '/api/portrait/photos' for method, path, _ in calls)
                page.locator('#person-auto-virtual').click()
                route, asset = held.pop()
                route.fulfill(json=asset)
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                expect(page.locator('#face-reference-preview img')).to_have_count(0)
                assert count == sum(method == 'POST' and path == '/api/portrait/photos' for method, path, _ in calls)
            # A copied run becomes a different draft while the local video request is pending.
            page.locator('[data-person-media=video]').click()
            switches['hold_video'] = True
            page.locator('#person-video-file').set_input_files(str(clip))
            for _ in range(30):
                if held:
                    break
                page.wait_for_timeout(50)
            assert held
            row = page.locator('[data-run-id=r2]')
            row.locator('.run-menu > summary').click()
            row.locator('[data-run-action=copy]').click()
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            page.wait_for_function('localStorage.getItem("production-current-draft-v1") === "d2"')
            route, asset = held.pop()
            route.fulfill(json=asset)
            page.wait_for_timeout(200)
            assert page.evaluate('productionPortraits.currentPhoto.id') == runs['r2']['snapshot']['person_video_asset_id']
            assert page.evaluate('productionPortraits.currentPhoto.id') != asset['id']
            assert not errors, errors
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            page.screenshot(path=str(ROOT / 'storage' / f'auto-virtual-{width}.png'), full_page=True)
            browser.close()
            print(f'PASS auto virtual {width}, sole_real={sole_real}: image/video local save, one-click submit, reload, late upload, retry')


def integration():
    """Use real routes, validation and SQLite with new default model settings."""
    from dataclasses import replace
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app import main, portrait_library, portrait_service, production_worker, storage_settings
    from app.production_store import ProductionStore

    with TemporaryDirectory(prefix='auto-virtual-api-') as tmp:
        settings = replace(main.settings, storage_dir=Path(tmp), seedance_mode='mock')
        portrait_service.save_config(settings, {'access_key': 'fixture-ak', 'secret_key': 'fixture-sk'})
        storage_settings.save_config(settings, {'enabled': True, 'bucket': 'fixture-bucket',
                                               'access_key': 'fixture-ak', 'secret_key': 'fixture-sk'})
        clip = Path(tmp) / 'clip.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-f', 'lavfi', '-i',
                        'color=c=#b0a0bc:s=640x640:r=24', '-t', '3', '-c:v', 'libx264',
                        '-pix_fmt', 'yuv420p', str(clip)], check=True, capture_output=True)
        image = io.BytesIO()
        Image.new('RGB', (400, 400), '#b29eac').save(image, format='PNG')
        with patch.object(main, 'settings', settings), patch.object(production_worker, 'wake', lambda *_: None), \
                patch.object(portrait_service.ArkPortraitClient, '_request', side_effect=AssertionError('No official calls before committed run')):
            client = TestClient(main.app, base_url='http://127.0.0.1:18759')
            store = ProductionStore(Path(tmp))
            library = portrait_library.PortraitLibrary(settings)
            library.add_person('group-real-fixture', '库内唯一真人', 'LivenessFace')
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge')
                page = browser.new_page(viewport={'width': 1440, 'height': 1000})
                errors, failed_requests = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))

                def route(reqroute):
                    req = reqroute.request
                    assert urlsplit(req.url).netloc == '127.0.0.1:18759', req.url
                    response = client.request(req.method, req.url, content=req.post_data_buffer,
                                              headers={k: v for k, v in req.headers.items() if k.lower() not in {'host', 'content-length'}})
                    if response.status_code >= 400:
                        failed_requests.append((req.method, urlsplit(req.url).path, response.text))
                    reqroute.fulfill(status=response.status_code, body=response.content,
                                     headers={k: v for k, v in response.headers.items() if k.lower() not in {'content-length', 'content-encoding', 'transfer-encoding'}})

                page.route('**/*', route)
                page.goto('http://127.0.0.1:18759/')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert page.evaluate('portraitPeople.selected') is None
                page.locator('#studio-source-video').set_input_files({'name': 'action.mp4', 'mimeType': 'video/mp4', 'buffer': clip.read_bytes()})
                for selector in ('#studio-face-image', '#studio-clothing-image'):
                    page.locator(selector).set_input_files({'name': 'reference.png', 'mimeType': 'image/png', 'buffer': image.getvalue()})
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert not library.photos_for_person(library.people()[0]['id'])
                page.locator('#studio-generate-submit').click()
                expect(page.locator('[data-run-id]')).to_have_count(1)
                first = store.list_runs()[0]
                assert first['snapshot']['person_input_policy'] == 'auto_virtual'
                assert first['snapshot']['person_id'] is None
                assert first['person_preparation']['state'] == 'pending'
                page.locator('[data-person-media=video]').click()
                page.locator('#person-video-file').set_input_files({'name': 'person.mp4', 'mimeType': 'video/mp4', 'buffer': clip.read_bytes()})
                expect(page.locator('#person-video-preview')).to_be_visible()
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                page.locator('#studio-generate-submit').click()
                expect(page.locator('[data-run-id]')).to_have_count(2)
                second = store.list_runs()[0]
                assert second['snapshot']['person_reference_mode'] == 'video'
                assert second['snapshot']['person_input_policy'] == 'auto_virtual'
                assert second['snapshot']['person_video_asset_id']
                assert store.get_run(first['id'])['snapshot']['person_reference_mode'] == 'image'
                page.reload()
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert page.evaluate('productionPortraits.inputPolicy') == 'auto_virtual'
                assert page.evaluate('productionPortraits.currentPhoto.kind') == 'person_video'
                assert not errors, errors
                assert not failed_requests, failed_requests
                browser.close()
                print('PASS real API auto virtual: default model, sole real isolation, local saves, image/video committed runs and reload')


if __name__ == '__main__':
    check(1440)
    check(390, sole_real=True)
    check(1440, sole_real=True, legacy=True)
    check(1440, sole_real=True, legacy=True, bound=True)
    integration()
