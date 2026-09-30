from app import main, portrait_library
from tests.test_person_video import setup, upload, video_bytes


def test_add_real_video_via_library_api_is_deduplicated(setup, tmp_path):
    lib = portrait_library.PortraitLibrary(main.settings)
    person = lib.add_person('group-real-video', '已授权真人')
    asset = upload(setup, video_bytes(tmp_path)).json()
    first = setup.post('/api/portrait/photos', json={'person_id': person['id'], 'asset_id': asset['id']})
    assert first.status_code == 200, first.text
    assert first.json()['status'] == 'queued'
    second = setup.post('/api/portrait/photos', json={'person_id': person['id'], 'asset_id': asset['id']})
    assert second.status_code == 200 and second.json()['id'] == first.json()['id']
    items = setup.get('/api/portrait/people/' + person['id'] + '/photos').json()['items']
    assert len(items) == 1 and items[0]['kind'] == 'person_video'
    assert setup.post('/api/portrait/photos/' + first.json()['id'] + '/use').status_code == 422
    assert setup.post('/api/portrait/photos', json={'person_id': 'unknown-person', 'asset_id': asset['id']}).status_code == 404
