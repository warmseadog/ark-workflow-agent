from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Literal

import cv2
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import Settings
from .local_mosaic import run_local_mosaic
from .media_errors import MediaPipelineError


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


def download_video(url: str, destination: Path) -> Path:
    """Download one video URL using yt-dlp into a deterministic path."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    template = str(destination.with_suffix('.%(ext)s'))
    command = [
        sys.executable,
        '-m',
        'yt_dlp',
        '--no-playlist',
        '--no-warnings',
        '--restrict-filenames',
        '-o',
        template,
        url,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-2000:]
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
    """Run the selected local face or hair-aware anonymization pipeline."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    options = options or BlurOptions.from_settings(settings)
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
