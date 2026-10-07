from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import hashlib
import threading

from app.config import settings
from app.generation_settings import GenerationConfig
from app.storage_settings import StorageConfig
from app.production_store import ProductionStore
from app import media, production_worker as worker, redaction_service


def make_run(root, source=b'source'):
    cfg = replace(settings, storage_dir=root)
    store = ProductionStore(root)
    for ident, kind, content in [('source', 'video', source), ('face', 'face', b'face')]:
        path = root / (ident + '.mp4' if kind == 'video' else ident + '.png')
        path.write_bytes(content)
        store.add_asset(ident, path.name, kind, path, len(content),
                        'video/mp4' if kind == 'video' else 'image/png', hashlib.sha256(content).hexdigest())
    draft = store.create_draft({'source_asset_id': 'source', 'face_asset_ids': ['face'], 'prompt': 'test'})
    run = store.create_run(draft['id'], 1, 'test', {
        'generation': asdict(GenerationConfig()), 'storage': asdict(StorageConfig())})
    return cfg, store, store.claim_next()


def test_different_videos_can_preprocess_concurrently(tmp_path, monkeypatch):
    runs = [make_run(tmp_path / str(i), str(i).encode()) for i in range(2)]
    barrier = threading.Barrier(2)

    def external(source, target, *_):
        barrier.wait(timeout=3)
        target.write_bytes(b'redacted')

    monkeypatch.setattr(worker, 'run_deface', external)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(worker.execute_run, *args) for args in runs]
        for future in futures:
            future.result(timeout=10)
    assert [store.get_run(run['id'])['status'] for _, store, run in runs] == ['succeeded', 'succeeded']


def test_external_calls_are_bounded_at_twenty(tmp_path, monkeypatch):
    cfg = replace(settings, storage_dir=tmp_path, redaction_service=redaction_service.ServiceConfig(mode='http'))
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    release, reached = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = peak = 0

    def external(src, dst, *_):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 20:
                reached.set()
        assert release.wait(5)
        dst.write_bytes(b'masked')
        with lock:
            active -= 1
        return dst

    monkeypatch.setattr(redaction_service, 'process', external)
    with ThreadPoolExecutor(24) as pool:
        futures = [pool.submit(media.run_deface, source, tmp_path / f'{i}.mp4', cfg) for i in range(24)]
        try:
            assert reached.wait(3)
            # All workers have started; further entrants must remain behind the limiter.
            threading.Event().wait(.15)
            assert peak == 20
        finally:
            release.set()
        for future in futures:
            future.result(timeout=5)


def test_local_fallbacks_remain_serial(tmp_path, monkeypatch):
    cfg = replace(settings, storage_dir=tmp_path, redaction_service=redaction_service.ServiceConfig(mode='http'))
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    lock = threading.Lock()
    active = peak = 0

    def external(*_):
        raise media.MediaPipelineError('failed')

    def local(src, dst, *_):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        threading.Event().wait(.05)
        dst.write_bytes(b'masked')
        with lock:
            active -= 1

    monkeypatch.setattr(redaction_service, 'process', external)
    monkeypatch.setattr(redaction_service, 'validate_output', lambda *_: None)
    monkeypatch.setattr(media, '_run_local_deface', local)
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda i: media.run_deface(source, tmp_path / f'{i}.mp4', cfg), range(4)))
    assert len(results) == 4
    assert peak == 1


def test_same_tenant_video_is_masked_only_once(tmp_path, monkeypatch):
    cfg, store, run = make_run(tmp_path)
    second = store.create_run(run['draft_id'], 1, 'second', run['private'])
    second = store.claim_next()
    calls = []

    def external(src, dst, *_):
        calls.append(src)
        threading.Event().wait(.1)
        dst.write_bytes(b'masked')

    monkeypatch.setattr(worker, 'run_deface', external)
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(worker.execute_run, cfg, store, r) for r in [run, second]]
        for future in futures:
            future.result(timeout=5)
    assert all(store.get_run(r['id'])['status'] == 'succeeded' for r in [run, second])
    assert len(calls) == 1


def test_generic_http_decoders_remain_serial(tmp_path, monkeypatch):
    cfg = replace(settings, storage_dir=tmp_path)
    service = redaction_service.ServiceConfig(mode='http', endpoint='https://mask.example/process')
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    lock = threading.Lock()
    active = peak = 0
    barrier = threading.Barrier(4)

    class Reply:
        status_code = 200
        headers = {'Content-Type': 'video/mp4'}
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def iter_content(self, **_):
            barrier.wait(timeout=3)
            yield b'masked'

    class Capture:
        def __init__(self, *_):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
        def read(self):
            threading.Event().wait(.05)
            return True, None
        def release(self):
            nonlocal active
            with lock: active -= 1

    monkeypatch.setattr(redaction_service.requests, 'post', lambda *a, **kw: Reply())
    monkeypatch.setattr(redaction_service.cv2, 'VideoCapture', Capture)
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda i: redaction_service.process(source, tmp_path / f'{i}.mp4',
                      cfg, media.BlurOptions(), service), range(4)))
    assert peak == 1
