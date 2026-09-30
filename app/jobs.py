from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from . import database
from .database import JobRecord
from .media import BlurOptions, download_video, run_deface
from .seedance import SeedanceClient
from .generation_settings import GenerationConfig
from .video_provider import VideoProvider
from .reference_media import publish_video
from . import storage_settings
from .security import safe_error, configured_secrets, secret_values


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
    candidate_id: str | None = None
    status: str = "queued"
    progress: int = 0
    message: str = "等待处理"
    logs: list[str] = field(default_factory=list)
    defaced_name: str | None = None
    output_name: str | None = None
    provider: str | None = None
    error: str | None = None
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data["defaced_url"] = f"/api/jobs/{self.id}/defaced" if self.defaced_name else None
        data["download_url"] = f"/api/jobs/{self.id}/download" if self.output_name else None
        return data


class JobStore:
    def __init__(self, persistent: bool = False) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self.persistent = persistent

    def create(self, candidate_id: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex, candidate_id=candidate_id)
        with self._lock:
            self._jobs[job.id] = job
            self._persist(job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None or not self.persistent:
                return job
            with database.session() as db:
                record = db.get(JobRecord, job_id)
                if record is None:
                    return None
                job = Job(**database.job_dict(record))
                self._jobs[job.id] = job
                return job

    def list(self) -> list[Job]:
        with self._lock:
            if not self.persistent:
                return list(self._jobs.values())
            from sqlalchemy import select
            with database.session() as db:
                records = db.scalars(select(JobRecord).order_by(JobRecord.created_at.desc())).all()
                result = []
                for record in records:
                    job = self._jobs.get(record.id) or Job(**database.job_dict(record))
                    self._jobs[job.id] = job
                    result.append(job)
                return result

    def find_active_for_candidate(self, candidate_id: str) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.candidate_id == candidate_id and job.status in {"queued", "running", "defaced", "succeeded"}:
                    return job
            if not self.persistent:
                return None
            with database.session() as db:
                from sqlalchemy import select
                record = db.scalar(select(JobRecord).where(
                    JobRecord.candidate_id == candidate_id,
                    JobRecord.status.in_(["queued", "running", "defaced", "succeeded"])))
                if record is None:
                    return None
                job = Job(**database.job_dict(record))
                self._jobs[job.id] = job
                return job

    def update(self, job_id: str, **changes: Any) -> Job:
        with self._lock:
            job = self.get(job_id)
            if job is None:
                raise KeyError(job_id)
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = _now()
            self._persist(job)
            return job

    def log(self, job_id: str, message: str) -> Job:
        with self._lock:
            job = self.get(job_id)
            if job is None:
                raise KeyError(job_id)
            job.logs.append(message)
            job.updated_at = _now()
            self._persist(job)
            return job

    def recover_interrupted(self) -> None:
        with self._lock:
            jobs = list(self._jobs.values())
            if self.persistent:
                from sqlalchemy import select
                with database.session() as db:
                    records = db.scalars(select(JobRecord).where(JobRecord.status.in_(["queued", "running"]))).all()
                    for record in records:
                        if record.id not in self._jobs:
                            self._jobs[record.id] = Job(**database.job_dict(record))
                    jobs = list(self._jobs.values())
            for job in jobs:
                if job.status in {"queued", "running"}:
                    job.status = "failed"
                    job.progress = 100
                    job.message = "服务重启，中断的任务需要重试"
                    job.error = "interrupted_by_restart"
                    self._persist(job)

    def _persist(self, job: Job) -> None:
        if not self.persistent:
            return
        payload = database.job_dict_to_record(job)
        with database.session() as db:
            record = db.get(JobRecord, job.id)
            if record is None:
                db.add(JobRecord(**payload))
            else:
                for key, value in payload.items():
                    setattr(record, key, value)
            db.commit()


store = JobStore(persistent=True)


def run_deface_pipeline(
    job_id: str,
    settings: Settings,
    video_path: Path | None,
    video_url: str | None,
    blur_options: BlurOptions | None = None,
) -> None:
    """Acquire the source video and stop after deface for user review."""
    job_dir = settings.storage_dir / "work" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    try:
        store.update(job_id, status="running", progress=5, message="准备输入视频")
        if video_url:
            store.log(job_id, "正在使用 yt-dlp 下载视频链接。")
            source_path = download_video(video_url, job_dir / "source.mp4", settings)
        elif video_path:
            source_path = video_path
        else:
            raise RuntimeError("没有可处理的视频输入。")

        defaced_path = job_dir / "defaced.mp4"
        blur_options = blur_options or BlurOptions.from_settings(settings)
        processor_name = "deface" if blur_options.mask_mode == "face" else "本地人脸/头发跟踪器"
        store.update(job_id, progress=25, message=f"正在使用{processor_name}处理")
        store.log(job_id, f"{processor_name}：模式 {blur_options.mask_mode}，样式 {blur_options.style}，遮罩 {blur_options.mask_scale} 倍，检测阈值 {blur_options.threshold}，稳定跟踪 {blur_options.robust_tracking}。")
        run_deface(source_path, defaced_path, settings, blur_options)
        store.update(
            job_id,
            status="defaced",
            progress=100,
            message="打码完成，请先预览视频",
            defaced_name=defaced_path.name,
        )
        store.log(job_id, "人脸/头发打码完成，可在下一步前预览结果。")
    except Exception as exc:  # noqa: BLE001 - surface pipeline failures to the UI
        message = safe_error(exc, configured_secrets(settings))
        store.log(job_id, f"失败：{message}")
        store.update(job_id, status="failed", progress=100, message="处理失败", error=message)


def run_generation_pipeline(
    job_id: str,
    settings: Settings,
    face_path: Path | None,
    clothing_path: Path | None,
    prompt: str,
    *,
    generation_config: GenerationConfig | None = None,
    storage_config: storage_settings.StorageConfig | None = None,
    face_paths: list[Path] | None = None,
    clothing_paths: list[Path] | None = None,
) -> None:
    """Generate the final video from a previously completed deface stage."""
    job_dir = settings.storage_dir / "work" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    def safe_message(value):
        frozen = [asdict(config) for config in (generation_config, storage_config) if config is not None]
        return safe_error(value, configured_secrets(settings) | secret_values(frozen))
    try:
        job = store.get(job_id)
        if not job or not job.defaced_name:
            raise RuntimeError("请先完成视频打码，再进入下一步。")
        defaced_path = job_dir / job.defaced_name
        if not defaced_path.exists():
            raise RuntimeError("找不到打码后的视频，请重新提交视频。")

        output_path = settings.storage_dir / "outputs" / f"{job_id}.mp4"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        store.update(job_id, status="running", progress=65, message="正在提交 Seedance 生成")
        if generation_config is None:
            seedance_result = SeedanceClient(settings).generate(
                defaced_path, face_path, clothing_path, prompt, output_path
            )
        else:
            video_url = None
            if generation_config.mode == 'http' and generation_config.protocol == 'ark':
                storage_config = storage_config or storage_settings.load_config(settings)
                if storage_config.enabled:
                    store.update(job_id, message='正在上传打码视频到 TOS', progress=63)
                    video_url = storage_settings.upload_redacted_video(defaced_path, settings, storage_config)
                else:
                    video_url = publish_video(defaced_path, settings.storage_dir, generation_config.public_base_url)
            def progress(message, percent):
                message = safe_message(message)
                store.update(job_id, message=message, progress=percent)
                if percent == 70:
                    store.log(job_id, message)
            client = VideoProvider(generation_config, settings.seedance_poll_seconds, progress)
            seedance_result = client.generate(defaced_path,
                face_paths if face_paths is not None else ([face_path] if face_path else []),
                clothing_paths if clothing_paths is not None else ([clothing_path] if clothing_path else []),
                prompt, output_path, video_url=video_url)
        store.log(job_id, safe_message(seedance_result.get("message", "Seedance 处理完成。")))
        store.update(
            job_id,
            status="succeeded",
            progress=100,
            message="处理完成",
            output_name=output_path.name,
            provider=seedance_result.get("provider"),
        )
    except Exception as exc:  # noqa: BLE001 - surface pipeline failures to the UI
        message = safe_message(exc)
        store.log(job_id, f"失败：{message}")
        store.update(job_id, status="failed", progress=100, message="处理失败", error=message)


def run_pipeline(
    job_id: str,
    settings: Settings,
    video_path: Path | None,
    video_url: str | None,
    face_path: Path | None,
    clothing_path: Path | None,
    prompt: str,
    blur_options: BlurOptions | None = None,
) -> None:
    """Run both stages for callers that still use the original API."""
    run_deface_pipeline(job_id, settings, video_path, video_url, blur_options)
    job = store.get(job_id)
    if job and job.status == "defaced":
        run_generation_pipeline(job_id, settings, face_path, clothing_path, prompt)
