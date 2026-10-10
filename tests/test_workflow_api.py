from tests.media_fixtures import image_bytes, video_bytes, media_bytes
import os
import pytest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.workflow_router import get_router
from app.workflow_store import WorkflowStore


@pytest.fixture(autouse=True)
def stop_legacy_dispatchers():
    yield
    from app.workflow_worker import stop_workflow_worker
    assert stop_workflow_worker()


def test_workflow_api_lifecycle(tmp_path):
    api = FastAPI()
    api.include_router(get_router(WorkflowStore(tmp_path / "workflow.sqlite3")))
    client = TestClient(api)

    response = client.post("/api/workflow/projects", json={"name": "flow"})
    assert response.status_code == 200
    project_id = response.json()["id"]

    response = client.post(
        f"/api/workflow/projects/{project_id}/source",
        json={"kind": "url", "uri": "https://example.test/video.mp4"},
    )
    assert response.status_code == 200

    for stage in (
        "SOURCE_INGESTING",
        "SOURCE_REVIEW",
        "SOURCE_READY",
        "REDACTION_EDITING",
        "REDACTION_REVIEW",
    ):
        response = client.post(
            f"/api/workflow/projects/{project_id}/stage",
            json={"stage": stage},
        )
        assert response.status_code == 200, response.text

    response = client.post(
        f"/api/workflow/projects/{project_id}/redaction/review",
        json={"approved": True, "revision": "r1"},
    )
    assert response.status_code == 200, response.text

    for kind in ("face", "garment"):
        response = client.post(
            f"/api/workflow/projects/{project_id}/materials",
            json={"kind": kind, "uri": f"storage/{kind}.png", "approved": True},
        )
        assert response.status_code == 200, response.text

    response = client.post(
        f"/api/workflow/projects/{project_id}/snapshot",
        json={"run_id": "run-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["project"]["stage"] == "READY_FOR_EXECUTION"


def test_snapshot_requires_both_reference_kinds(tmp_path):
    api = FastAPI()
    api.include_router(get_router(WorkflowStore(tmp_path / "workflow.sqlite3")))
    client = TestClient(api)
    project_id = client.post("/api/workflow/projects", json={"name": "incomplete"}).json()["id"]
    client.post(
        f"/api/workflow/projects/{project_id}/source",
        json={"kind": "upload", "uri": "storage/source.mp4"},
    )
    response = client.post(
        f"/api/workflow/projects/{project_id}/snapshot",
        json={"run_id": "run-1"},
    )
    assert response.status_code == 409
def test_source_upload_is_streamed_and_hashed(tmp_path, monkeypatch):
    api = FastAPI()
    api.include_router(get_router(WorkflowStore(tmp_path / "workflow.sqlite3")))
    client = TestClient(api)
    project_id = client.post("/api/workflow/projects", json={"name": "upload"}).json()["id"]

    response = client.post(
        f"/api/workflow/projects/{project_id}/source-upload",
        files={"video": ("clip.mp4", video_bytes(), "video/mp4")},
        data={"rights_status": "declared"},
    )
    assert response.status_code == 200
    asset = response.json()["asset"]
    assert asset["kind"] == "upload"
    assert asset["sha256"]
    assert asset["size_bytes"] == len(video_bytes())
def test_redaction_render_is_idempotent(tmp_path, monkeypatch):
    api = FastAPI()
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    api.include_router(get_router(store))
    client = TestClient(api)
    project_id = client.post("/api/workflow/projects", json={"name": "render"}).json()["id"]
    client.post(
        f"/api/workflow/projects/{project_id}/source",
        json={"kind": "url", "uri": "https://example.test/video.mp4"},
    )

    monkeypatch.setattr("app.workflow_worker.run_redaction_task", lambda *args, **kwargs: None)
    first = client.post(
        f"/api/workflow/projects/{project_id}/redaction/render",
        json={"idempotency_key": "render-1"},
    )
    second = client.post(
        f"/api/workflow/projects/{project_id}/redaction/render",
        json={"idempotency_key": "render-1"},
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["task"]["id"] == second.json()["task"]["id"]
    assert second.json()["task"]["status"] == "running"
def test_material_upload_and_execute_task_are_on_workflow_store(tmp_path, monkeypatch):
    api = FastAPI()
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    api.include_router(get_router(store))
    client = TestClient(api)
    project_id = client.post("/api/workflow/projects", json={"name": "execute"}).json()["id"]

    face = client.post(
        f"/api/workflow/projects/{project_id}/material-upload",
        files={"material": ("face.png", image_bytes(), "image/png")},
        data={"kind": "face", "approved": "true"},
    )
    garment = client.post(
        f"/api/workflow/projects/{project_id}/material-upload",
        files={"material": ("garment.png", image_bytes(), "image/png")},
        data={"kind": "garment", "approved": "true"},
    )
    assert face.status_code == garment.status_code == 200

    redacted = tmp_path / "redacted.mp4"
    redacted.write_bytes(b"redacted")
    render_task = store.create_stage_task(project_id, "redaction_render")
    store.transition_stage_task(render_task["id"], "running")
    store.transition_stage_task(
        render_task["id"],
        "succeeded",
        output_data={"artifact": {"path": str(redacted), "sha256": "demo"}},
    )

    monkeypatch.setattr("app.workflow_worker.run_generation_task", lambda *args, **kwargs: None)
    response = client.post(
        f"/api/workflow/projects/{project_id}/execute",
        json={"prompt": "保持动作", "idempotency_key": "execute-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["task"]["status"] == "running"
def test_all_ui_entrypoints_keep_legacy_studio_style():
    from app.main import app

    client = TestClient(app)
    for path in ("/", "/v1", "/studio"):
        response = client.get(path)
        assert response.status_code == 200
        assert 'id="studio-job-form"' in response.text
        assert 'id="studio-defaced-video"' in response.text
