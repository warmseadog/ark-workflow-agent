from dataclasses import replace
import pytest
from app import media
from app.config import settings
from app.tenancy import user_settings
from app.media_errors import MediaPipelineError
from tests.test_access_control import protected,accounts_clients


@pytest.mark.parametrize('url',['http://127.0.0.1/private.mp4','http://169.254.169.254/latest/meta-data/',
    'http://[::1]/video.mp4','https://internal.example/video.mp4','https://douyin.com.evil.example/video/123'])
def test_tenant_generic_import_is_rejected_before_any_downloader(tmp_path,monkeypatch,url):
    tenant=user_settings(replace(settings,storage_dir=tmp_path),{'id':'a'*32,'legacy_owner':False})
    def forbidden(*args,**kwargs):raise AssertionError('Untrusted URL reached external downloader')
    monkeypatch.setattr(media.subprocess,'run',forbidden)
    with pytest.raises(MediaPipelineError):
        media.download_video(url,tenant.storage_dir/'imports'/'test.mp4',tenant)


def test_supported_social_url_keeps_server_parser_path(tmp_path,monkeypatch):
    from app import tikhub
    tenant=user_settings(replace(settings,storage_dir=tmp_path),{'id':'b'*32,'legacy_owner':False})
    seen=[]
    def parser(url,destination,effective):
        seen.append((url,effective.user_id));return destination
    monkeypatch.setattr(tikhub,'download_tikhub_video',parser)
    expected=tenant.storage_dir/'reference.mp4'
    assert media.download_video('https://v.douyin.com/abcd/',expected,tenant)==expected
    assert seen==[('https://v.douyin.com/abcd/','b'*32)]


def test_both_authenticated_import_apis_block_private_urls(accounts_clients,monkeypatch):
    _,_,(_,alice,_)=accounts_clients
    def forbidden(*args,**kwargs):raise AssertionError('Unsafe URL reached yt-dlp')
    monkeypatch.setattr(media.subprocess,'run',forbidden)
    for endpoint in ('/api/video-link/import','/api/production/assets/import','/api/video-link/inspect'):
        result=alice.post(endpoint,json={'text':'http://127.0.0.1:18080/healthz'})
        assert result.status_code==422,(endpoint,result.text)
