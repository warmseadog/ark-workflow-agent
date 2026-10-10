from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading

import pytest

from app import media, redaction_service
from app.config import settings


@pytest.mark.parametrize('fallback', [False, True])
def test_three_local_jobs_run_and_fourth_waits(tmp_path, monkeypatch, fallback):
    cfg = replace(settings, storage_dir=tmp_path,
                  redaction_service=redaction_service.ServiceConfig(mode='http' if fallback else 'local'))
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    guard = threading.Lock()
    release, three, fourth = threading.Event(), threading.Event(), threading.Event()
    active = peak = 0

    def local(src, dst, *_):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
            if active == 3:
                three.set()
            if active > 3:
                fourth.set()
        try:
            assert release.wait(8)
            dst.write_bytes(b'masked')
            return dst
        finally:
            with guard:
                active -= 1

    def external(*_):
        raise media.MediaPipelineError('upstream rejected')

    monkeypatch.setattr(media, '_run_local_deface', local)
    monkeypatch.setattr(redaction_service, 'process', external)
    monkeypatch.setattr(redaction_service, 'validate_output', lambda *_: None)
    with ThreadPoolExecutor(6) as pool:
        futures = [pool.submit(media.run_deface, source, tmp_path / f'{i}.mp4', cfg) for i in range(6)]
        try:
            assert three.wait(3), 'Three local jobs must be admitted together'
            assert not fourth.wait(.15), 'Fourth job must wait for a free slot'
        finally:
            release.set()
        assert all(f.result(timeout=5).is_file() for f in futures)
    assert peak == 3


def test_local_and_fallback_share_slots_and_release_on_failure(tmp_path, monkeypatch):
    from app.preprocessing_limits import local_redaction_slots
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'source')
    local_cfg = replace(settings, storage_dir=tmp_path,
                        redaction_service=redaction_service.ServiceConfig(mode='local'))
    http_cfg = replace(local_cfg, redaction_service=redaction_service.ServiceConfig(mode='http'))
    entered = threading.Event()

    def fail(*_):
        entered.set()
        raise media.MediaPipelineError('test failure')

    monkeypatch.setattr(media, '_run_local_deface', fail)
    monkeypatch.setattr(redaction_service, 'process', fail)
    # Hold two slots, then verify either path can use and release the last slot.
    with local_redaction_slots, local_redaction_slots:
        for i, cfg in enumerate((local_cfg, http_cfg, local_cfg)):
            entered.clear()
            with pytest.raises(media.MediaPipelineError):
                media.run_deface(source, tmp_path / f'{i}.mp4', cfg)
            assert entered.is_set()
            assert local_redaction_slots.acquire(blocking=False)
            local_redaction_slots.release()
