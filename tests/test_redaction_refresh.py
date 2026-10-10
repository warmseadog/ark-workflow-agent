"""API copies, uploads and resumed preprocessing always get fresh masking."""
import pytest
from app import main, production_worker as worker
from app.production_store import ProductionStore
from app.video_provider import ProviderError
from tests.test_production_api import client, complete_draft, asset


def submit(client, draft, key):
    response = client.post('/api/production/runs', json={'draft_id':draft['id'], 'revision':draft['revision'], 'idempotency_key':key})
    assert response.status_code == 200, response.text
    store = ProductionStore(main.settings.storage_dir)
    worker.execute_run(main.settings, store, store.claim_next())
    return store.get_run(response.json()['id'])


@pytest.fixture
def seeded(client, monkeypatch):
    monkeypatch.setattr(worker, 'run_deface', lambda src,dst,*args: dst.write_bytes(b'old-mask'))
    draft = complete_draft(client)
    run = submit(client, draft, 'original')
    assert run['status'] == 'succeeded'
    calls = []
    def redact(src,dst,*args):
        calls.append(src); dst.write_bytes(b'new-mask')
    monkeypatch.setattr(worker, 'run_deface', redact)
    return draft, run, None, calls


@pytest.mark.parametrize('mode', ['unchanged', 'copy-run', 'copy-draft', 'reupload', 'reupload-new-draft'])
def test_all_new_runs_redact_again_and_preserve_historical_file(client, seeded, mode):
    draft, original, _, calls = seeded
    if mode == 'copy-run': draft = client.post('/api/production/runs/'+original['id']+'/copy').json()
    elif mode == 'copy-draft': draft = client.post('/api/production/drafts', json={'copy_from':draft['id']}).json()
    elif mode == 'reupload-new-draft': draft = complete_draft(client)
    elif mode == 'reupload':
        uploaded = asset(client,'video','source.mp4')
        draft = client.put('/api/production/drafts/'+draft['id'], json={'revision':draft['revision'], 'source_asset_id':uploaded['id']}).json()
    run = submit(client, draft, 'again')
    assert run['status'] == 'succeeded'
    assert len(calls) == 1
    assert client.get('/api/production/runs/'+run['id']+'/defaced').content == b'new-mask'
    assert client.get('/api/production/runs/'+original['id']+'/defaced').content == b'old-mask'
    assert not run['timing']['phases']['masking'].get('cached')
    assert not list((main.settings.storage_dir/'cache'/'redacted').glob('*'))


def test_polling_an_accepted_provider_task_does_not_resubmit(client, seeded, monkeypatch):
    draft, _, _, calls = seeded
    submissions = []
    def generate(self, video, faces, clothes, prompt, output, **kwargs):
        if not kwargs['resume_task_id']:
            submissions.append('remote-one'); kwargs['on_submitted']('remote-one')
            raise ProviderError('query timeout', error_kind='query_unavailable')
        assert kwargs['resume_task_id'] == 'remote-one'
        output.write_bytes(b'finished')
    monkeypatch.setattr(worker.VideoProvider, 'generate', generate)
    run = submit(client, draft, 'resume')
    assert run['status'] == 'needs_attention'
    store = ProductionStore(main.settings.storage_dir);store.resume_run(run['id'])
    worker.execute_run(main.settings, store, store.claim_next())
    assert store.get_run(run['id'])['status'] == 'succeeded'
    assert submissions == ['remote-one'] and len(calls) == 1


def test_preprocessing_resume_redacts_original_again(client, seeded, monkeypatch):
    draft, original, _, calls = seeded
    store = ProductionStore(main.settings.storage_dir)
    run = store.create_run(draft['id'],draft['revision'],'planning-retry',{
        **store.get_run(original['id'],private=True)['private'],
        'variation':{'group_key':'retry','inspiration':'test','skill_version':'test','config':{'model':'test'}}})
    attempts = []
    def prepare(settings,current_store,current_run,*args):
        attempts.append(1)
        if len(attempts)==1:
            current_store.update_run(current_run['id'],stage='variation_planning')
            raise ValueError('planning temporarily unavailable')
        return {}
    monkeypatch.setattr('app.variation.prepare',prepare)
    worker.execute_run(main.settings,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='needs_attention'
    store.resume_run(run['id']);worker.execute_run(main.settings,store,store.claim_next())
    assert store.get_run(run['id'])['status']=='succeeded'
    assert len(calls)==2
