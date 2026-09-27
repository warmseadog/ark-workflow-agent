"""Durable workflow storage for the video redaction pipeline.

The module deliberately uses only the Python standard library so it can be
mounted behind FastAPI, a CLI, or a background worker without coupling the
workflow model to the current HTTP layer.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


class WorkflowStoreError(Exception):
    """Base exception for workflow persistence errors."""


class NotFoundError(WorkflowStoreError):
    """Raised when a requested workflow object does not exist."""


class ConflictError(WorkflowStoreError):
    """Raised when an idempotency key or unique value is already in use."""


class InvalidTransitionError(WorkflowStoreError):
    """Raised when a state transition is not allowed."""


STAGE_STATUSES = ("queued", "running", "succeeded", "failed", "cancelled")
PROJECT_STATUSES = ("draft", "active", "paused", "completed", "archived")
REVIEW_DECISIONS = ("approved", "rejected", "changes_requested")

_STAGE_TRANSITIONS = {
    "queued": {"running", "cancelled"},
    "running": {"succeeded", "failed", "cancelled"},
    "failed": {"queued", "cancelled"},
    "succeeded": set(),
    "cancelled": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _decode(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    for key in ("metadata_json", "input_json", "output_json", "error_json", "payload_json", "snapshot_json"):
        if key in result and result[key] is not None:
            result[key[:-5] if key.endswith("_json") else key] = json.loads(result.pop(key))
    return result


class WorkflowStore:
    """SQLite-backed store for projects and their durable workflow state."""

    def __init__(self, database: str | Path = "workflow.db") -> None:
        self.database = str(database)
        if self.database != ":memory:":
            Path(self.database).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        if self.database != ":memory:":
            connection.execute("PRAGMA journal_mode = WAL")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'draft',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_assets (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN ('upload', 'url')),
                    uri TEXT NOT NULL,
                    sha256 TEXT,
                    mime_type TEXT,
                    size_bytes INTEGER,
                    status TEXT NOT NULL DEFAULT 'ready',
                    version INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_source_assets_project
                    ON source_assets(project_id, created_at);
                CREATE TABLE IF NOT EXISTS reference_assets (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN ('face', 'garment')),
                    uri TEXT NOT NULL,
                    sha256 TEXT,
                    mime_type TEXT,
                    status TEXT NOT NULL DEFAULT 'ready',
                    version INTEGER NOT NULL DEFAULT 1,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reference_assets_project
                    ON reference_assets(project_id, kind, created_at);
                CREATE TABLE IF NOT EXISTS stage_tasks (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    input_json TEXT NOT NULL DEFAULT '{}',
                    output_json TEXT NOT NULL DEFAULT '{}',
                    error_json TEXT NOT NULL DEFAULT '{}',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_stage_tasks_project
                    ON stage_tasks(project_id, stage, created_at);
                CREATE TABLE IF NOT EXISTS review_decisions (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    task_id TEXT REFERENCES stage_tasks(id) ON DELETE SET NULL,
                    stage TEXT NOT NULL,
                    decision TEXT NOT NULL CHECK(decision IN ('approved', 'rejected', 'changes_requested')),
                    reviewer TEXT,
                    note TEXT,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS run_snapshots (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    run_id TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'created',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    UNIQUE(project_id, run_id, version)
                );
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_events_entity
                    ON workflow_events(entity_type, entity_id, id);
                """
            )

    @staticmethod
    def _id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _existing_by_key(connection: sqlite3.Connection, table: str, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        row = connection.execute(
            f"SELECT * FROM {table} WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return _decode(row) if row else None

    def create_project(
        self,
        name: str,
        *,
        project_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        now, project_id = _now(), project_id or self._id()
        with self._transaction() as connection:
            existing = self._existing_by_key(connection, "projects", idempotency_key)
            if existing:
                return existing
            try:
                connection.execute(
                    """INSERT INTO projects
                       (id, name, metadata_json, idempotency_key, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (project_id, name, _json(metadata), idempotency_key, now, now),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError("project id or idempotency key already exists") from error
            self._event(connection, project_id, "project", project_id, "project.created", {"name": name})
            return self.get_project(project_id, connection=connection)

    def get_project(self, project_id: str, *, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        if connection is not None:
            row = connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not row:
                raise NotFoundError(f"project not found: {project_id}")
            return _decode(row)
        with self._connection() as conn:
            return self.get_project(project_id, connection=conn)

    def list_projects(self, *, status: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            if status:
                rows = connection.execute(
                    "SELECT * FROM projects WHERE status = ? ORDER BY created_at DESC", (status,)
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
            return [_decode(row) for row in rows]

    def update_project_status(self, project_id: str, status: str) -> dict[str, Any]:
        if status not in PROJECT_STATUSES:
            raise InvalidTransitionError(f"unknown project status: {status}")
        with self._transaction() as connection:
            self.get_project(project_id, connection=connection)
            now = _now()
            connection.execute(
                "UPDATE projects SET status = ?, updated_at = ? WHERE id = ?", (status, now, project_id)
            )
            self._event(connection, project_id, "project", project_id, "project.status_changed", {"status": status})
            return self.get_project(project_id, connection=connection)

    def transition_project(self, project_id: str, target: str, *, actor: str = "system", reason: str | None = None) -> dict[str, Any]:
        """Move the domain stage while keeping the coarse project status active."""
        from .workflow import ProjectStage, transition

        with self._transaction() as connection:
            project = self.get_project(project_id, connection=connection)
            metadata = dict(project.get("metadata") or {})
            current = metadata.get("stage", ProjectStage.DRAFT.value)
            try:
                next_stage = transition(current, target).value
            except (ValueError, TypeError) as error:
                raise InvalidTransitionError(str(error)) from error
            metadata["stage"] = next_stage
            now = _now()
            connection.execute(
                "UPDATE projects SET status = ?, metadata_json = ?, updated_at = ? WHERE id = ?",
                ("completed" if next_stage == ProjectStage.COMPLETED.value else "active", _json(metadata), now, project_id),
            )
            self._event(
                connection,
                project_id,
                "project",
                project_id,
                "project.stage_changed",
                {"from_stage": current, "to_stage": next_stage, "actor": actor, "reason": reason},
            )
            return self.get_project(project_id, connection=connection)
    def _create_asset(
        self,
        table: str,
        project_id: str,
        kind: str,
        uri: str,
        *,
        sha256: str | None = None,
        mime_type: str | None = None,
        size_bytes: int | None = None,
        status: str = "ready",
        version: int = 1,
        metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        now, asset_id = _now(), self._id()
        with self._transaction() as connection:
            self.get_project(project_id, connection=connection)
            existing = self._existing_by_key(connection, table, idempotency_key)
            if existing:
                return existing
            columns = (
                "id, project_id, kind, uri, sha256, mime_type, size_bytes, status, version, "
                "metadata_json, idempotency_key, created_at"
            )
            values = (
                asset_id, project_id, kind, uri, sha256, mime_type, size_bytes, status, version,
                _json(metadata), idempotency_key, now,
            )
            if table == "reference_assets":
                columns = columns.replace(", size_bytes", "")
                values = (asset_id, project_id, kind, uri, sha256, mime_type, status, version, _json(metadata), idempotency_key, now)
            try:
                connection.execute(
                    f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in values)})", values
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError("asset id or idempotency key already exists") from error
            self._event(connection, project_id, table[:-7] + ".created", asset_id, f"{table[:-7]}.created", {"kind": kind})
            row = connection.execute(f"SELECT * FROM {table} WHERE id = ?", (asset_id,)).fetchone()
            return _decode(row)

    def create_source_asset(self, project_id: str, kind: str, uri: str, **kwargs: Any) -> dict[str, Any]:
        if kind not in ("upload", "url"):
            raise ValueError("source asset kind must be upload or url")
        return self._create_asset("source_assets", project_id, kind, uri, **kwargs)

    def create_reference_asset(self, project_id: str, kind: str, uri: str, **kwargs: Any) -> dict[str, Any]:
        if kind not in ("face", "garment"):
            raise ValueError("reference asset kind must be face or garment")
        return self._create_asset("reference_assets", project_id, kind, uri, **kwargs)

    def list_assets(self, project_id: str, *, references: bool = False, kind: str | None = None) -> list[dict[str, Any]]:
        table = "reference_assets" if references else "source_assets"
        with self._connection() as connection:
            query, params = f"SELECT * FROM {table} WHERE project_id = ?", [project_id]
            if kind:
                query += " AND kind = ?"
                params.append(kind)
            query += " ORDER BY created_at ASC"
            return [_decode(row) for row in connection.execute(query, params).fetchall()]

    def create_stage_task(
        self,
        project_id: str,
        stage: str,
        *,
        input_data: Mapping[str, Any] | None = None,
        task_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        now, task_id = _now(), task_id or self._id()
        with self._transaction() as connection:
            self.get_project(project_id, connection=connection)
            existing = self._existing_by_key(connection, "stage_tasks", idempotency_key)
            if existing:
                return existing
            try:
                connection.execute(
                    """INSERT INTO stage_tasks
                       (id, project_id, stage, input_json, idempotency_key, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (task_id, project_id, stage, _json(input_data), idempotency_key, now, now),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError("task id or idempotency key already exists") from error
            self._event(connection, project_id, "stage_task", task_id, "stage_task.created", {"stage": stage})
            return self.get_stage_task(task_id, connection=connection)

    def get_stage_task(self, task_id: str, *, connection: sqlite3.Connection | None = None) -> dict[str, Any]:
        if connection is not None:
            row = connection.execute("SELECT * FROM stage_tasks WHERE id = ?", (task_id,)).fetchone()
            if not row:
                raise NotFoundError(f"stage task not found: {task_id}")
            return _decode(row)
        with self._connection() as conn:
            return self.get_stage_task(task_id, connection=conn)

    def list_stage_tasks(self, project_id: str, *, stage: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            query, params = "SELECT * FROM stage_tasks WHERE project_id = ?", [project_id]
            if stage:
                query += " AND stage = ?"
                params.append(stage)
            query += " ORDER BY created_at ASC"
            return [_decode(row) for row in connection.execute(query, params).fetchall()]

    def transition_stage_task(
        self,
        task_id: str,
        status: str,
        *,
        output_data: Mapping[str, Any] | None = None,
        error_data: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in STAGE_STATUSES:
            raise InvalidTransitionError(f"unknown task status: {status}")
        with self._transaction() as connection:
            task = self.get_stage_task(task_id, connection=connection)
            current = task["status"]
            if status != current and status not in _STAGE_TRANSITIONS.get(current, set()):
                raise InvalidTransitionError(f"cannot transition task from {current} to {status}")
            attempts = task["attempts"] + (1 if status == "running" else 0)
            now = _now()
            connection.execute(
                """UPDATE stage_tasks
                   SET status = ?, output_json = ?, error_json = ?, attempts = ?, updated_at = ?
                   WHERE id = ?""",
                (status, _json(output_data), _json(error_data), attempts, now, task_id),
            )
            self._event(
                connection, task["project_id"], "stage_task", task_id, "stage_task.status_changed",
                {"from": current, "to": status},
            )
            return self.get_stage_task(task_id, connection=connection)

    def create_review_decision(
        self,
        project_id: str,
        stage: str,
        decision: str,
        *,
        task_id: str | None = None,
        reviewer: str | None = None,
        note: str | None = None,
        payload: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if decision not in REVIEW_DECISIONS:
            raise ValueError(f"unknown review decision: {decision}")
        decision_id, now = self._id(), _now()
        with self._transaction() as connection:
            self.get_project(project_id, connection=connection)
            existing = self._existing_by_key(connection, "review_decisions", idempotency_key)
            if existing:
                return existing
            if task_id:
                self.get_stage_task(task_id, connection=connection)
            try:
                connection.execute(
                    """INSERT INTO review_decisions
                       (id, project_id, task_id, stage, decision, reviewer, note, payload_json,
                        idempotency_key, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (decision_id, project_id, task_id, stage, decision, reviewer, note, _json(payload), idempotency_key, now),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError("review decision id or idempotency key already exists") from error
            self._event(connection, project_id, "review_decision", decision_id, "review.created", {"stage": stage, "decision": decision})
            row = connection.execute("SELECT * FROM review_decisions WHERE id = ?", (decision_id,)).fetchone()
            return _decode(row)

    def create_run_snapshot(
        self,
        project_id: str,
        run_id: str,
        snapshot: Mapping[str, Any],
        *,
        version: int = 1,
        status: str = "created",
        snapshot_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        snapshot_id, now = snapshot_id or self._id(), _now()
        with self._transaction() as connection:
            self.get_project(project_id, connection=connection)
            existing = self._existing_by_key(connection, "run_snapshots", idempotency_key)
            if existing:
                return existing
            try:
                connection.execute(
                    """INSERT INTO run_snapshots
                       (id, project_id, run_id, version, status, snapshot_json, idempotency_key, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (snapshot_id, project_id, run_id, version, status, _json(snapshot), idempotency_key, now),
                )
            except sqlite3.IntegrityError as error:
                raise ConflictError("snapshot id, run/version, or idempotency key already exists") from error
            self._event(connection, project_id, "run_snapshot", snapshot_id, "run_snapshot.created", {"run_id": run_id, "version": version})
            row = connection.execute("SELECT * FROM run_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            return _decode(row)

    def get_run_snapshot(self, project_id: str, run_id: str, *, version: int | None = None) -> dict[str, Any]:
        with self._connection() as connection:
            if version is None:
                row = connection.execute(
                    "SELECT * FROM run_snapshots WHERE project_id = ? AND run_id = ? ORDER BY version DESC LIMIT 1",
                    (project_id, run_id),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM run_snapshots WHERE project_id = ? AND run_id = ? AND version = ?",
                    (project_id, run_id, version),
                ).fetchone()
            if not row:
                raise NotFoundError(f"run snapshot not found: {project_id}/{run_id}")
            return _decode(row)

    def append_event(
        self,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        project_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        with self._transaction() as connection:
            existing = self._existing_by_key(connection, "workflow_events", idempotency_key)
            if existing:
                return existing
            event_id = self._event(
                connection, project_id, entity_type, entity_id, event_type, payload, idempotency_key=idempotency_key
            )
            row = connection.execute("SELECT * FROM workflow_events WHERE id = ?", (event_id,)).fetchone()
            return _decode(row)

    def list_events(self, *, project_id: str | None = None, entity_type: str | None = None, entity_id: str | None = None) -> list[dict[str, Any]]:
        with self._connection() as connection:
            query, params = "SELECT * FROM workflow_events WHERE 1 = 1", []
            if project_id:
                query += " AND project_id = ?"
                params.append(project_id)
            if entity_type:
                query += " AND entity_type = ?"
                params.append(entity_type)
            if entity_id:
                query += " AND entity_id = ?"
                params.append(entity_id)
            query += " ORDER BY id ASC"
            return [_decode(row) for row in connection.execute(query, params).fetchall()]

    @staticmethod
    def _event(
        connection: sqlite3.Connection,
        project_id: str | None,
        entity_type: str,
        entity_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> int:
        try:
            cursor = connection.execute(
                """INSERT INTO workflow_events
                   (project_id, entity_type, entity_id, event_type, payload_json, idempotency_key, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (project_id, entity_type, entity_id, event_type, _json(payload), idempotency_key, _now()),
            )
            return int(cursor.lastrowid)
        except sqlite3.IntegrityError as error:
            if idempotency_key:
                row = connection.execute(
                    "SELECT id FROM workflow_events WHERE idempotency_key = ?", (idempotency_key,)
                ).fetchone()
                if row:
                    return int(row["id"])
            raise ConflictError("event idempotency key already exists") from error

