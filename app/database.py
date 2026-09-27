from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class Base(DeclarativeBase):
    pass


class CandidateRecord(Base):
    __tablename__ = "discovery_candidates"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(160))
    canonical_url: Mapped[str] = mapped_column(String(1000), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    author: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[str | None] = mapped_column(String(80))
    metrics_json: Mapped[str] = mapped_column(Text, default="{}")
    cover_url: Mapped[str | None] = mapped_column(String(1000))
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    rights_status: Mapped[str] = mapped_column(String(24), default="unknown")
    status: Mapped[str] = mapped_column(String(24), default="pending")
    score: Mapped[int | None] = mapped_column(Integer)
    review_note: Mapped[str | None] = mapped_column(Text)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class CaseRecord(Base):
    __tablename__ = "discovery_cases"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    shot_notes: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(24), default="success")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class JobRecord(Base):
    __tablename__ = "video_jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="等待处理")
    logs_json: Mapped[str] = mapped_column(Text, default="[]")
    defaced_name: Mapped[str | None] = mapped_column(String(240))
    output_name: Mapped[str | None] = mapped_column(String(240))
    provider: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(80))
    updated_at: Mapped[str] = mapped_column(String(80))


_lock = threading.RLock()
_engine = None
_database_url: str | None = None


def _default_url() -> str:
    storage = Path(os.getenv("STORAGE_DIR", "storage"))
    storage.mkdir(parents=True, exist_ok=True)
    return os.getenv("DATABASE_URL", f"sqlite:///{(storage / 'studio.db').resolve().as_posix()}")


def configure_database(url: str | None = None):
    global _engine, _database_url
    with _lock:
        selected = url or _database_url or _default_url()
        if _engine is None or _database_url != selected:
            kwargs = {"check_same_thread": False} if selected.startswith("sqlite") else {}
            _engine = create_engine(selected, future=True, connect_args=kwargs)
            _database_url = selected
            Base.metadata.create_all(_engine)
        return _engine


def session() -> Session:
    return Session(configure_database())


def _json(value: Any, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


def candidate_dict(record: CandidateRecord) -> dict[str, Any]:
    return {
        "id": record.id, "source": record.source, "source_id": record.source_id,
        "canonical_url": record.canonical_url, "title": record.title, "author": record.author,
        "published_at": record.published_at, "metrics": _json(record.metrics_json, {}),
        "cover_url": record.cover_url, "tags": _json(record.tags_json, []),
        "rights_status": record.rights_status, "status": record.status, "score": record.score,
        "review_note": record.review_note,
    }


def case_dict(record: CaseRecord) -> dict[str, Any]:
    return {"id": record.id, "title": record.title, "tags": _json(record.tags_json, []),
            "shot_notes": record.shot_notes, "outcome": record.outcome, "notes": record.notes}


def job_dict(record: JobRecord) -> dict[str, Any]:
    return {"id": record.id, "candidate_id": record.candidate_id, "status": record.status,
            "progress": record.progress, "message": record.message, "logs": _json(record.logs_json, []),
            "defaced_name": record.defaced_name, "output_name": record.output_name,
            "provider": record.provider, "error": record.error, "created_at": record.created_at,
            "updated_at": record.updated_at}


def serialise(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def job_dict_to_record(job: Any) -> dict[str, Any]:
    return {
        "id": job.id,
        "candidate_id": job.candidate_id,
        "status": job.status,
        "progress": job.progress,
        "message": job.message,
        "logs_json": serialise(job.logs),
        "defaced_name": job.defaced_name,
        "output_name": job.output_name,
        "provider": job.provider,
        "error": job.error,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"

