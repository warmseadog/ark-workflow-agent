"""Admin summaries expose the same tenant-local wall-clock record as task details."""
from datetime import datetime, timedelta, timezone

from app import main, production_store
from app.production_store import ProductionStore
from app.tenancy import user_settings
from tests.test_access_control import protected, accounts_clients


def test_admin_overview_uses_persisted_timing_and_not_video_duration(accounts_clients, monkeypatch):
    _, users, (admin, alice, bob) = accounts_clients
    elapsed = [0]
    monkeypatch.setattr(production_store, 'now', lambda: (
        datetime(2026, 9, 29, tzinfo=timezone.utc) + timedelta(seconds=elapsed[0])).isoformat())
    records = []
    for user in users[1:]:
        store = ProductionStore(user_settings(main.settings, user).storage_dir)
        draft = store.create_draft({'model': {'duration': 8}})
        task = store.create_run(draft['id'], draft['revision'], 'same-per-user-key', {})
        records.append((store, task))
    first, task = records[0]
    elapsed[0] = 5
    first.claim_next()
    elapsed[0] = 20
    first.update_run(task['id'], status='succeeded')
    elapsed[0] = 80
    first.update_run(task['id'], message='Later metadata update')
    response = admin.get('/api/admin/tasks')
    assert response.status_code == 200
    data = response.json()
    tasks = {item['id']: item for item in data['items']}
    finished = tasks[task['id']]
    assert finished['timing']['queue_seconds'] == 5
    assert finished['timing']['execution_seconds'] == 15
    assert finished['timing']['total_seconds'] == 20
    assert finished['timing']['is_live'] is False
    assert finished['duration'] is None  # Model requested 8 s, actual duration is unknown.
    assert data['stats']['generated_seconds'] == 0 and data['stats']['duration_unknown'] == 1
    queued = tasks[records[1][1]['id']]
    assert queued['timing']['queue_seconds'] == queued['timing']['total_seconds'] == 80
    assert queued['timing']['execution_seconds'] == 0 and queued['timing']['is_live'] is True
    detail = admin.get('/api/admin/tasks/' + users[1]['id'] + '/' + task['id'])
    assert detail.status_code == 200
    assert detail.json()['task']['timing'] == finished['timing']
    assert alice.get('/api/admin/tasks').status_code == bob.get('/api/admin/tasks').status_code == 403


def test_old_task_timing_stays_unknown_in_admin_summary(accounts_clients):
    _, users, (admin, _, _) = accounts_clients
    store = ProductionStore(user_settings(main.settings, users[1]).storage_dir)
    draft = store.create_draft({})
    task = store.create_run(draft['id'], draft['revision'], 'historical', {})
    store.update_run(task['id'], status='succeeded')
    with store.connection() as db:
        db.execute('DELETE FROM production_run_timing WHERE run_id=?', (task['id'],))
    response = admin.get('/api/admin/tasks', params={'user_id': users[1]['id']})
    assert response.status_code == 200
    item = response.json()['items'][0]
    assert item['timing']['available'] is False
    assert item['timing']['total_seconds'] is None
