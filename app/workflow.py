"""Durable workflow domain primitives for the manual video pipeline."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ProjectStage(str, Enum):
    DRAFT = "DRAFT"
    SOURCE_INGESTING = "SOURCE_INGESTING"
    SOURCE_REVIEW = "SOURCE_REVIEW"
    SOURCE_READY = "SOURCE_READY"
    REDACTION_EDITING = "REDACTION_EDITING"
    REDACTION_REVIEW = "REDACTION_REVIEW"
    REDACTION_APPROVED = "REDACTION_APPROVED"
    FACE_MATERIAL_REVIEW = "FACE_MATERIAL_REVIEW"
    GARMENT_MATERIAL_REVIEW = "GARMENT_MATERIAL_REVIEW"
    MATERIAL_REVIEW = "MATERIAL_REVIEW"
    READY_FOR_EXECUTION = "READY_FOR_EXECUTION"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    RETRY_WAIT = "RETRY_WAIT"
    RETRYABLE_FAILED = "RETRYABLE_FAILED"
    TERMINAL_FAILED = "TERMINAL_FAILED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


_TRANSITIONS: dict[ProjectStage, set[ProjectStage]] = {
    ProjectStage.DRAFT: {ProjectStage.SOURCE_INGESTING},
    ProjectStage.SOURCE_INGESTING: {ProjectStage.SOURCE_REVIEW, ProjectStage.FAILED},
    ProjectStage.SOURCE_REVIEW: {ProjectStage.SOURCE_READY, ProjectStage.SOURCE_INGESTING},
    ProjectStage.SOURCE_READY: {ProjectStage.REDACTION_EDITING},
    ProjectStage.REDACTION_EDITING: {ProjectStage.REDACTION_REVIEW},
    ProjectStage.REDACTION_REVIEW: {ProjectStage.REDACTION_EDITING, ProjectStage.REDACTION_APPROVED},
    ProjectStage.REDACTION_APPROVED: {
        ProjectStage.FACE_MATERIAL_REVIEW,
        ProjectStage.GARMENT_MATERIAL_REVIEW,
        ProjectStage.MATERIAL_REVIEW,
        ProjectStage.READY_FOR_EXECUTION,
    },
    ProjectStage.FACE_MATERIAL_REVIEW: {
        ProjectStage.GARMENT_MATERIAL_REVIEW,
        ProjectStage.MATERIAL_REVIEW,
        ProjectStage.READY_FOR_EXECUTION,
        ProjectStage.REDACTION_APPROVED,
    },
    ProjectStage.GARMENT_MATERIAL_REVIEW: {
        ProjectStage.FACE_MATERIAL_REVIEW,
        ProjectStage.MATERIAL_REVIEW,
        ProjectStage.READY_FOR_EXECUTION,
        ProjectStage.REDACTION_APPROVED,
    },
    ProjectStage.MATERIAL_REVIEW: {
        ProjectStage.FACE_MATERIAL_REVIEW,
        ProjectStage.GARMENT_MATERIAL_REVIEW,
        ProjectStage.READY_FOR_EXECUTION,
        ProjectStage.REDACTION_APPROVED,
    },
    ProjectStage.READY_FOR_EXECUTION: {ProjectStage.EXECUTING},
    ProjectStage.EXECUTING: {ProjectStage.COMPLETED, ProjectStage.FAILED, ProjectStage.READY_FOR_EXECUTION},
    ProjectStage.FAILED: {ProjectStage.SOURCE_INGESTING, ProjectStage.READY_FOR_EXECUTION},
    ProjectStage.COMPLETED: set(),
}


_TASK_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.QUEUED: {TaskState.RUNNING, TaskState.CANCELED},
    TaskState.RUNNING: {
        TaskState.SUCCEEDED,
        TaskState.RETRY_WAIT,
        TaskState.RETRYABLE_FAILED,
        TaskState.TERMINAL_FAILED,
        TaskState.FAILED,
        TaskState.CANCELED,
    },
    TaskState.RETRY_WAIT: {TaskState.QUEUED, TaskState.CANCELED},
    TaskState.RETRYABLE_FAILED: {TaskState.QUEUED, TaskState.CANCELED},
    TaskState.TERMINAL_FAILED: set(),
    TaskState.FAILED: {TaskState.QUEUED, TaskState.CANCELED},
    TaskState.SUCCEEDED: set(),
    TaskState.CANCELED: set(),
}


def can_transition(current: ProjectStage | TaskState | str, target: ProjectStage | TaskState | str) -> bool:
    enum_type = ProjectStage if isinstance(current, ProjectStage) or str(current) in {item.value for item in ProjectStage} else TaskState
    try:
        current_value = enum_type(current)
        target_value = enum_type(target)
    except ValueError:
        return False
    if enum_type is ProjectStage:
        return target_value in _TRANSITIONS.get(current_value, set())
    return target_value in _TASK_TRANSITIONS.get(current_value, set())


def transition(current: ProjectStage | str, target: ProjectStage | str) -> ProjectStage:
    current_value, target_value = ProjectStage(current), ProjectStage(target)
    if not can_transition(current_value, target_value):
        raise ValueError(f"invalid project transition: {current_value.value} -> {target_value.value}")
    return target_value


def transition_task(current: TaskState | str, target: TaskState | str) -> TaskState:
    current_value, target_value = TaskState(current), TaskState(target)
    if not can_transition(current_value, target_value):
        raise ValueError(f"invalid task transition: {current_value.value} -> {target_value.value}")
    return target_value


@dataclass(frozen=True)
class WorkflowEvent:
    project_id: str
    from_stage: ProjectStage
    to_stage: ProjectStage
    actor: str = "system"
    reason: str | None = None


def validate_required_materials(
    *, source_ready: bool, redaction_approved: bool, face_approved: bool, garment_approved: bool
) -> bool:
    return all((source_ready, redaction_approved, face_approved, garment_approved))


def next_material_stage(face_approved: bool, garment_approved: bool) -> ProjectStage:
    return ProjectStage.READY_FOR_EXECUTION if face_approved and garment_approved else ProjectStage.MATERIAL_REVIEW