from pathlib import Path

import pytest
from pydantic import ValidationError

from app import media
from app.config import settings


def test_mask_scale_defaults_to_1_4_without_changing_other_defaults():
    options = media.BlurOptions()
    assert options.mask_scale == 1.4
    assert options.style == 'mosaic'
    assert options.shape == 'ellipse'
    assert options.mosaic_size == 20
    assert options.threshold == 0.2
    assert options.detection_size is None
    assert options.keep_audio is True
    assert options.mask_mode == 'face'
    assert options.robust_tracking is False


def test_task_options_reach_deface_without_changing_server_defaults():
    options = media.BlurOptions(style='mosaic', mask_scale=1.0, mosaic_size=32, threshold=0.4, keep_audio=False)
    command = media.build_deface_command(Path('input.mp4'), Path('output.mp4'), settings, options)
    assert command[command.index('--mask-scale') + 1] == '1.0'
    assert command[command.index('--mosaicsize') + 1] == '32'
    assert command[command.index('--thresh') + 1] == '0.4'
    assert '--keep-audio' not in command
    defaults = media.BlurOptions.from_settings(settings)
    assert defaults.mask_scale == settings.deface_mask_scale
    assert settings.deface_mask_scale == 1.4


@pytest.mark.parametrize('values', [
    {'mask_scale': 0}, {'mask_scale': float('nan')}, {'mosaic_size': 0},
    {'threshold': 1.1}, {'style': 'none'}, {'detection_size': 999}, {'shape': 'triangle'},
])
def test_invalid_options_are_rejected(values):
    with pytest.raises(ValidationError):
        media.BlurOptions(**values)


def test_hair_modes_require_mosaic_style():
    for mode in ['face_hair_primary', 'hair_primary', 'face_hair_all']:
        options = media.BlurOptions(mask_mode=mode)
        assert options.mask_mode == mode
        with pytest.raises(ValidationError, match='马赛克'):
            media.BlurOptions(mask_mode=mode, style='blur')


def test_shape_only_applies_to_blur_and_image_requires_upload():
    for style in ['blur', 'mosaic', 'solid']:
        command = media.build_deface_command(Path('in.mp4'), Path('out.mp4'), settings, media.BlurOptions(style=style, shape='box'))
        assert ('--boxes' in command) == (style == 'blur')
        assert ('--mosaicsize' in command) == (style == 'mosaic')
    with pytest.raises(ValueError, match='图片'):
        media.build_deface_command(Path('in.mp4'), Path('out.mp4'), settings, media.BlurOptions(style='img'))


def test_detection_resize_preserves_portrait_aspect_ratio():
    assert media.detection_dimensions(1080, 1920, 640) == (360, 640)
    assert media.detection_dimensions(1920, 1080, 1280) == (1280, 720)
    assert media.detection_dimensions(320, 180, 640) == (320, 180)
