"""Local masking by default, or a synchronous multipart/MP4 adapter."""
from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
import time

import cv2
import requests

from .media_errors import MediaPipelineError
from .secure_transport import validate_endpoint
from .tenancy import config_root

_lock = RLock()


@dataclass(frozen=True)
class ServiceConfig:
    mode: str = 'local'
    endpoint: str = ''
    api_key: str = field(default='', repr=False)
    timeout_seconds: int = 600

    def public(self):
        return {'mode': self.mode, 'endpoint': self.endpoint,
                'has_api_key': bool(self.api_key), 'timeout_seconds': self.timeout_seconds}


def config_path(settings):
    return config_root(settings) / 'private' / 'redaction-service.json'


def load_config(settings):
    if settings.redaction_service is not None:
        return settings.redaction_service
    with _lock:
        path = config_path(settings)
        return ServiceConfig(**json.loads(path.read_text(encoding='utf-8'))) if path.exists() else ServiceConfig()


def save_config(settings, payload):
    with _lock:
        current = load_config(settings)
        if set(payload) - set(asdict(current)) - {'clear_api_key'}:
            raise ValueError('包含不支持的打码服务配置。')
        values = {**asdict(current), **{k:v for k,v in payload.items() if k != 'clear_api_key'}}
        for name in ('mode', 'endpoint', 'api_key'):
            value = values[name]
            if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
                raise ValueError('打码服务配置格式不正确。')
            values[name] = value.strip()
        if values['mode'] not in {'local', 'http'}:
            raise ValueError('请选择本地打码或外部 API。')
        if values['endpoint']:
            values['endpoint'] = validate_endpoint(values['endpoint'])
        elif values['mode'] == 'http':
            raise ValueError('使用外部 API 时请填写接口地址。')
        if type(values['timeout_seconds']) is not int or not 10 <= values['timeout_seconds'] <= 3600:
            raise ValueError('超时时间应为 10–3600 秒。')
        if type(payload.get('clear_api_key', False)) is not bool:
            raise ValueError('清除密钥选项不正确。')
        key = payload.get('api_key', '').strip()
        if any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ValueError('API Key 应为不含空格的英文字符。')
        values['api_key'] = '' if payload.get('clear_api_key') else (key or (current.api_key if values['endpoint'] == current.endpoint else ''))
        config = ServiceConfig(**values)
        path = config_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(dir=path.parent, prefix='.redaction-service-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(asdict(config), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
        return config


def freeze(settings):
    return replace(settings, redaction_service=load_config(settings))


def fingerprint(settings):
    config = load_config(settings)
    if config.mode == 'local':
        return settings.deface_bin + ':v1'
    return 'external-v1:' + hashlib.sha256(json.dumps(asdict(config), sort_keys=True).encode()).hexdigest()


def process(input_path, output_path, settings, options, config):
    try:
        endpoint = validate_endpoint(config.endpoint)
    except ValueError as exc:
        raise MediaPipelineError(str(exc)) from None
    if options.style == 'img':
        raise MediaPipelineError('外部打码 API 不支持图片覆盖样式，请改用本地处理。')
    headers = {'Accept': 'video/mp4'}
    if config.api_key:
        headers['Authorization'] = 'Bearer ' + config.api_key
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=output_path.parent, prefix='.mask-', suffix='.mp4')
    os.close(fd)
    temporary = Path(name)
    started = time.monotonic()
    try:
        with input_path.open('rb') as source:
            with requests.post(endpoint, headers=headers,
                    files={'video': (input_path.name, source, 'video/mp4')},
                    data={'options': json.dumps(options.model_dump(mode='json', exclude={'replace_image'}))},
                    timeout=(min(15, config.timeout_seconds), config.timeout_seconds),
                    stream=True, allow_redirects=False) as response:
                if response.status_code != 200:
                    raise MediaPipelineError(f'外部打码失败（HTTP {response.status_code}），请检查接口配置。')
                if response.headers.get('Content-Type', '').split(';')[0].strip().lower() not in {'video/mp4', 'application/octet-stream'}:
                    raise MediaPipelineError('外部打码接口须同步返回 MP4 视频，请检查接口适配。')
                size = 0
                with temporary.open('wb') as target:
                    for chunk in response.iter_content(chunk_size=1024*1024):
                        size += len(chunk)
                        if time.monotonic() - started > config.timeout_seconds:
                            raise MediaPipelineError('外部打码超时，请稍后重试或调整超时时间。')
                        if size > settings.max_upload_mb * 1024 * 1024:
                            raise MediaPipelineError('外部打码结果超过视频大小上限。')
                        target.write(chunk)
        capture = cv2.VideoCapture(str(temporary))
        try:
            valid, _ = capture.read()
        finally:
            capture.release()
        if not valid:
            raise MediaPipelineError('外部打码未返回有效视频，请检查接口适配。')
        temporary.replace(output_path)
        return output_path
    except requests.Timeout:
        raise MediaPipelineError('外部打码超时，请稍后重试或调整超时时间。') from None
    except requests.RequestException:
        raise MediaPipelineError('外部打码连接失败，请检查接口地址和网络。') from None
    finally:
        temporary.unlink(missing_ok=True)
