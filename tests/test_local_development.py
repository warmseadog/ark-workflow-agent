"""Onboarding isolation, and real Windows media regressions (no cloud calls)."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def dev_module():
    path = ROOT / 'tools' / 'dev.py'
    assert path.exists(), 'The shared local development launcher is missing'
    spec = importlib.util.spec_from_file_location('local_dev', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_demo_environment_does_not_inherit_credentials_or_databases(tmp_path):
    dev = dev_module()
    (tmp_path / '.env').write_text('SEEDANCE_API_KEY=production-secret\nSTORAGE_DIR=storage\n')
    inherited = {'PATH': os.environ['PATH'], 'SEEDANCE_API_KEY': 'secret',
                 'DATABASE_URL': 'sqlite:///production.db', 'WORKFLOW_DB': 'production.db',
                 'APP_AUTH_ENABLED': 'true', 'TOS_ACCESS_KEY': 'secret'}
    env = dev.prepare_environment(tmp_path, 'demo', 8000, inherited)
    assert env['STORAGE_DIR'] == str(tmp_path / '.dev-data' / 'demo')
    assert env['WORKFLOW_DB'].startswith(env['STORAGE_DIR'])
    assert env['DATABASE_URL'].endswith('/.dev-data/demo/studio.db')
    assert not env.get('SEEDANCE_API_KEY') and not env.get('TOS_ACCESS_KEY')
    assert env['PYTHON_DOTENV_DISABLED'] == '1'
    assert env['APP_COOKIE_SECURE'] == 'false'
    assert env['SEEDANCE_MODE'] == 'mock'
    assert not (tmp_path / 'storage').exists()


def test_profiles_have_distinct_data_and_integration_requires_explicit_config(tmp_path):
    dev = dev_module()
    with pytest.raises(ValueError, match='env.integration'):
        dev.prepare_environment(tmp_path, 'integration', 8000, {})
    (tmp_path / '.env.integration').write_text('SEEDANCE_MODE=http\nSEEDANCE_API_KEY=test-key\n')
    env = dev.prepare_environment(tmp_path, 'integration', 8123, {})
    assert env['STORAGE_DIR'].endswith(str(Path('.dev-data/integration')))
    assert env['APP_PUBLIC_ORIGIN'] == 'http://127.0.0.1:8123'
    assert env['SEEDANCE_API_KEY'] == 'test-key'


@pytest.mark.parametrize('key,value', [('STORAGE_DIR','storage'),('WORKFLOW_DB','old.db'),
    ('DATABASE_URL','sqlite:///old.db'),('APP_HOST','0.0.0.0'),('PYTHONPATH','other')])
def test_integration_cannot_override_isolation(tmp_path, key, value):
    dev = dev_module()
    (tmp_path / '.env.integration').write_text(f'{key}={value}\n')
    with pytest.raises(ValueError, match=key):
        dev.prepare_environment(tmp_path, 'integration', 8000, {})


def test_demo_rejects_saved_real_provider_settings_without_overwriting(tmp_path):
    dev = dev_module()
    env = dev.prepare_environment(tmp_path, 'demo', 8000, {})
    path = Path(env['STORAGE_DIR']) / 'private' / 'generation-settings.json'
    before = '{"mode":"http","api_key":"must-not-be-erased"}'
    path.write_text(before)
    with pytest.raises(ValueError, match='demo'):
        dev.prepare_environment(tmp_path, 'demo', 8000, {})
    assert path.read_text() == before


def test_demo_network_guard_blocks_remote_but_allows_loopback():
    dev = dev_module()
    assert dev.is_loopback('127.0.0.1')
    assert dev.is_loopback('::1')
    assert not dev.is_loopback('example.com')
    assert not dev.is_loopback('169.254.169.254')


def test_demo_guard_blocks_before_dns_or_connection():
    # Separate interpreter: audit hooks are intentionally irreversible per process.
    code = '''
from tools.dev import install_network_guard
import socket
install_network_guard()
for operation in [lambda: socket.getaddrinfo('should-never-resolve.invalid',443),
                  lambda: socket.socket().connect(('203.0.113.1',443))]:
    try: operation()
    except OSError as exc: assert 'Local demo blocks' in str(exc)
    else: raise AssertionError('External call escaped the guard')
print('blocked')
'''
    result = subprocess.run([sys.executable,'-c',code],cwd=ROOT,
        capture_output=True,text=True,timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'blocked'


@pytest.mark.skipif(sys.platform != 'win32', reason='PowerShell entry point')
def test_powershell_check_works_from_another_directory(tmp_path):
    import shutil
    shell = shutil.which('pwsh') or shutil.which('powershell')
    if not shell or not (ROOT/'.venv-dev/Scripts/python.exe').is_file():
        pytest.skip('Install the development environment to test its PowerShell entry point')
    result = subprocess.run([shell,'-NoProfile','-ExecutionPolicy','Bypass','-File',
        str(ROOT/'run.ps1'),'-Check'],cwd=tmp_path,capture_output=True,timeout=45)
    assert result.returncode == 0, result.stderr.decode('utf-8',errors='replace')
    output = result.stdout.decode('utf-8',errors='replace')
    assert '.venv-dev' in output and '"video_mode": "mock"' in output


@pytest.mark.skipif(sys.platform != 'win32', reason='Windows launcher regression')
def test_default_deface_uses_current_interpreter_not_path(monkeypatch):
    from app.config import settings
    from app.media import build_deface_command, BlurOptions
    from dataclasses import replace
    command = build_deface_command(Path('input.mp4'), Path('output.mp4'),
                                   replace(settings, deface_bin='deface'), BlurOptions())
    assert command[0] == sys.executable
    assert command[1].endswith('deface_runner.py')


def test_custom_deface_command_is_preserved():
    from app.config import settings
    from app.media import build_deface_command, BlurOptions
    from dataclasses import replace
    command = build_deface_command(Path('input.mp4'), Path('output.mp4'),
                                   replace(settings, deface_bin='custom-deface'), BlurOptions())
    assert command[0] == 'custom-deface'


@pytest.mark.parametrize('mode', ['face', 'hair_primary'])
def test_media_subprocess_keeps_utf8_error_details(tmp_path, monkeypatch, mode):
    from app import local_mosaic
    from app.config import settings
    from app.media import run_deface, BlurOptions, MediaPipelineError
    from dataclasses import replace
    script = tmp_path / 'process_primary_face_mosaic.py'
    script.write_text("import sys\nsys.stderr.buffer.write('模型读取失败'.encode('utf-8'))\nsys.exit(1)\n", encoding='utf-8')
    monkeypatch.setattr(local_mosaic, 'SCRIPT_DIR', tmp_path)
    with pytest.raises(MediaPipelineError, match='模型读取失败'):
        run_deface(script, tmp_path/'out.mp4', replace(settings, storage_dir=tmp_path,
            config_root=tmp_path, deface_bin=sys.executable), BlurOptions(mask_mode=mode))


@pytest.mark.parametrize('mode', ['face', 'hair_primary', 'face_hair_primary', 'face_hair_all'])
def test_real_media_pipeline_in_unicode_workspace(tmp_path, mode):
    """Actually load models and encode video: mocks missed these path failures."""
    import imageio_ffmpeg
    from app.config import settings
    from app.media import run_deface, BlurOptions
    from dataclasses import replace
    directory = tmp_path / '中文素材'
    directory.mkdir()
    source, output = directory / '动作.mp4', directory / '结果.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-v', 'error', '-f',
                    'lavfi', '-i', 'color=c=blue:s=320x240:d=0.3', '-c:v', 'libx264',
                    str(source)], check=True, timeout=30, capture_output=True)
    run_deface(source, output, replace(settings, storage_dir=tmp_path, config_root=tmp_path,
               deface_bin='deface'), BlurOptions(mask_mode=mode, keep_audio=False))
    import cv2
    capture = cv2.VideoCapture(str(output))
    try:
        assert capture.isOpened()
        assert capture.read()[0]
        assert int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)) == 320
    finally:
        capture.release()
