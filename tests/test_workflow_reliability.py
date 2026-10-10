"""Crash boundaries and concurrency of the real legacy workflow/HTTP adapter."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event
import time

import pytest
import requests

from app.config import settings as base_settings
from app.seedance import SeedanceClient, SeedanceError
from app.workflow_store import ConflictError, WorkflowStore
from app import workflow_worker as worker


@pytest.fixture
def workflow(tmp_path):
    settings = replace(base_settings, storage_dir=tmp_path, seedance_mode="http",
                       seedance_api_url="https://provider.example", seedance_api_key="fixture",
                       seedance_poll_seconds=0)
    store = WorkflowStore(tmp_path / "workflow.sqlite3")
    project = store.create_project("recovery")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    task = store.create_stage_task(project["id"], "generation", input_data={"prompt": "hello"})
    return settings, store, project["id"], task["id"], source


def response(payload, status=200):
    import json
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps(payload).encode()
    return result


def run_generation(workflow):
    settings, store, project_id, task_id, source = workflow
    return worker.run_generation_task(store, task_id, project_id, str(source), None, None,
                                      "hello", settings=settings)


def install_success_transport(monkeypatch):
    monkeypatch.setattr(worker, "run_deface", lambda src,dst,*args: dst.write_bytes(src.read_bytes()+b"-fresh-mask"))
    monkeypatch.setattr("app.seedance.requests.get", lambda *a, **kw: response(
        {"status": "succeeded", "output_url": "https://media.example/result.mp4"}))
    monkeypatch.setattr(SeedanceClient, "_download_result",
                        staticmethod(lambda url, path: path.write_bytes(b"generated")))


def test_two_workers_cannot_submit_the_same_logical_task_twice(workflow, monkeypatch):
    # Removing the atomic queued claim permits two paid POSTs for one task.
    install_success_transport(monkeypatch)
    posts = []
    start = Barrier(2)
    def post(*args, **kwargs):
        posts.append(args[0])
        return response({"id": "accepted-1"})
    monkeypatch.setattr("app.seedance.requests.post", post)
    def run():
        start.wait(timeout=3)
        try:
            return run_generation(workflow)
        except Exception:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: run(), range(2)))
    assert len(posts) == 1
    saved = workflow[1].get_stage_task(workflow[3])
    assert saved["status"] == "succeeded"
    assert saved["attempts"] == 1
    assert Path(saved["output"]["artifact"]["path"]).read_bytes() == b"generated"


@pytest.mark.parametrize("change", ["input", "stage", "project"])
def test_same_key_with_changed_submission_is_a_conflict(workflow, change):
    # Returning an unrelated prior record hides stale or cross-project submissions.
    _, store, project_id, _, _ = workflow
    store.create_stage_task(project_id, "generation", input_data={"prompt": "first"}, idempotency_key="same")
    target_project = store.create_project("other")["id"] if change == "project" else project_id
    target_stage = "redaction_render" if change == "stage" else "generation"
    prompt = "second" if change == "input" else "first"
    with pytest.raises(ConflictError):
        store.create_stage_task(target_project, target_stage, input_data={"prompt": prompt}, idempotency_key="same")


def test_provider_identity_and_result_are_durable_before_later_io(workflow, monkeypatch):
    # Moving a checkpoint after I/O loses the only safe route after a process crash.
    _, store, _, task_id, _ = workflow
    def post(*args, **kwargs):
        assert store.get_stage_task(task_id).get("provider_state", {}).get("phase") == "submitting"
        return response({"id": "accepted-1"})
    def query(*args, **kwargs):
        state = WorkflowStore(store.database).get_stage_task(task_id)["provider_state"]
        assert state["task_id"] == "accepted-1"
        assert state["phase"] == "querying"
        return response({"status": "succeeded", "output_url": "https://media.example/result.mp4"})
    def download(url, path):
        state = WorkflowStore(store.database).get_stage_task(task_id)["provider_state"]
        assert state["result_url"] == url
        assert state["phase"] == "downloading"
        path.write_bytes(b"generated")
    monkeypatch.setattr("app.seedance.requests.post", post)
    monkeypatch.setattr("app.seedance.requests.get", query)
    monkeypatch.setattr(SeedanceClient, "_download_result", staticmethod(download))
    run_generation(workflow)
    assert store.get_stage_task(task_id)["status"] == "succeeded"


def test_ambiguous_post_is_durable_and_second_worker_does_not_repost(workflow, monkeypatch):
    posts = []
    def post(*args, **kwargs):
        posts.append(args[0])
        raise requests.Timeout("accepted but response lost")
    monkeypatch.setattr("app.seedance.requests.post", post)
    with pytest.raises(SeedanceError):
        run_generation(workflow)
    saved = WorkflowStore(workflow[1].database).get_stage_task(workflow[3])
    assert saved.get("provider_state", {}).get("submission_uncertain") is True
    run_generation(workflow)
    assert len(posts) == 1


@pytest.mark.parametrize("phase,remote_id,result_url,want_status", [
    ("preparing", None, None, "queued"),
    ("submitting", None, None, "failed"),
    (None, None, None, "failed"),
    ("querying", "remote-1", None, "queued"),
    ("downloading", "remote-1", "https://media.example/result.mp4", "queued"),
])
def test_restart_recovers_only_known_safe_work(workflow, phase, remote_id, result_url, want_status):
    # Requeueing unknown/submitting work may incur a second bill after a crash.
    _, store, _, task_id, _ = workflow
    store.transition_stage_task(task_id, "running")
    import json
    with store._connection() as connection:
        connection.execute("UPDATE stage_tasks SET provider_state_json=? WHERE id=?",
                           (json.dumps({"phase": phase, "task_id": remote_id, "result_url": result_url}), task_id))
    worker.recover_workflow_tasks(WorkflowStore(store.database))
    saved = store.get_stage_task(task_id)
    assert saved["status"] == want_status
    assert saved["provider_state"].get("task_id") == remote_id
    if want_status == "failed":
        assert saved["provider_state"]["submission_uncertain"] is True
        with pytest.raises(ConflictError):
            store.resume_stage_task(task_id)


@pytest.mark.parametrize("download_only", [False, True])
def test_manual_resume_uses_durable_remote_identity_without_inputs(workflow, monkeypatch, download_only):
    # A query/download retry must not need removed local inputs or issue a POST.
    _, store, _, task_id, source = workflow
    task = store.claim_stage_task(task_id)
    store.checkpoint_stage_task(task_id, task["execution_token"], phase="downloading" if download_only else "querying",
                               task_id="remote-1", result_url="https://media.example/result.mp4" if download_only else None)
    store.transition_stage_task(task_id, "failed", error_data={"message": "offline"})
    source.unlink()
    monkeypatch.setattr("app.seedance.requests.post", lambda *a, **kw: pytest.fail("Resume performed a paid POST"))
    install_success_transport(monkeypatch)
    if download_only:
        monkeypatch.setattr("app.seedance.requests.get", lambda *a, **kw: pytest.fail("Saved URL should skip query"))
    store.resume_stage_task(task_id)
    run_generation(workflow)
    assert store.get_stage_task(task_id)["status"] == "succeeded"


def test_startup_discovers_queued_task_and_starts_only_one_dispatcher(workflow, monkeypatch):
    # Returning an existing live dispatcher prevents restart recovery stealing active work.
    settings, store, project_id, task_id, source = workflow
    original = store.create_source_asset(project_id, "upload", str(source))
    render = store.create_stage_task(project_id, "redaction_render", input_data={"source_asset_id":original["id"], "options":{}})
    store.transition_stage_task(render["id"], "running")
    store.transition_stage_task(render["id"], "succeeded", output_data={"artifact": {"path": str(source)}})
    face = store.create_reference_asset(project_id, "face", str(source))
    garment = store.create_reference_asset(project_id, "garment", str(source))
    import json
    with store._connection() as connection:
        connection.execute("UPDATE stage_tasks SET input_json=? WHERE id=?", (json.dumps({
            "redaction_task_id": render["id"], "face_asset_id": face["id"],
            "garment_asset_id": garment["id"], "prompt": "hello"}), task_id))
    install_success_transport(monkeypatch)
    accepted = Event()
    release = Event()
    posts = []
    def post(*a, **kw):
        posts.append(a[0])
        accepted.set()
        assert release.wait(3)
        return response({"id": "remote-1"})
    monkeypatch.setattr("app.seedance.requests.post", post)
    try:
        first = worker.start_workflow_worker(settings, store)
        assert accepted.wait(3), "Queued task was lost on startup"
        assert worker.start_workflow_worker(settings, WorkflowStore(store.database)) is first
        release.set()
        deadline = time.monotonic() + 3
        while store.get_stage_task(task_id)["status"] == "running" and time.monotonic() < deadline:
            time.sleep(.01)
        assert store.get_stage_task(task_id)["status"] == "succeeded"
        assert len(posts) == 1
    finally:
        release.set()
        assert worker.stop_workflow_worker()


def test_restored_storage_never_starts_worker_or_direct_submission(workflow, monkeypatch):
    settings, store, _, task_id, _ = workflow
    (settings.storage_dir / ".restore-hold.json").write_text("{}")
    monkeypatch.setattr("app.seedance.requests.post", lambda *a, **kw: pytest.fail("Restore hold allowed POST"))
    assert worker.start_workflow_worker(settings, store) is None
    run_generation(workflow)
    assert store.get_stage_task(task_id)["status"] == "queued"


def ready_api(workflow):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.workflow_router import get_router
    _, store, project_id, unused_task, source = workflow
    # The fixture's unrelated empty generation should never enter the dispatcher.
    store.transition_stage_task(unused_task, "cancelled")
    original = store.create_source_asset(project_id, "upload", str(source))
    render = store.create_stage_task(project_id, "redaction_render", input_data={"source_asset_id":original["id"], "options":{}})
    store.transition_stage_task(render["id"], "running")
    store.transition_stage_task(render["id"], "succeeded", output_data={"artifact": {"path": str(source)}})
    for kind in ("face", "garment"):
        store.create_reference_asset(project_id, kind, str(source), metadata={"approved": True})
    api = FastAPI()
    api.include_router(get_router(store))
    return TestClient(api)


def test_concurrent_execute_requests_with_one_key_make_one_paid_post(workflow, monkeypatch):
    client = ready_api(workflow)
    settings, store, project_id, _, _ = workflow
    monkeypatch.setattr(worker.Settings, "from_env", lambda: settings)
    install_success_transport(monkeypatch)
    posts = []
    monkeypatch.setattr("app.seedance.requests.post", lambda *a, **kw: posts.append(a[0]) or response({"id": "one"}))
    create = store.create_stage_task
    both_created = Barrier(2)
    def synchronized_create(*args, **kwargs):
        task = create(*args, **kwargs)
        both_created.wait(timeout=3)
        return task
    monkeypatch.setattr(store, "create_stage_task", synchronized_create)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: client.post(f"/api/workflow/projects/{project_id}/execute",
                           json={"prompt": "hello", "idempotency_key": "same-request"}), range(2)))
        assert [item.status_code for item in results] == [200, 200]
        ids = {item.json()["task"]["id"] for item in results}
        assert len(ids) == 1
        task_id = ids.pop()
        deadline = time.monotonic() + 3
        while store.get_stage_task(task_id)["status"] == "running" and time.monotonic() < deadline:
            time.sleep(.01)
        assert store.get_stage_task(task_id)["status"] == "succeeded"
        assert len(posts) == 1
    finally:
        assert worker.stop_workflow_worker()


def test_http_resume_does_not_expose_signed_url_or_require_deleted_materials(workflow, monkeypatch):
    client = ready_api(workflow)
    settings, store, project_id, _, source = workflow
    task = store.create_stage_task(project_id, "generation", input_data={"prompt": "hello"})
    claimed = store.claim_stage_task(task["id"])
    store.checkpoint_stage_task(task["id"], claimed["execution_token"], phase="downloading", task_id="original",
                               result_url="https://media.example/result?signature=private")
    store.transition_stage_task(task["id"], "failed")
    source.unlink()
    monkeypatch.setattr(worker.Settings, "from_env", lambda: settings)
    monkeypatch.setattr("app.seedance.requests.post", lambda *a, **kw: pytest.fail("HTTP resume reposted"))
    install_success_transport(monkeypatch)
    try:
        result = client.post(f"/api/workflow/projects/{project_id}/tasks/{task['id']}/resume")
        assert result.status_code == 200, result.text
        assert "signature=private" not in result.text
        deadline = time.monotonic() + 3
        while store.get_stage_task(task["id"])["status"] == "running" and time.monotonic() < deadline:
            time.sleep(.01)
        saved = store.get_stage_task(task["id"])
        assert saved["status"] == "succeeded"
        assert saved["output"]["artifact"]["task_id"] == "original"
        public = client.get(f"/api/workflow/projects/{project_id}").text
        assert "signature=private" not in public
        assert claimed["execution_token"] not in public
    finally:
        assert worker.stop_workflow_worker()


@pytest.mark.parametrize("boundary,want_status", [
    ("before_submit_checkpoint", "queued"),
    ("after_acceptance_before_id", "failed"),
    ("query", "queued"),
    ("download", "queued"),
    ("publish", "queued"),
])
def test_process_crash_never_duplicates_accepted_submission(workflow, monkeypatch, boundary, want_status):
    # Kill-style exceptions bypass normal cleanup, exactly the gap durable checkpoints cover.
    class ProcessCrash(BaseException):
        pass
    settings, store, _, task_id, source = workflow
    posts = []
    install_success_transport(monkeypatch)
    monkeypatch.setattr("app.seedance.requests.post", lambda *a, **kw: posts.append(a[0]) or response({"id": "original"}))
    checkpoint = store.checkpoint_stage_task
    download = SeedanceClient._download_result
    query = requests.get
    publish = worker.atomic_publish
    def crash(*args, **kwargs):
        raise ProcessCrash()
    if boundary in {"before_submit_checkpoint", "after_acceptance_before_id"}:
        def crashing_checkpoint(*args, **kwargs):
            phase = "submitting" if boundary == "before_submit_checkpoint" else "querying"
            if kwargs.get("phase") == phase:
                raise ProcessCrash()
            return checkpoint(*args, **kwargs)
        monkeypatch.setattr(store, "checkpoint_stage_task", crashing_checkpoint)
    elif boundary == "query":
        monkeypatch.setattr("app.seedance.requests.get", crash)
    elif boundary == "download":
        monkeypatch.setattr(SeedanceClient, "_download_result", staticmethod(crash))
    else:
        monkeypatch.setattr(worker, "atomic_publish", crash)
    with pytest.raises(ProcessCrash):
        run_generation(workflow)
    assert store.get_stage_task(task_id)["status"] == "running"
    reopened = WorkflowStore(store.database)
    worker.recover_workflow_tasks(reopened)
    assert reopened.get_stage_task(task_id)["status"] == want_status
    monkeypatch.setattr(store, "checkpoint_stage_task", checkpoint)
    monkeypatch.setattr("app.seedance.requests.get", query)
    monkeypatch.setattr(SeedanceClient, "_download_result", staticmethod(download))
    monkeypatch.setattr(worker, "atomic_publish", publish)
    if boundary not in {"before_submit_checkpoint", "after_acceptance_before_id"}:
        source.unlink()
    run_generation(workflow)
    assert len(posts) == 1
    if want_status == "queued":
        assert reopened.get_stage_task(task_id)["status"] == "succeeded"


def test_redaction_workers_share_the_same_atomic_claim(workflow, monkeypatch):
    settings, store, project_id, _, source = workflow
    task = store.create_stage_task(project_id, "redaction_render")
    start = Barrier(2)
    processed = []
    def process(source, output, settings, options):
        processed.append(str(source))
        output.write_bytes(b"redacted")
    monkeypatch.setattr(worker, "run_deface", process)
    def run(_):
        start.wait(3)
        worker.run_redaction_task(store, task["id"], project_id, str(source), "upload", settings=settings)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(run, range(2)))
    assert len(processed) == 1
    assert store.get_stage_task(task["id"])["status"] == "succeeded"


@pytest.mark.parametrize("mode,want_status", [("local", "queued"), ("http", "failed")])
def test_redaction_restart_repeats_only_local_processing(workflow, monkeypatch, mode, want_status):
    from app.redaction_service import ServiceConfig
    class ProcessCrash(BaseException):
        pass
    settings, store, project_id, _, source = workflow
    settings = replace(settings, redaction_service=ServiceConfig(mode=mode, endpoint="https://mask.example"))
    task = store.create_stage_task(project_id, "redaction_render")
    def crashed(*a, **kw):
        raise ProcessCrash()
    monkeypatch.setattr(worker, "run_deface", crashed)
    with pytest.raises(ProcessCrash):
        worker.run_redaction_task(store, task["id"], project_id, str(source), "upload", settings=settings)
    worker.recover_workflow_tasks(WorkflowStore(store.database))
    saved = store.get_stage_task(task["id"])
    assert saved["status"] == want_status
    if mode == "http":
        assert saved["provider_state"]["submission_uncertain"] is True


def test_graceful_stop_leaves_accepted_remote_work_queued_for_restart(workflow, monkeypatch):
    # Treating a normal shutdown as a permanent failure loses automatic recovery.
    settings, store, project_id, task_id, source = workflow
    monkeypatch.setattr("app.seedance.requests.post", lambda *a, **kw: response({"id": "remote"}))
    stop = Event()
    def query(*a, **kw):
        stop.set()
        return response({"status": "running"})
    monkeypatch.setattr("app.seedance.requests.get", query)
    worker.run_generation_task(store, task_id, project_id, str(source), None, None, "hello",
                               settings=settings, should_stop=stop.is_set)
    saved = store.get_stage_task(task_id)
    assert saved["status"] == "queued"
    assert saved["provider_state"]["task_id"] == "remote"
    assert saved["provider_state"].get("submission_uncertain") is not True


def test_resume_refreshes_expired_result_url_by_querying_original_task(workflow, monkeypatch):
    _, store, _, task_id, _ = workflow
    task = store.claim_stage_task(task_id)
    store.checkpoint_stage_task(task_id, task["execution_token"], phase="downloading", task_id="original",
                               result_url="https://media.example/expired")
    store.transition_stage_task(task_id, "failed")
    store.resume_stage_task(task_id)
    monkeypatch.setattr("app.seedance.requests.post", lambda *a, **kw: pytest.fail("Refresh must not submit"))
    def query(url, **kwargs):
        assert url == "https://provider.example/tasks/original"
        return response({"status": "succeeded", "output_url": "https://media.example/fresh"})
    def download(url, output):
        if url.endswith("/expired"):
            raise SeedanceError("expired")
        assert url == "https://media.example/fresh"
        assert store.get_stage_task(task_id)["provider_state"]["result_url"] == url
        output.write_bytes(b"fresh-result")
    monkeypatch.setattr("app.seedance.requests.get", query)
    monkeypatch.setattr(SeedanceClient, "_download_result", staticmethod(download))
    run_generation(workflow)
    assert store.get_stage_task(task_id)["status"] == "succeeded"


def test_restored_project_cannot_create_a_replacement_submission(workflow):
    _, store, project_id, task_id, _ = workflow
    with store._connection() as connection:
        connection.execute("UPDATE stage_tasks SET status='restore_held' WHERE id=?", (task_id,))
    with pytest.raises(ConflictError):
        store.create_stage_task(project_id, "generation", input_data={"prompt": "hello"}, idempotency_key="new-key")
    from app.workflow_router import _task_payload
    public = _task_payload(store.get_stage_task(task_id))
    assert public["requires_reconciliation"] is True
    assert public["can_resume"] is False
