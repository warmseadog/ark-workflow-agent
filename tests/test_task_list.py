from dataclasses import replace
from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from app import main,production_worker,jobs
from app.production_store import ProductionStore

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'settings',replace(main.settings,storage_dir=tmp_path,seedance_mode='mock'))
    monkeypatch.setattr(production_worker,'wake',lambda *_:None)
    monkeypatch.setattr(jobs,'store',jobs.JobStore())
    store=ProductionStore(tmp_path)
    draft=store.create_draft({'name':'原名称','prompt':'冻结参数','model':{'duration':8}})
    for index in range(23):store.create_run(draft['id'],1,str(index),{})
    return TestClient(main.app),store,draft

def test_server_pagination_is_compact_stable_and_clamps_last_page(setup):
    client,store,_=setup
    first=client.get('/api/production/runs?page=1&page_size=10').json()
    second=client.get('/api/production/runs?page=2&page_size=10').json()
    last=client.get('/api/production/runs?page=3&page_size=10').json()
    assert len(first['items'])==len(second['items'])==10 and len(last['items'])==3
    assert first['total']==23 and first['pages']==3
    assert len({r['id'] for p in [first,second,last] for r in p['items']})==23
    assert all('snapshot' not in r and 'private' not in r for r in first['items'])
    for row in last['items']:client.delete('/api/production/runs/'+row['id'])
    result=client.get('/api/production/runs?page=3&page_size=10').json()
    assert result['page']==2 and result['total']==20
    assert client.get('/api/production/runs?page=0').status_code==422
    assert client.get('/api/production/runs?page=1&page_size=1000').status_code==422

def test_name_is_persistent_and_never_changes_generation_snapshot(setup):
    client,store,draft=setup
    run=store.list_runs()[0];url='/api/production/runs/'+run['id']
    result=client.put(url+'/name',json={'name':'  秋季穿搭  '})
    assert result.status_code==200,result.text
    assert client.get(url).json()['name']=='秋季穿搭'
    assert ProductionStore(main.settings.storage_dir).get_run(run['id'])['name']=='秋季穿搭'
    assert store.get_run(run['id'])['snapshot']==run['snapshot']
    assert store.get_draft(draft['id'])['name']=='原名称'
    for invalid in ['', ' '*5, 'a'*121, 'a\nb', {'name':'bad'}]:
        assert client.put(url+'/name',json={'name':invalid}).status_code==422
    assert client.put(url+'/name',json={'name':'x'},headers={'Origin':'https://evil.example'}).status_code==403
    client.delete(url)
    assert client.put(url+'/name',json={'name':'x'}).status_code==404

def test_legacy_tasks_share_paging_and_name_persistence(setup):
    client,store,_=setup
    job=jobs.store.create();jobs.store.update(job.id,status='failed')
    ident='legacy-'+job.id
    first=client.get('/api/production/runs?page=1').json()
    assert first['total']==24 and first['items'][0]['id']==ident
    assert client.put('/api/production/runs/'+ident+'/name',json={'name':'旧作品'}).status_code==200
    assert client.get('/api/production/runs/'+ident).json()['name']=='旧作品'
    assert client.get('/api/production/runs?page=1').json()['items'][0]['name']=='旧作品'

def test_new_draft_has_useful_default_name(setup):
    client,_,_=setup
    name=client.post('/api/production/drafts',json={}).json()['name']
    parsed=datetime.strptime(name,'%Y-%m-%d %H:%M:%S')
    assert parsed.strftime('%Y-%m-%d %H:%M:%S')==name


@pytest.mark.parametrize('reference,expected', [
    ('req-public-123', 'req-public-123'),
    ('FAKE-SNAPSHOT-KEY', None),
    ('https://private.example/result?token=secret', None),
])
def test_task_list_and_detail_expose_only_safe_problem_references(setup, reference, expected):
    client, store, draft = setup
    run = store.create_run(draft['id'], 1, 'problem-reference', {'generation': {'api_key': 'FAKE-SNAPSHOT-KEY'}})
    store.update_run(run['id'], status='needs_attention', error_kind='submission_uncertain', request_id=reference)
    responses = [client.get('/api/production/runs?page=1'), client.get('/api/production/runs/'+run['id'])]
    for response in responses:
        assert response.status_code == 200
        payload = response.json()
        item = next(x for x in payload['items'] if x['id'] == run['id']) if 'items' in payload else payload
        assert 'request_id' in item
        assert item['request_id'] == expected
        assert 'FAKE-SNAPSHOT-KEY' not in response.text and 'private.example' not in response.text
        assert '_private' not in item and 'private' not in item
