"""Persisted wall-clock timing; media duration is an unrelated field."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import main, production_store, production_worker, jobs
from app.production_store import ProductionStore, Conflict


class Clock:
    seconds = 0

    def now(self):
        return (datetime(2026, 9, 29, 15, 59, 50, tzinfo=timezone.utc)
                + timedelta(seconds=self.seconds)).isoformat()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    clock = Clock()
    monkeypatch.setattr(production_store, 'now', clock.now)
    monkeypatch.setenv('APP_AUTH_ENABLED', 'false')
    monkeypatch.setattr(main, 'settings', replace(main.settings, storage_dir=tmp_path,
                                                config_root=None, user_id='', seedance_mode='mock'))
    monkeypatch.setattr(production_worker, 'wake', lambda *_: None)
    monkeypatch.setattr(jobs, 'store', jobs.JobStore())
    store = ProductionStore(tmp_path)
    draft = store.create_draft({'model': {'duration': 8}})
    run = store.create_run(draft['id'], 1, 'once', {})
    return clock, store, draft, run, TestClient(main.app)


def test_beijing_default_and_copies_never_rename_originals(setup):
    clock, store, draft, run, client = setup
    assert draft['name'] == '2026-09-29 23:59:50'
    clock.seconds = 15
    custom = store.save_draft(draft['id'], 1, {'name': '用户自己的名称'})
    for payload in ({}, {'copy_from': draft['id']}):
        response = client.post('/api/production/drafts', json=payload)
        assert response.status_code == 200
        assert response.json()['name'] == '2026-09-30 00:00:05'
    assert client.post('/api/production/drafts', json={'name': '自定义'}).json()['name'] == '自定义'
    assert client.post('/api/production/runs/'+run['id']+'/copy').json()['name'] == '2026-09-30 00:00:05'
    legacy = jobs.store.create()
    assert client.post('/api/production/runs/legacy-'+legacy.id+'/copy').json()['name'] == '2026-09-30 00:00:05'
    assert store.get_draft(custom['id'])['name'] == '用户自己的名称'
    assert store.get_run(run['id'])['name'] == '2026-09-29 23:59:50'


def test_queue_claim_progress_and_success_freeze_real_timestamps(setup):
    clock, store, _, run, _ = setup
    clock.seconds = 10
    timing = store.get_run(run['id'])['timing']
    assert timing['queue_seconds'] == timing['total_seconds'] == 10
    assert timing['execution_seconds'] == 0 and timing['is_live'] is True
    assert timing['started_at'] is None and timing['finished_at'] is None
    claimed = store.claim_next()
    started = clock.now()
    assert claimed['timing']['started_at'] == started
    clock.seconds = 35
    store.update_run(run['id'], progress=50, message='仍在运行')
    timing = store.get_run(run['id'])['timing']
    assert (timing['queue_seconds'], timing['execution_seconds'], timing['total_seconds']) == (10, 25, 35)
    assert timing['finished_at'] is None
    clock.seconds = 50
    finished = clock.now()
    store.update_run(run['id'], status='succeeded')
    clock.seconds = 1000
    store.update_run(run['id'], status='succeeded', message='修改描述不是新结束时间')
    value = ProductionStore(store.storage).get_run(run['id'])
    timing = value['timing']
    assert (timing['queue_seconds'], timing['execution_seconds'], timing['total_seconds']) == (10, 40, 50)
    assert timing['started_at'] == started and timing['finished_at'] == finished
    assert timing['is_live'] is False and timing['available'] is True
    assert timing['interrupted'] is False and value['updated_at'] != finished


@pytest.mark.parametrize('status', ['succeeded', 'failed', 'cancelled', 'needs_attention'])
def test_stopped_states_freeze_and_do_not_treat_updated_at_as_finish(setup, status):
    clock, store, _, run, _ = setup
    clock.seconds = 12
    if status == 'cancelled':
        store.cancel_run(run['id'])
    else:
        store.claim_next()
        clock.seconds = 30
        store.update_run(run['id'], status=status)
    stopped = store.get_run(run['id'])['timing']
    clock.seconds = 600
    store.update_run(run['id'], message='后续消息')
    assert store.get_run(run['id'])['timing'] == stopped
    assert stopped['is_live'] is False
    assert (stopped['finished_at'] is None) == (status == 'needs_attention')
    assert stopped['execution_seconds'] == (0 if status == 'cancelled' else 18)
    assert stopped['total_seconds'] == (12 if status == 'cancelled' else 30)


def test_requeue_and_resume_accumulate_segments_and_separate_attention_wait(setup):
    clock, store, _, run, _ = setup
    clock.seconds = 10
    store.claim_next()
    first_start = clock.now()
    clock.seconds = 20
    store.update_run(run['id'], status='queued')
    clock.seconds = 40
    store.claim_next()
    clock.seconds = 50
    store.update_run(run['id'], status='needs_attention', provider_task_id='remote')
    clock.seconds = 100
    assert store.get_run(run['id'])['timing']['total_seconds'] == 50
    store.resume_run(run['id'])
    clock.seconds = 120
    store.claim_next()
    clock.seconds = 150
    timing = store.update_run(run['id'], status='succeeded')['timing']
    assert timing['started_at'] == first_start
    assert timing['queue_seconds'] == 50
    assert timing['execution_seconds'] == 50
    assert timing['paused_seconds'] == 50
    assert timing['total_seconds'] == 150
    assert timing['finished_at'] == clock.now()


def test_preparation_retry_reopens_finish_without_resetting_prior_work(setup):
    clock, store, draft, _, _ = setup
    private = {'person_preparation': {'account': 'local', 'input_digest': 'digest'}}
    run = store.create_run(draft['id'], 1, 'prep', private)
    clock.seconds = 10
    store.update_run(run['id'], status='running')
    clock.seconds = 20
    store.update_preparation(run['id'], retryable=True)
    store.update_run(run['id'], status='failed')
    clock.seconds = 70
    timing = store.retry_preparation(run['id'])['timing']
    assert timing['finished_at'] is None and timing['is_live'] is True
    assert timing['paused_seconds'] == 50 and timing['execution_seconds'] == 10
    clock.seconds = 80
    store.update_run(run['id'], status='running')
    clock.seconds = 90
    timing = store.update_run(run['id'], status='succeeded')['timing']
    assert (timing['queue_seconds'], timing['execution_seconds'], timing['total_seconds']) == (20, 20, 90)


@pytest.mark.parametrize('stage,remote,status', [('generating', 'remote', 'queued'),
                                               ('submitting', None, 'needs_attention')])
def test_restart_does_not_count_unknown_downtime_as_execution(setup, stage, remote, status):
    clock, store, _, run, _ = setup
    clock.seconds = 10
    store.claim_next()
    store.update_run(run['id'], stage=stage, provider_task_id=remote)
    clock.seconds = 1000
    restarted = ProductionStore(store.storage)
    restarted.recover()
    value = restarted.get_run(run['id'])
    assert value['status'] == status
    assert value['timing']['interrupted'] is True
    assert value['timing']['execution_seconds'] is None
    assert value['timing']['queue_seconds'] == 10
    assert value['timing']['finished_at'] is None
    if status == 'queued':
        clock.seconds = 1010
        restarted.claim_next()
        clock.seconds = 1030
        value = restarted.update_run(run['id'], status='succeeded')
        assert value['timing']['execution_seconds'] is None
        assert value['timing']['queue_seconds'] == 20
        assert value['timing']['total_seconds'] == 1030


def test_old_rows_and_legacy_never_get_guessed_timings(setup):
    clock, store, _, run, client = setup
    store.update_run(run['id'], status='succeeded')
    with store.connection() as db:
        db.execute('DROP TABLE IF EXISTS production_run_timing')
    store = ProductionStore(store.storage)
    clock.seconds = 100
    store.update_run(run['id'], message='旧记录')
    legacy = jobs.store.create()
    for ident in (run['id'], 'legacy-'+legacy.id):
        response = client.get('/api/production/runs/'+ident)
        assert response.status_code == 200
        timing = response.json()['timing']
        assert timing['available'] is False
        assert all(timing[key] is None for key in ('started_at', 'finished_at', 'queue_seconds',
                   'execution_seconds', 'total_seconds', 'paused_seconds'))


def test_paged_unpaged_and_detail_agree_without_changing_video_duration(setup):
    clock, store, _, run, client = setup
    clock.seconds = 5
    store.claim_next()
    clock.seconds = 30
    detail = client.get('/api/production/runs/'+run['id']).json()
    compact = client.get('/api/production/runs?page=1').json()['items'][0]
    full = client.get('/api/production/runs').json()['items'][0]
    assert compact['timing'] == full['timing'] == detail['timing']
    assert compact['duration'] == detail['snapshot']['model']['duration'] == 8
    assert compact['timing']['total_seconds'] == 30
    assert 'snapshot' not in compact and 'private' not in compact


def test_rejected_resume_and_idempotent_submission_do_not_reset_timing(setup):
    clock, store, draft, run, _ = setup
    clock.seconds = 10
    store.claim_next()
    clock.seconds = 20
    store.update_run(run['id'], status='needs_attention', provider_task_id='remote')
    before = store.get_run(run['id'])['timing']
    clock.seconds = 100
    with pytest.raises(Conflict):
        store.resume_run(run['id'], max_queued=0)
    assert store.create_run(draft['id'], 1, 'once', {})['timing'] == before
    assert store.get_run(run['id'])['timing'] == before


def test_deleting_a_queued_run_closes_its_timer(setup):
    clock, store, _, run, _ = setup
    clock.seconds = 15
    store.delete_run(run['id'])
    clock.seconds = 100
    timing = store.get_run(run['id'])['timing']
    assert timing['total_seconds'] == timing['queue_seconds'] == 15
    assert timing['finished_at'] is not None and timing['is_live'] is False


def test_batch_helper_keeps_missing_rows_unknown_and_old_insert_layout(setup):
    _, store, _, run, _ = setup
    values = store.run_timings([run['id'], 'legacy-missing', 'missing'])
    assert values[run['id']] == store.run_timing(run['id'])
    assert values['legacy-missing']['available'] is False
    assert values['missing']['total_seconds'] is None
    assert store.run_timings([]) == {}
    with store.connection() as db:
        # Old binaries may use positional INSERTs: the main table stays at 17 columns.
        original = list(db.execute('SELECT * FROM production_runs WHERE id=?', (run['id'],)).fetchone())
        assert len(original) == 17
        original[0], original[3] = 'old-writer', 'old-writer-key'
        db.execute('INSERT INTO production_runs VALUES ('+','.join('?' for _ in original)+')', original)
    assert store.get_run('old-writer')['timing']['available'] is False


@pytest.mark.parametrize('stage,remote,expected', [('queued', None, 'failed'),
    ('submitting', None, 'needs_attention'), ('generating', 'remote', 'needs_attention')])
def test_worker_unexpected_exception_stops_timer_and_releases_slot(setup, monkeypatch, stage, remote, expected):
    clock, store, _, run, _ = setup
    store.update_run(run['id'], stage=stage, provider_task_id=remote)
    manager = production_worker.QueueManager(main.settings)
    clock.seconds = 10

    def broken(settings, store, value):
        clock.seconds = 15
        manager.stop.set()
        raise OSError('fixture: exception outside normal provider handling')

    monkeypatch.setattr(production_worker, 'execute_run', broken)
    manager.loop()
    clock.seconds = 100
    value = store.get_run(run['id'])
    assert value['status'] == expected
    assert value['timing']['execution_seconds'] == 5
    assert value['timing']['total_seconds'] == 15
    assert manager._active == {}


def test_browser_preserves_historical_name_and_polls_open_timing_details(setup):
    from playwright.sync_api import sync_playwright, expect
    clock, store, draft, run, client = setup
    store.save_draft(draft['id'], 1, {'name': '未命名视频'})
    clock.seconds = 10
    store.claim_next()
    clock.seconds = 35
    origin = 'http://127.0.0.1:18765'
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel='msedge', headless=True)
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def route(route):
                request = route.request
                response = client.request(request.method, request.url, content=request.post_data_buffer,
                    headers={key: value for key, value in request.headers.items()
                             if key.lower() not in {'host', 'content-length'}})
                route.fulfill(status=response.status_code, body=response.content,
                    headers={key: value for key, value in response.headers.items()
                             if key.lower() not in {'content-length', 'content-encoding', 'transfer-encoding'}})

            page.route('**/*', route)
            page.goto(origin+'/')
            expect(page.locator('#draft-save-status')).to_contain_text('已保存')
            expect(page.locator('#draft-task-name')).to_have_text('未命名视频')
            page.locator('.production-tasks-shortcut').click()
            expect(page.locator('.production-runs')).to_be_visible()
            row = page.locator('[data-run-id="'+run['id']+'"]')
            expect(row.locator('.run-wall-time')).to_have_text('已耗时 35 秒')
            expect(row.locator('.run-duration')).to_contain_text('视频 8 秒')
            row.locator('.run-menu summary').click()
            row.locator('[data-run-action=details]').click()
            details = row.locator('.run-detail-panel')
            expect(details.locator('.run-timing-details')).to_contain_text('排队 10 秒')
            expect(details.locator('.run-timing-details')).to_contain_text('执行 25 秒')
            clock.seconds = 65
            # The real five-second list poll must also update an already-open panel.
            expect(row.locator('.run-wall-time')).to_have_text('已耗时 1 分 5 秒', timeout=9000)
            expect(details.locator('.run-timing-details')).to_contain_text('执行 55 秒')
            clock.seconds = 90
            store.update_run(run['id'], status='succeeded')
            expect(row.locator('.run-wall-time')).to_have_text('总耗时 1 分 30 秒', timeout=9000)
            clock.seconds = 900
            page.locator('#runs-refresh').click()
            expect(details.locator('.run-timing-details')).to_contain_text('总耗时 1 分 30 秒')
            for width in (390, 320):
                page.set_viewport_size({'width': width, 'height': 1000})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
            assert store.get_draft(draft['id'])['name'] == '未命名视频'
            assert not errors, errors
        finally:
            browser.close()
