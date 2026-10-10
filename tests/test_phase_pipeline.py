from datetime import datetime, timedelta, timezone
from dataclasses import asdict

from tests.test_production_worker import setup
from tests.test_video_provider import response
from app import production_store, production_worker as worker
from app.generation_settings import GenerationConfig
from app.video_provider import VideoProvider
from app import hairstyle_mask
from PIL import Image


def test_worker_measures_lock_wait_separately_from_masking_and_cache(setup, monkeypatch):
    cfg, store, draft, private = setup
    seconds = [0]
    monkeypatch.setattr(production_store, 'now', lambda: (datetime(2026, 10, 5, tzinfo=timezone.utc) + timedelta(seconds=seconds[0])).isoformat())
    class BusyLock:
        def __enter__(self): seconds[0] += 3
        def __exit__(self, *args): pass
    def mask(src, dst, *args):
        seconds[0] += 7
        dst.write_bytes(b'masked')
    monkeypatch.setattr(worker, 'run_deface', mask)
    for index in range(2):
        run = store.create_run(draft['id'], 1, str(index), private)
        worker.execute_run(cfg, store, store.claim_next())
        result = store.get_run(run['id'])
        assert result['status'] == 'succeeded'
        phases = result['timing']['phases']
        assert phases['waiting']['seconds'] == 0
        assert phases['masking']['seconds'] == 7
        assert not phases['masking'].get('cached', False)


def test_real_provider_upload_poll_download_are_exclusive_phases(setup, monkeypatch):
    cfg, store, draft, private = setup
    seconds = [0]
    monkeypatch.setattr(production_store, 'now', lambda: (datetime(2026, 10, 5, tzinfo=timezone.utc) + timedelta(seconds=seconds[0])).isoformat())
    config = GenerationConfig(mode='http', provider='toapis', protocol='toapis',
                              base_url='https://provider.example/v1', api_key='test', model='my-model')
    run = store.create_run(draft['id'], 1, 'provider', private)
    store.claim_next()
    class UploadLock:
        def __enter__(self): seconds[0] += 2
        def __exit__(self, *args): pass
    monkeypatch.setattr('app.video_provider._upload_lock', UploadLock())
    def request(method, url, **kwargs):
        if method == 'POST':
            seconds[0] += 4
            return response({'data': {'url': 'https://uploaded.example/video'}} if url.endswith('/uploads/videos') else {'id': 'task'})
        seconds[0] += 12
        return response({'status': 'completed', 'result': {'data': [{'url': 'https://output.example/video'}]}})
    monkeypatch.setattr('app.video_provider.requests.request', request)
    def download(self, url, output):
        seconds[0] += 5
        output.write_bytes(b'output')
    monkeypatch.setattr(VideoProvider, '_download', download)
    provider = VideoProvider(config, 0)
    provider.phase_callback = lambda phase: store.set_phase(run['id'], phase)
    provider.generate(cfg.storage_dir / 'source.mp4', [cfg.storage_dir / 'face.png'],
                      [cfg.storage_dir / 'clothes.png'], 'prompt', cfg.storage_dir / 'output.mp4')
    result = store.update_run(run['id'], status='succeeded')['timing']['phases']
    assert {key: result[key]['seconds'] for key in ('waiting', 'upload', 'model', 'other')} == {
        'waiting': 4, 'upload': 8, 'model': 12, 'other': 5}


def test_hairstyle_internal_lock_and_cached_mask_are_measured(setup, monkeypatch):
    cfg, store, draft, private = setup
    import hashlib
    seconds = [0]
    monkeypatch.setattr(production_store, 'now', lambda: (datetime(2026, 10, 5, tzinfo=timezone.utc) + timedelta(seconds=seconds[0])).isoformat())
    folder = cfg.storage_dir / 'assets'
    folder.mkdir()
    path = folder / 'hair.png'
    Image.new('RGB', (16, 16)).save(path)
    store.add_asset('hair', 'hair.png', 'hairstyle', path, path.stat().st_size, 'image/png', hashlib.sha256(path.read_bytes()).hexdigest())
    draft = store.save_draft(draft['id'], draft['revision'], {'hairstyle_enabled': True, 'hairstyle_asset_ids': ['hair']})
    class HairLock:
        def __enter__(self): seconds[0] += 2
        def __exit__(self, *args): pass
    def detect(*args):
        seconds[0] += 5
        return []
    monkeypatch.setattr(hairstyle_mask, '_lock', HairLock())
    monkeypatch.setattr(hairstyle_mask, '_detect_faces', detect)
    for index in range(2):
        run = store.create_run(draft['id'], draft['revision'], str(index), private)
        worker.execute_run(cfg, store, store.claim_next())
        value = store.get_run(run['id'])
        assert value['status'] == 'succeeded', value['error']
        phases = value['timing']['phases']
        assert phases['waiting']['seconds'] == 2
        assert phases['masking']['seconds'] == 5
        assert not phases['masking'].get('cached', False)


def test_continuation_model_upload_and_finalization_accumulate_once(setup, monkeypatch):
    from tests.test_continuation_worker import configure
    from app import continuation, continuation_llm, continuation_media
    cfg, store, draft, private, _ = configure(setup, monkeypatch)
    private['storage']['enabled'] = True
    seconds = [0]
    monkeypatch.setattr(production_store, 'now', lambda: (datetime(2026, 10, 5, tzinfo=timezone.utc) + timedelta(seconds=seconds[0])).isoformat())
    def plan(*args, **kw):
        seconds[0] += 3
        return {'continuation_prompt': 'continue'}
    def publish(*args):
        seconds[0] += 4
        return 'https://assets.example/video'
    def finalize(src, dst, *args):
        seconds[0] += 2
        dst.write_bytes(src.read_bytes())
    class Provider:
        def __init__(self, *args): pass
        def generate(self, video, faces, clothes, prompt, output, **kw):
            kw['on_submitted']('base')
            seconds[0] += 6
            output.write_bytes(b'base')
        def extend(self, video, prompt, output, **kw):
            kw['on_submitted']('extension')
            seconds[0] += 8
            output.write_bytes(b'extended')
    monkeypatch.setattr(continuation_llm, 'plan_continuation', plan)
    monkeypatch.setattr(continuation, 'publish_base', publish)
    monkeypatch.setattr(continuation_media, 'finalize', finalize)
    monkeypatch.setattr(worker, 'VideoProvider', Provider)
    monkeypatch.setattr('app.video_provider.VideoProvider', Provider)
    run = store.create_run(draft['id'], 1, 'extend-timing', private)
    worker.execute_run(cfg, store, store.claim_next())
    result = store.get_run(run['id'])
    assert result['status'] == 'succeeded', result['error']
    phases = result['timing']['phases']
    assert {key: phases[key]['seconds'] for key in ('model', 'upload', 'other')} == {
        'model': 17, 'upload': 4, 'other': 2}
