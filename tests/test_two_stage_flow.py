from dataclasses import replace

from app import jobs
from app.config import settings
from app.media import BlurOptions


def test_job_public_exposes_defaced_preview_only_after_blur(tmp_path):
    store = jobs.JobStore()
    job = store.create()
    assert job.public()["defaced_url"] is None

    store.update(job.id, status="defaced", defaced_name="defaced.mp4")
    public = store.get(job.id).public()
    assert public["defaced_url"] == f"/api/jobs/{job.id}/defaced"
    assert public["download_url"] is None


def test_deface_pipeline_stops_before_seedance(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    local_settings = replace(settings, storage_dir=tmp_path)
    store = jobs.JobStore()
    monkeypatch.setattr(jobs, "store", store)
    job = store.create()
    called = []

    def fake_deface(input_path, output_path, _settings, _options):
        called.append(input_path)
        output_path.write_bytes(b"defaced")
        return output_path

    monkeypatch.setattr(jobs, "run_deface", fake_deface)
    jobs.run_deface_pipeline(job.id, local_settings, source, None, BlurOptions())

    current = store.get(job.id)
    assert called == [source]
    assert current.status == "defaced"
    assert current.defaced_name == "defaced.mp4"
    assert current.output_name is None
    assert (tmp_path / "work" / job.id / "defaced.mp4").read_bytes() == b"defaced"


def test_generation_pipeline_consumes_defaced_video(monkeypatch, tmp_path):
    local_settings = replace(settings, storage_dir=tmp_path)
    store = jobs.JobStore()
    monkeypatch.setattr(jobs, "store", store)
    job = store.create()
    job_dir = tmp_path / "work" / job.id
    job_dir.mkdir(parents=True)
    defaced = job_dir / "defaced.mp4"
    defaced.write_bytes(b"defaced")
    store.update(job.id, status="defaced", defaced_name="defaced.mp4")
    calls = []

    class FakeSeedance:
        def __init__(self, _settings):
            pass

        def generate(self, video_path, face_path, clothing_path, prompt, output_path):
            calls.append((video_path, face_path, clothing_path, prompt))
            output_path.write_bytes(video_path.read_bytes())
            return {"provider": "mock", "message": "生成完成"}

    monkeypatch.setattr(jobs, "SeedanceClient", FakeSeedance)
    jobs.run_generation_pipeline(job.id, local_settings, None, None, "动作保持稳定")

    current = store.get(job.id)
    assert calls == [(defaced, None, None, "动作保持稳定")]
    assert current.status == "succeeded"
    assert current.output_name == f"{job.id}.mp4"
    assert (tmp_path / "outputs" / f"{job.id}.mp4").read_bytes() == b"defaced"


def test_generation_pipeline_marks_job_running_before_seedance(monkeypatch, tmp_path):
    local_settings = replace(settings, storage_dir=tmp_path)
    store = jobs.JobStore()
    monkeypatch.setattr(jobs, "store", store)
    job = store.create()
    job_dir = tmp_path / "work" / job.id
    job_dir.mkdir(parents=True)
    (job_dir / "defaced.mp4").write_bytes(b"defaced")
    store.update(job.id, status="defaced", defaced_name="defaced.mp4")
    observed = []

    class FakeSeedance:
        def __init__(self, _settings):
            pass

        def generate(self, *_args):
            observed.append(store.get(job.id).status)
            output_path = _args[-1]
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"final")
            return {"provider": "mock", "message": "生成完成"}

    monkeypatch.setattr(jobs, "SeedanceClient", FakeSeedance)
    jobs.run_generation_pipeline(job.id, local_settings, None, None, "")

    assert observed == ["running"]


def test_empty_detection_size_is_treated_as_original_resolution():
    from app.main import _parse_detection_size

    assert _parse_detection_size("") is None
    assert _parse_detection_size(None) is None
    assert _parse_detection_size("640") == 640


def test_deface_pipeline_log_identifies_hair_aware_mode(monkeypatch, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    local_settings = replace(settings, storage_dir=tmp_path)
    store = jobs.JobStore()
    monkeypatch.setattr(jobs, "store", store)
    job = store.create()

    def fake_deface(_input_path, output_path, _settings, _options):
        output_path.write_bytes(b"defaced")
        return output_path

    monkeypatch.setattr(jobs, "run_deface", fake_deface)
    options = BlurOptions(mask_mode="face_hair_primary", robust_tracking=True)
    jobs.run_deface_pipeline(job.id, local_settings, source, None, options)

    assert any("face_hair_primary" in entry for entry in store.get(job.id).logs)
    assert any("本地" in entry for entry in store.get(job.id).logs)
