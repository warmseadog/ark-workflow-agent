"""One local entry point. No SSH, deployment, production config import or cloud setup."""
from __future__ import annotations

import argparse
import importlib
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
MANAGED = ('APP_', 'SEEDANCE_', 'TOS_', 'ARK_', 'PORTRAIT_', 'TIKHUB_',
           'YTDLP_', 'WORKFLOW_', 'DEFACE_')
NAMES = {'STORAGE_DIR', 'DATABASE_URL', 'MAX_UPLOAD_MB', 'PYTHON_DOTENV_DISABLED',
         'PYTHONPATH', 'PYTHONHOME', 'UVICORN_APP', 'UVICORN_HOST', 'UVICORN_PORT'}
INTEGRATION_KEYS = {'SEEDANCE_MODE', 'SEEDANCE_API_URL', 'SEEDANCE_API_KEY',
    'TIKHUB_API_KEY', 'TOS_ENABLED', 'TOS_REGION', 'TOS_ENDPOINT', 'TOS_BUCKET',
    'TOS_ACCESS_KEY', 'TOS_SECRET_KEY', 'PORTRAIT_PUBLIC_BASE_URL', 'MAX_UPLOAD_MB'}


def is_loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def install_network_guard():
    """Demo subprocess only: fail before DNS/connect, including redirects."""
    def guard(event, args):
        if event == 'socket.getaddrinfo':
            host = args[0]
            if host is not None and not is_loopback(host):
                raise OSError('Local demo blocks cloud access; use the integration profile.')
        elif event in {'socket.connect', 'socket.sendto'}:
            address = args[-1]
            if isinstance(address, tuple) and not is_loopback(address[0]):
                raise OSError('Local demo blocks cloud access; use the integration profile.')
    sys.addaudithook(guard)


def prepare_environment(root, profile, port, inherited=None):
    root = Path(root).resolve()
    if profile not in {'demo', 'integration'} or not 1024 <= port <= 65535:
        raise ValueError('Use demo/integration and a port between 1024 and 65535.')
    inherited = dict(os.environ if inherited is None else inherited)
    env = {k: v for k, v in inherited.items()
           if k.upper() not in NAMES and not k.upper().startswith(MANAGED)}
    values = {}
    if profile == 'integration':
        path = root / '.env.integration'
        if not path.is_file():
            raise ValueError('Copy .env.integration.example to .env.integration; use TEST credentials only.')
        from dotenv import dotenv_values
        values = dotenv_values(path, interpolate=False)
        unknown = set(values) - INTEGRATION_KEYS
        if unknown:
            raise ValueError('Unsupported .env.integration keys: ' + ', '.join(sorted(unknown)))
        values = {k: v or '' for k, v in values.items()}
    data = root / '.dev-data' / profile
    if data.resolve() != data or (root / '.dev-data').is_symlink():
        raise ValueError('Local data directory cannot redirect to another location.')
    private = data / 'private'
    private.mkdir(parents=True, exist_ok=True)
    if profile == 'demo':
        defaults = {
            'generation-settings.json': {'mode': 'mock'},
            'storage-settings.json': {'enabled': False},
            'redaction-service.json': {'mode': 'local'},
            'continuation-settings.json': {'enabled': False},
            'variation-settings.json': {'enabled': False},
            'inspiration-settings.json': {'enabled': False},
        }
        for name, content in defaults.items():
            path = private / name
            if path.exists():
                saved = json.loads(path.read_text(encoding='utf-8'))
                if any(saved.get(k) != v for k, v in content.items()):
                    raise ValueError(f'demo config changed: {name}; use integration for real services. File retained.')
            else:
                with path.open('x', encoding='utf-8') as stream:
                    json.dump(content, stream)
        # No ambient proxy should tunnel demo traffic through a local proxy.
        env = {k:v for k,v in env.items() if k.upper() not in {'HTTP_PROXY','HTTPS_PROXY','ALL_PROXY'}}
        env['NO_PROXY'] = '*'
    env.update(values)
    env.update({
        'PYTHON_DOTENV_DISABLED':'1', 'PYTHONUTF8':'1', 'PYTHONIOENCODING':'utf-8',
        'APP_LOCAL_DEV_PROFILE':profile, 'APP_HOST':'127.0.0.1', 'APP_PORT':str(port),
        'APP_PUBLIC_ORIGIN':f'http://127.0.0.1:{port}', 'APP_AUTH_ENABLED':'false',
        'APP_COOKIE_SECURE':'false', 'APP_VIDEO_WORKERS':'2', 'APP_SUBMISSION_WORKERS':'2',
        'APP_REDACTION_CONCURRENCY':'2', 'APP_MAX_QUEUED_TOTAL':'10',
        'STORAGE_DIR':str(data), 'DATABASE_URL':'sqlite:///' + (data/'studio.db').as_posix(),
        'WORKFLOW_DB':str(data/'workflow.db'), 'WORKFLOW_STORAGE':str(data/'workflow'),
        'DEFACE_BIN':'deface', 'YTDLP_COOKIES_FROM_BROWSER':'', 'YTDLP_COOKIE_FILE':'',
    })
    if profile == 'demo':
        env.update(SEEDANCE_MODE='mock', TOS_ENABLED='false', PORTRAIT_PUBLIC_BASE_URL='')
    # Child media processes inherit these exact paths; they cannot find global deface.
    env['PATH'] = str(Path(sys.executable).parent) + os.pathsep + env.get('PATH', '')
    return env


def check():
    from importlib.metadata import version
    checks = []
    for name in ['fastapi','uvicorn','dotenv','cv2','mediapipe','imageio_ffmpeg','deface','sqlalchemy','tos','PIL']:
        try:
            importlib.import_module(name)
            checks.append({'check': name, 'ok': True})
        except (ImportError, OSError) as exc:
            checks.append({'check':name,'ok':False,'error':type(exc).__name__})
    try:
        dotenv_version = tuple(int(x) for x in version('python-dotenv').split('.')[:2])
        checks.append({'check':'dotenv isolation >= 1.2', 'ok':dotenv_version >= (1,2)})
        import imageio_ffmpeg
        checks.append({'check':'bundled FFmpeg', 'ok':Path(imageio_ffmpeg.get_ffmpeg_exe()).is_file()})
    except Exception as exc:
        checks.append({'check':'runtime', 'ok':False, 'error':type(exc).__name__})
    for name in ['face_detection_yunet_2023mar.onnx','selfie_multiclass_256x256.tflite']:
        path = ROOT/'storage/attached_local_face_mosaic_v3/local-face-mosaic-tracking/models'/name
        checks.append({'check':name, 'ok':path.is_file() and path.stat().st_size > 1000})
    checks.append({'check':'Python 3.10-3.12', 'ok':(3,10) <= sys.version_info[:2] < (3,13)})
    print(json.dumps({'python':sys.executable,'profile':os.environ['APP_LOCAL_DEV_PROFILE'],
        'data':os.environ['STORAGE_DIR'],'checks':checks},ensure_ascii=False,indent=2), flush=True)
    if all(item['ok'] for item in checks):
        from app.config import settings
        from app import continuation_settings, variation_settings, inspiration_settings, generation_settings, storage_settings
        capabilities = {'video_mode':generation_settings.load_config(settings).mode,
            'storage_ready':storage_settings.load_config(settings).ready,
            'continuation_ready':not bool(continuation_settings.load_config(settings).problem()),
            'variation_ready':not bool(variation_settings.load_config(settings).problem()),
            'inspiration_ready':not bool(inspiration_settings.load_config(settings).resolved(settings).problem())}
        print(json.dumps({'capabilities':capabilities},ensure_ascii=False), flush=True)
        return 0
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run','check','test','smoke'])
    parser.add_argument('--profile', choices=['demo','integration'], default='demo')
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--reload', action='store_true')
    args, extra = parser.parse_known_args()
    if extra and args.command != 'test':
        parser.error('Unexpected arguments: ' + ' '.join(extra))
    os.chdir(ROOT)
    env = prepare_environment(ROOT, args.profile, args.port)
    os.environ.clear()
    os.environ.update(env)
    if args.command == 'test':
        # Tests always get an empty disposable data root, even after integration use.
        import tempfile
        with tempfile.TemporaryDirectory(prefix='test-', dir=ROOT/'.dev-data') as folder:
            test_env = prepare_environment(Path(folder), 'demo', args.port, env)
            # Most tests exercise normal application semantics. Individual demo
            # tests opt into local_demo_enabled explicitly, like the launcher.
            test_env.pop('APP_LOCAL_DEV_PROFILE', None)
            test_env['APP_PUBLIC_ORIGIN'] = ''
            command = [sys.executable,'-m','pytest',*(extra or [
                'tests/test_local_development.py','tests/test_local_demo_submission.py',
                'tests/test_production_api.py','tests/test_production_worker.py',
                'tests/test_local_mosaic.py','tests/test_local_mask_controls.py',
                'tests/test_blur_settings.py','tests/test_generation_settings.py']),
                '-p','no:cacheprovider','--basetemp',str(Path(folder)/'pytest')]
            return subprocess.call(command, cwd=ROOT, env=test_env)
    if args.command == 'smoke':
        from tools.dev_smoke import run
        return run()
    if check():
        return 1
    if args.command == 'check':
        return 0
    print(f'Local {args.profile}: http://127.0.0.1:{args.port}/__dev', flush=True)
    print('Cloud features require integration credentials; demo output is NOT AI generation.', flush=True)
    import uvicorn
    uvicorn.run('tools.dev_app:app', host='127.0.0.1', port=args.port, reload=args.reload)
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as exc:
        print(f'Local development: {exc}', file=sys.stderr)
        raise SystemExit(1)
