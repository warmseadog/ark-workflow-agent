"""Run the real photo UI against offline provider replies."""
import json
from playwright.sync_api import expect

from tests.test_account_frontend import browser
from tests.test_real_person_video_ui import library_page, open_real


def test_library_stopped_photo_is_visible_and_can_continue_original_record(library_page):
    # Catches hiding an expired query behind the empty-library state.
    page, state = library_page
    state['status'] = 'stopped'
    page.clock.install()
    open_real(page)
    expect(page.locator('[data-person-id=real-1]')).to_contain_text('自动查询已停止')
    page.locator('[data-person-id=real-1]').click()
    expect(page.locator('[data-detail-photos]')).to_contain_text('自动查询已停止')
    with page.expect_request('**/api/portrait/photos/job-video/retry') as request:
        page.get_by_role('button', name='继续检查原记录', exact=True).click()
    assert request.value.method == 'POST'
    assert not state['errors']


def test_picker_stops_polling_until_explicit_continue(browser):
    # Catches retry controls missing from stopped photos, or timers polling forever.
    page = browser.new_page()
    try:
        page.clock.install()
        page.set_content('<div id="person-picker"></div><input id="person-search">')
        page.evaluate('''() => {
            window.photoCalls = [];
            window.photoState = 'stopped';
            window.portraitPeople = {selected:'person-1',refresh:async()=>{},request:async(path,method)=>{
                window.photoCalls.push({path,method:method || 'GET'});
                if (path === 'photos/photo-1/retry') { window.photoState = 'processing'; return {}; }
                return {items:[{id:'photo-1',asset_id:'a',name:'photo.png',kind:'face',
                    status:window.photoState,message:'自动查询已停止',can_manage:true}]};
            }};
        }''')
        from pathlib import Path
        page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / 'app/static/portrait-photos.js'))
        page.evaluate("portraitPhotos.open({id:'person-1',name:'测试人物'})")
        expect(page.locator('[data-photo-grid]')).to_contain_text('自动查询已停止')
        initial = page.evaluate('photoCalls.length')
        page.clock.fast_forward(60000)
        assert page.evaluate('photoCalls.length') == initial
        page.get_by_role('button', name='继续检查原记录', exact=True).click()
        expect(page.locator('[data-photo-grid]')).to_contain_text('入库检查中')
        assert page.evaluate("photoCalls.filter(c=>c.method==='POST').map(c=>c.path)") == ['photos/photo-1/retry']
        after_retry = page.evaluate('photoCalls.length')
        page.clock.fast_forward(6000)
        expect(page.locator('[data-photo-grid]')).to_contain_text('入库检查中')
        assert page.evaluate('photoCalls.length') > after_retry
    finally:
        page.close()


def test_library_upload_notifies_when_automatic_query_window_stops(library_page):
    page, state = library_page
    page.clock.install()
    open_real(page)
    page.locator('[data-person-id=real-1] [data-video-upload]').set_input_files(
        {'name': '模特.mp4', 'mimeType': 'video/mp4', 'buffer': b'fixture'})
    expect(page.locator('.virtual-library-dialog > [data-status]')).to_contain_text('视频已添加')
    state['status'] = 'stopped'
    page.route('**/api/portrait/photos?*', lambda route: route.fulfill(
        content_type='application/json', body=json.dumps({'items': [{
            'id': 'job-video', 'person_id': 'real-1', 'asset_id': 'video-1',
            'status': 'stopped', 'message': '自动查询已停止，可继续检查原记录',
            'retryable': True, 'query_window': {'attempts': 60, 'stop_reason': 'attempt_limit'}}]})))
    page.clock.fast_forward(6000)
    expect(page.locator('.virtual-library-dialog > [data-status]')).to_contain_text('自动查询已停止')
