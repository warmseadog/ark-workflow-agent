from pathlib import Path
from types import SimpleNamespace

from app.workflow_store import WorkflowStore
from app.workflow_worker import run_generation_task, run_redaction_task


def test_redaction_worker_persists_verified_artifact(tmp_path, monkeypatch):
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    project = store.create_project("worker")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    task = store.create_stage_task(project["id"], "redaction_render")

    def fake_deface(input_path, output_path, settings, options):
        output_path.write_bytes(input_path.read_bytes() + b"-redacted")
        return output_path

    monkeypatch.setattr("app.workflow_worker.run_deface", fake_deface)
    artifact = run_redaction_task(
        store,
        task["id"],
        project["id"],
        str(source),
        "upload",
        {"mask_mode": "face", "style": "mosaic"},
        settings=SimpleNamespace(storage_dir=tmp_path, deface_bin="fake"),
    )

    assert artifact["sha256"]
    assert Path(artifact["path"]).read_bytes() == b"source-redacted"
    assert store.get_stage_task(task["id"])["status"] == "succeeded"


def test_generation_worker_persists_provider_output(tmp_path, monkeypatch):
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    project = store.create_project("worker")
    source = tmp_path / "redacted.mp4"
    source.write_bytes(b"redacted")
    face = tmp_path / "face.png"
    face.write_bytes(b"face")
    garment = tmp_path / "garment.png"
    garment.write_bytes(b"garment")
    task = store.create_stage_task(project["id"], "generation")

    class FakeClient:
        def __init__(self, settings):
            pass

        def generate(self, video_path, face_path, clothing_path, prompt, output_path):
            output_path.write_bytes(video_path.read_bytes() + b"-generated")
            return {"provider": "fake", "message": "ok"}

    monkeypatch.setattr("app.workflow_worker.SeedanceClient", FakeClient)
    artifact = run_generation_task(
        store,
        task["id"],
        project["id"],
        str(source),
        str(face),
        str(garment),
        "保持动作",
        settings=SimpleNamespace(storage_dir=tmp_path),
    )

    assert artifact["provider"] == "fake"
    assert Path(artifact["path"]).read_bytes() == b"redacted-generated"
    assert store.get_stage_task(task["id"])["status"] == "succeeded"