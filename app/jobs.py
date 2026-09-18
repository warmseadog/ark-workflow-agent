from __future__ import annotations

import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import Settings
from .media import BlurOptions, download_video, run_deface
from .seedance import SeedanceClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Job:
    id: str
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
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex)
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def update(self, job_id: str, **changes: Any) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
            job.updated_at = _now()
            return job

    def log(self, job_id: str, message: str) -> Job:
        with self._lock:
            job = self._jobs[job_id]
            job.logs.append(message)
            job.updated_at = _now()
            return job


store = JobStore()


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
            source_path = download_video(video_url, job_dir / "source.mp4")
        elif video_path:
            source_path = video_path
        else:
            raise RuntimeError("没有可处理的视频输入。")

        defaced_path = job_dir / "defaced.mp4"
        store.update(job_id, progress=25, message="正在使用 deface 进行人脸打码")
        blur_options = blur_options or BlurOptions.from_settings(settings)
        store.log(job_id, f"deface：{blur_options.style}，遮罩 {blur_options.mask_scale} 倍，检测阈值 {blur_options.threshold}。")
        run_deface(source_path, defaced_path, settings, blur_options)
        store.update(
            job_id,
            status="defaced",
            progress=100,
            message="打码完成，请先预览视频",
            defaced_name=defaced_path.name,
        )
        store.log(job_id, "人脸打码完成，可在下一步前预览结果。")
    except Exception as exc:  # noqa: BLE001 - surface pipeline failures to the UI
        store.log(job_id, f"失败：{exc}")
        store.update(job_id, status="failed", progress=100, message="处理失败", error=str(exc))


def run_generation_pipeline(
    job_id: str,
    settings: Settings,
    face_path: Path | None,
    clothing_path: Path | None,
    prompt: str,
) -> None:
    """Generate the final video from a previously completed deface stage."""
    job_dir = settings.storage_dir / "work" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
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
        seedance_result = SeedanceClient(settings).generate(
            defaced_path, face_path, clothing_path, prompt, output_path
        )
        store.log(job_id, seedance_result.get("message", "Seedance 处理完成。"))
        store.update(
            job_id,
            status="succeeded",
            progress=100,
            message="处理完成",
            output_name=output_path.name,
            provider=seedance_result.get("provider"),
        )
    except Exception as exc:  # noqa: BLE001 - surface pipeline failures to the UI
        store.log(job_id, f"失败：{exc}")
        store.update(job_id, status="failed", progress=100, message="处理失败", error=str(exc))


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
