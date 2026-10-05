"""Adapters that run the existing media engine from durable workflow tasks."""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Mapping

from .artifacts import atomic_publish, sha256_file
from .config import Settings
from .media import BlurOptions, download_video, run_deface
from .seedance import SeedanceClient
from .redaction_service import freeze as freeze_redaction
from .workflow_store import ConflictError, WorkflowStore
from .security import safe_error, configured_secrets

_log = logging.getLogger(__name__)
_dispatchers: dict[str, "_Dispatcher"] = {}
_dispatchers_lock = RLock()


def _claim(store: WorkflowStore, task_id: str, project_id: str, settings: Settings,
           execution_token: str | None) -> dict[str, Any] | None:
    from .recovery_guard import is_restore_held
    if is_restore_held(settings):
        return None
    task = store.get_stage_task(task_id)
    if task["project_id"] != project_id:
        raise ConflictError("task belongs to a different project")
    if execution_token:
        if task["status"] != "running" or task["execution_token"] != execution_token:
            return None
        return task
    return store.claim_stage_task(task_id)


def run_redaction_task(
    store: WorkflowStore,
    task_id: str,
    project_id: str,
    source_uri: str,
    source_kind: str,
    options: Mapping[str, Any] | None = None,
    *,
    settings: Settings | None = None,
    execution_token: str | None = None,
) -> dict[str, Any]:
    """Run the current deface backend and persist a verified derived artifact.

    The source asset is never overwritten. A failed media command leaves the
    task in a durable failed state with a structured error payload.
    """
    settings = settings or Settings.from_env()
    task = _claim(store, task_id, project_id, settings, execution_token)
    if task is None:
        return (store.get_stage_task(task_id).get("output") or {}).get("artifact") or {}
    work_dir = settings.storage_dir / "workflow" / project_id / "redaction" / task_id
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        source_path = Path(source_uri)
        if source_kind == "url":
            source_path = download_video(source_uri, work_dir / "source.mp4", settings)
        if not source_path.exists():
            raise FileNotFoundError(f"source asset does not exist: {source_path}")
        raw_options = dict(options or {})
        raw_options.pop("idempotency_key", None)
        blur_options = BlurOptions(**raw_options)
        temporary = work_dir / "redacted.tmp.mp4"
        output = work_dir / "redacted.mp4"
        # Freeze the same service configuration that run_deface will consume.
        settings = freeze_redaction(settings)
        remote = settings.redaction_service.mode == "http"
        store.checkpoint_stage_task(task_id, task["execution_token"],
                                    phase="submitting" if remote else "local_processing")
        run_deface(source_path, temporary, settings, blur_options)
        published = atomic_publish(temporary, output)
        artifact = {
            "path": str(published),
            "sha256": sha256_file(published),
            "source_uri": source_uri,
            "processor": settings.deface_bin if blur_options.mask_mode == "face" else "local_mosaic",
            "options": raw_options,
        }
        store.transition_stage_task(task_id, "succeeded", output_data={"artifact": artifact})
        return artifact
    except Exception as error:
        current = store.get_stage_task(task_id)
        uncertain = current["provider_state"].get("phase") == "submitting"
        if uncertain:
            store.checkpoint_stage_task(task_id, task["execution_token"], submission_uncertain=True)
        store.transition_stage_task(
            task_id,
            "failed",
            error_data={"type": type(error).__name__, "message": (
                "外部打码处理结果无法确认，已停止自动提交，请先核对服务商记录。"
                if uncertain else safe_error(error, configured_secrets(settings)))},
        )
        raise


def recover_workflow_tasks(store: WorkflowStore) -> dict[str, int]:
    """Called once by the exclusive dispatcher at ordinary process startup."""
    return store.recover_stage_tasks()


def _execute_record(store: WorkflowStore, task: dict[str, Any], settings: Settings, stop: Event) -> None:
    """Reconstruct execution entirely from durable references, never a request closure."""
    try:
        project_id = task["project_id"]
        data = task["input"]
        if task["stage"] == "redaction_render":
            source = next(asset for asset in store.list_assets(project_id) if asset["id"] == data["source_asset_id"])
            run_redaction_task(store, task["id"], project_id, source["uri"], source["kind"], data.get("options"),
                               settings=settings, execution_token=task["execution_token"])
        else:
            remote = task["provider_state"].get("task_id") or task["provider_state"].get("result_url")
            source, face, garment = "", None, None
            if not remote:
                redaction = store.get_stage_task(data["redaction_task_id"])
                if redaction["project_id"] != project_id or redaction["status"] != "succeeded":
                    raise ConflictError("redaction artifact is not available for this project")
                source = redaction["output"]["artifact"]["path"]
                assets = {asset["id"]: asset for asset in store.list_assets(project_id, references=True)}
                face = assets[data["face_asset_id"]]["uri"]
                garment = assets[data["garment_asset_id"]]["uri"]
            run_generation_task(store, task["id"], project_id, source, face, garment, data.get("prompt", ""),
                                settings=settings, execution_token=task["execution_token"], should_stop=stop.is_set)
    except Exception as error:
        # Input resolution and thread startup can fail before the worker's handler.
        current = store.get_stage_task(task["id"])
        if current["status"] == "running":
            state = current["provider_state"]
            uncertain = state.get("phase") == "submitting" and not state.get("task_id")
            if uncertain:
                store.checkpoint_stage_task(task["id"], task["execution_token"], submission_uncertain=True)
            store.transition_stage_task(task["id"], "failed", error_data={
                "type": type(error).__name__, "message": safe_error(error, configured_secrets(settings))})


class _Dispatcher:
    def __init__(self, store: WorkflowStore, settings: Settings):
        self.store, self.settings = store, settings
        self.stop = Event()
        self.wake = Event()
        self.children: set[Thread] = set()
        self.lockfile = None
        self.thread = Thread(target=self.loop, daemon=True, name="legacy-workflow-dispatcher")

    def acquire(self) -> bool:
        handle = open(self.store.database + ".worker.lock", "a+b")
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self.lockfile = handle
        return True

    def dispatch(self, task_id: str) -> None:
        from .recovery_guard import is_restore_held
        with _dispatchers_lock:
            if self.stop.is_set() or is_restore_held(self.settings):
                return
            task = self.store.claim_stage_task(task_id)
            if task is None:
                return
            self.children = {thread for thread in self.children if thread.is_alive()}
            thread = Thread(target=_execute_record, args=(self.store, task, self.settings, self.stop),
                            daemon=True, name="legacy-workflow-task")
            self.children.add(thread)
            try:
                thread.start()
            except Exception:
                self.children.discard(thread)
                self.store.transition_stage_task(task_id, "failed", error_data={"message": "后台启动失败，请稍后重启服务恢复。"})
                self.store.transition_stage_task(task_id, "queued")
                raise

    def loop(self) -> None:
        from .recovery_guard import is_restore_held
        while not self.stop.is_set():
            try:
                if not is_restore_held(self.settings):
                    for task in self.store.queued_stage_tasks():
                        self.dispatch(task["id"])
            except Exception:
                # Keep the loop alive for transient local database failures.
                _log.error("Legacy workflow dispatcher could not process queued work")
            self.wake.wait(.5)
            self.wake.clear()


def start_workflow_worker(settings: Settings, store: WorkflowStore | None = None) -> Thread | None:
    """Start at most one dispatcher per DB, including across OS processes."""
    from .recovery_guard import is_restore_held
    if is_restore_held(settings):
        return None
    store = store or WorkflowStore(Path(os.getenv("WORKFLOW_DB", "storage/workflow.db")))
    key = str(Path(store.database).resolve())
    with _dispatchers_lock:
        existing = _dispatchers.get(key)
        if existing:
            if not existing.stop.is_set():
                return existing.thread
            if existing.thread.is_alive() or any(thread.is_alive() for thread in existing.children):
                return None
            existing.lockfile.close()
            del _dispatchers[key]
        dispatcher = _Dispatcher(store, settings)
        if not dispatcher.acquire():
            return None
        try:
            recover_workflow_tasks(store)
            _dispatchers[key] = dispatcher
            dispatcher.thread.start()
        except Exception:
            _dispatchers.pop(key, None)
            dispatcher.lockfile.close()
            raise
        return dispatcher.thread


def dispatch_workflow_task(store: WorkflowStore, task_id: str, settings: Settings | None = None) -> None:
    """Wake the owner and eagerly claim the requested task when this process owns it."""
    settings = settings or Settings.from_env()
    if start_workflow_worker(settings, store) is None:
        return
    with _dispatchers_lock:
        dispatcher = _dispatchers.get(str(Path(store.database).resolve()))
        if dispatcher:
            dispatcher.dispatch(task_id)
            dispatcher.wake.set()


def stop_workflow_worker(timeout: float = 5.0) -> bool:
    """Stop all legacy dispatchers; retain locks while any child is still active."""
    deadline = time.monotonic() + max(0, timeout)
    with _dispatchers_lock:
        items = list(_dispatchers.items())
        for _, dispatcher in items:
            dispatcher.stop.set()
            dispatcher.wake.set()
    for _, dispatcher in items:
        for thread in [dispatcher.thread, *dispatcher.children]:
            thread.join(max(0, deadline - time.monotonic()))
    with _dispatchers_lock:
        for key, dispatcher in items:
            if not dispatcher.thread.is_alive() and not any(thread.is_alive() for thread in dispatcher.children):
                dispatcher.lockfile.close()
                _dispatchers.pop(key, None)
        return not _dispatchers


def run_generation_task(
    store: WorkflowStore,
    task_id: str,
    project_id: str,
    video_path: str,
    face_path: str | None,
    garment_path: str | None,
    prompt: str,
    *,
    settings: Settings | None = None,
    execution_token: str | None = None,
    should_stop: Any = None,
) -> dict[str, Any]:
    """Run the existing Seedance adapter as a durable workflow task."""
    settings = settings or Settings.from_env()
    task = _claim(store, task_id, project_id, settings, execution_token)
    if task is None:
        return (store.get_stage_task(task_id).get("output") or {}).get("artifact") or {}
    token = task["execution_token"]
    provider_state = task["provider_state"]
    work_dir = settings.storage_dir / "workflow" / project_id / "generation" / task_id
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        source = Path(video_path)
        face = Path(face_path) if face_path else None
        garment = Path(garment_path) if garment_path else None
        resuming = provider_state.get("task_id") or provider_state.get("result_url")
        if resuming:
            endpoint = provider_state.get("endpoint")
            if endpoint and endpoint != settings.seedance_api_url.rstrip("/"):
                raise ConflictError("服务商地址已变更，请恢复原配置后继续查询原任务。")
        else:
            if not source.exists():
                raise FileNotFoundError(f"redacted artifact does not exist: {source}")
            if face and not face.exists():
                raise FileNotFoundError(f"face asset does not exist: {face}")
            if garment and not garment.exists():
                raise FileNotFoundError(f"garment asset does not exist: {garment}")
        temporary = work_dir / "result.tmp.mp4"
        output = work_dir / "result.mp4"
        from .recovery_guard import is_restore_held
        def stopped() -> bool:
            return is_restore_held(settings) or bool(should_stop and should_stop())
        result = SeedanceClient(settings).generate(
            source, face, garment, prompt, temporary,
            on_submitting=lambda: store.checkpoint_stage_task(
                task_id, token, phase="submitting", endpoint=settings.seedance_api_url.rstrip("/")),
            on_submitted=lambda remote_id: store.checkpoint_stage_task(
                task_id, token, phase="querying", task_id=remote_id, submission_uncertain=False),
            on_result=lambda url: store.checkpoint_stage_task(task_id, token, phase="downloading", result_url=url),
            resume_task_id=provider_state.get("task_id"), resume_result_url=provider_state.get("result_url"),
            should_stop=stopped,
        )
        published = atomic_publish(temporary, output)
        artifact = {
            "path": str(published),
            "sha256": sha256_file(published),
            "provider": result.get("provider"),
            "task_id": result.get("task_id"),
            "prompt": prompt,
            "source_uri": video_path,
        }
        store.transition_stage_task(task_id, "succeeded", output_data={"artifact": artifact, "provider_result": result})
        return artifact
    except Exception as error:
        state = store.get_stage_task(task_id)["provider_state"]
        uncertain = state.get("phase") == "submitting" and not state.get("task_id")
        if should_stop and should_stop() and not uncertain:
            store.release_stage_task(task_id, token)
            return {}
        if uncertain:
            store.checkpoint_stage_task(task_id, token, submission_uncertain=True)
        store.transition_stage_task(
            task_id,
            "failed",
            error_data={"type": type(error).__name__, "message": (
                "提交结果无法确认，已停止自动提交。请先在服务商控制台核对，避免重复扣费。"
                if uncertain else safe_error(error, configured_secrets(settings)))},
        )
        raise
