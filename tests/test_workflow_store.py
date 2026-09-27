from pathlib import Path

import pytest

from app.workflow_store import (
    InvalidTransitionError,
    WorkflowStore,
)


def test_project_asset_and_task_lifecycle_is_durable(tmp_path: Path) -> None:
    database = tmp_path / "workflow.sqlite3"
    store = WorkflowStore(database)

    project = store.create_project("demo", idempotency_key="project-demo")
    source = store.create_source_asset(
        project["id"],
        "url",
        "https://example.test/video.mp4",
        idempotency_key="source-demo",
    )
    task = store.create_stage_task(
        project["id"],
        "redaction",
        input_data={"source_asset_id": source["id"]},
        idempotency_key="task-demo",
    )

    assert store.create_project("ignored", idempotency_key="project-demo")["id"] == project["id"]
    assert store.create_source_asset(
        project["id"], "url", "ignored", idempotency_key="source-demo"
    )["id"] == source["id"]
    assert store.create_stage_task(
        project["id"], "redaction", idempotency_key="task-demo"
    )["id"] == task["id"]

    running = store.transition_stage_task(task["id"], "running")
    succeeded = store.transition_stage_task(
        task["id"], "succeeded", output_data={"artifact_id": "derived-1"}
    )
    assert running["attempts"] == 1
    assert succeeded["status"] == "succeeded"
    assert succeeded["output"]["artifact_id"] == "derived-1"

    reopened = WorkflowStore(database)
    assert reopened.get_project(project["id"])["name"] == "demo"
    assert reopened.get_stage_task(task["id"])["status"] == "succeeded"
    assert len(reopened.list_events(project_id=project["id"])) >= 4


def test_task_state_machine_rejects_invalid_transition(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    project = store.create_project("demo")
    task = store.create_stage_task(project["id"], "source_ingest")

    with pytest.raises(InvalidTransitionError):
        store.transition_stage_task(task["id"], "succeeded")

    store.transition_stage_task(task["id"], "running")
    store.transition_stage_task(task["id"], "failed", error_data={"message": "network"})
    retried = store.transition_stage_task(task["id"], "queued")
    assert retried["status"] == "queued"


def test_snapshot_versions_and_review_are_auditable(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    project = store.create_project("demo")
    task = store.create_stage_task(project["id"], "review")

    first = store.create_run_snapshot(
        project["id"], "run-1", {"source": "a"}, version=1, idempotency_key="snapshot-1"
    )
    second = store.create_run_snapshot(
        project["id"], "run-1", {"source": "b"}, version=2, idempotency_key="snapshot-2"
    )
    decision = store.create_review_decision(
        project["id"],
        "review",
        "approved",
        task_id=task["id"],
        reviewer="operator",
        note="looks good",
        idempotency_key="review-1",
    )

    assert store.get_run_snapshot(project["id"], "run-1")["id"] == second["id"]
    assert store.get_run_snapshot(project["id"], "run-1", version=1)["id"] == first["id"]
    assert decision["decision"] == "approved"
    assert any(event["event_type"] == "review.created" for event in store.list_events(project_id=project["id"]))
