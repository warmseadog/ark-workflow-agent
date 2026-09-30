from dataclasses import asdict, replace
import pytest
from app import main, generation_settings, model_catalog, continuation_settings
from app.production_store import ProductionStore
from tests.test_production_api import client, complete_draft


def extension_draft(client,monkeypatch,target=9,source=8):
    generation_settings.save_config(main.settings,{'model':model_catalog.SD25,'duration':8,'mode':'http','api_key':'test-video-key','public_base_url':'https://studio.example'})
    rows=model_catalog.catalog(main.settings)['items']
    for row in rows:
        if row['id']==model_catalog.SD25:row.update(enabled=True,verified=True)
    model_catalog.save_catalog(main.settings,{'items':rows})
    draft=complete_draft(client)
    monkeypatch.setattr('app.source_clip.validate_source',lambda *a,**kw:{'duration':source})
    result=client.put('/api/production/drafts/'+draft['id'],json={'revision':draft['revision'],'target_duration':target,
        'model':{**draft['model'],'model':model_catalog.SD25,'duration':-1}})
    assert result.status_code==200,result.text
    return result.json()


def submit(client,draft):
    return client.post('/api/production/runs',json={'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'extension'})


def test_missing_llm_key_rejected_before_preparing_people(client,monkeypatch):
    draft=extension_draft(client,monkeypatch)
    monkeypatch.setattr('app.portrait_generation.prepare',lambda *a:pytest.fail('paid prepare reached'))
    response=submit(client,draft)
    assert response.status_code==422,response.text
    assert '密钥' in response.text or 'Key' in response.text
    assert not ProductionStore(main.settings.storage_dir).list_runs()
    assert client.get('/api/production/drafts/'+draft['id']).json()['prompt']=='original'


@pytest.mark.parametrize('target,expected',[(8.999,False),(9,True),(9.001,True)])
def test_submission_freezes_only_triggered_config(client,monkeypatch,target,expected):
    draft=extension_draft(client,monkeypatch,target)
    continuation_settings.save_config(main.settings,{'api_key':'llm-secret','skill':'遵循末尾画面，自然续写，保持人物和服装一致。'})
    response=submit(client,draft)
    assert response.status_code==200,response.text
    private=ProductionStore(main.settings.storage_dir).get_run(response.json()['id'],private=True)['private']
    assert ('continuation' in private) is expected
    assert 'llm-secret' not in response.text
    assert response.json()['snapshot']['prompt']=='original'
    if expected:
        assert private['continuation']['config']['model']=='doubao-seed-2-1-lite-260915'
        continuation_settings.save_config(main.settings,{'api_key':'new-key'})
        frozen=ProductionStore(main.settings.storage_dir).get_run(response.json()['id'],private=True)
        assert frozen['private']['continuation']['config']['api_key']=='llm-secret'
