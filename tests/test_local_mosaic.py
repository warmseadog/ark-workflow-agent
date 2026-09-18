from pathlib import Path

import pytest

from app import local_mosaic, media
from app.config import settings


@pytest.mark.parametrize(
    ('mode', 'script_name'),
    [
        ('face_hair_primary', 'process_primary_face_mosaic.py'),
        ('hair_primary', 'process_primary_face_mosaic.py'),
        ('face_hair_all', 'process_all_faces_mosaic.py'),
    ],
)
def test_local_command_maps_mask_mode_to_bundled_processor(mode, script_name):
    options = media.BlurOptions(mask_mode=mode, robust_tracking=True)
    command = local_mosaic.build_local_mosaic_command(
        Path('input.mp4'), Path('output.mp4'), options,
    )
    assert command[0] == local_mosaic.sys.executable
    assert command[1].endswith(script_name)
    assert '--hair-only' in command if mode == 'hair_primary' else '--hair-only' not in command
    if mode == 'face_hair_all':
        assert '--robust' not in command
    else:
        assert '--robust' in command


def test_run_deface_dispatches_hair_mode_to_local_processor(monkeypatch, tmp_path):
    called = []

    def fake_local(input_path, output_path, options):
        called.append((input_path, output_path, options.mask_mode))
        output_path.write_bytes(b'local')
        return output_path

    monkeypatch.setattr(media, 'run_local_mosaic', fake_local, raising=False)
    output = media.run_deface(
        tmp_path / 'input.mp4', tmp_path / 'output.mp4', settings,
        media.BlurOptions(mask_mode='hair_primary'),
    )
    assert output.read_bytes() == b'local'
    assert called[0][2] == 'hair_primary'


def test_processor_scripts_expose_hair_only_mode():
    root = Path('storage/attached_local_face_mosaic_v3/local-face-mosaic-tracking/scripts')
    primary = (root / 'process_primary_face_mosaic.py').read_text(encoding='utf-8')
    all_faces = (root / 'process_all_faces_mosaic.py').read_text(encoding='utf-8')
    assert "--hair-only" in primary
    assert "--hair-only" in all_faces
    assert "if not args.hair_only" in primary
    assert "if not args.hair_only" in all_faces


def test_hair_segmenter_loads_model_bytes_for_unicode_workspace_paths():
    source = Path('storage/attached_local_face_mosaic_v3/local-face-mosaic-tracking/scripts/hair_mosaic.py').read_text(encoding='utf-8')
    assert 'model_asset_buffer=model.read_bytes()' in source


def test_all_face_detector_has_unicode_path_fallback():
    source = Path('storage/attached_local_face_mosaic_v3/local-face-mosaic-tracking/scripts/process_all_faces_mosaic.py').read_text(encoding='utf-8')
    assert 'NamedTemporaryFile' in source
    assert 'model.read_bytes()' in source
