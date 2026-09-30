"""Every visible ordinary-user navigation destination must remain usable."""
from tests.test_access_control import protected, accounts_clients


def test_ordinary_user_can_open_own_video_library_without_admin_access(accounts_clients):
    _, _, (_, alice, _) = accounts_clients
    page = alice.get('/videos')
    assert page.status_code == 200
    assert 'id="video-library-grid"' in page.text
    assert alice.get('/api/production/videos').status_code == 200
    assert alice.get('/admin/users').status_code == 403
    assert alice.get('/admin/settings').status_code == 403
    assert alice.post('/videos').status_code == 403


def test_video_library_remains_private(protected):
    response = protected.get('/videos', follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/login'
    assert protected.get('/api/production/videos').status_code == 401
