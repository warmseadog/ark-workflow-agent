import pytest
from app.production_store import ProductionStore, Conflict


def test_draft_survives_new_store_and_rejects_stale_revision(tmp_path):
    store=ProductionStore(tmp_path)
    draft=store.create_draft({'prompt':'one'})
    saved=store.save_draft(draft['id'],draft['revision'],{'prompt':'two'})
    assert ProductionStore(tmp_path).get_draft(draft['id'])['prompt']=='two'
    with pytest.raises(Conflict): store.save_draft(draft['id'],draft['revision'],{'prompt':'stale'})
    assert saved['revision']==draft['revision']+1


def test_snapshot_is_immutable_and_duplicate_submission_does_not_create_run(tmp_path):
    store=ProductionStore(tmp_path)
    draft=store.create_draft({'prompt':'original','face_asset_ids':['face-a']})
    first=store.create_run(draft['id'],draft['revision'],'same-key',{'generation':{}})
    again=store.create_run(draft['id'],draft['revision'],'same-key',{'generation':{}})
    assert first['id']==again['id']
    store.save_draft(draft['id'],draft['revision'],{'prompt':'changed','face_asset_ids':['face-b']})
    assert store.get_run(first['id'])['snapshot']['prompt']=='original'
    assert store.get_run(first['id'])['snapshot']['face_asset_ids']==['face-a']
    assert len(store.list_runs())==1
    with pytest.raises(Conflict): store.create_run(draft['id'],2,'same-key',{})


def test_claim_and_cancel_are_atomic(tmp_path):
    store=ProductionStore(tmp_path)
    draft=store.create_draft({})
    run=store.create_run(draft['id'],1,'key',{})
    assert store.claim_next()['id']==run['id']
    assert store.claim_next() is None
    with pytest.raises(Conflict): store.cancel_run(run['id'])


def test_recovery_never_resubmits_uncertain_submission(tmp_path):
    store=ProductionStore(tmp_path)
    draft=store.create_draft({})
    ambiguous=store.create_run(draft['id'],1,'a',{})
    resumable=store.create_run(draft['id'],1,'b',{})
    local=store.create_run(draft['id'],1,'c',{})
    store.update_run(ambiguous['id'],status='running',stage='submitting')
    store.update_run(resumable['id'],status='running',stage='generating',provider_task_id='cloud-1')
    store.update_run(local['id'],status='running',stage='preprocess')
    store.recover()
    assert store.get_run(ambiguous['id'])['status']=='needs_attention'
    assert store.get_run(resumable['id'])['status']=='queued'
    assert store.get_run(resumable['id'])['provider_task_id']=='cloud-1'
    assert store.get_run(local['id'])['status']=='queued'


def test_private_configuration_never_appears_in_public_run(tmp_path):
    store=ProductionStore(tmp_path)
    draft=store.create_draft({})
    run=store.create_run(draft['id'],1,'a',{'generation':{'api_key':'private-secret'}})
    assert 'private-secret' not in str(run)
    assert 'private-secret' not in str(store.list_runs())
    assert store.get_run(run['id'],private=True)['private']['generation']['api_key']=='private-secret'


def test_save_returns_own_committed_revision_when_another_tab_saves(tmp_path, monkeypatch):
    store = ProductionStore(tmp_path)
    other = ProductionStore(tmp_path)
    draft = store.create_draft({'prompt': 'initial'})
    original_get = store.get_draft
    def interleaved_read(ident):
        other.save_draft(ident, 2, {'prompt': 'tab B'})
        return original_get(ident)
    monkeypatch.setattr(store, 'get_draft', interleaved_read)
    saved = store.save_draft(draft['id'], 1, {'prompt': 'tab A'})
    assert saved['prompt'] == 'tab A'
    assert saved['revision'] == 2


def test_public_recovery_capabilities_do_not_expose_result_url(tmp_path):
    store = ProductionStore(tmp_path)
    draft = store.create_draft({})
    run = store.create_run(draft['id'], 1, 'capability', {})
    assert run['can_cancel'] is True
    assert run['can_resume'] is False
    store.update_run(run['id'], status='needs_attention', result_url='https://cdn.example/result?secret=token')
    public = store.get_run(run['id'])
    assert public['can_resume'] is True
    assert public['can_cancel'] is False
    assert 'secret=token' not in str(public)
