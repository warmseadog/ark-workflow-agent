"""Offline behavior contracts for durable, bounded official photo queries."""
from dataclasses import replace
import hashlib
import sqlite3

import pytest
from PIL import Image

from app import main, portrait_library, portrait_service
from app.portrait_library import PortraitLibrary


@pytest.fixture
def photo_env(tmp_path, monkeypatch):
    settings = replace(main.settings, storage_dir=tmp_path)
    portrait_service.save_config(settings, {'access_key': 'test-ak', 'secret_key': 'test-sk'})
    clock = [10000.0]
    monkeypatch.setattr(portrait_library.time, 'time', lambda: clock[0])
    lib = PortraitLibrary(settings)
    person = lib.add_person('group-photo', '测试人物')
    path = tmp_path / 'assets' / 'photo.png'
    path.parent.mkdir(exist_ok=True)
    Image.new('RGB', (400, 400), 'red').save(path)
    asset = '1' * 32
    lib.store.add_asset(asset, 'photo.png', 'face', path, path.stat().st_size,
                        'image/png', hashlib.sha256(path.read_bytes()).hexdigest())
    photo = lib.enqueue(person['id'], asset)
    return lib, photo['id'], clock


def query_api(monkeypatch, *, result='Processing', fail=False, uncertain=False):
    calls = []

    class API:
        def __init__(self, *args, **kwargs): pass
        def get_group(self, group): return {'Id': group}
        def asset_state(self, remote, group):
            assert (remote, group) == ('asset-existing', 'group-photo')
            calls.append('query')
            if fail: raise portrait_service.PortraitError('查询暂时不可用')
            return {'status': result, 'error_code': ''}
        def find_created_asset(self, group, name):
            assert group == 'group-photo' and name.startswith('portrait-')
            calls.append('find')
            if fail: raise portrait_service.PortraitError('查询暂时不可用')
            return None if uncertain else 'asset-existing'
        def create_asset(self, *args):
            raise AssertionError('An accepted or uncertain photo must never be created again')

    monkeypatch.setattr(portrait_service, 'ArkPortraitClient', API)
    return calls


@pytest.mark.parametrize('state', ['processing', 'uncertain'])
def test_sixtieth_query_stops_and_restart_cannot_spend_a_sixty_first(photo_env, monkeypatch, state):
    # Catches unbounded polling, or a budget kept only in memory.
    lib, ident, clock = photo_env
    calls = query_api(monkeypatch, uncertain=True)
    lib.update(ident, status=state, remote_id='asset-existing' if state == 'processing' else None)
    for attempt in range(60):
        lib.update(ident, next_check=0)
        lib.process_one()
        clock[0] += 1
    photo = lib.get_photo(ident)
    assert photo['status'] == 'stopped'
    assert photo['retryable'] is True
    assert photo['query_window']['attempts'] == 60
    assert photo['query_window']['stop_reason'] == 'attempt_limit'
    restarted = PortraitLibrary(lib.settings)
    restarted.recover()
    restarted.process_one()
    assert len(calls) == 60


def test_deadline_stops_even_when_next_check_is_later_without_network(photo_env, monkeypatch):
    # Catches deadline checks made only after issuing a remote query.
    lib, ident, clock = photo_env
    calls = query_api(monkeypatch)
    lib.update(ident, status='processing', remote_id='asset-existing')
    lib.process_one()
    clock[0] = 11800.0
    lib.update(ident, next_check=12000)
    restarted = PortraitLibrary(lib.settings)
    restarted.process_one()
    assert restarted.get_photo(ident)['status'] == 'stopped'
    assert restarted.get_photo(ident)['query_window']['stop_reason'] == 'deadline'
    assert calls == ['query']


def test_errors_back_off_5_15_30_60_and_restart_preserves_due_time(photo_env, monkeypatch):
    # Catches lost failure counters and restart bypassing the backoff.
    lib, ident, clock = photo_env
    calls = query_api(monkeypatch, fail=True)
    lib.update(ident, status='processing', remote_id='asset-existing')
    for index, delay in enumerate((5, 15, 30, 60, 60), 1):
        lib.process_one()
        photo = lib.get_photo(ident, private=True)
        assert photo['next_check'] == clock[0] + delay
        assert photo['query_window']['attempts'] == index
        assert photo['query_window']['consecutive_errors'] == index
        lib = PortraitLibrary(lib.settings)
        lib.recover()
        lib.process_one()
        assert len(calls) == index
        clock[0] += delay


def test_attempt_reservation_survives_crash_before_remote_response(photo_env, monkeypatch):
    # Catches charging the attempt only after remote I/O returns.
    lib, ident, clock = photo_env
    lib.update(ident, status='processing', remote_id='asset-existing')

    class Interrupted(BaseException): pass
    class API:
        def __init__(self, *args, **kwargs): pass
        def asset_state(self, remote, group):
            with sqlite3.connect(lib.store.path) as db:
                spent = db.execute('SELECT attempts FROM portrait_query_windows WHERE photo_id=?', (ident,)).fetchone()
            assert spent[0] == 1
            raise Interrupted()

    monkeypatch.setattr(portrait_service, 'ArkPortraitClient', API)
    with pytest.raises(Interrupted): lib.process_one()
    restarted = PortraitLibrary(lib.settings)
    restarted.recover()
    assert restarted.get_photo(ident)['query_window']['attempts'] == 1
    assert restarted.get_photo(ident)['query_window']['started_at'] == 10000


@pytest.mark.parametrize('remote', [None, 'asset-existing'])
def test_manual_continue_starts_fresh_window_and_keeps_history_without_create(photo_env, monkeypatch, remote):
    # Catches stopped uncertainty being sent to the queued/CreateAsset branch.
    lib, ident, clock = photo_env
    calls = query_api(monkeypatch)
    lib.update(ident, status='processing' if remote else 'uncertain', remote_id=remote)
    lib.process_one()
    clock[0] = 11800
    lib.process_one()
    assert lib.get_photo(ident)['status'] == 'stopped'
    continued = lib.retry(ident)
    assert continued['status'] == 'processing'
    assert continued['query_window']['attempts'] == 0
    assert continued['query_window']['started_at'] == 11800
    assert continued['query_window']['deadline_at'] == 13600
    with lib.store.connection() as db:
        history = db.execute('SELECT attempts,stop_reason FROM portrait_query_windows WHERE photo_id=? ORDER BY number', (ident,)).fetchall()
    assert [(row['attempts'], row['stop_reason']) for row in history] == [(1, 'deadline'), (0, '')]
    lib.process_one()
    assert calls == (['query', 'query'] if remote else ['find', 'query'])


def test_old_database_gets_an_expiring_window_without_recreating_asset(photo_env, monkeypatch):
    # Catches migrations granting an old endless job another endless lifetime.
    lib, ident, clock = photo_env
    lib.update(ident, status='uncertain', next_check=0)
    with lib.store.connection() as db:
        db.execute('DROP TABLE IF EXISTS portrait_query_windows')
    clock[0] = 12000
    calls = query_api(monkeypatch, uncertain=True)
    restarted = PortraitLibrary(lib.settings)
    restarted.recover()
    restarted.process_one()
    assert restarted.get_photo(ident)['status'] == 'stopped'
    assert calls == []


def test_stopped_uncertain_retry_only_searches_original_create(photo_env, monkeypatch):
    lib, ident, clock = photo_env
    calls = query_api(monkeypatch, uncertain=True)
    lib.update(ident, status='uncertain')
    lib.process_one()
    clock[0] = 11800
    lib.process_one()
    continued = lib.retry(ident)
    assert continued['status'] == 'uncertain'
    assert continued['query_window']['attempts'] == 0
    lib.process_one()
    assert lib.get_photo(ident)['status'] == 'uncertain'
    assert calls == ['find', 'find']


def test_restore_hold_prevents_photo_remote_queries(photo_env, monkeypatch):
    # Catches a worker bypassing the restored-snapshot reconciliation gate.
    lib, ident, _ = photo_env
    calls = query_api(monkeypatch)
    lib.update(ident, status='processing', remote_id='asset-existing')
    (lib.settings.storage_dir / '.restore-hold.json').write_text('{}')
    lib.process_one()
    assert calls == []
    assert lib.get_photo(ident)['status'] == 'processing'


def test_stopped_photo_reports_retryable_generation_error(photo_env, monkeypatch):
    # Catches treating a stopped window as pending or permanently rejected.
    from dataclasses import asdict
    from app.portrait_generation import verify, PortraitPhotoError
    lib, ident, _ = photo_env
    query_api(monkeypatch)
    photo = lib.get_photo(ident, private=True)
    lib.update(ident, status='stopped', retryable=True, message='自动查询已停止')
    snapshot = {'config': asdict(lib.config), 'person_id': photo['person_id'],
        'group_id': 'group-photo', 'person_type': 'LivenessFace',
        'uploads': {photo['asset_id']: ident}, 'bindings': {}}
    with pytest.raises(PortraitPhotoError) as caught:
        verify(snapshot, lib.store, settings=lib.settings)
    assert caught.value.retryable is True
    assert caught.value.error_kind == 'person_preparation_timeout'


def test_deadline_is_checked_after_waiting_for_sqlite_write_lock(photo_env, monkeypatch):
    # Catches a stale timestamp granting a query after a busy database unlocks.
    from contextlib import contextmanager
    lib, ident, clock = photo_env
    calls = query_api(monkeypatch)
    lib.update(ident, status='processing', remote_id='asset-existing')
    lib.process_one()
    calls.clear()
    clock[0] = 11799
    original_connection = lib.store.connection

    @contextmanager
    def delayed_connection():
        with original_connection() as db:
            class DelayedWrite:
                def execute(self, sql, args=()):
                    result = db.execute(sql, args)
                    if sql == 'BEGIN IMMEDIATE':
                        clock[0] = 11800
                    return result
            yield DelayedWrite()

    monkeypatch.setattr(lib.store, 'connection', delayed_connection)
    lib.process_one()
    assert calls == []
    assert lib.get_photo(ident)['query_window']['attempts'] == 1


@pytest.mark.parametrize('failed', [False, True])
def test_old_query_response_cannot_stop_manually_renewed_window(photo_env, failed):
    # Models a query in the worker process returning after an API process renewed
    # the uncertain job. Its spent old budget must not stop the fresh window.
    lib, ident, clock = photo_env
    lib.update(ident, status='uncertain', remote_id='asset-existing')
    old_number = lib._reserve_query(ident)
    with lib.store.connection() as db:
        db.execute('UPDATE portrait_query_windows SET attempts=60 WHERE photo_id=? AND number=?',
                   (ident, old_number))
    clock[0] += 1
    renewed = lib.retry(ident)
    assert renewed['status'] == 'processing'
    assert renewed['query_window']['number'] == 2
    lib._finish_query(ident, old_number, failed=failed)
    current = lib.get_photo(ident, private=True)
    assert current['status'] == 'processing'
    assert current['query_window'] == renewed['query_window']
    assert current['next_check'] == 0
