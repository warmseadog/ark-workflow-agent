"""Local UI check using synthetic PNG files; never submits a generation job."""
import struct
import zlib
from playwright.sync_api import sync_playwright, expect


def png(name, color):
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind + data) & 0xffffffff)
    raw = (b'\0' + bytes(color) * 48) * 64
    image = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', 48, 64, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')
    return {'name': name, 'mimeType': 'image/png', 'buffer': image}


def check(width, height):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='msedge')
        page = browser.new_page(viewport={'width': width, 'height': height})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:8000/v1')
        for kind in ['clothing', 'face']:
            picker = page.locator(f'#{kind}-picker')
            files = [png(f'{kind}-{i}.png', (170+i*10, 140, 110)) for i in range(6)]
            picker.locator('input[type=file]').set_input_files(files)
            expect(picker.locator('img')).to_have_count(6)
            page.wait_for_function('(kind)=>[...document.querySelectorAll(`#${kind}-picker img`)].every(img=>img.complete && img.naturalWidth>0 && img.getBoundingClientRect().width>30 && img.getBoundingClientRect().height>30)', arg=kind)
            with page.expect_file_chooser() as chooser:
                page.locator(f'[data-add-images={kind}]').click()
            chooser.value.set_files(png('added.png', (60, 110, 190)))
            expect(picker.locator('img')).to_have_count(7)
            picker.get_by_role('button', name='替换第 7 张图片', exact=True).scroll_into_view_if_needed()
            assert picker.locator('.asset-thumbnails').evaluate('(el)=>el.scrollTop') > 0
            with page.expect_file_chooser() as chooser:
                picker.get_by_role('button', name='替换第 2 张图片', exact=True).click()
            chooser.value.set_files(png('replacement.png', (160, 70, 95)))
            expect(picker.locator('img')).to_have_count(7)
            picker.get_by_role('button', name='删除第 1 张图片', exact=True).click()
            expect(picker.locator('img')).to_have_count(6)
            names = page.evaluate('(kind)=>new FormData(document.getElementById("studio-generate-form")).getAll(kind === "face" ? "face_image" : "clothing_image").map(file=>file.name)', kind)
            assert names == ['replacement.png'] + [f'{kind}-{i}.png' for i in range(2,6)] + ['added.png'], names
            assert picker.locator('.reference-thumb').first.get_by_text('主参考', exact=True).count() == 1
            page.wait_for_function('(kind)=>[...document.querySelectorAll(`#${kind}-picker img`)].every(img=>img.complete && img.naturalWidth>0)', arg=kind)
            picker.locator('.asset-thumbnails').evaluate('(el)=>el.scrollTop=0')
        page.evaluate('window.scrollTo(0,0)')
        page.screenshot(path=f'storage/asset-editor-{width}.png', full_page=True)
        page.locator('[name=video]').set_input_files({'name': 'video.mp4', 'mimeType': 'video/mp4', 'buffer': b'test'})
        expect(page.locator('#studio-generate-submit')).to_be_enabled()
        with page.expect_file_chooser() as chooser:
            page.get_by_role('button', name='更换视频', exact=True).click()
        chooser.value.set_files({'name': 'new-video.mp4', 'mimeType': 'video/mp4', 'buffer': b'test'})
        expect(page.locator('#source-file-name')).to_have_text('new-video.mp4')
        page.get_by_role('button', name='移除视频', exact=True).click()
        expect(page.locator('#studio-generate-submit')).to_be_disabled()
        expect(page.locator('#source-file-name')).to_have_text('建议 5–15 秒')
        for _ in range(5):
            page.locator('#face-picker').get_by_role('button', name='删除第 1 张图片', exact=True).click()
        page.wait_for_function('()=>{const img=document.querySelector("#face-picker img");return img.complete && img.naturalWidth>0 && img.getBoundingClientRect().width>100}')
        page.locator('#face-picker').get_by_role('button', name='删除第 1 张图片', exact=True).click()
        expect(page.locator('#face-picker .asset-empty')).to_be_visible()
        assert page.locator('#studio-face-image').evaluate('(el)=>el.files.length') == 0
        assert not errors, errors
        print(f'PASS {width}px: six decoded thumbnails, append, replace, remove, exact FormData, video replace/remove, empty recovery')
        browser.close()


if __name__ == '__main__':
    check(1440, 1000)
    check(390, 844)
