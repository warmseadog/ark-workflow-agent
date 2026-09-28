from concurrent.futures import ThreadPoolExecutor
from app.production_store import ProductionStore, Conflict


def test_queue_limit_is_atomic_and_idempotent(tmp_path):
    store=ProductionStore(tmp_path)
    draft=store.create_draft({})
    def submit(key):
        try:
            return store.create_run(draft['id'],1,key,{},max_queued=2)['id']
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes=list(pool.map(submit,[str(i) for i in range(5)]))
    assert len([x for x in outcomes if x]) == 2
    accepted=next(i for i,x in enumerate(outcomes) if x)
    assert submit(str(accepted)) == outcomes[accepted]
    assert len(store.list_runs()) == 2


def test_resume_cannot_bypass_waiting_limit(tmp_path):
    store=ProductionStore(tmp_path)
    draft=store.create_draft({})
    first=store.create_run(draft['id'],1,'a',{})
    store.update_run(first['id'],status='needs_attention',provider_task_id='remote-existing')
    store.create_run(draft['id'],1,'b',{})
    try:
        store.resume_run(first['id'],max_queued=1)
    except Conflict:
        pass
    else:
        raise AssertionError('resume bypassed the account queue quota')
    assert store.get_run(first['id'])['status']=='needs_attention'
