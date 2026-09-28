"""Local UI + real API: replacing a library reference must never append to its person.

Official services are fixtures. All browser requests stay in TestClient.
"""
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from io import BytesIO
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PIL import Image
from fastapi.testclient import TestClient
from playwright.sync_api import sync_playwright, expect
from app import main, portrait_service, portrait_library, production_worker


def check(width):
    with TemporaryDirectory(prefix='upload-flow-', dir=ROOT/'storage') as tmp:
        settings = replace(main.settings, storage_dir=Path(tmp), seedance_mode='mock')
        portrait_service.save_config(settings, {'access_key': 'fixture', 'secret_key': 'fixture'})
        pictures = {}
        for aid, color in [('asset-library', '#b0a3b7'), ('local', '#c2b391')]:
            out = BytesIO(); Image.new('RGB', (400, 500), color).save(out, format='PNG')
            pictures[aid] = out.getvalue()

        def official(self, action, payload):
            if action == 'GetAssetGroup':
                return {'Id': 'group-library', 'GroupType': 'AIGC', 'ProjectName': 'default'}
            if action == 'GetAsset':
                return {'Id': payload['Id'], 'GroupId': 'group-library', 'AssetType': 'Image',
                        'ProjectName': 'default', 'Status': 'Active', 'Name': '库中照片',
                        'URL': 'https://ark-asset.cn-beijing.volcengine.com/library.png'}
            raise AssertionError(action)

        with patch.object(main, 'settings', settings), patch.object(production_worker, 'wake', lambda *_: None), \
             patch.object(portrait_service.ArkPortraitClient, '_request', official), \
             patch.object(portrait_service, 'download_image', lambda remote, path: path.write_bytes(pictures['asset-library'])):
            client = TestClient(main.app, base_url='http://127.0.0.1:18759')
            response = client.post('/api/portrait/import', json={'remote_asset_id': 'asset-library', 'person_type': 'AIGC'})
            assert response.status_code == 200, response.text
            lib = portrait_library.PortraitLibrary(settings)
            person = lib.people()[0]; lib.rename(person['id'], 'test')
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='msedge')
                page = browser.new_page(viewport={'width': width, 'height': 1000})
                errors, calls = [], []
                page.on('pageerror', lambda error: errors.append(str(error)))

                def route(r):
                    req = r.request; calls.append((req.method, req.url))
                    res = client.request(req.method, req.url, content=req.post_data_buffer,
                                         headers={k:v for k,v in req.headers.items() if k.lower() not in {'host','content-length'}})
                    r.fulfill(status=res.status_code, body=res.content,
                              headers={k:v for k,v in res.headers.items() if k.lower() not in {'content-length','content-encoding','transfer-encoding'}})
                page.route('**/*', route)
                page.goto('http://127.0.0.1:18759/')
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')

                def select_library():
                    page.locator('#person-picker > summary').click()
                    page.locator('[data-person-type=AIGC]').click()
                    page.locator('[data-person-id="'+person['id']+'"]').click()
                    page.locator('[data-photo-use]').click()
                    expect(page.locator('#person-photos-dialog')).not_to_be_visible()
                    expect(page.locator('#draft-save-status')).to_contain_text('已保存')

                select_library()
                page.reload(); expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert page.evaluate('portraitPeople.selected') == person['id']
                assert page.evaluate('productionPortraits.inputPolicy') == 'existing_person'
                # Merely looking at the other media mode is not a replacement.
                page.locator('[data-person-media=video]').click()
                page.locator('[data-person-media=image]').click()
                expect(page.locator('#face-reference-preview img')).to_have_count(1)
                assert page.evaluate('portraitPeople.selected') == person['id']
                assert page.evaluate('productionPortraits.currentPhoto.portrait.remote_asset_id') == 'asset-library'
                count = sum(method == 'POST' and url.endswith('/api/portrait/photos') for method, url in calls)
                with page.expect_file_chooser() as chooser:
                    page.locator('#face-reference-preview button', has_text='替换').click()
                chooser.value.set_files({'name':'新人物.png', 'mimeType':'image/png', 'buffer':pictures['local']})
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert page.evaluate('portraitPeople.selected') is None, 'Local replacement must detach the old library person'
                assert page.evaluate('productionPortraits.inputPolicy') == 'auto_virtual'
                assert count == sum(method == 'POST' and url.endswith('/api/portrait/photos') for method, url in calls), 'Replacement must not enqueue into test'
                expect(page.locator('#face-reference-preview img')).to_have_count(1)
                page.reload(); expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                assert page.evaluate('portraitPeople.selected') is None
                assert page.evaluate('productionPortraits.currentPhoto.name') == '新人物.png'

                # Browsing and backing out must not modify the editor.
                page.locator('#person-picker > summary').click()
                expect(page.locator('#person-photos-dialog')).to_be_visible()
                page.locator('[data-person-type=AIGC]').click()
                page.locator('[data-person-id="'+person['id']+'"]').click()
                expect(page.locator('[data-photo-upload]')).to_have_count(0)
                expect(page.locator('[data-photo-manage]')).to_have_attribute('href', '/admin/settings#people')
                page.locator('[data-photo-back]').click()
                expect(page.locator('#person-search')).to_be_visible()
                page.locator('[data-photo-close]').click()
                assert page.evaluate('productionPortraits.currentPhoto.name') == '新人物.png'

                # Uploading through the main input also replaces a library set.
                select_library()
                count = sum(method == 'POST' and url.endswith('/api/portrait/photos') for method, url in calls)
                page.locator('#studio-face-image').set_input_files({'name':'另一人物.png', 'mimeType':'image/png', 'buffer':pictures['local']})
                expect(page.locator('#draft-save-status')).to_contain_text('已保存')
                expect(page.locator('#face-reference-preview img')).to_have_count(1)
                assert page.evaluate('portraitPeople.selected') is None
                assert count == sum(method == 'POST' and url.endswith('/api/portrait/photos') for method, url in calls)
                page.locator('[data-person-media=video]').click()
                expect(page.locator('#person-video-file')).to_be_attached()
                page.locator('[data-person-media=image]').click()
                expect(page.locator('#face-reference-preview img')).to_have_count(1)
                expect(page.locator('#person-auto-virtual')).to_have_count(0)
                expect(page.locator('#person-current')).not_to_be_visible()
                assert not errors, errors
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                page.locator('#person-reference-card').screenshot(path=str(ROOT/'storage'/f'person-upload-{width}.png'))
                page.locator('#person-picker > summary').click()
                page.locator('#person-photos-dialog').screenshot(path=str(ROOT/'storage'/f'person-library-{width}.png'))
                browser.close()
                print(f'PASS upload-first {width}: library reuse, cancel, replace/add detachment, reload, compact picker')


if __name__ == '__main__':
    check(1440)
    check(390)
