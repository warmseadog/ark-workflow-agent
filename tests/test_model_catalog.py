from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from app import main, generation_settings

SD25 = 'doubao-seedance-2-5-260628'
SD20 = 'doubao-seedance-2-0-260128'

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path, seedance_mode='mock'))
    generation_settings.save_config(main.settings, {'mode':'http','api_key':'fixture-secret','duration':8})
    return TestClient(main.app)

def enable(client, model=SD25):
    data = client.get('/api/model-catalog').json()
    for row in data['items']:
        if row['id'] == model:
            row.update(enabled=True, verified=True)
    result = client.put('/api/model-catalog', json={'items':data['items']})
    assert result.status_code == 200, result.text

def test_frontend_options_hide_connections_and_pending_models(client):
    response = client.get('/api/production/model-options')
    assert response.status_code == 200
    data = response.json()
    assert set(data['defaults']) == {'model','duration','resolution','ratio','generate_audio'}
    assert SD20 in [x['id'] for x in data['items']]
    assert SD25 not in [x['id'] for x in data['items']]
    assert all(x not in response.text for x in ('fixture-secret','base_url','api_key','volces.com'))

def test_admin_enables_verified_model_without_changing_default(client):
    enable(client)
    data = client.get('/api/production/model-options').json()
    item = next(x for x in data['items'] if x['id'] == SD25)
    assert item['resolutions'] == ['480p','720p','1080p']
    assert item['max_images'] == 30 and item['max_video_seconds'] == 30
    assert data['defaults']['model'] == SD20

def test_switch_only_changes_current_draft_and_uses_edit_duration(client):
    enable(client)
    first = client.post('/api/production/drafts',json={}).json()
    second = client.post('/api/production/drafts',json={}).json()
    response = client.put('/api/production/drafts/'+first['id'],json={
        'revision':first['revision'],'model':{'model':SD25,'duration':8,'resolution':'1080p'}})
    assert response.status_code == 200, response.text
    assert response.json()['model'] == {'model':SD25,'duration':-1,'resolution':'1080p','ratio':'adaptive','generate_audio':False}
    assert client.get('/api/production/drafts/'+second['id']).json()['model']['model'] == SD20
    saved = generation_settings.load_config(main.settings)
    assert saved.model == SD20 and saved.duration == 8 and saved.api_key == 'fixture-secret'

def test_task_cannot_override_backend_destination(client):
    draft = client.post('/api/production/drafts',json={}).json()
    response = client.put('/api/production/drafts/'+draft['id'],json={
        'revision':draft['revision'],'model':{'base_url':'https://other.invalid/v1'}})
    assert response.status_code == 422

@pytest.mark.parametrize('model,resolution,duration',[(SD25,'4k',8),(SD25,'720p',31),('doubao-seedance-2-0-mini-260615','1080p',8),(SD20,'720p',7.7)])
def test_invalid_model_capabilities_rejected_by_server(client,model,resolution,duration):
    response = client.put('/api/model-settings',json={'model':model,'resolution':resolution,'duration':duration})
    assert response.status_code == 422, response.text

def test_new_model_accepts_30_second_admin_default(client):
    response=client.put('/api/model-settings',json={'model':SD25,'duration':30,'resolution':'1080p'})
    assert response.status_code == 200, response.text


def test_seedance20_auto_duration_is_preserved_in_draft(client):
    draft = client.post('/api/production/drafts',json={}).json()
    response = client.put('/api/production/drafts/'+draft['id'],json={
        'revision':draft['revision'],'model':{'model':SD20,'duration':-1}})
    assert response.status_code == 200, response.text
    assert response.json()['model']['duration'] == -1

def test_unverified_enablement_rejected(client):
    data=client.get('/api/model-catalog').json()
    next(x for x in data['items'] if x['id']==SD25)['enabled']=True
    assert client.put('/api/model-catalog',json={'items':data['items']}).status_code==422

def test_legacy_matching_connection_is_migrated_to_task_fields(client):
    draft=client.post('/api/production/drafts',json={}).json()
    legacy=generation_settings.load_config(main.settings).public()
    fields=('provider','protocol','mode','base_url','model','duration','fps','resolution','public_base_url')
    response=client.put('/api/production/drafts/'+draft['id'],json={
        'revision':draft['revision'],'model':{k:legacy[k] for k in fields}})
    assert response.status_code==200,response.text
    assert set(response.json()['model'])=={'model','duration','resolution','ratio','generate_audio'}

def test_changing_default_does_not_enable_unverified_model(client):
    response=client.put('/api/model-settings',json={'model':'doubao-seedance-2-0-fast-260128'})
    assert response.status_code==200
    data=client.get('/api/production/model-options').json()
    assert [x['id'] for x in data['items']]==[SD20]
    assert data['defaults']['model']==SD20

def test_changing_credentials_invalidates_model_acceptance(client):
    enable(client)
    assert client.put('/api/model-settings',json={'api_key':'different-fixture'}).status_code==200
    data=client.get('/api/production/model-options').json()
    assert data['items']==[]

@pytest.mark.parametrize('duration',[31,'invalid',None,False])
def test_invalid_task_duration_is_rejected_before_edit_normalization(client,duration):
    draft=client.post('/api/production/drafts',json={}).json()
    response=client.put('/api/production/drafts/'+draft['id'],json={
        'revision':draft['revision'],'model':{'model':SD25,'duration':duration}})
    assert response.status_code==422,response.text

def test_25_draft_accepts_21_face_references(client):
    ids=[]
    for i in range(21):
        asset=client.post('/api/production/assets',data={'kind':'face'},files={'file':(f'{i}.png',b'fixture','image/png')})
        assert asset.status_code==200
        ids.append(asset.json()['id'])
    draft=client.post('/api/production/drafts',json={}).json()
    response=client.put('/api/production/drafts/'+draft['id'],json={
        'revision':draft['revision'],'model':{'model':SD25},'face_asset_ids':ids})
    assert response.status_code==200,response.text
    assert len(response.json()['face_asset_ids'])==21
