"""HTTP adapter for the durable staged workflow.

The legacy /api/jobs API remains available. These routes use the durable
WorkflowStore and expose explicit project, asset, review, task, and snapshot
operations for the new manual-production flow.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from threading import Thread
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .media_validation import validate_media, VIDEO_SUFFIXES
from .workflow import ProjectStage
from .workflow_worker import run_generation_task, run_redaction_task
from .workflow_store import (
    ConflictError,
    InvalidTransitionError,
    NotFoundError,
    WorkflowStore,
)


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class SourceIn(BaseModel):
    kind: Literal["upload", "url"] | None = None
    source_type: Literal["upload", "url"] | None = None
    uri: str | None = None
    url: str | None = None
    path: str | None = None
    sha256: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class MaterialIn(BaseModel):
    kind: Literal["face", "garment"]
    uri: str | None = None
    path: str | None = None
    sha256: str | None = None
    mime_type: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False
    idempotency_key: str | None = None


class TaskIn(BaseModel):
    stage: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class TransitionIn(BaseModel):
    stage: ProjectStage
    actor: str = "operator"
    reason: str | None = None


class ReviewIn(BaseModel):
    approved: bool = True
    revision: str | None = None
    track: str | None = None
    reviewer: str = "operator"
    note: str | None = None
    idempotency_key: str | None = None


class RedactionRunIn(BaseModel):
    style: str = "mosaic"
    shape: str = "ellipse"
    mask_mode: str = "face"
    robust_tracking: bool = False
    mask_scale: float = Field(default=1.4, gt=0)
    mosaic_size: int = Field(default=20, gt=0)
    threshold: float = Field(default=0.2, ge=0, le=1)
    detection_size: int | None = Field(default=None, gt=0)
    keep_audio: bool = True
    idempotency_key: str | None = None

class GenerationRunIn(BaseModel):
    prompt: str = "保持原视频动作、镜头和节奏，使用参考人脸和服装生成稳定自然的视频。"
    idempotency_key: str | None = None

class SnapshotIn(BaseModel):
    run_id: str | None = None
    version: int = Field(default=1, ge=1)
    idempotency_key: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _project_payload(store: WorkflowStore, project_id: str) -> dict[str, Any]:
    project = store.get_project(project_id)
    project["source_assets"] = store.list_assets(project_id)
    project["reference_assets"] = store.list_assets(project_id, references=True)
    project["tasks"] = store.list_stage_tasks(project_id)
    project["stage"] = (project.get("metadata") or {}).get("stage", ProjectStage.DRAFT.value)
    return project


def _not_found(error: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(error))


def get_router(store: WorkflowStore | None = None) -> APIRouter:
    db = store or WorkflowStore(Path(os.getenv("WORKFLOW_DB", "storage/workflow.db")))
    router = APIRouter(prefix="/api/workflow", tags=["workflow"])

    @router.post("/projects")
    def create_project(payload: ProjectIn):
        try:
            project = db.create_project(
                payload.name,
                metadata={"stage": ProjectStage.DRAFT.value, **payload.metadata},
                idempotency_key=payload.idempotency_key,
            )
            return _project_payload(db, project["id"])
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.get("/projects/{project_id}")
    def get_project(project_id: str):
        try:
            return _project_payload(db, project_id)
        except NotFoundError as exc:
            raise _not_found(exc) from exc

    @router.post("/projects/{project_id}/stage")
    def transition_project(project_id: str, payload: TransitionIn):
        try:
            db.transition_project(
                project_id,
                payload.stage.value,
                actor=payload.actor,
                reason=payload.reason,
            )
            return _project_payload(db, project_id)
        except NotFoundError as exc:
            raise _not_found(exc) from exc
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/projects/{project_id}/source-upload")
    def upload_source(
        project_id: str,
        video: UploadFile = File(...),
        idempotency_key: str | None = Form(default=None),
        rights_status: str = Form(default="unknown"),
    ):
        try:
            db.get_project(project_id)
        except NotFoundError as exc:
            raise _not_found(exc) from exc
        filename = Path(video.filename or "source.mp4")
        suffix = filename.suffix.lower()
        content_type = video.content_type or "application/octet-stream"
        if suffix not in VIDEO_SUFFIXES:
            raise HTTPException(status_code=415, detail="only video uploads are supported")
        max_bytes = int(os.getenv("WORKFLOW_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
        root = Path(os.getenv("WORKFLOW_STORAGE", "storage/workflow")) / project_id / "sources"
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"source-{os.urandom(8).hex()}{suffix}"
        digest = hashlib.sha256()
        total = 0
        try:
            with target.open("wb") as stream:
                while True:
                    chunk = video.file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(status_code=413, detail="upload exceeds workflow size limit")
                    digest.update(chunk)
                    stream.write(chunk)
            validate_media(target, "video", max_bytes)
            asset = db.create_source_asset(
                project_id,
                "upload",
                str(target),
                sha256=digest.hexdigest(),
                mime_type=content_type,
                size_bytes=total,
                metadata={"original_filename": video.filename, "rights_status": rights_status},
                idempotency_key=idempotency_key,
            )
            return {"asset": asset, "project": _project_payload(db, project_id)}
        except HTTPException:
            target.unlink(missing_ok=True)
            raise
        except (ConflictError, ValueError) as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="素材保存失败，请稍后重试。") from exc

    @router.post("/projects/{project_id}/source")
    def add_source(project_id: str, payload: SourceIn):
        kind = payload.kind or payload.source_type or ("url" if payload.url else "upload")
        uri = payload.uri or payload.url or payload.path
        if not uri:
            raise HTTPException(status_code=422, detail="source uri/url/path is required")
        try:
            asset = db.create_source_asset(
                project_id,
                kind,
                uri,
                sha256=payload.sha256,
                mime_type=payload.mime_type,
                size_bytes=payload.size_bytes,
                metadata=payload.metadata,
                idempotency_key=payload.idempotency_key,
            )
            return {"asset": asset, "project": _project_payload(db, project_id)}
        except NotFoundError as exc:
            raise _not_found(exc) from exc
        except (ConflictError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/projects/{project_id}/tasks")
    def enqueue_task(project_id: str, payload: TaskIn):
        try:
            task = db.create_stage_task(
                project_id,
                payload.stage,
                input_data=payload.input_data,
                idempotency_key=payload.idempotency_key,
            )
            return {"task": task, "project": _project_payload(db, project_id)}
        except NotFoundError as exc:
            raise _not_found(exc) from exc
        except ConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/projects/{project_id}/redaction/render")
    def render_redaction(project_id: str, payload: RedactionRunIn):
        try:
            project = _project_payload(db, project_id)
            if not project["source_assets"]:
                raise HTTPException(status_code=409, detail="source asset is required before redaction")
            source = project["source_assets"][-1]
            task = db.create_stage_task(
                project_id,
                "redaction_render",
                input_data={"source_asset_id": source["id"], "options": payload.model_dump()},
                idempotency_key=payload.idempotency_key,
            )
            if task["status"] == "queued":
                db.transition_stage_task(task["id"], "running")
                Thread(
                    target=run_redaction_task,
                    args=(
                        db,
                        task["id"],
                        project_id,
                        source["uri"],
                        source["kind"],
                        payload.model_dump(),
                    ),
                    daemon=True,
                ).start()
            return {"task": db.get_stage_task(task["id"]), "project": _project_payload(db, project_id)}
        except NotFoundError as exc:
            raise _not_found(exc) from exc
        except (ConflictError, InvalidTransitionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/projects/{project_id}/redaction/review")
    def redaction_review(project_id: str, payload: ReviewIn):
        decision = "approved" if payload.approved else "changes_requested"
        target = (
            ProjectStage.REDACTION_APPROVED
            if payload.approved
            else ProjectStage.REDACTION_EDITING
        )
        try:
            db.create_review_decision(
                project_id,
                ProjectStage.REDACTION_REVIEW.value,
                decision,
                reviewer=payload.reviewer,
                note=payload.note,
                payload={"revision": payload.revision, "track": payload.track},
                idempotency_key=payload.idempotency_key,
            )
            db.transition_project(project_id, target.value, actor=payload.reviewer, reason=payload.note)
            return _project_payload(db, project_id)
        except NotFoundError as exc:
            raise _not_found(exc) from exc
        except (ConflictError, InvalidTransitionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/projects/{project_id}/materials")
    def add_material(project_id: str, payload: MaterialIn):
        uri = payload.uri or payload.path
        if not uri:
            raise HTTPException(status_code=422, detail="material uri/path is required")
        try:
            asset = db.create_reference_asset(
                project_id,
                payload.kind,
                uri,
                sha256=payload.sha256,
                mime_type=payload.mime_type,
                metadata={"approved": payload.approved, **payload.metadata},
                idempotency_key=payload.idempotency_key,
            )
            if payload.approved:
                target = (
                    ProjectStage.FACE_MATERIAL_REVIEW
                    if payload.kind == "face"
                    else ProjectStage.GARMENT_MATERIAL_REVIEW
                )
                current = (_project_payload(db, project_id).get("stage") or ProjectStage.DRAFT.value)
                if current == ProjectStage.REDACTION_APPROVED.value:
                    db.transition_project(project_id, target.value, actor="operator")
            return {"asset": asset, "project": _project_payload(db, project_id)}
        except NotFoundError as exc:
            raise _not_found(exc) from exc
        except (ConflictError, InvalidTransitionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post("/projects/{project_id}/material-upload")
    def upload_material(
        project_id: str,
        kind: Literal["face", "garment"] = Form(...),
        material: UploadFile = File(...),
        approved: bool = Form(default=True),
        idempotency_key: str | None = Form(default=None),
    ):
        try:
            db.get_project(project_id)
        except NotFoundError as exc:
            raise _not_found(exc) from exc
        filename = Path(material.filename or "material.bin")
        suffix = filename.suffix.lower()
        allowed = {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mov"}
        if suffix not in allowed:
            raise HTTPException(status_code=415, detail="unsupported material format")
        content_type = material.content_type or "application/octet-stream"
        if not (content_type.startswith("image/") or content_type.startswith("video/")):
            raise HTTPException(status_code=415, detail="face and garment assets must be images or videos")
        max_bytes = int(os.getenv("WORKFLOW_MAX_REFERENCE_BYTES", str(20 * 1024 * 1024)))
        root = Path(os.getenv("WORKFLOW_STORAGE", "storage/workflow")) / project_id / "materials" / kind
        root.mkdir(parents=True, exist_ok=True)
        target = root / f"{kind}-{os.urandom(8).hex()}{suffix}"
        digest = hashlib.sha256()
        total = 0
        try:
            with target.open("wb") as stream:
                while True:
                    chunk = material.file.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(status_code=413, detail="reference asset exceeds size limit")
                    digest.update(chunk)
                    stream.write(chunk)
            validate_media(target, "video" if suffix in VIDEO_SUFFIXES else "image", max_bytes)
            asset = db.create_reference_asset(
                project_id,
                kind,
                str(target),
                sha256=digest.hexdigest(),
                mime_type=content_type,
                metadata={"approved": approved, "original_filename": material.filename, "size_bytes": total},
                idempotency_key=idempotency_key,
            )
            return {"asset": asset, "project": _project_payload(db, project_id)}
        except HTTPException:
            target.unlink(missing_ok=True)
            raise
        except (ConflictError, ValueError) as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="素材保存失败，请稍后重试。") from exc

    @router.get("/projects/{project_id}/tasks/{task_id}")
    def get_task(project_id: str, task_id: str):
        try:
            task = db.get_stage_task(task_id)
            if task["project_id"] != project_id:
                raise NotFoundError(f"task not found: {task_id}")
            return task
        except NotFoundError as exc:
            raise _not_found(exc) from exc

    @router.get("/projects/{project_id}/tasks/{task_id}/artifact")
    def get_task_artifact(project_id: str, task_id: str):
        try:
            task = db.get_stage_task(task_id)
            if task["project_id"] != project_id:
                raise NotFoundError(f"task not found: {task_id}")
            artifact = (task.get("output") or {}).get("artifact") or {}
            path = Path(artifact.get("path", ""))
            if task["status"] != "succeeded" or not path.is_file():
                raise NotFoundError("artifact is not ready")
            return FileResponse(path, media_type="video/mp4", filename=f"{task_id}.mp4")
        except NotFoundError as exc:
            raise _not_found(exc) from exc

    @router.post("/projects/{project_id}/execute")
    def execute_project(project_id: str, payload: GenerationRunIn):
        try:
            project = _project_payload(db, project_id)
            reference = {
                asset["kind"]: asset
                for asset in project["reference_assets"]
                if (asset.get("metadata") or {}).get("approved") is True
            }
            if "face" not in reference or "garment" not in reference:
                raise HTTPException(status_code=409, detail="approved face and garment assets are required")
            redaction = next(
                (
                    task for task in reversed(project["tasks"])
                    if task["stage"] == "redaction_render" and task["status"] == "succeeded"
                ),
                None,
            )
            if not redaction:
                raise HTTPException(status_code=409, detail="a completed redaction task is required")
            artifact = (redaction.get("output") or {}).get("artifact") or {}
            if not artifact.get("path"):
                raise HTTPException(status_code=409, detail="redaction artifact is missing")
            task = db.create_stage_task(
                project_id,
                "generation",
                input_data={
                    "redaction_task_id": redaction["id"],
                    "face_asset_id": reference["face"]["id"],
                    "garment_asset_id": reference["garment"]["id"],
                    "prompt": payload.prompt,
                },
                idempotency_key=payload.idempotency_key,
            )
            if task["status"] == "queued":
                db.transition_stage_task(task["id"], "running")
                Thread(
                    target=run_generation_task,
                    args=(
                        db,
                        task["id"],
                        project_id,
                        artifact["path"],
                        reference["face"]["uri"],
                        reference["garment"]["uri"],
                        payload.prompt,
                    ),
                    daemon=True,
                ).start()
            return {"task": db.get_stage_task(task["id"]), "project": project}
        except NotFoundError as exc:
            raise _not_found(exc) from exc
        except (ConflictError, InvalidTransitionError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    @router.post("/projects/{project_id}/snapshot")
    def snapshot(project_id: str, payload: SnapshotIn):
        try:
            project = _project_payload(db, project_id)
            stage = project["stage"]
            if not project["source_assets"]:
                raise HTTPException(status_code=409, detail="source asset is required before snapshot")
            kinds = {asset.get("kind") for asset in project["reference_assets"]}
            approved = {asset.get("kind") for asset in project["reference_assets"] if (asset.get("metadata") or {}).get("approved") is True}
            if "face" not in kinds or "garment" not in kinds or not {"face", "garment"}.issubset(approved):
                raise HTTPException(status_code=409, detail="approved face and garment assets are required before snapshot")
            if stage != ProjectStage.READY_FOR_EXECUTION.value:
                allowed = {
                    ProjectStage.REDACTION_APPROVED.value,
                    ProjectStage.FACE_MATERIAL_REVIEW.value,
                    ProjectStage.GARMENT_MATERIAL_REVIEW.value,
                    ProjectStage.MATERIAL_REVIEW.value,
                }
                if stage not in allowed:
                    raise HTTPException(status_code=409, detail=f"project is not ready for snapshot: {stage}")
                db.transition_project(project_id, ProjectStage.READY_FOR_EXECUTION.value, actor="operator")
                project = _project_payload(db, project_id)
            run_id = payload.run_id or project_id
            snapshot = {
                "project_id": project_id,
                "source_assets": project["source_assets"],
                "reference_assets": project["reference_assets"],
                "stage": project["stage"],
                "metadata": payload.metadata,
            }
            result = db.create_run_snapshot(
                project_id,
                run_id,
                snapshot,
                version=payload.version,
                idempotency_key=payload.idempotency_key,
            )
            return {"snapshot": result, "project": project}
        except NotFoundError as exc:
            raise _not_found(exc) from exc
        except (ConflictError, InvalidTransitionError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return router
