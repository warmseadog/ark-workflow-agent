from dataclasses import replace
import json
import sys

import pytest

from app import media, redaction_service
from app.config import settings
from app.media_errors import MediaPipelineError
from tests.test_person_video import video_bytes

HOST = 'https://mediakit.cn-beijing.volces.com'


class Reply:
    def __init__(self, data=None, content=b'', status=200):
        self.data, self.content, self.status_code = data, content, status
        self.headers = {'Content-Type': 'video/mp4'}
    def json(self): return self.data
    def iter_content(self, chunk_size): yield self.content
    def __enter__(self): return self
    def __exit__(self, *_): pass


@pytest.fixture
def pipeline(tmp_path, monkeypatch):
    from app import mediakit_redaction
    # Network contract tests exercise the real worker in-process; isolation is
    # covered separately with an actual blocked child process below.
    monkeypatch.setattr(mediakit_redaction, 'process_isolated', mediakit_redaction.process, raising=False)
    source, output = tmp_path/'source.mp4', tmp_path/'masked.mp4'
    content = video_bytes(tmp_path, seconds=1, size=(64, 64), fps=25)
    source.write_bytes(content)
    cfg = replace(settings, storage_dir=tmp_path, redaction_service=redaction_service.ServiceConfig(
        mode='http', endpoint=HOST, api_key='secret-key', timeout_seconds=10))
    calls = []
    pending = [Reply({'success': True, 'status': 'running'}),
               Reply({'success': True, 'status': 'completed', 'result': {'video_url': 'https://8.8.8.8/output.mp4?signature=private'}})]
    def post(url, **kw):
        calls.append(('POST', url, kw))
        if url.endswith('/request-media-upload-url'):
            return Reply({'success': True, 'result': {'file_id': 'mediakit://file-1', 'method': 'PUT',
                'upload_url': 'https://8.8.8.8/input?signature=private',
                'upload_headers': [{'key': 'X-Test-Upload', 'value': 'signed'}]}})
        if url.endswith('/face-blur-video'):
            return Reply({'success': True, 'task_id': 'task-1'})
        return Reply(status=404)
    def put(url, **kw):
        calls.append(('PUT', url, {**kw, 'data': kw['data'].read()}))
        return Reply(status=204)
    def get(url, **kw):
        calls.append(('GET', url, kw))
        return pending.pop(0) if '/api/v1/tasks/' in url else Reply(content=content)
    monkeypatch.setattr(redaction_service.requests, 'post', post)
    monkeypatch.setattr(redaction_service.requests, 'put', put)
    monkeypatch.setattr(redaction_service.requests, 'get', get)
    return source, output, cfg, calls, pending, content


@pytest.mark.parametrize('scale,expand', [(1.4, 0.4), (1.8, 0.8), (2.0, 1.0)])
def test_mediakit_upload_poll_download_and_map_parameters(pipeline, scale, expand):
    source, output, cfg, calls, _, content = pipeline
    result = redaction_service.process(source, output, cfg, media.BlurOptions(mask_scale=scale), cfg.redaction_service)
    assert result == output and output.read_bytes() == content
    assert [c[:2] for c in calls] == [
        ('POST', HOST+'/api/v1/tools-sync/request-media-upload-url'),
        ('PUT', 'https://8.8.8.8/input?signature=private'),
        ('POST', HOST+'/api/v1/tools/face-blur-video'),
        ('GET', HOST+'/api/v1/tasks/task-1'), ('GET', HOST+'/api/v1/tasks/task-1'),
        ('GET', 'https://8.8.8.8/output.mp4?signature=private')]
    body = calls[2][2]['json']
    assert body['video_url'] == 'mediakit://file-1'
    assert body['mask_mode'] == 'mosaic' and body['face_confidence'] == 0.2
    assert body['face_box_expand'] == pytest.approx(expand)
    assert body['mask_strength'] == 'medium'
    assert calls[1][2]['data'] == content
    assert calls[1][2]['headers']['X-Test-Upload'] == 'signed'
    assert 'Authorization' not in calls[1][2]['headers']
    assert 'Authorization' not in calls[-1][2].get('headers', {})
    assert all(c[2]['allow_redirects'] is False for c in calls)


@pytest.mark.parametrize('reply', [
    {'success': False, 'error': {'code': 'Unauthorized', 'message': 'secret-key'}},
    {'success': True, 'status': 'failed', 'error': {'message': 'secret-key'}},
    {'success': True, 'status': 'completed', 'result': {}},
    {'success': True, 'status': 'completed', 'result': {'video_url': 'https://127.0.0.1/private'}},
    None,
])
def test_mediakit_bad_result_does_not_publish_or_leak(pipeline, reply):
    source, output, cfg, _, pending, _ = pipeline
    pending[:] = [Reply(reply)]
    with pytest.raises(MediaPipelineError) as error:
        redaction_service.process(source, output, cfg, media.BlurOptions(), cfg.redaction_service)
    assert 'secret-key' not in str(error.value)
    assert not output.exists()
    assert not list(output.parent.glob('.mask-*.mp4'))


def test_external_failure_falls_back_once_with_original_options(pipeline, monkeypatch, caplog):
    source, output, cfg, _, pending, content = pipeline
    pending[:] = [Reply({'success': True, 'status': 'failed'})]
    options = media.BlurOptions()
    local_calls = []
    def local(src, dst, settings, opts):
        local_calls.append((src, opts))
        dst.write_bytes(content)
        return dst
    monkeypatch.setattr(media, '_run_local_deface', local, raising=False)
    assert media.run_deface(source, output, cfg, options) == output
    assert local_calls == [(source, options)]
    assert output.read_bytes() == content
    assert 'fallback' in caplog.text and 'secret-key' not in caplog.text


def test_both_fail_never_publish_partial_output(pipeline, monkeypatch):
    source, output, cfg, _, pending, _ = pipeline
    pending[:] = [Reply({'success': True, 'status': 'failed'})]
    def local(src, dst, settings, opts):
        dst.write_bytes(b'partial')
        raise MediaPipelineError('local failed')
    monkeypatch.setattr(media, '_run_local_deface', local, raising=False)
    with pytest.raises(MediaPipelineError, match='本地'):
        media.run_deface(source, output, cfg)
    assert not output.exists()
    assert not list(output.parent.glob('.mask-*.mp4'))


def test_mediakit_timeout_falls_back(pipeline, monkeypatch):
    source, output, cfg, _, _, content = pipeline
    def timeout(*a, **kw): raise redaction_service.requests.Timeout('secret-key')
    monkeypatch.setattr(redaction_service.requests, 'post', timeout)
    def local(src, dst, settings, opts): dst.write_bytes(content); return dst
    monkeypatch.setattr(media, '_run_local_deface', local, raising=False)
    assert media.run_deface(source, output, cfg) == output


def test_unsupported_hair_mode_uses_local_without_upload(pipeline, monkeypatch):
    source, output, cfg, calls, _, content = pipeline
    def local(src, dst, settings, opts):
        assert opts.mask_mode == 'face_hair_all'
        dst.write_bytes(content)
        return dst
    monkeypatch.setattr(media, '_run_local_deface', local, raising=False)
    assert media.run_deface(source, output, cfg, media.BlurOptions(mask_mode='face_hair_all')) == output
    assert not calls


@pytest.mark.parametrize('scale', [2.01, 3.0])
def test_expansion_beyond_cloud_limit_uses_original_local_scale_without_upload(pipeline, monkeypatch, scale):
    source, output, cfg, calls, _, content = pipeline
    local_options = []
    def local(src, dst, settings, opts):
        local_options.append(opts.mask_scale)
        dst.write_bytes(content)
        return dst
    monkeypatch.setattr(media, '_run_local_deface', local)
    assert media.run_deface(source, output, cfg, media.BlurOptions(mask_scale=scale)) == output
    assert local_options == [scale]
    assert not calls


def test_polling_deadline_stops_without_resubmission(pipeline, monkeypatch):
    from app import mediakit_redaction
    from types import SimpleNamespace
    source, output, cfg, calls, pending, _ = pipeline
    clock = [0]
    def sleep(seconds): clock[0] += seconds
    monkeypatch.setattr(mediakit_redaction, 'time', SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    pending[:] = [Reply({'success': True, 'status': 'running'}) for _ in range(10)]
    with pytest.raises(MediaPipelineError, match='超时'):
        redaction_service.process(source, output, cfg, media.BlurOptions(), cfg.redaction_service)
    assert clock[0] == 10
    assert len([c for c in calls if c[1].endswith('/face-blur-video')]) == 1
    assert not output.exists()


@pytest.mark.parametrize('bad', ['html', 'truncated', 'too_long', 'oversize'])
def test_bad_download_is_rejected(pipeline, monkeypatch, bad):
    source, output, cfg, _, pending, content = pipeline
    pending[:] = [pending[-1]]
    if bad == 'html': payload = b'<html>error</html>'
    elif bad == 'truncated': payload = content[:len(content)//2]
    elif bad == 'too_long': payload = video_bytes(output.parent, seconds=3, size=(64, 64), fps=25)
    else:
        cfg = replace(cfg, max_upload_mb=0)
        payload = content
    def get(url, **kw):
        return pending.pop(0) if '/api/v1/tasks/' in url else Reply(content=payload)
    monkeypatch.setattr(redaction_service.requests, 'get', get)
    with pytest.raises(MediaPipelineError):
        redaction_service.process(source, output, cfg, media.BlurOptions(), cfg.redaction_service)
    assert not output.exists()
    assert not list(output.parent.glob('.mask-*.mp4'))


@pytest.mark.skipif(sys.platform == 'win32' and not sys.executable.isascii(),
                    reason='Upstream OpenCV deface cannot read its ONNX model in a non-ASCII Windows environment; run on Linux deployment')
def test_generic_http_401_falls_back_to_real_local_processor(pipeline, monkeypatch):
    from pathlib import Path
    import sys
    source, output, cfg, _, _, _ = pipeline
    cfg = replace(cfg, deface_bin=str(Path(sys.executable).with_name('deface.exe' if sys.platform == 'win32' else 'deface')),
                  redaction_service=replace(cfg.redaction_service, endpoint='https://mask.example/process'))
    monkeypatch.setattr(redaction_service.requests, 'post', lambda *a, **kw: Reply(status=401))
    assert media.run_deface(source, output, cfg) == output
    redaction_service.validate_output(output, cfg, source)


def test_invalid_local_fallback_output_is_not_published(pipeline, monkeypatch):
    source, output, cfg, _, pending, _ = pipeline
    pending[:] = [Reply({'success': True, 'status': 'failed'})]
    def local(src, dst, settings, opts): dst.write_bytes(b'partial'); return dst
    monkeypatch.setattr(media, '_run_local_deface', local)
    with pytest.raises(MediaPipelineError, match='本地'):
        media.run_deface(source, output, cfg)
    assert not output.exists()


def test_cloud_deadline_terminates_blocked_child(tmp_path, monkeypatch):
    import subprocess
    import time
    from app import mediakit_redaction
    original = subprocess.Popen
    children = []
    def blocked(args, **kwargs):
        if args[0] != sys.executable:
            return original(args, **kwargs)
        child = original([sys.executable, '-c', 'import time; time.sleep(30)'], **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(subprocess, 'Popen', blocked)
    cfg = redaction_service.ServiceConfig(mode='http', endpoint=HOST, api_key='secret', timeout_seconds=1)
    started = time.monotonic()
    with pytest.raises(MediaPipelineError, match='超时'):
        mediakit_redaction.process_isolated(tmp_path/'input.mp4', tmp_path/'output.mp4', settings, media.BlurOptions(), cfg)
    assert time.monotonic()-started < 5
    assert children and children[0].poll() is not None
    assert not list(tmp_path.glob('.mask-cloud-*'))
    assert not (tmp_path/'output.mp4').exists()


def test_cloud_deadline_terminates_descendants(tmp_path, monkeypatch):
    import subprocess
    import time
    from app import mediakit_redaction
    original = subprocess.Popen
    marker = tmp_path/'surviving-child.txt'
    grandchild = 'import time,pathlib; time.sleep(2); pathlib.Path('+repr(str(marker))+').write_text("alive")'
    parent = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",'+repr(grandchild)+']); time.sleep(30)'
    def blocked(args, **kwargs):
        if args[0] == sys.executable:
            args = [sys.executable, '-c', parent]
        return original(args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', blocked)
    cfg = redaction_service.ServiceConfig(mode='http', endpoint=HOST, api_key='secret', timeout_seconds=1)
    with pytest.raises(MediaPipelineError, match='超时'):
        mediakit_redaction.process_isolated(tmp_path/'input.mp4', tmp_path/'output.mp4', settings, media.BlurOptions(), cfg)
    time.sleep(2.2)
    assert not marker.exists()
