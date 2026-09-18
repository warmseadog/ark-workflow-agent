from __future__ import annotations

import re
from pathlib import Path
from threading import Thread

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import settings
from .jobs import run_deface_pipeline, run_generation_pipeline, store
from .media import BlurOptions

app = FastAPI(title="Face & Clothing Video Workflow", version="0.1.0")
APP_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")


def _safe_suffix(filename: str | None, default: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    return suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else default


def _parse_detection_size(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="检测尺寸必须是数字或留空。") from exc


def _save_upload(upload: UploadFile, destination: Path, max_bytes: int) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with destination.open("wb") as target:
        while chunk := upload.file.read(1024 * 1024):
            total += len(chunk)
            if total > max_bytes:
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="上传文件超过大小限制。")
            target.write(chunk)
    return destination


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"seedance_mode": settings.seedance_mode, "max_upload_mb": settings.max_upload_mb},
    )


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "seedance_mode": settings.seedance_mode}


@app.post("/api/jobs")
async def create_job(
    video: UploadFile | None = File(default=None),
    video_url: str | None = Form(default=None),
    replace_image: UploadFile | None = File(default=None),
    blur_style: str = Form(default="mosaic"),
    blur_shape: str = Form(default="ellipse"),
    mask_mode: str = Form(default="face"),
    robust_tracking: bool | None = Form(default=None),
    mask_scale: float = Form(default=1.4),
    mosaic_size: int = Form(default=20),
    threshold: float = Form(default=0.2),
    detection_size: str | None = Form(default=None),
    keep_audio: bool | None = Form(default=None),
):
    if not video and not (video_url and video_url.strip()):
        raise HTTPException(status_code=400, detail="请上传视频文件或填写视频链接。")
    if video and video_url and video_url.strip():
        raise HTTPException(status_code=400, detail="视频文件和视频链接二选一。")

    job = store.create()
    job_dir = settings.storage_dir / "work" / job.id
    source_path: Path | None = None
    if video:
        source_path = _save_upload(
            video,
            job_dir / f"source{_safe_suffix(video.filename, '.mp4')}",
            settings.max_upload_mb * 1024 * 1024,
        )
    replace_path = None
    if replace_image:
        replace_path = _save_upload(
            replace_image,
            job_dir / f"replace{_safe_suffix(replace_image.filename, '.png')}",
            20 * 1024 * 1024,
        )
    if blur_style == "img" and replace_path is None:
        raise HTTPException(status_code=422, detail="图片覆盖模式需要上传有效的替换图片。")
    detection_size_value = _parse_detection_size(detection_size)
    try:
        blur_options = BlurOptions(
            style=blur_style,
            shape=blur_shape,
            mask_mode=mask_mode,
            robust_tracking=robust_tracking is True,
            mask_scale=mask_scale,
            mosaic_size=mosaic_size,
            threshold=threshold,
            detection_size=detection_size_value,
            keep_audio=keep_audio is True,
            replace_image=replace_path,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"打码设置无效：{exc}") from exc

    worker = Thread(
        target=run_deface_pipeline,
        args=(job.id, settings, source_path, video_url.strip() if video_url else None, blur_options),
        daemon=True,
    )
    worker.start()
    return job.public()


@app.get("/api/jobs/{job_id}/defaced")
async def preview_defaced(job_id: str):
    job = store.get(job_id)
    if not job or not job.defaced_name:
        raise HTTPException(status_code=404, detail="打码视频还没有准备好。")
    preview_path = settings.storage_dir / "work" / job_id / job.defaced_name
    if not preview_path.exists():
        raise HTTPException(status_code=404, detail="打码视频文件不存在，请重新提交。")
    return FileResponse(preview_path, media_type="video/mp4", filename=f"{job_id}_defaced.mp4")


@app.post("/api/jobs/{job_id}/generate")
async def generate_job(
    job_id: str,
    face_image: UploadFile | None = File(default=None),
    clothing_image: UploadFile | None = File(default=None),
    prompt: str = Form(default="保持原视频动作和镜头，使用参考人脸和服装生成自然、稳定的短视频。"),
):
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到任务。")
    if job.status != "defaced" or not job.defaced_name:
        raise HTTPException(status_code=409, detail="请先完成视频打码，再进入下一步。")

    job_dir = settings.storage_dir / "work" / job_id
    face_path = None
    if face_image:
        face_path = _save_upload(face_image, job_dir / f"face{_safe_suffix(face_image.filename, '.png')}", 20 * 1024 * 1024)
    clothing_path = None
    if clothing_image:
        clothing_path = _save_upload(
            clothing_image,
            job_dir / f"clothing{_safe_suffix(clothing_image.filename, '.png')}",
            20 * 1024 * 1024,
        )
    store.update(job_id, status="running", progress=60, message="正在准备 Seedance 生成")
    worker = Thread(
        target=run_generation_pipeline,
        args=(job_id, settings, face_path, clothing_path, prompt.strip()),
        daemon=True,
    )
    worker.start()
    return job.public()


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到任务。")
    return job.public()


@app.get("/api/jobs/{job_id}/download")
async def download_job(job_id: str):
    job = store.get(job_id)
    if not job or not job.output_name:
        raise HTTPException(status_code=404, detail="任务还没有可下载的结果。")
    output = settings.storage_dir / "outputs" / job.output_name
    if not output.exists():
        raise HTTPException(status_code=404, detail="结果文件不存在。")
    return FileResponse(output, media_type="video/mp4", filename=f"{job_id}_final.mp4")
