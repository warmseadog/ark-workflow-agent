from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from .media_errors import MediaPipelineError

if TYPE_CHECKING:
    from .media import BlurOptions


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = PROJECT_ROOT / 'storage' / 'attached_local_face_mosaic_v3' / 'local-face-mosaic-tracking' / 'scripts'
SCRIPT_BY_MODE = {
    'face_hair_primary': 'process_primary_face_mosaic.py',
    'hair_primary': 'process_primary_face_mosaic.py',
    'face_hair_all': 'process_all_faces_mosaic.py',
}


def build_local_mosaic_command(
    input_path: Path,
    output_path: Path,
    options: 'BlurOptions',
) -> list[str]:
    """Build a command for the bundled local face/hair processor."""
    try:
        script_name = SCRIPT_BY_MODE[options.mask_mode]
    except KeyError as exc:
        raise ValueError(f'本地头发处理器不支持模式：{options.mask_mode}') from exc
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        raise MediaPipelineError(f'本地头发处理脚本不存在：{script_path}')

    command = [sys.executable, str(script_path), str(input_path), str(output_path)]
    if options.mask_mode == 'hair_primary':
        command.append('--hair-only')
    if options.mask_mode in {'face_hair_primary', 'hair_primary'} and options.robust_tracking:
        command.append('--robust')
    controls = options.local_options
    if controls is not None:
        command += ['--mosaic-size', str(options.mosaic_size), '--mask-scale', str(options.mask_scale),
                    '--hair-mosaic-size', str(controls.hair_mosaic_size),
                    '--detection-width', str(controls.detection_width),
                    '--hair-update-hz', str(controls.hair_update_hz), '--encoder', controls.encoder]
        if not options.keep_audio:
            command.append('--no-audio')
        if options.mask_mode == 'face_hair_all':
            command += ['--score-threshold', str(controls.score_threshold), '--hold-frames', str(controls.hold_frames)]
        else:
            command += ['--primary-confidence', str(controls.primary_confidence),
                        '--tracking-width', str(controls.tracking_width)]
    return command


def run_local_mosaic(
    input_path: Path,
    output_path: Path,
    options: 'BlurOptions',
) -> Path:
    """Run the bundled local processor and return its generated MP4."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_local_mosaic_command(input_path, output_path, options)
    try:
        result = subprocess.run(
            command,
            cwd=SCRIPT_DIR,
            capture_output=True,
            text=True,
            timeout=3600,
            check=False,
        )
    except OSError as exc:
        raise MediaPipelineError(f'无法启动本地头发处理器：{exc}') from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()[-3000:]
        raise MediaPipelineError(f'本地人脸/头发打码失败：{detail}')
    if not output_path.exists():
        raise MediaPipelineError('本地头发处理器执行完成，但没有找到输出视频。')
    return output_path

