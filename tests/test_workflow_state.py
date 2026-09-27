from app.workflow import ProjectStage, TaskState, can_transition, transition


def test_source_ingestion_gate_is_explicit():
    assert can_transition(ProjectStage.DRAFT, ProjectStage.SOURCE_INGESTING)
    assert not can_transition(ProjectStage.DRAFT, ProjectStage.READY_FOR_EXECUTION)
    assert transition(ProjectStage.DRAFT, ProjectStage.SOURCE_INGESTING) is ProjectStage.SOURCE_INGESTING


def test_material_branches_join_only_after_approval():
    assert can_transition(ProjectStage.REDACTION_APPROVED, ProjectStage.FACE_MATERIAL_REVIEW)
    assert can_transition(ProjectStage.REDACTION_APPROVED, ProjectStage.GARMENT_MATERIAL_REVIEW)
    assert not can_transition(ProjectStage.REDACTION_REVIEW, ProjectStage.FACE_MATERIAL_REVIEW)


def test_task_states_keep_retry_separate_from_terminal_failure():
    assert TaskState.RETRY_WAIT.value != TaskState.FAILED.value
    assert can_transition(TaskState.RUNNING, TaskState.RETRY_WAIT)
    assert can_transition(TaskState.RETRY_WAIT, TaskState.QUEUED)

