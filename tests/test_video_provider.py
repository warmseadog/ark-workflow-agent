from dataclasses import replace
import json
import pytest
import requests
from app.generation_settings import GenerationConfig
from app.video_provider import VideoProvider, ProviderError


def response(payload, status=200):
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(payload).encode()
    return r


@pytest.fixture
def media(tmp_path):
    paths = []
    for name in ['defaced.mp4', 'face1.png', 'face2.png', 'clothing.png']:
        path = tmp_path / name
        path.write_bytes(name.encode())
        paths.append(path)
    return paths


@pytest.mark.parametrize('protocol', ['ark', 'toapis', 'adapter'])
def test_provider_sends_saved_parameters_all_images_and_downloads(protocol, media, tmp_path, monkeypatch):
    config = GenerationConfig(provider=protocol if protocol != 'adapter' else 'custom', protocol=protocol,
        mode='http', api_key='test-secret', base_url='https://provider.example/v1', model='my-model', duration=8,
        public_base_url='https://studio.example', fps=30 if protocol == 'adapter' else 0)
    calls = []
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith('/uploads/videos'):
            assert kwargs['files']['file'][1].read() == b'defaced.mp4'
            return response({'success': True, 'data': {'url': 'https://uploaded.example/redacted.mp4'}})
        if method == 'POST':
            if protocol == 'adapter':
                assert kwargs['data']['duration'] == 8
                assert kwargs['data']['fps'] == 30
                assert len(kwargs['files']) == 4
            else:
                body = kwargs['json']
                assert body['model'] == 'my-model' and body['duration'] == 8
                assert body['resolution'] == '720p' and 'fps' not in body
                refs = [i for i in body['content'] if i['type'] == 'image_url'] if protocol == 'ark' else body['image_with_roles']
                assert len(refs) == 3
                text = body['content'][0]['text'] if protocol == 'ark' else body['prompt']
                assert '@Image3' in text and '@Image1' in text and '@Image2' in text
            return response({'id': 'task-123'})
        if protocol == 'ark': return response({'status': 'succeeded', 'content': {'video_url': 'https://result.example/video.mp4'}})
        if protocol == 'toapis': return response({'status': 'completed', 'result': {'data': [{'url': 'https://result.example/video.mp4'}]}})
        return response({'status': 'completed', 'output_url': 'https://result.example/video.mp4'})
    monkeypatch.setattr('app.video_provider.requests.request', request)
    downloads = []
    monkeypatch.setattr(VideoProvider, '_download', lambda self, url, path: downloads.append(url) or path.write_bytes(b'final-video'))
    output = tmp_path / 'final.mp4'
    result = VideoProvider(config, poll_seconds=0).generate(media[0], media[1:3], [media[3]], 'keep motion', output, video_url='https://studio.example/redacted.mp4')
    assert result['provider'] == config.provider
    assert result['task_id'] == 'task-123'
    assert output.read_bytes() == b'final-video'
    assert downloads == ['https://result.example/video.mp4']
    assert all(c[2]['allow_redirects'] is False for c in calls)
    assert all(c[2]['headers']['Authorization'] == 'Bearer test-secret' for c in calls)


def test_provider_error_is_visible_but_redacts_key(media, tmp_path, monkeypatch):
    config = GenerationConfig(mode='http', api_key='test-secret', public_base_url='https://studio.example')
    monkeypatch.setattr('app.video_provider.requests.request', lambda *a, **kw: response({'error': {'code': 'AuthenticationError', 'message': 'bad test-secret'}}, 401))
    with pytest.raises(ProviderError) as caught:
        VideoProvider(config).generate(media[0], [media[1]], [media[3]], '', tmp_path/'out.mp4', video_url='https://studio.example/video')
    assert '401' in str(caught.value)
    assert 'test-secret' not in str(caught.value)


def test_ark_requires_reachable_reference_before_request(media, tmp_path, monkeypatch):
    monkeypatch.setattr('app.video_provider.requests.request', lambda *a, **kw: pytest.fail('must not submit'))
    config = GenerationConfig(mode='http', api_key='secret')
    with pytest.raises(ProviderError, match='公网'):
        VideoProvider(config).generate(media[0], [media[1]], [media[3]], '', tmp_path/'out.mp4')


def test_upstream_failed_task_never_returns_demo(media, tmp_path, monkeypatch):
    def request(method, *a, **kw):
        return response({'id': 'task-1'} if method == 'POST' else {'status': 'failed', 'error': {'message': 'model not enabled'}})
    monkeypatch.setattr('app.video_provider.requests.request', request)
    config = GenerationConfig(mode='http', api_key='secret', public_base_url='https://studio.example')
    with pytest.raises(ProviderError, match='model not enabled'):
        VideoProvider(config, poll_seconds=0).generate(media[0], [media[1]], [media[3]], '', tmp_path/'out.mp4', video_url='https://studio.example/video')
    assert not (tmp_path/'out.mp4').exists()


def test_temporary_poll_failure_retries_without_resubmitting(media, tmp_path, monkeypatch):
    methods = []
    responses = iter([response({'id':'task-retry'}), response({'message':'busy'},503), response({'status':'completed','result':{'data':[{'url':'https://result.example/final.mp4'}]}})])
    def request(method, *a, **kw):
        methods.append(method)
        if a[0].endswith('/uploads/videos'):
            return response({'data':{'url':'https://uploaded.example/video'}})
        return next(responses)
    monkeypatch.setattr('app.video_provider.requests.request', request)
    monkeypatch.setattr('app.video_provider.time.sleep', lambda _: None)
    monkeypatch.setattr(VideoProvider,'_download',lambda self,url,path:path.write_bytes(b'final'))
    c = GenerationConfig(provider='toapis',protocol='toapis',mode='http',api_key='key')
    VideoProvider(c,poll_seconds=0).generate(media[0], [media[1]], [media[3]], '', tmp_path/'final.mp4')
    assert methods == ['POST','POST','GET','GET']


def test_poll_failure_retains_remote_task_id(media, tmp_path, monkeypatch):
    monkeypatch.setattr('app.video_provider.requests.request', lambda method,*a,**kw: response({'id':'remote-123'}) if method=='POST' else response({'error':{'message':'unauthorized'}},401))
    c = GenerationConfig(mode='http',api_key='key',public_base_url='https://studio.example')
    with pytest.raises(ProviderError, match='remote-123'):
        VideoProvider(c).generate(media[0], [media[1]], [media[3]], '', tmp_path/'final.mp4',video_url='https://studio.example/video')


def test_authorized_portrait_uses_asset_uri_preserving_image_order(media, tmp_path, monkeypatch):
    calls=[]
    def request(method,url,**kwargs):
        if method=='POST':
            calls.append(kwargs['json'])
            return response({'id':'authorized-task'})
        return response({'status':'succeeded','content':{'video_url':'https://result.example/video.mp4'}})
    monkeypatch.setattr('app.video_provider.requests.request',request)
    monkeypatch.setattr(VideoProvider,'_download',lambda self,url,path:path.write_bytes(b'final'))
    config=GenerationConfig(mode='http',api_key='secret',public_base_url='https://studio.example')
    VideoProvider(config,0).generate(media[0],media[1:3],[media[3]],'test',tmp_path/'out.mp4',
        video_url='https://studio.example/defaced.mp4',image_asset_uris={str(media[1]):'asset://asset-person'})
    refs=[item['image_url']['url'] for item in calls[0]['content'] if item['type']=='image_url']
    assert refs[0]=='asset://asset-person'
    assert refs[1].startswith('data:image/') and refs[2].startswith('data:image/')


def test_authorized_portrait_cannot_be_forwarded_to_other_provider(media,tmp_path,monkeypatch):
    monkeypatch.setattr('app.video_provider.requests.request',lambda *a,**k:pytest.fail('must not submit'))
    config=GenerationConfig(protocol='toapis',mode='http',api_key='secret',base_url='https://provider.example/v1')
    with pytest.raises(ProviderError,match='官方'):
        VideoProvider(config).generate(media[0],[media[1]],[media[3]],'',tmp_path/'out.mp4',
            image_asset_uris={str(media[1]):'asset://asset-person'})
