from __future__ import annotations

import re
import mimetypes
import subprocess
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from threading import Thread

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from . import local_preferences, media
from . import admin_settings, storage_settings, redaction_settings
from .video_links import LABELS, platform_for_url

from .config import settings
from .generation_settings import PRESETS, load_config, save_config, resolve_config
from .model_connection import test_connection
from .reference_media import get_video
from .jobs import run_deface_pipeline, run_generation_pipeline, store
from .media import BlurOptions, extract_video_url
from . import database
from .discovery import service as discovery
from .workflow_router import get_router as get_workflow_router

@asynccontextmanager
async def production_lifespan(app):
    from .production_worker import wake, shutdown
    wake(settings)
    try:
        yield
    finally:
        shutdown(settings)


app = FastAPI(title="Face & Clothing Video Workflow", version="0.1.0", lifespan=production_lifespan)
APP_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")
templates = Jinja2Templates(directory=APP_DIR / "templates")

# Versioned workflow API; legacy /api/jobs routes remain compatible.
app.include_router(get_workflow_router())


def _local_config_request(request: Request) -> None:
    if request.url.hostname not in {'127.0.0.1', 'localhost', '::1', 'testserver'}:
        raise HTTPException(status_code=403, detail='请通过本机地址打开模型配置。')
    if request.client and request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
        raise HTTPException(status_code=403, detail='模型配置仅允许在本机访问。')
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(status_code=403, detail='配置请求必须来自本工作台。')



from .production_router import get_router as get_production_router
app.include_router(get_production_router(lambda: settings, _local_config_request))
from .portrait_router import get_router as get_portrait_router
app.include_router(get_portrait_router(lambda: settings, _local_config_request))
from .portrait_sessions import routers as get_portrait_session_routers
for portrait_session_router in get_portrait_session_routers(lambda: settings, _local_config_request):
    app.include_router(portrait_session_router)

class PromptTemplateInput(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    content: str = Field(min_length=1, max_length=10000)

class LinkSettingsInput(BaseModel):
    api_key: str = Field(default='', max_length=2048)
    clear_api_key: bool = False

class LinkInput(BaseModel):
    text: str = Field(min_length=1, max_length=10000)

@app.get('/api/link-settings')
def get_link_settings(request: Request):
    _local_config_request(request)
    return {'has_api_key': bool(local_preferences.get_tikhub_key(settings)), 'provider': 'TikHub'}

@app.put('/api/link-settings')
def put_link_settings(request: Request, payload: LinkSettingsInput):
    _local_config_request(request)
    try:
        local_preferences.save_tikhub_key(settings, payload.api_key, payload.clear_api_key)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return get_link_settings(request)

@app.post('/api/video-link/inspect')
def inspect_video_link(request: Request, payload: LinkInput):
    _local_config_request(request)
    try:
        url = extract_video_url(payload.text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    platform = platform_for_url(url)
    return {'url': url, 'platform': platform, 'label': LABELS.get(platform, '其他链接'),
            'configured': bool(local_preferences.get_tikhub_key(settings))}

@app.post('/api/video-link/import')
def import_video_link(request: Request, payload: LinkInput):
    """Download on confirmation and return a file usable by the existing uploader."""
    _local_config_request(request)
    try:
        url = extract_video_url(payload.text)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    import_root = settings.storage_dir / 'imports'
    import_root.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(prefix='video-', dir=import_root)
    try:
        path = media.download_video(url, Path(temporary.name) / 'reference.mp4', settings)
        size = path.stat().st_size
        if size == 0:
            raise media.MediaPipelineError('下载到的视频为空，请尝试其他链接。')
        if size > settings.max_upload_mb * 1024 * 1024:
            raise media.MediaPipelineError('下载视频超过项目大小限制，请选择更短的视频。')
        return FileResponse(
            path, media_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
            filename='reference' + path.suffix,
            headers={'Cache-Control': 'no-store'},
            background=BackgroundTask(temporary.cleanup),
        )
    except media.MediaPipelineError as exc:
        temporary.cleanup()
        raise HTTPException(422, str(exc)) from None
    except (OSError, subprocess.TimeoutExpired):
        temporary.cleanup()
        raise HTTPException(502, '视频下载失败或超时，请检查网络与磁盘空间后重试。') from None
    except Exception:
        temporary.cleanup()
        raise


@app.get('/api/prompt-templates')
def get_prompt_templates(request: Request):
    _local_config_request(request)
    return {'items': local_preferences.list_templates(settings)}

def _save_prompt(payload, template_id=None):
    try:
        return local_preferences.save_template(settings, payload.name, payload.content, template_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None

@app.post('/api/prompt-templates')
def create_prompt_template(request: Request, payload: PromptTemplateInput):
    _local_config_request(request)
    return _save_prompt(payload)

@app.put('/api/prompt-templates/{template_id}')
def update_prompt_template(request: Request, template_id: str, payload: PromptTemplateInput):
    _local_config_request(request)
    return _save_prompt(payload, template_id)

@app.delete('/api/prompt-templates/{template_id}')
def delete_prompt_template(request: Request, template_id: str):
    _local_config_request(request)
    try:
        local_preferences.delete_template(settings, template_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from None
    return {'deleted': True}


@app.get('/api/model-settings')
def get_model_settings(request: Request):
    _local_config_request(request)
    return {'config': admin_settings.public_model(settings), 'presets': PRESETS}


@app.get('/api/model-catalog')
def get_model_catalog(request: Request):
    _local_config_request(request)
    from .model_catalog import catalog
    return catalog(settings)


@app.put('/api/model-catalog')
def put_model_catalog(request: Request, payload: dict):
    _local_config_request(request)
    from .model_catalog import save_catalog
    try:
        return save_catalog(settings, payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from None


@app.put('/api/model-settings')
def put_model_settings(request: Request, payload: dict):
    _local_config_request(request)
    try:
        config = save_config(settings, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except OSError:
        raise HTTPException(status_code=500, detail='无法保存本机配置，请检查存储目录权限。') from None
    return {'config': admin_settings.public_model(settings, config), 'presets': PRESETS}


@app.get('/admin/settings', response_class=HTMLResponse)
def admin_page(request: Request):
    _local_config_request(request)
    return templates.TemplateResponse(request=request, name='admin_settings.html', context={})


@app.get('/api/admin/overview')
def admin_overview(request: Request):
    _local_config_request(request)
    return admin_settings.overview(settings)


@app.get('/api/storage-settings')
def get_storage_settings(request: Request):
    _local_config_request(request)
    return {'config': storage_settings.load_config(settings).public()}


@app.get('/api/redaction-settings')
def get_redaction_settings(request: Request):
    _local_config_request(request)
    return {'config': redaction_settings.load_config(settings)}


@app.put('/api/redaction-settings')
def put_redaction_settings(request: Request, payload: dict):
    _local_config_request(request)
    try:
        return {'config': redaction_settings.save_config(settings, payload)}
    except (ValueError, TypeError):
        raise HTTPException(422, '打码参数无效，请检查数值范围；头发遮挡仅支持马赛克。') from None
    except OSError:
        raise HTTPException(500, '无法保存打码设置，请检查存储目录权限。') from None


@app.put('/api/storage-settings')
def put_storage_settings(request: Request, payload: dict):
    _local_config_request(request)
    try:
        return {'config': storage_settings.save_config(settings, payload).public()}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except OSError:
        raise HTTPException(500, '无法保存 TOS 配置，请检查存储目录权限。') from None


@app.post('/api/storage-settings/test')
def test_storage_settings(request: Request, payload: dict):
    _local_config_request(request)
    try:
        config = storage_settings.resolve_config(settings, payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return storage_settings.test_connection(config)


@app.post('/api/link-settings/test')
def test_link_settings(request: Request, payload: LinkSettingsInput):
    _local_config_request(request)
    return admin_settings.test_tikhub(settings, payload.api_key, payload.clear_api_key)


@app.post('/api/model-settings/test')
def test_model_settings(request: Request, payload: dict):
    _local_config_request(request)
    try:
        config = resolve_config(settings, payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return test_connection(config)


@app.get('/api/reference-videos/{token}')
def reference_video(token: str):
    path = get_video(token, settings.storage_dir)
    if path is None:
        raise HTTPException(status_code=404, detail='视频地址无效或已过期。')
    return FileResponse(path, media_type='video/mp4', headers={'Cache-Control': 'no-store'})


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
        name="production.html",
        context={"seedance_mode": settings.seedance_mode, "max_upload_mb": settings.max_upload_mb},
    )


@app.get("/studio", response_class=HTMLResponse)
async def studio(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="production.html",
        context={"seedance_mode": settings.seedance_mode, "max_upload_mb": settings.max_upload_mb},
    )


@app.get("/v1", response_class=HTMLResponse)
async def v1(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="production.html",
        context={"seedance_mode": settings.seedance_mode, "max_upload_mb": settings.max_upload_mb},
    )

@app.get("/api/discovery/candidates")
async def discovery_candidates(status: str | None = None):
    return {"items": discovery.list_candidates(status)}


@app.post("/api/discovery/import")
async def import_candidates(payload: dict):
    links = payload.get("links") or []
    if not isinstance(links, list):
        raise HTTPException(status_code=422, detail="links 必须是数组。")
    return discovery.create_candidates(links, payload.get("title"), payload.get("tags") or [])


@app.get("/api/discovery/cases")
async def discovery_cases():
    return {"items": discovery.list_cases()}


@app.post("/api/discovery/cases")
async def create_discovery_case(payload: dict):
    try:
        return discovery.save_case(payload)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/discovery/runs")
async def create_discovery_run(payload: dict):
    try:
        return discovery.query_plan(payload.get("topic", ""), payload.get("platforms") or ["douyin", "xiaohongshu"], payload.get("case_ids"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/api/discovery/candidates/{candidate_id}/review")
async def review_discovery_candidate(candidate_id: str, payload: dict):
    try:
        return discovery.review_candidate(candidate_id, payload.get("decision", "pending"), payload.get("note"))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "seedance_mode": settings.seedance_mode}


@app.post("/api/jobs")
async def create_job(
    video: UploadFile | None = File(default=None),
    video_url: str | None = Form(default=None),
    candidate_id: str | None = Form(default=None),
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
    has_video = bool(video and (video.filename or "").strip())
    candidate = discovery.get_candidate(candidate_id) if candidate_id else None
    if candidate_id and not candidate:
        raise HTTPException(status_code=404, detail="找不到候选视频。")
    if candidate and candidate["status"] != "approved":
        raise HTTPException(status_code=409, detail="候选视频还没有通过人工审核。")
    if candidate:
        active = store.find_active_for_candidate(candidate_id)
        if active:
            return active.public()
        if not has_video and not video_url:
            video_url = candidate["canonical_url"]
    if not has_video and not (video_url and video_url.strip()):
        raise HTTPException(status_code=400, detail="请上传视频文件或填写视频链接。")
    if has_video and video_url and video_url.strip():
        raise HTTPException(status_code=400, detail="视频文件和视频链接二选一。")
    if video_url:
        try:
            video_url = extract_video_url(video_url)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    job = store.create(candidate_id=candidate_id)
    job_dir = settings.storage_dir / "work" / job.id
    source_path: Path | None = None
    if has_video:
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
    request: Request,
    face_image: list[UploadFile] = File(default=[]),
    clothing_image: list[UploadFile] = File(default=[]),
    prompt: str = Form(default="保持原视频动作和镜头，使用参考人脸和服装生成自然、稳定的短视频。"),
    generation_model: str = Form(default="seedance-default"),
    duration: int = Form(default=5),
    fps: int = Form(default=24),
    resolution: str = Form(default="720p"),
):
    job = store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="找不到任务。")
    if job.status != "defaced" or not job.defaced_name:
        raise HTTPException(status_code=409, detail="请先完成视频打码，再进入下一步。")
    _local_config_request(request)
    generation_config = load_config(settings)
    storage_config = storage_settings.load_config(settings)
    problem = admin_settings.generation_problem(generation_config, storage_config)
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    if generation_config.mode == 'http' and generation_config.protocol != 'adapter' and len(face_image) + len(clothing_image) > 9:
        raise HTTPException(status_code=422, detail='人物和衣服参考图合计最多 9 张，请删除部分图片后重试。')

    job_dir = settings.storage_dir / "work" / job_id
    face_paths = []
    for index, upload in enumerate(face_image, start=1):
        if upload and (upload.filename or "").strip():
            face_paths.append(_save_upload(upload, job_dir / f"face-{index:02d}{_safe_suffix(upload.filename, '.png')}", 20 * 1024 * 1024))
    clothing_paths = []
    for index, upload in enumerate(clothing_image, start=1):
        if upload and (upload.filename or "").strip():
            clothing_paths.append(_save_upload(upload, job_dir / f"clothing-{index:02d}{_safe_suffix(upload.filename, '.png')}", 20 * 1024 * 1024))
    store.log(job_id, f"参考素材：{len(face_paths)} 张人物图、{len(clothing_paths)} 张衣服图。")
    store.log(job_id, f"生成配置：{generation_config.model} · {generation_config.duration} 秒 · {generation_config.resolution}。")
    store.update(job_id, status="running", progress=60, message="正在准备视频生成", error=None)
    worker = Thread(
        target=run_generation_pipeline,
        args=(job_id, settings, face_paths[0] if face_paths else None, clothing_paths[0] if clothing_paths else None, prompt.strip()),
        kwargs={'generation_config': generation_config, 'face_paths': face_paths, 'clothing_paths': clothing_paths, 'storage_config': storage_config},
        daemon=True,
    )
    worker.start()
    return job.public()


@app.get("/api/jobs")
async def list_jobs():
    return {"items": [job.public() for job in store.list()]}


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
