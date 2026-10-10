import pytest
from app import main, production_worker
from app.production_store import ProductionStore
from tests.test_production_api import client, complete_draft


@pytest.mark.parametrize('origin',['draft','run'])
def test_copy_uses_latest_admin_mask_and_redacts_again(client,monkeypatch,origin):
    draft=complete_draft(client)
    original_mask=draft['mask']
    seen=[]
    def redact(source,output,settings,options):
        seen.append(options.model_dump())
        output.write_bytes(('fresh-mask-'+str(len(seen))).encode())
    monkeypatch.setattr(production_worker,'run_deface',redact)
    def submit(value,key):
        response=client.post('/api/production/runs',json={'draft_id':value['id'],'revision':value['revision'],'idempotency_key':key})
        assert response.status_code==200,response.text
        store=ProductionStore(main.settings.storage_dir)
        production_worker.execute_run(main.settings,store,store.claim_next())
        return store.get_run(response.json()['id'])
    old_run=submit(draft,'before-settings-change')
    old_output=client.get('/api/production/runs/'+old_run['id']+'/defaced').content
    values={'mask_mode':'face_hair_primary','blur_style':'mosaic','mask_scale':1.0,'mosaic_size':60,
            'robust_tracking':True,'local_options':{'hair_mosaic_size':48,'hair_update_hz':12.0}}
    saved=client.put('/api/redaction-settings',json={'profile':'local','values':values})
    assert saved.status_code==200,saved.text
    latest=saved.json()['config']
    response=(client.post('/api/production/drafts',json={'copy_from':draft['id']}) if origin=='draft'
              else client.post('/api/production/runs/'+old_run['id']+'/copy'))
    assert response.status_code==200,response.text
    copied=response.json()
    assert copied['mask']==latest
    for key in ('source_asset_id','face_asset_ids','clothing_asset_ids','prompt'):
        assert copied[key]==draft[key]
    fresh=submit(copied,'after-settings-change')
    assert fresh['status']=='succeeded'
    assert fresh['snapshot']['mask']==latest
    assert len(seen)==2 and seen[-1]['mask_mode']=='face_hair_primary'
    assert seen[-1]['local_options']['hair_mosaic_size']==48
    assert client.get('/api/production/runs/'+old_run['id']).json()['snapshot']['mask']==original_mask
    assert client.get('/api/production/drafts/'+draft['id']).json()['mask']==original_mask
    assert client.get('/api/production/runs/'+old_run['id']+'/defaced').content==old_output
    assert client.get('/api/production/runs/'+fresh['id']+'/defaced').content!=old_output
