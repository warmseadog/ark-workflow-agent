from tests.media_fixtures import image_bytes, video_bytes, media_bytes
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json

import pytest
import requests
from fastapi.testclient import TestClient
from app import main, generation_settings, production_worker
from app.production_store import ProductionStore
from app.video_provider import ProviderError, VideoProvider

CODE = 'OutputAudioSensitiveContentDetected.PolicyViolation'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path, seedance_mode='mock'))
    monkeypatch.setattr(production_worker, 'wake', lambda settings: None)
    return TestClient(main.app)


def failed_run(client, audio=True):
    ids = {}
    for kind, name in [('video','video.mp4'), ('face','face.png'), ('clothing','clothing.png')]:
        result = client.post('/api/production/assets', data={'kind':kind}, files={'file':(name,media_bytes(name))})
        assert result.status_code == 200, result.text
        ids[kind] = result.json()['id']
    draft = client.post('/api/production/drafts', json={}).json()
    changed = client.put('/api/production/drafts/'+draft['id'], json={
        'revision':draft['revision'], 'source_asset_id':ids['video'], 'face_asset_ids':[ids['face']],
        'clothing_asset_ids':[ids['clothing']], 'prompt':'keep the motion',
        'model':{**draft['model'], 'generate_audio':audio}})
    assert changed.status_code == 200, changed.text
    draft = changed.json()
    result = client.post('/api/production/runs', json={'draft_id':draft['id'], 'revision':draft['revision'], 'idempotency_key':'first'})
    assert result.status_code == 200, result.text
    store = ProductionStore(main.settings.storage_dir)
    return store.update_run(result.json()['id'], status='failed', stage='generating', error=CODE+' copyright',
        message='模型任务failed：'+CODE, provider_task_id='upstream-original')


def test_new_draft_silent_choice_saved_and_old_draft_defaults_preserved(client):
    draft = client.post('/api/production/drafts', json={}).json()
    assert draft['model']['generate_audio'] is False
    changed = client.put('/api/production/drafts/'+draft['id'], json={'revision':draft['revision'], 'model':{'generate_audio':True}})
    assert changed.status_code == 200, changed.text
    assert client.get('/api/production/drafts/'+draft['id']).json()['model']['generate_audio'] is True
    old = ProductionStore(main.settings.storage_dir).create_draft({'model':{'model':'doubao-seedance-2-0-260128','duration':5,'resolution':'720p'}})
    assert client.get('/api/production/drafts/'+old['id']).json()['model']['generate_audio'] is True
    copied = client.post('/api/production/drafts', json={'copy_from':old['id']})
    assert copied.status_code == 200, copied.text
    assert copied.json()['model']['generate_audio'] is True


@pytest.mark.parametrize('value', ['false', 0, None])
def test_audio_setting_requires_boolean(client, value):
    draft = client.post('/api/production/drafts', json={}).json()
    result = client.put('/api/production/drafts/'+draft['id'], json={'revision':draft['revision'],'model':{'generate_audio':value}})
    assert result.status_code == 422


def test_audio_retry_preserves_inputs_original_and_deduplicates(client):
    old = failed_run(client)
    summary = client.get('/api/production/runs?page=1').json()['items'][0]
    assert summary['can_retry_without_audio'] and '版权' in summary['message']
    url = '/api/production/runs/'+old['id']+'/retry-without-audio'
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(url,json={}), range(2)))
    # A duplicate still being validated is rejected promptly; retrying after
    # the first commit must return the same task, without another generation.
    assert any(r.status_code == 200 for r in responses), [r.text for r in responses]
    for i, response in enumerate(responses):
        if response.status_code == 429:
            assert response.headers['retry-after'] == '2'
            responses[i] = client.post(url,json={})
    assert all(r.status_code == 200 for r in responses), [r.text for r in responses]
    a, b = (r.json() for r in responses)
    assert a['run']['id'] == b['run']['id'] != old['id']
    assert a['draft']['model']['generate_audio'] is False
    assert a['run']['snapshot']['model']['generate_audio'] is False
    for field in ('source_asset_id','face_asset_ids','clothing_asset_ids','prompt','mask'):
        assert a['run']['snapshot'][field] == old['snapshot'][field]
    store = ProductionStore(main.settings.storage_dir)
    saved = store.get_run(a['run']['id'], private=True)
    assert saved['private']['generation']['generate_audio'] is False
    assert saved['provider_task_id'] is None
    assert store.get_run(old['id'])['snapshot']['model']['generate_audio'] is True
    assert len(store.list_runs()) == 2


def test_audio_retry_rejects_other_errors_silent_and_hidden_runs(client):
    old = failed_run(client)
    store = ProductionStore(main.settings.storage_dir)
    url = '/api/production/runs/'+old['id']+'/retry-without-audio'
    store.update_run(old['id'], error='OutputVideoSensitiveContentDetected', error_kind='processing_failed')
    assert client.post(url,json={}).status_code == 409
    store.update_run(old['id'], error=CODE)
    store.delete_run(old['id'])
    assert client.post(url,json={}).status_code == 404


def test_silent_audio_failure_does_not_offer_same_retry(client):
    old = failed_run(client, audio=False)
    assert not client.get('/api/production/runs/'+old['id']).json()['can_retry_without_audio']
    assert client.post('/api/production/runs/'+old['id']+'/retry-without-audio',json={}).status_code == 409


def test_legacy_error_is_eligible_and_copy_preserves_original_sound(client):
    old = failed_run(client)
    store = ProductionStore(main.settings.storage_dir)
    with store.connection() as db:
        db.execute("UPDATE production_runs SET snapshot=json_remove(snapshot,'$.model.generate_audio'), private=json_remove(private,'$.generation.generate_audio') WHERE id=?",(old['id'],))
    assert client.get('/api/production/runs?page=1').json()['items'][0]['can_retry_without_audio']
    copy = client.post('/api/production/runs/'+old['id']+'/copy').json()
    assert copy['model']['generate_audio'] is True
    retry = client.post('/api/production/runs/'+old['id']+'/retry-without-audio',json={})
    assert retry.status_code == 200, retry.text


def test_retry_respects_queue_limit_and_reuses_draft_after_rejection(client, monkeypatch):
    old = failed_run(client)
    store=ProductionStore(main.settings.storage_dir)
    original = store.get_run(old['id'])
    from app.production_store import Conflict
    check=ProductionStore.check_queue_limit
    monkeypatch.setattr(ProductionStore,'check_queue_limit',lambda *args: (_ for _ in ()).throw(Conflict('quota')))
    url='/api/production/runs/'+old['id']+'/retry-without-audio'
    assert client.post(url,json={}).status_code==409
    assert len(store.list_runs())==1
    drafts=len(store.list_drafts())
    monkeypatch.setattr(ProductionStore,'check_queue_limit',check)
    assert client.post(url,json={}).status_code==200
    assert len(store.list_drafts())==drafts and store.get_run(old['id'])['snapshot']==original['snapshot']


@pytest.mark.parametrize('audio', [False, True])
def test_provider_sends_explicit_audio_flag(tmp_path, monkeypatch, audio):
    files=[]
    for name in ('source.mp4','face.png','clothing.png'):
        path=tmp_path/name;path.write_bytes(b'fixture');files.append(path)
    config = generation_settings.GenerationConfig(mode='http', api_key='fixture', generate_audio=audio)
    calls=[]
    monkeypatch.setattr(VideoProvider, '_request', lambda self,*args,**kwargs: calls.append(kwargs['json']) or {'id':'task'})
    monkeypatch.setattr(VideoProvider, '_poll', lambda *args: {})
    VideoProvider(config).generate(files[0],[files[1]],[files[2]],'motion',tmp_path/'output.mp4',video_url='https://example.test/video')
    assert calls[0]['generate_audio'] is audio


def test_audio_error_has_specific_kind_and_keeps_original_details(monkeypatch):
    response=requests.Response();response.status_code=200
    response._content=json.dumps({'status':'failed','error':{'code':CODE,'message':'copyright restrictions'}}).encode()
    monkeypatch.setattr('app.video_provider.requests.request',lambda *a,**kw:response)
    with pytest.raises(ProviderError) as caught:
        VideoProvider(generation_settings.GenerationConfig())._poll('upstream-id',None,None)
    assert caught.value.error_kind == 'audio_copyright'
    assert CODE in str(caught.value) and 'upstream-id' in str(caught.value)
