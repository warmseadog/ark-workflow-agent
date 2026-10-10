import json

import pytest
import requests

from app.generation_settings import GenerationConfig
from app.video_provider import ProviderError, VideoProvider
from tests.test_production_worker import setup


def provider():
    client = VideoProvider(GenerationConfig(mode='http', api_key='private-key'), poll_seconds=0)
    client._last_request_id = 'request-123'
    client._content_roles = {1: '第 1 张人物参考图', 2: '动作参考视频「dance.mp4」',
                             3: '人物参考视频「person.mp4」'}
    return client


def test_output_audio_copyright_explains_generated_audio_without_blame():
    error = provider()._provider_error('模型任务失败：', detail={
        'code': 'OutputAudioSensitiveContentDetected.PolicyViolation',
        'message': 'The generated audio may contain copyrighted content.'})
    assert '生成声音' in str(error) and '版权' in str(error)
    assert '关闭生成声音' in str(error)
    assert 'dance.mp4' not in str(error) and 'person.mp4' not in str(error)
    assert error.error_kind == 'audio_copyright'
    assert error.request_id == 'request-123'
    assert not error.retryable and not error.submission_uncertain


@pytest.mark.parametrize('location', ['content[2]', 'content.2.video_url'])
def test_explicit_content_rejection_locates_only_identified_video(location):
    error = provider()._provider_error('模型接口错误：', detail={
        'code': 'InputVideoSensitiveContentDetected', 'param': location,
        'message': 'The input video violates the content policy.'})
    assert '动作参考视频「dance.mp4」' in str(error)
    assert '内容审核' in str(error)
    assert 'person.mp4' not in str(error)
    assert error.error_kind == 'material_rejected'


def test_unlocated_content_rejection_does_not_invent_offending_video():
    error = provider()._provider_error('', detail={
        'code': 'InputVideoSensitiveContentDetected', 'message': 'Content rejected.'})
    assert '未指出具体素材' in str(error)
    assert 'dance.mp4' not in str(error) and 'person.mp4' not in str(error)


def test_validation_explains_duration_and_sanitizes_provider_details():
    error = provider()._provider_error('', error_kind='configuration', detail={
        'code': 'InvalidParameter', 'param': 'content[3].video_url',
        'message': 'Video duration exceeds limit; https://private.example/a?token=x private-key'})
    assert '人物参考视频「person.mp4」' in str(error)
    assert '时长' in str(error) and '参数校验' in str(error)
    assert 'private-key' not in str(error) and 'private.example' not in str(error)
    assert error.error_kind == 'configuration' and error.request_id == 'request-123'


def test_unknown_transport_error_keeps_uncertainty_and_retry_flags():
    error = provider()._provider_error('提交结果不确定：', detail='upstream unavailable',
        error_kind='submission_uncertain', retryable=True, submission_uncertain=True)
    assert error.error_kind == 'submission_uncertain'
    assert error.retryable and error.submission_uncertain


def test_resumed_failure_keeps_task_id_and_localized_filename(tmp_path, monkeypatch):
    response = requests.Response()
    response.status_code = 200
    response._content = json.dumps({'status': 'failed', 'error': {
        'code': 'InputVideoSensitiveContentDetected', 'message': 'content[2] rejected'}}).encode()
    response.headers['X-Request-Id'] = 'req-resume'
    monkeypatch.setattr('app.video_provider.requests.request', lambda *a, **kw: response)
    with pytest.raises(ProviderError) as caught:
        provider().generate(tmp_path/'missing.mp4', [], [], '', tmp_path/'out.mp4',
            resume_task_id='remote-1', reference_roles={2: '动作参考视频「dance.mp4」'})
    assert '动作参考视频「dance.mp4」' in str(caught.value)
    assert '内容审核' in str(caught.value)
    assert caught.value.provider_task_id == 'remote-1'
    assert caught.value.request_id == 'req-resume'
    assert caught.value.terminal_failure


@pytest.mark.parametrize('missing_asset', [False, True])
def test_worker_resumed_feedback_uses_original_video_name_without_requiring_asset(setup, monkeypatch, missing_asset):
    from app import production_worker as worker
    cfg, store, draft, private = setup
    run = store.create_run(draft['id'], 1, 'feedback', private)
    store.update_run(run['id'], provider_task_id='remote-2')
    if missing_asset:
        with store.connection() as db:
            db.execute("DELETE FROM production_assets WHERE id='source'")
    response = requests.Response()
    response.status_code = 200
    response.headers['X-Request-Id'] = 'req-worker'
    response._content = json.dumps({'status': 'failed', 'error': {
        'code': 'InputVideoSensitiveContentDetected', 'message': 'content[3] rejected'}}).encode()
    monkeypatch.setattr('app.video_provider.requests.request', lambda *a, **kw: response)
    worker.execute_run(cfg, store, store.claim_next())
    value = store.get_run(run['id'])
    assert value['status'] == 'failed' and value['error_kind'] == 'material_rejected'
    assert '动作参考视频' in value['error'] and '内容审核' in value['error']
    if not missing_asset:
        assert 'source.mp4' in value['error']
    assert value['request_id'] == 'req-worker' and value['provider_task_id'] == 'remote-2'


def test_new_submission_keeps_provided_reference_names(tmp_path, monkeypatch):
    video = tmp_path/'defaced.mp4'
    video.write_bytes(b'adapter-video')
    client = VideoProvider(GenerationConfig(protocol='adapter', mode='http', api_key='key',
        provider='custom', base_url='https://provider.example', model='custom'))
    response = requests.Response()
    response.status_code = 400
    response._content = json.dumps({'error': {'code': 'InputVideoSensitiveContentDetected',
                                             'message': 'content[1] rejected'}}).encode()
    monkeypatch.setattr('app.video_provider.requests.request', lambda *a, **kw: response)
    with pytest.raises(ProviderError) as caught:
        client.generate(video, [], [], 'test', tmp_path/'out.mp4',
            reference_roles={1: '动作参考视频「original.mp4」'})
    assert '动作参考视频「original.mp4」' in str(caught.value)
