from dataclasses import replace
import io
import pytest
from fastapi.testclient import TestClient
from app import main


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'settings',replace(main.settings,storage_dir=tmp_path,seedance_mode='mock'))
    from app import production_worker
    monkeypatch.setattr(production_worker,'wake',lambda settings:None)
    return TestClient(main.app)


def asset(client,kind,name):
    r=client.post('/api/production/assets',data={'kind':kind},files={'file':(name,b'fixture','video/mp4' if kind=='video' else 'image/png')})
    assert r.status_code==200,r.text
    return r.json()


def complete_draft(client):
    video=asset(client,'video','source.mp4'); face=asset(client,'face','face.png'); clothing=asset(client,'clothing','dress.png')
    draft=client.post('/api/production/drafts',json={}).json()
    r=client.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],
        'source_asset_id':video['id'],'face_asset_ids':[face['id']],'clothing_asset_ids':[clothing['id']],'prompt':'original'})
    assert r.status_code==200,r.text
    return r.json()


def test_assets_and_draft_are_restorable_and_not_secret(client):
    draft=complete_draft(client)
    loaded=client.get('/api/production/drafts/'+draft['id']).json()
    assert loaded['prompt']=='original' and len(loaded['assets'])==3
    assert client.get(loaded['assets'][0]['url']).content==b'fixture'
    assert 'api_key' not in loaded['model']
    assert client.put('/api/production/drafts/'+draft['id'],json={'revision':1,'prompt':'lost'}).status_code==409


def test_runs_capture_snapshot_deduplicate_and_cancel_queue(client):
    draft=complete_draft(client)
    body={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'one-click'}
    first=client.post('/api/production/runs',json=body)
    assert first.status_code==200,first.text
    run=first.json()
    assert client.post('/api/production/runs',json=body).json()['id']==run['id']
    client.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'prompt':'second'})
    assert client.get('/api/production/runs/'+run['id']).json()['snapshot']['prompt']=='original'
    assert client.post('/api/production/runs/'+run['id']+'/cancel').json()['status']=='cancelled'
    copied=client.post('/api/production/runs/'+run['id']+'/copy').json()
    assert copied['prompt']=='original' and copied['id']!=draft['id']


def test_missing_asset_and_private_fields_rejected(client):
    draft=client.post('/api/production/drafts',json={}).json()
    assert client.put('/api/production/drafts/'+draft['id'],json={'revision':1,'source_asset_id':'missing'}).status_code==422
    assert client.put('/api/production/drafts/'+draft['id'],json={'revision':1,'model':{'api_key':'secret'}}).status_code==422
    assert client.post('/api/production/runs',json={'draft_id':draft['id'],'revision':1,'idempotency_key':'empty'}).status_code==422


def test_cross_origin_assets_and_drafts_rejected(client):
    assert client.post('/api/production/drafts',json={},headers={'Origin':'https://evil.example'}).status_code==403


def test_partial_model_edits_keep_saved_snapshot_and_invalid_ids_are_validation_errors(client):
    draft=client.post('/api/production/drafts',json={}).json()
    response=client.put('/api/production/drafts/'+draft['id'],json={'revision':1,'model':{'duration':9}})
    assert response.status_code==200,response.text
    assert response.json()['model']['model']==draft['model']['model']
    assert response.json()['model']['duration']==9
    assert client.put('/api/production/drafts/'+draft['id'],json={'revision':2,'source_asset_id':{'bad':1}}).status_code==422
    assert client.post('/api/production/drafts',json={'copy_from':['bad']}).status_code==422
    assert client.post('/api/production/runs',json={'draft_id':{},'revision':1,'idempotency_key':'bad'}).status_code==422


def test_delete_task_persists_without_deleting_draft_or_duplicate_submission(client):
    from app.production_store import ProductionStore
    draft = complete_draft(client)
    body = {'draft_id': draft['id'], 'revision': draft['revision'], 'idempotency_key': 'delete-once'}
    run = client.post('/api/production/runs', json=body).json()
    assert client.delete('/api/production/runs/'+run['id']).status_code == 200
    assert run['id'] not in [r['id'] for r in client.get('/api/production/runs').json()['items']]
    assert client.get('/api/production/runs/'+run['id']).status_code == 404
    assert client.get('/api/production/drafts/'+draft['id']).status_code == 200
    assert client.get(draft['assets'][0]['url']).status_code == 200
    assert client.post('/api/production/runs', json=body).status_code == 409
    assert ProductionStore(main.settings.storage_dir).get_run(run['id'])['status'] == 'cancelled'
    assert client.post('/api/production/runs/'+run['id']+'/copy').status_code == 404
    assert client.delete('/api/production/runs/'+run['id']).status_code == 200


def test_running_task_delete_rejected_and_terminal_task_details_remain_available(client):
    from app.production_store import ProductionStore
    draft = complete_draft(client)
    run = client.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'running-delete'}).json()
    store = ProductionStore(main.settings.storage_dir)
    store.claim_next()
    assert client.delete('/api/production/runs/'+run['id']).status_code == 409
    assert client.get('/api/production/runs/'+run['id']).json()['can_delete'] is False
    store.update_run(run['id'],status='failed',error='fixture error')
    detail = client.get('/api/production/runs/'+run['id']).json()
    assert detail['snapshot']['prompt'] == 'original'
    assert detail['error'] == 'fixture error' and detail['can_delete'] is True
    assert client.delete('/api/production/runs/'+run['id'],headers={'Origin':'https://evil.example'}).status_code == 403
    assert client.delete('/api/production/runs/'+run['id']).status_code == 200


def test_legacy_task_details_and_delete_survive_reload(client, monkeypatch):
    from app import jobs
    legacy = jobs.JobStore()
    job = legacy.create()
    legacy.update(job.id,status='failed',error='legacy failure')
    monkeypatch.setattr(jobs,'store',legacy)
    ident = 'legacy-'+job.id
    assert client.get('/api/production/runs/'+ident).json()['error'] == 'legacy failure'
    assert client.delete('/api/production/runs/'+ident).status_code == 200
    assert client.get('/api/production/runs').json()['items'] == []
    assert client.get('/api/production/runs/'+ident).status_code == 404
    assert client.post('/api/production/runs/'+ident+'/copy').status_code == 404
    assert client.delete('/api/production/runs/missing').status_code == 404


def test_optional_references_snapshot_restore_and_copy(client):
    draft=complete_draft(client)
    hair=asset(client,'hairstyle','hair.png'); scene=asset(client,'scene','room.png')
    values={'hairstyle_asset_ids':[hair['id']], 'scene_asset_ids':[scene['id']],
            'hairstyle_enabled':True, 'scene_enabled':True, 'scene_description':'暖色室内'}
    response=client.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],**values})
    assert response.status_code==200,response.text
    saved=response.json()
    run=client.post('/api/production/runs',json={'draft_id':saved['id'],'revision':saved['revision'],'idempotency_key':'extras'}).json()
    client.put('/api/production/drafts/'+saved['id'],json={'revision':saved['revision'],'scene_enabled':False,'hairstyle_asset_ids':[]})
    frozen=client.get('/api/production/runs/'+run['id']).json()['snapshot']
    assert all(frozen[k]==v for k,v in values.items())
    assert {a['kind'] for a in frozen['assets']}=={'video','face','clothing','scene','hairstyle'}
    copied=client.post('/api/production/runs/'+run['id']+'/copy').json()
    assert all(copied[k]==v for k,v in values.items())
    for bad in ({'scene_enabled':'true'},{'hairstyle_asset_ids':[scene['id']]},{'scene_description':3}):
        assert client.put('/api/production/drafts/'+copied['id'],json={'revision':copied['revision'],**bad}).status_code==422


def test_optional_references_count_only_when_enabled(client):
    draft=complete_draft(client)
    faces=[draft['face_asset_ids'][0]]+[asset(client,'face',f'f{i}.png')['id'] for i in range(7)]
    scene=asset(client,'scene','scene.png')
    saved=client.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'face_asset_ids':faces,
        'scene_asset_ids':[scene['id']],'scene_enabled':True}).json()
    result=client.post('/api/production/runs',json={'draft_id':saved['id'],'revision':saved['revision'],'idempotency_key':'too-many'})
    assert result.status_code==422
    saved=client.put('/api/production/drafts/'+saved['id'],json={'revision':saved['revision'],'scene_enabled':False}).json()
    assert client.post('/api/production/runs',json={'draft_id':saved['id'],'revision':saved['revision'],'idempotency_key':'within-limit'}).status_code==200


def test_generated_prompt_block_does_not_consume_user_prompt_budget(client):
    draft=complete_draft(client)
    user='a'*10000
    block='\n\n【素材联动】\n@Image3 发型参考：参考发型。\n场景补充：'+('场景'*1000)+'\n【联动结束】'
    result=client.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'prompt':user+block})
    assert result.status_code==200,result.text
    assert result.json()['prompt']==user+block
    assert client.put('/api/production/drafts/'+draft['id'],json={'revision':result.json()['revision'],'prompt':'a'*10001}).status_code==422


def test_hairstyle_mask_is_separate_validated_and_frozen(client):
    draft=complete_draft(client)
    assert draft['hairstyle_mask']=={'mask_scale':1.0,'threshold':0.2}
    r=client.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'hairstyle_mask':{'mask_scale':1.05,'threshold':0.15}})
    assert r.status_code==200
    saved=r.json()
    run=client.post('/api/production/runs',json={'draft_id':saved['id'],'revision':saved['revision'],'idempotency_key':'hair-mask-config'}).json()
    assert run['snapshot']['hairstyle_mask']=={'mask_scale':1.05,'threshold':0.15}
    assert run['snapshot']['mask']['mask_scale']==1.4
    for bad in [{'mask_scale':0}, {'mask_scale':3}, {'threshold':0}, {'mask_mode':'face_hair_all'}, {'mask_scale':True}]:
        assert client.put('/api/production/drafts/'+saved['id'],json={'revision':saved['revision'],'hairstyle_mask':bad}).status_code==422
