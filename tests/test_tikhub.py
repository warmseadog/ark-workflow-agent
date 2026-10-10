from dataclasses import replace
from pathlib import Path

import pytest
import requests

from app.config import settings
from app.media_errors import MediaPipelineError

def test_platform_hosts_and_multiple_links():
    from app.video_links import platform_for_url, extract_video_url
    assert platform_for_url('https://douyin.com.evil.test/video/1') is None
    assert platform_for_url('https://www.bilibili.com/video/BV1y7411Q7Eq') == 'bilibili'
    with pytest.raises(ValueError, match='多个'):
        extract_video_url('https://v.douyin.com/a https://b23.tv/b')
    assert extract_video_url('分享 https://www.xiaohongshu.com/explore/abc?xsec_token=a%2Bb%3D&xsec_source=pc。') == 'https://www.xiaohongshu.com/explore/abc?xsec_token=a%2Bb%3D&xsec_source=pc'

@pytest.mark.parametrize('platform,data,expected', [
    ('douyin', {'aweme_details': [{'video': {'play_addr': {'url_list': ['https://cdn.example/d.mp4']}}}]}, 'https://cdn.example/d.mp4'),
    ('xiaohongshu', {'data': [{'note_list': [{'video_info_v2': {'media': {'stream': {'h264': [{'master_url': 'https://cdn.example/x.mp4'}]}}}}]}]}, 'https://cdn.example/x.mp4'),
    ('kuaishou', {'visionVideoDetail': {'photo': {'photoUrl': 'https://cdn.example/k.mp4'}}}, 'https://cdn.example/k.mp4'),
])
def test_platform_video_parsers(platform, data, expected):
    from app.tikhub import parse_media
    assert parse_media(platform, data).videos == [expected]

def test_cover_and_music_are_not_treated_as_video():
    from app.tikhub import parse_media
    with pytest.raises(MediaPipelineError, match='视频'):
        parse_media('xiaohongshu', {'cover': {'url': 'https://cdn.example/cover.jpg'}, 'music': {'play_url': 'https://cdn.example/music.mp3'}})


def test_douyin_live_photo_prefers_embedded_h264_over_outer_slideshow_metadata():
    from app.tikhub import parse_media
    data = {'aweme_detail': {'aweme_type': 68, 'is_live_photo': 1,
        'video': {'duration': 31000, 'play_addr': {'url_list': ['https://cdn.example/outer-placeholder']}},
        'images': [{'video': {'duration': 2867,
            'play_addr_h264': {'url_list': ['https://cdn.example/live-h264.mp4']},
            'play_addr': {'url_list': ['https://cdn.example/live-hevc.mp4']}}}]}}
    plan = parse_media('douyin', data)
    assert plan.videos == ['https://cdn.example/live-h264.mp4']
    assert plan.live_photo
    assert plan.audio is None  # Preserve the actual clip's audio, not the 31-second music wrapper.


def test_douyin_multiple_live_photos_keep_order_and_ignore_still_image_urls():
    from app.tikhub import parse_media
    plan = parse_media('douyin', {'aweme_detail': {'images': [
        {'url_list': ['https://cdn.example/still.jpg']},
        {'video': {'play_addr': {'url_list': ['https://cdn.example/first.mp4']}}},
        {'video': {'play_addr_h264': {'url_list': ['https://cdn.example/second.mp4']}}},
    ]}})
    assert plan.videos == ['https://cdn.example/first.mp4', 'https://cdn.example/second.mp4']


def test_douyin_still_album_does_not_download_fake_outer_video():
    from app.tikhub import parse_media
    with pytest.raises(MediaPipelineError, match='静态图片'):
        parse_media('douyin', {'aweme_detail': {'aweme_type': 68,
            'video': {'play_addr': {'url_list': ['https://cdn.example/not-a-video']}},
            'images': [{'url_list': ['https://cdn.example/still.jpg']}]}})


def test_live_photo_download_normalizes_mixed_clips_and_preserves_audio(tmp_path, monkeypatch):
    import io
    import subprocess
    import imageio_ffmpeg
    import cv2
    import numpy as np
    from app import tikhub
    from app.person_video import probe
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    media = {}
    for name, size, color, audio in [('first', '64x48', 'red', True), ('second', '48x64', 'blue', False)]:
        path = tmp_path/(name+'.mp4')
        command = [ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={color}:s={size}:r=24:d=1.5']
        if audio:
            command += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=1.5', '-c:a', 'aac']
        subprocess.run(command+['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-t', '1.5', str(path)], check=True, capture_output=True)
        media['https://cdn.example/'+name] = path.read_bytes()
    monkeypatch.setattr(tikhub, 'get_tikhub_key', lambda cfg: 'test-key')
    monkeypatch.setattr(tikhub, 'api_get', lambda *a: {'aweme_detail': {'images': [
        {'video': {'play_addr': {'url_list': [url]}}} for url in media]}})
    def download(url, headers):
        assert 'Authorization' not in headers
        response = requests.Response(); response.status_code = 200
        response.headers['Content-Type'] = 'video/mp4'
        response.raw = io.BytesIO(media[url])
        return response, url
    monkeypatch.setattr(tikhub, 'public_get', download)
    output = tikhub.download_tikhub_video('https://v.douyin.com/live', tmp_path/'import.mp4', replace(settings, storage_dir=tmp_path))
    info = probe(output)
    assert info['duration'] == pytest.approx(3, abs=.15)
    assert (info['width'], info['height'], info['fps']) == (64, 48, 30)
    capture = cv2.VideoCapture(str(output))
    try:
        for second, channel in [(.5, 2), (2, 0)]:
            capture.set(cv2.CAP_PROP_POS_MSEC, second*1000)
            ok, frame = capture.read()
            assert ok and frame[24, 32, channel] > 220
    finally:
        capture.release()
    decoded = subprocess.run([ffmpeg, '-v', 'error', '-i', str(output), '-map', '0:a:0',
                              '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'], check=True, capture_output=True)
    samples = np.frombuffer(decoded.stdout, dtype=np.float32)
    assert np.max(np.abs(samples[4000:8000])) > .05
    assert np.max(np.abs(samples[16000:20000])) < .001
    assert not list(tmp_path.glob('tikhub-*'))


def test_live_photo_album_does_not_accumulate_audio_padding_or_drop_final_frames(tmp_path, monkeypatch):
    import io
    import subprocess
    import imageio_ffmpeg
    import cv2
    from app import tikhub
    from app.person_video import probe
    media = {}
    for color in ('blue', 'red'):
        path = tmp_path/(color+'.mp4')
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        f'color=c={color}:s=32x32:r=30', '-frames:v', '86', '-c:v', 'libx264', str(path)],
                       check=True, capture_output=True)
        media['https://cdn.example/'+color] = path.read_bytes()
    monkeypatch.setattr(tikhub, 'get_tikhub_key', lambda cfg: 'test-key')
    monkeypatch.setattr(tikhub, 'resolve_media', lambda *a: tikhub.MediaPlan(
        ['https://cdn.example/blue']*29+['https://cdn.example/red'], live_photo=True))
    def download(url, headers):
        response = requests.Response(); response.status_code = 200
        response.headers['Content-Type'] = 'video/mp4'; response.raw = io.BytesIO(media[url])
        return response, url
    monkeypatch.setattr(tikhub, 'public_get', download)
    output = tikhub.download_tikhub_video('https://v.douyin.com/album', tmp_path/'album.mp4', replace(settings, storage_dir=tmp_path))
    assert probe(output)['duration'] == pytest.approx(86, abs=.01)
    decoded = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-i', str(output),
                              '-map', '0:a:0', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'],
                             check=True, capture_output=True)
    assert len(decoded.stdout)/(8000*2) == pytest.approx(86, abs=.1)
    capture = cv2.VideoCapture(str(output))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, 83.3*1000)
        ok, frame = capture.read()
        assert ok and frame[16, 16, 2] > 220
        capture.set(cv2.CAP_PROP_POS_MSEC, 85.95*1000)
        ok, frame = capture.read()
        assert ok and frame[16, 16, 2] > 220
    finally:
        capture.release()

def test_bilibili_dash_keeps_audio_and_highest_video():
    from app.tikhub import parse_media
    plan = parse_media('bilibili', {'data': {'dash': {
        'video': [{'id': 32, 'baseUrl': 'https://cdn.example/low'}, {'id': 80, 'baseUrl': 'https://cdn.example/high'}],
        'audio': [{'bandwidth': 100, 'base_url': 'https://cdn.example/audio'}]}}})
    assert plan.videos == ['https://cdn.example/high']
    assert plan.audio == 'https://cdn.example/audio'

def test_bilibili_multiple_segments_are_preserved():
    from app.tikhub import parse_media
    plan = parse_media('bilibili', {'durl': [{'order': 2, 'url': 'https://cdn.example/2'}, {'order': 1, 'url': 'https://cdn.example/1'}]})
    assert plan.videos == ['https://cdn.example/1', 'https://cdn.example/2']

def test_bilibili_selected_page_and_official_params(monkeypatch):
    from app import tikhub
    calls = []
    def api(path, params, key):
        calls.append((path, params))
        if path.endswith('fetch_one_video'):
            return {'data': {'pages': [{'page': 1, 'cid': 111}, {'page': 2, 'cid': 222}]}}
        return {'data': {'durl': [{'url': 'https://cdn.example/video'}]}}
    monkeypatch.setattr(tikhub, 'api_get', api)
    plan = tikhub.resolve_media('https://www.bilibili.com/video/BV1y7411Q7Eq?p=2', 'test')
    assert plan.videos == ['https://cdn.example/video']
    assert calls[-1] == ('/api/v1/bilibili/web/fetch_video_playurl', {'bv_id': 'BV1y7411Q7Eq', 'cid': '222'})

@pytest.mark.parametrize('status', [401, 402, 429, 500])
def test_api_errors_do_not_expose_key(monkeypatch, status):
    from app import tikhub
    response = requests.Response()
    response.status_code = status
    response._content = b'{"message": "secret-test-key"}'
    monkeypatch.setattr(tikhub.requests, 'get', lambda *a, **kw: response)
    with pytest.raises(MediaPipelineError) as error:
        tikhub.api_get('/test', {}, 'secret-test-key')
    assert 'secret-test-key' not in str(error.value)

def test_supported_download_requires_key_without_ytdlp_fallback(tmp_path, monkeypatch):
    from app.media import download_video
    monkeypatch.delenv('TIKHUB_API_KEY', raising=False)
    with pytest.raises(MediaPipelineError, match='TikHub'):
        download_video('https://v.douyin.com/abc', tmp_path / 'source.mp4', replace(settings, storage_dir=tmp_path))

def test_private_media_destinations_are_rejected():
    from app.tikhub import validate_public_url
    for url in ['http://127.0.0.1/a', 'http://169.254.169.254/a', 'http://[::1]/a', 'file:///tmp/a']:
        with pytest.raises(MediaPipelineError):
            validate_public_url(url)


def test_short_bilibili_link_resolves_to_requested_page(monkeypatch):
    from app import tikhub
    class Page:
        def close(self): pass
    monkeypatch.setattr(tikhub, 'public_get', lambda *a, **kw: (Page(), 'https://www.bilibili.com/video/BV1y7411Q7Eq?p=2'))
    assert tikhub.bilibili_identity('https://b23.tv/share') == ('BV1y7411Q7Eq', 2)

def test_download_real_media_keeps_audio_and_never_sends_key_to_cdn(tmp_path, monkeypatch):
    import io
    import subprocess
    import imageio_ffmpeg
    from app import tikhub
    from app.local_preferences import save_tikhub_key
    config = replace(settings, storage_dir=tmp_path)
    save_tikhub_key(config, 'secret-fixture')
    sample = tmp_path / 'fixture.mp4'
    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run([ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=black:s=32x32:d=0.5',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.5', '-c:v', 'libx264', '-c:a', 'aac',
                    '-shortest', str(sample)], check=True, capture_output=True)
    media = sample.read_bytes()
    def get(url, **kwargs):
        response = requests.Response()
        response.status_code = 200
        if url.startswith('https://api.tikhub.io/'):
            assert kwargs['headers']['Authorization'] == 'Bearer secret-fixture'
            assert kwargs['params'] == {'share_url': 'https://v.douyin.com/a'}
            response._content = b'{"code":200,"data":{"aweme_details":[{"video":{"play_addr":{"url_list":["https://cdn.example/movie"]}}}]}}'
        else:
            assert 'Authorization' not in kwargs['headers']
            response.raw = io.BytesIO(media)
            response.headers['Content-Type'] = 'video/mp4'
        return response
    monkeypatch.setattr(tikhub.requests, 'get', get)
    monkeypatch.setattr(tikhub, 'validate_public_url', lambda url: None)
    output = tikhub.download_tikhub_video('https://v.douyin.com/a', tmp_path / 'out.mp4', config)
    result = subprocess.run([ffmpeg, '-i', str(output), '-f', 'null', '-'], capture_output=True)
    assert result.returncode == 0
    assert b'Audio: aac' in result.stderr
    assert b'Video: h264' in result.stderr
    assert not list(tmp_path.glob('tikhub-*'))

def test_cdn_redirect_cannot_target_localhost(monkeypatch):
    from app import tikhub
    monkeypatch.setattr(tikhub.socket, 'getaddrinfo', lambda host, *a, **kw: [(2, 1, 6, '', ('93.184.216.34' if host == 'cdn.example' else '127.0.0.1', 443))])
    response = requests.Response()
    response.status_code = 302
    response.headers['Location'] = 'http://127.0.0.1/secret'
    response._content = b''
    response._content_consumed = True
    monkeypatch.setattr(tikhub.requests, 'get', lambda *a, **kw: response)
    with pytest.raises(MediaPipelineError, match='本地网络'):
        tikhub.public_get('https://cdn.example/movie')
