"""Adapters that run the existing media engine from durable workflow tasks."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .artifacts import atomic_publish, sha256_file
from .config import Settings
from .media import BlurOptions, download_video, run_deface
from .seedance import SeedanceClient
from .workflow_store import WorkflowStore


def run_redaction_task(
    store: WorkflowStore,
    task_id: str,
    project_id: str,
    source_uri: str,
    source_kind: str,
    options: Mapping[str, Any] | None = None,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Run the current deface backend and persist a verified derived artifact.

    The source asset is never overwritten. A failed media command leaves the
    task in a durable failed state with a structured error payload.
    """
    settings = settings or Settings.from_env()
    work_dir = settings.storage_dir / "workflow" / project_id / "redaction" / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        current_task = store.get_stage_task(task_id)
        if current_task["status"] == "queued":
            store.transition_stage_task(task_id, "running")
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
        store.transition_stage_task(
            task_id,
            "failed",
            error_data={"type": type(error).__name__, "message": str(error)},
        )
        raise
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
) -> dict[str, Any]:
    """Run the existing Seedance adapter as a durable workflow task."""
    settings = settings or Settings.from_env()
    work_dir = settings.storage_dir / "workflow" / project_id / "generation" / task_id
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        current_task = store.get_stage_task(task_id)
        if current_task["status"] == "queued":
            store.transition_stage_task(task_id, "running")
        source = Path(video_path)
        if not source.exists():
            raise FileNotFoundError(f"redacted artifact does not exist: {source}")
        face = Path(face_path) if face_path else None
        garment = Path(garment_path) if garment_path else None
        if face and not face.exists():
            raise FileNotFoundError(f"face asset does not exist: {face}")
        if garment and not garment.exists():
            raise FileNotFoundError(f"garment asset does not exist: {garment}")
        temporary = work_dir / "result.tmp.mp4"
        output = work_dir / "result.mp4"
        result = SeedanceClient(settings).generate(source, face, garment, prompt, temporary)
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
        store.transition_stage_task(
            task_id,
            "failed",
            error_data={"type": type(error).__name__, "message": str(error)},
        )
        raise
