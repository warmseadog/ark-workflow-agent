from dataclasses import asdict
import pytest
from app import continuation
from app.production_store import ProductionStore, Conflict


@pytest.mark.parametrize('source,target,expected', [(8,8.999,False),(8,9,True),(8,9.001,True),(7.7,8.7,True),(8,8,False),(8,7,False)])
def test_threshold_uses_unrounded_duration(source,target,expected):
    assert continuation.should_extend(source,target) is expected


@pytest.mark.parametrize('value', [True, '9', float('nan'), float('inf'), 0, 31])
def test_target_validation(value):
    with pytest.raises(ValueError): continuation.normalize_target(value)


def test_state_is_tenant_local_and_hides_signed_urls(tmp_path):
    store=ProductionStore(tmp_path/'one'); other=ProductionStore(tmp_path/'two')
    draft=store.create_draft({}); run=store.create_run(draft['id'],1,'x',{})
    store.update_continuation(run['id'], base_ready=True, provider_task_id='extension', result_url='https://signed/secret', plan={'continuation_prompt':'new'})
    assert store.get_run(run['id'])['continuation']['provider_task_id']=='extension'
    assert 'result_url' not in store.get_run(run['id'])['continuation']
    assert store.get_continuation(run['id'])['result_url']=='https://signed/secret'
    assert other.get_continuation(run['id'])=={}


def test_uncertain_extension_cannot_resume_despite_base_remote_id(tmp_path):
    store=ProductionStore(tmp_path); draft=store.create_draft({})
    run=store.create_run(draft['id'],1,'x',{})
    store.update_run(run['id'],status='running',stage='continuation_submitting',provider_task_id='base')
    store.update_continuation(run['id'],base_ready=True)
    store.recover()
    value=store.get_run(run['id'])
    assert value['status']=='needs_attention'
    assert value['error_kind']=='submission_uncertain'
    assert not value['can_resume']
    assert not store.page_runs(1,10,[])['items'][0]['can_resume']
    with pytest.raises(Conflict):store.resume_run(run['id'])


def test_extension_recovery_with_durable_id_is_safe(tmp_path):
    store=ProductionStore(tmp_path); draft=store.create_draft({})
    run=store.create_run(draft['id'],1,'x',{})
    store.update_run(run['id'],status='running',stage='continuation_submitting',provider_task_id='base')
    store.update_continuation(run['id'],base_ready=True,provider_task_id='extension')
    store.recover()
    assert store.get_run(run['id'])['status']=='queued'


def test_explicit_retry_of_terminal_failure_keeps_base_and_plan(tmp_path):
    store=ProductionStore(tmp_path); draft=store.create_draft({})
    run=store.create_run(draft['id'],1,'x',{})
    store.update_run(run['id'],status='needs_attention',stage='continuation_generating',provider_task_id='base')
    store.update_continuation(run['id'],base_ready=True,provider_task_id='failed-extension',terminal_failure=True,plan={'continuation_prompt':'继续向前走'})
    store.resume_run(run['id'])
    state=store.get_continuation(run['id'])
    assert state['provider_task_id'] is None
    assert state['previous_task_ids']==['failed-extension']
    assert state['base_ready'] and state['plan']['continuation_prompt']=='继续向前走'
    assert store.get_run(run['id'])['provider_task_id']=='base'
