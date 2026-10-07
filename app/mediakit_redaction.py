"""Volcengine MediaKit upload, asynchronous face masking and result download."""
import ipaddress
import json
from dataclasses import asdict
import mimetypes
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlsplit, quote

import requests

from .media_errors import MediaPipelineError
from .secure_transport import validate_endpoint

HOST = 'https://mediakit.cn-beijing.volces.com'
TASK_PATH = '/api/v1/tools/face-blur-video'


def process_isolated(input_path, output_path, settings, options, config):
    """Bound even a trickling response/upload by terminating the cloud worker."""
    # Check unsupported options before spawning or sending any source bytes.
    parameters(options)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_path.parent, prefix='.mask-cloud-') as directory:
        result_path = Path(directory)/'result.mp4'
        payload = {'input': str(input_path.resolve()), 'output': str(result_path.resolve()),
                   'max_upload_mb': settings.max_upload_mb, 'config': asdict(config),
                   'options': options.model_dump(mode='json', exclude={'replace_image'})}
        started = time.monotonic()
        _run_isolated_worker(payload, config.timeout_seconds)
        if not result_path.is_file():
            raise MediaPipelineError('火山打码进程处理失败。')
        # Only one heavyweight decoder runs at a time, in a killable child.
        # Queue time does not consume its remaining active-processing budget.
        budget = min(120, config.timeout_seconds - (time.monotonic() - started))
        if budget <= 0:
            raise MediaPipelineError('火山打码超时。')
        from .preprocessing_limits import local_lock
        from .run_phases import notify
        notify('waiting')
        with local_lock:
            notify('masking')
            _run_isolated_worker({'validate_only': True, 'input': payload['input'],
                'output': payload['output'], 'max_upload_mb': settings.max_upload_mb,
                'timeout_seconds': budget}, budget)
        result_path.replace(output_path)
        return output_path


def _run_isolated_worker(payload, timeout_seconds):
    child = None
    try:
        child = subprocess.Popen([sys.executable, '-m', 'app.mediakit_redaction'],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env={**os.environ, 'PYTHONUTF8': '1', 'PYTHON_DOTENV_DISABLED': '1'},
            start_new_session=os.name != 'nt')
        stdout, _ = child.communicate(json.dumps(payload).encode(), timeout=timeout_seconds)
        response = json.loads(stdout)
        if child.returncode != 0 or response.get('ok') is not True:
            raise MediaPipelineError(response.get('error') or '火山打码进程处理失败。')
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            import signal
            try: os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        else:
            subprocess.run(['taskkill', '/PID', str(child.pid), '/T', '/F'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW)
            if child.poll() is None:
                child.kill()
        child.communicate()
        raise MediaPipelineError('火山打码超时，已结束外部处理等待。') from None
    except (OSError, ValueError, TypeError):
        raise MediaPipelineError('火山打码进程启动失败或响应无效。') from None


def matches(endpoint):
    return urlsplit(endpoint).hostname == 'mediakit.cn-beijing.volces.com'


def public_media_url(value):
    """Provider-supplied signed URLs must not address internal services."""
    value = validate_endpoint(value, allow_local=False, allow_query=True)
    parsed = urlsplit(value)
    if parsed.port not in (None, 443):
        raise ValueError('媒体地址端口不受支持。')
    addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('媒体地址不可指向内网。')
    return value


def parameters(options):
    if (options.mask_mode != 'face' or options.style not in {'mosaic', 'blur'}
            or not options.keep_audio or not 1 < options.mask_scale <= 2 or options.threshold < 0.1):
        raise MediaPipelineError('火山打码不支持当前遮挡参数，改用本地处理。')
    # deface.scale_bb expands EACH edge by (mask_scale - 1), as does MediaKit.
    return {'mask_mode': options.style,
            'mask_strength': 'low' if options.mosaic_size < 12 else 'medium' if options.mosaic_size <= 30 else 'high',
            'face_box_expand': options.mask_scale - 1,
            'face_confidence': options.threshold}


def process(input_path, output_path, settings, options, config, *, validate_result=True):
    body = parameters(options)
    endpoint = validate_endpoint(config.endpoint, allow_local=False)
    if endpoint not in {HOST, HOST+'/api/v1', HOST+TASK_PATH}:
        raise MediaPipelineError('火山打码接口路径不正确，请填写 MediaKit 服务域名。')
    if not config.api_key:
        raise MediaPipelineError('火山打码未配置 API Key。')
    deadline = time.monotonic() + config.timeout_seconds
    headers = {'Authorization': 'Bearer '+config.api_key, 'Accept': 'application/json'}

    def remaining():
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            raise MediaPipelineError('火山打码超时。')
        return seconds

    def timeout():
        seconds = remaining()
        return (min(15, seconds), min(60, seconds))

    def api(method, path, **kw):
        with method(HOST+path, headers=headers, timeout=timeout(), allow_redirects=False, **kw) as response:
            if response.status_code != 200:
                raise MediaPipelineError(f'火山打码请求失败（HTTP {response.status_code}）。')
            result = response.json()
        if not isinstance(result, dict) or result.get('success') is not True:
            raise MediaPipelineError('火山打码接口拒绝请求或响应无效，请检查 Key、权限和参数。')
        remaining()
        return result

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=output_path.parent, prefix='.mask-', suffix='.mp4')
    os.close(fd)
    temporary = Path(name)
    try:
        upload = api(requests.post, '/api/v1/tools-sync/request-media-upload-url', json={})['result']
        if not isinstance(upload, dict) or upload.get('method') != 'PUT':
            raise ValueError('invalid upload response')
        url = public_media_url(upload['upload_url'])
        file_id = upload['file_id']
        if not isinstance(file_id, str) or not re.fullmatch(r'(?:mediakit://)?[A-Za-z0-9_=-]+', file_id):
            raise ValueError('invalid file id')
        upload_headers = {h['key']: h['value'] for h in upload.get('upload_headers', [])}
        if not all(isinstance(k, str) and isinstance(v, str) and '\n' not in k+v and '\r' not in k+v
                   for k, v in upload_headers.items()):
            raise ValueError('invalid upload headers')
        if not any(k.lower() == 'content-type' for k in upload_headers):
            upload_headers['Content-Type'] = mimetypes.guess_type(input_path.name)[0] or 'application/octet-stream'
        with input_path.open('rb') as stream:
            with requests.put(url, data=stream, headers=upload_headers, timeout=timeout(), allow_redirects=False) as response:
                if not 200 <= response.status_code < 300:
                    raise MediaPipelineError(f'火山打码视频上传失败（HTTP {response.status_code}）。')
        body['video_url'] = file_id if file_id.startswith('mediakit://') else 'mediakit://'+file_id
        submitted = api(requests.post, TASK_PATH, json=body)
        task_id = submitted.get('task_id')
        if not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}', task_id):
            raise ValueError('invalid task id')
        # Never resubmit a possibly accepted task. Poll only this ID until the deadline.
        while True:
            result = api(requests.get, '/api/v1/tasks/'+quote(task_id, safe=''))
            status = result.get('status')
            if status in {'completed', 'succeeded'}:
                url = public_media_url(result['result']['video_url'])
                break
            if status in {'failed', 'cancelled', 'canceled'}:
                raise MediaPipelineError('火山打码任务处理失败。')
            if status not in {'running', 'pending', 'queued', 'processing'}:
                raise MediaPipelineError('火山打码任务状态无效。')
            time.sleep(min(2, remaining()))
        with requests.get(url, stream=True, timeout=timeout(), allow_redirects=False) as response:
            if response.status_code != 200:
                raise MediaPipelineError(f'火山打码结果下载失败（HTTP {response.status_code}）。')
            size = 0
            with temporary.open('wb') as target:
                for chunk in response.iter_content(chunk_size=1024*1024):
                    remaining()
                    size += len(chunk)
                    if size > settings.max_upload_mb * 1024 * 1024:
                        raise MediaPipelineError('火山打码结果超过视频大小上限。')
                    target.write(chunk)
        if validate_result:
            from .redaction_service import validate_output
            from .preprocessing_limits import local_lock
            with local_lock:
                validate_output(temporary, settings, input_path, timeout_seconds=remaining())
        remaining()
        temporary.replace(output_path)
        return output_path
    except requests.Timeout:
        raise MediaPipelineError('火山打码超时。') from None
    except requests.RequestException:
        raise MediaPipelineError('火山打码连接失败。') from None
    except (ValueError, KeyError, TypeError, OSError):
        raise MediaPipelineError('火山打码上传或结果响应无效。') from None
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    from types import SimpleNamespace
    try:
        payload = json.load(sys.stdin)
        if payload.get('validate_only'):
            from .redaction_service import validate_output
            validate_output(Path(payload['output']), SimpleNamespace(max_upload_mb=payload['max_upload_mb']),
                            Path(payload['input']), timeout_seconds=payload['timeout_seconds'])
        else:
            process(Path(payload['input']), Path(payload['output']),
                SimpleNamespace(max_upload_mb=payload['max_upload_mb']),
                SimpleNamespace(**payload['options']), SimpleNamespace(**payload['config']),
                validate_result=False)
        print(json.dumps({'ok': True}))
    except MediaPipelineError as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        sys.exit(1)
    except Exception:
        print(json.dumps({'ok': False, 'error': '火山打码进程处理失败。'}))
        sys.exit(1)
