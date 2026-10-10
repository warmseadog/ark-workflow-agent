"""Legacy caches must never affect new attempts or be repopulated."""
import hashlib
import json
import pytest
from app import production_worker as worker, redaction_service
from app.video_provider import ProviderError
from tests.test_production_worker import setup


@pytest.mark.parametrize('outcome', ['success', 'failure', 'cancelled'])
def test_legacy_cache_is_ignored_and_no_candidate_is_written(setup, monkeypatch, outcome):
    cfg, store, draft, private = setup
    source = store.get_asset('source', private=True)
    options = worker.mask_options(draft['mask'])
    key = hashlib.sha256((source['sha256'] + json.dumps(options.model_dump(mode='json'), sort_keys=True) + redaction_service.fingerprint(cfg)).encode()).hexdigest()
    old = cfg.storage_dir/'cache'/'redacted'/(key+'.mp4')
    old.parent.mkdir(parents=True)
    old.write_bytes(b'legacy-cache')
    run = store.create_run(draft['id'], 1, outcome, private)
    def mask(src, dst, *args):
        assert src.read_bytes() == b'source'
        dst.write_bytes(b'new-mask')
        if outcome == 'cancelled': store.cancel_run(run['id'])
    def generate(self, video, faces, clothes, prompt, output, **kwargs):
        assert video.read_bytes() == b'new-mask'
        if outcome == 'failure': raise ProviderError('generation failed')
        output.write_bytes(b'finished')
    monkeypatch.setattr(worker, 'run_deface', mask)
    monkeypatch.setattr(worker.VideoProvider, 'generate', generate)
    worker.execute_run(cfg, store, store.claim_next())
    assert store.get_run(run['id'])['status'] == {'success':'succeeded','failure':'failed','cancelled':'cancelled'}[outcome]
    assert old.read_bytes() == b'legacy-cache'
    assert list(old.parent.glob('*')) == [old]
    assert not list((cfg.storage_dir/'work').glob('*/redaction-cache*'))
