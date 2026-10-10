from __future__ import annotations

import subprocess
import sys
import re
import logging
import os
import tempfile
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import cv2
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import Settings
from .local_mosaic import run_local_mosaic
from .media_errors import MediaPipelineError

from .video_links import extract_video_url, platform_for_url


class LocalMosaicOptions(BaseModel):
    """Versioned opt-in controls: absent on historical task snapshots."""
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    hair_mosaic_size: int = Field(default=20, ge=4, le=100)
    detection_width: int = Field(default=540, ge=160, le=1920)
    tracking_width: int = Field(default=960, ge=160, le=1920)
    hair_update_hz: float = Field(default=6.0, ge=1, le=60)
    primary_confidence: float = Field(default=0.15, ge=0.05, le=1)
    score_threshold: float = Field(default=0.65, ge=0.05, le=1)
    hold_frames: int = Field(default=12, ge=0, le=120)
    encoder: Literal['auto','libx264','h264_nvenc','h264_qsv','h264_amf','h264_videotoolbox'] = 'auto'


class BlurOptions(BaseModel):
    """Per-job deface options exposed by the UI."""

    model_config = ConfigDict(extra='forbid')
    style: Literal['blur', 'mosaic', 'solid', 'img'] = 'mosaic'
    shape: Literal['ellipse', 'box'] = 'ellipse'
    mask_mode: Literal['face', 'face_hair_primary', 'hair_primary', 'face_hair_all'] = 'face'
    robust_tracking: bool = False
    mask_scale: float = Field(default=1.4, gt=0, le=3)
    mosaic_size: int = Field(default=20, ge=4, le=100)
    threshold: float = Field(default=0.2, gt=0, le=1)
    detection_size: int | None = Field(default=None)
    keep_audio: bool = True
    replace_image: Path | None = None
    local_options: LocalMosaicOptions | None = None

    @field_validator('mask_scale', 'threshold')
    @classmethod
    def finite_number(cls, value: float) -> float:
        if value != value or value in (float('inf'), float('-inf')):
            raise ValueError('必须是有限数字')
        return value

    @field_validator('detection_size')
    @classmethod
    def supported_detection_size(cls, value: int | None) -> int | None:
        if value is not None and value not in {320, 640, 960, 1280, 1920, 2560, 4096}:
            raise ValueError('检测尺寸必须选择 320、640、960、1280、1920、2560 或 4096')
        return value

    @model_validator(mode='after')
    def hair_modes_use_mosaic(self) -> 'BlurOptions':
        if self.mask_mode != 'face' and self.style != 'mosaic':
            raise ValueError('头发遮挡模式目前只支持马赛克样式')
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> 'BlurOptions':
        return cls(
            style=settings.deface_replacewith,
            mask_scale=settings.deface_mask_scale,
            mosaic_size=settings.deface_mosaic_size,
        )


def detection_dimensions(width: int, height: int, max_side: int) -> tuple[int, int]:
    scale = min(1.0, max_side / max(width, height))
    return (max(1, round(width * scale)), max(1, round(height * scale)))


def build_deface_command(
    input_path: Path,
    output_path: Path,
    settings: Settings,
    options: BlurOptions,
) -> list[str]:
    if options.mask_mode != 'face':
        raise ValueError('头发遮挡模式必须使用本地处理器')
    command = [settings.deface_bin, str(input_path), '--replacewith', options.style]
    command += ['--mask-scale', str(options.mask_scale), '--thresh', str(options.threshold)]
    if options.style == 'mosaic':
        command += ['--mosaicsize', str(options.mosaic_size)]
    if options.style == 'blur' and options.shape == 'box':
        command.append('--boxes')
    if options.style == 'img':
        if not options.replace_image or not options.replace_image.exists():
            raise ValueError('图片覆盖模式需要上传有效的替换图片')
        command += ['--replaceimg', str(options.replace_image)]
    if options.keep_audio:
        command.append('--keep-audio')
    if options.detection_size:
        capture = cv2.VideoCapture(str(input_path))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        capture.release()
        if width and height:
            detect_width, detect_height = detection_dimensions(width, height, options.detection_size)
            command += ['--scale', f'{detect_width}x{detect_height}']
    command += ['--output', str(output_path)]
    return command


def build_download_command(url: str, destination: Path, settings: Settings) -> list[str]:
    """Build the yt-dlp command, optionally carrying browser authentication."""
    template = str(destination.with_suffix('.%(ext)s'))
    command = [
        sys.executable,
        '-m',
        'yt_dlp',
        '--no-playlist',
        '--no-warnings',
        '--restrict-filenames',
    ]
    if settings.ytdlp_cookies_from_browser:
        command += ['--cookies-from-browser', settings.ytdlp_cookies_from_browser]
    elif settings.ytdlp_cookie_file:
        command += ['--cookies', str(settings.ytdlp_cookie_file)]
    command += ['-o', template, url]
    return command


def validate_import_source(url, settings):
    if getattr(settings,'user_id','') and not platform_for_url(url):
        raise MediaPipelineError('多用户工作台仅支持抖音、小红书、快手和 B 站分享链接；其他来源请先下载到本地再上传。')


def download_video(url: str, destination: Path, settings: Settings | None = None) -> Path:
    """Use TikHub for social sites and preserve generic yt-dlp compatibility."""
    settings = settings or Settings.from_env()
    url = extract_video_url(url)
    validate_import_source(url,settings)
    if platform_for_url(url):
        from .tikhub import download_tikhub_video
        return download_tikhub_video(url, destination, settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = build_download_command(url, destination, settings or Settings.from_env())
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        lower_detail = detail.lower()
        if 'could not copy chrome cookie database' in lower_detail:
            detail += '；浏览器正在占用 Cookie 数据库，请完全退出 Chrome/Edge 后重试。'
        elif 'fresh cookies' in lower_detail or ('cookies' in lower_detail and 'needed' in lower_detail):
            detail += '；抖音要求新鲜浏览器 Cookie，请在 Chrome/Edge 登录抖音后配置 YTDLP_COOKIES_FROM_BROWSER。'
        raise MediaPipelineError(f'视频链接下载失败：{detail}')
    candidates = sorted(destination.parent.glob(destination.stem + '.*'))
    candidates = [p for p in candidates if p.suffix.lower() not in {'.part', '.ytdl'}]
    if not candidates:
        raise MediaPipelineError('视频下载命令完成，但没有找到输出文件。')
    return candidates[0]


def run_deface(
    input_path: Path,
    output_path: Path,
    settings: Settings,
    options: BlurOptions | None = None,
) -> Path:
    """Run the configured local or external anonymization pipeline."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    options = options or BlurOptions.from_settings(settings)
    from . import redaction_service
    service = redaction_service.load_config(settings)
    from .preprocessing_limits import cloud_slots, local_redaction_slots
    from .run_phases import notify
    if service.mode == 'http':
        try:
            notify('waiting')
            with cloud_slots:
                notify('masking')
                return redaction_service.process(input_path, output_path, settings, options, service)
        except (MediaPipelineError, OSError) as external_error:
            reason = str(external_error) if isinstance(external_error, MediaPipelineError) else '外部打码文件处理失败。'
            logging.getLogger(__name__).warning('redaction local fallback: %s', reason)
            fd, name = tempfile.mkstemp(dir=output_path.parent, prefix='.mask-fallback-', suffix='.mp4')
            os.close(fd)
            temporary = Path(name)
            try:
                notify('waiting')
                with local_redaction_slots:
                    notify('masking')
                    _run_local_deface(input_path, temporary, settings, options)
                    redaction_service.validate_output(temporary, settings, input_path)
                temporary.replace(output_path)
                return output_path
            except (MediaPipelineError, OSError, subprocess.SubprocessError):
                raise MediaPipelineError(f'外部打码失败：{reason} 本地兜底打码也失败，请检查本地处理器和视频素材。') from None
            finally:
                temporary.unlink(missing_ok=True)
    notify('waiting')
    with local_redaction_slots:
        notify('masking')
        return _run_local_deface(input_path, output_path, settings, options)


def _run_local_deface(input_path, output_path, settings, options):
    if options.mask_mode != 'face':
        return run_local_mosaic(input_path, output_path, options)
    command = build_deface_command(input_path, output_path, settings, options)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=3600)
    except FileNotFoundError as exc:
        raise MediaPipelineError(
            '找不到 deface 命令。请先运行 pip install deface，或在 .env 中设置 DEFACE_BIN。'
        ) from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-3000:]
        raise MediaPipelineError(f'人脸打码失败：{detail}')
    if not output_path.exists():
        raise MediaPipelineError('deface 执行完成，但没有找到输出视频。')
    return output_path
