"""Public error boundaries: never echo credentials, raw exceptions or local paths."""
import json
import logging
import os
import re
import sqlite3
import uuid
from pathlib import Path

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

_logger = logging.getLogger('ark.security')
_SECRET_NAMES = {'apikey', 'accesskey', 'secretkey', 'secretaccesskey', 'password', 'token', 'authorization'}
_ERROR_FIELDS = {'error', 'errors', 'message', 'detail', 'logs', 'error_data'}
_CONFIG_FILES = ('generation-settings.json', 'storage-settings.json', 'portrait.json',
                 'continuation-settings.json', 'variation-settings.json', 'redaction-service.json')


def secret_values(value):
    result = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).replace('_', '').replace('-', '').lower() in _SECRET_NAMES and isinstance(item, str) and item:
                result.add(item)
            elif isinstance(item, (dict, list)):
                result.update(secret_values(item))
    elif isinstance(value, list):
        for item in value:
            result.update(secret_values(item))
    return result


def configured_secrets(settings):
    """Read only known credential stores; never initialize/migrate storage on an error."""
    root = Path(getattr(settings, 'config_root', None) or settings.storage_dir)
    values = {getattr(settings, 'seedance_api_key', ''),
              getattr(getattr(settings, 'redaction_service', None), 'api_key', '')}
    for name in ('SEEDANCE_API_KEY', 'TIKHUB_API_KEY', 'TOS_ACCESS_KEY', 'TOS_SECRET_KEY',
                 'ARK_ACCESS_KEY', 'ARK_SECRET_KEY'):
        values.add(os.getenv(name, ''))
    for name in _CONFIG_FILES:
        try:
            values.update(secret_values(json.loads((root/'private'/name).read_text(encoding='utf-8'))))
        except (OSError, ValueError):
            pass
    path = root/'local-preferences.db'
    if path.is_file():
        try:
            with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.2) as db:
                row = db.execute("SELECT value FROM preferences WHERE key='tikhub_api_key'").fetchone()
                if row:
                    values.add(row[0])
        except sqlite3.Error:
            pass
    return {value for value in values if isinstance(value, str) and value}


def safe_error(value, secrets=(), *, limit=1200):
    text = str(value)
    for secret in sorted((v for v in secrets if isinstance(v, str) and v), key=len, reverse=True):
        text = text.replace(secret, '[已隐藏]')
    text = re.sub(r'(?i)data:[^\s]+', '[媒体内容已隐藏]', text)
    text = re.sub(r'(?i)https?://[^\s<>]+', '[资源地址已隐藏]', text)
    text = re.sub(r'(?i)Bearer\s+[^\s,;\"\']+', 'Bearer [已隐藏]', text)
    text = re.sub(r'''(?ix)(["']?(?:api[_-]?key|access[_-]?key|secret[_-]?(?:access[_-]?)?key|password|token|authorization)["']?\s*[:=]\s*)(?:"[^"]*"|'[^']*'|[^\s,;}]+)''', r'\1[已隐藏]', text)
    # Replace Windows drive/UNC paths and POSIX absolute paths, including quoted paths with spaces.
    text = re.sub(r'''(?i)(?:[a-z]:[\\/]|\\\\)[^\r\n"'<>]+''', '[内部路径已隐藏]', text)
    text = re.sub(r'''(["'])/[^\r\n"']+\1''', '[内部路径已隐藏]', text)
    text = re.sub(r'''(?<![\w:/])/[a-zA-Z0-9_.-]+(?:/[^\s"'<>，。；,;:]*)?''', '[内部路径已隐藏]', text)
    if 'Traceback (most recent call last)' in text or re.search(r'\[(?:WinError|Errno) \d+\]', text):
        return '操作失败，请稍后重试或联系管理员。'
    return text[:limit]


def safe_payload(value, secrets=(), *, error_context=False):
    if isinstance(value, dict):
        return {key: (safe_request_id(item, secrets) if key == 'request_id' else
                      safe_payload(item, secrets, error_context=error_context or key in _ERROR_FIELDS))
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [safe_payload(item, secrets, error_context=error_context) for item in value]
    if error_context and isinstance(value, str):
        return safe_error(value, secrets)
    return value


def safe_request_id(value, secrets=()):
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,63}', value)
            or any(secret and secret in value for secret in secrets)):
        return None
    return value


def response_class(settings_getter):
    class SafeJSONResponse(JSONResponse):
        def render(self, content):
            return super().render(safe_payload(content, configured_secrets(settings_getter())))
    return SafeJSONResponse


def install(app, settings_getter):
    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        return JSONResponse({'detail': '请求字段无效，请检查填写内容。'}, status_code=422,
                            headers={'Cache-Control': 'no-store'})

    @app.exception_handler(HTTPException)
    async def public_http_error(request, error):
        return JSONResponse({'detail': safe_payload(error.detail, configured_secrets(settings_getter()), error_context=True)},
                            status_code=error.status_code, headers={**(error.headers or {}), 'Cache-Control': 'no-store'})

    @app.middleware('http')
    async def unexpected_error(request, call_next):
        try:
            return await call_next(request)
        except Exception as error:
            ident = uuid.uuid4().hex
            # No raw exception, request body, URL or traceback enters this log.
            _logger.error('request_failed id=%s type=%s', ident, type(error).__name__)
            return JSONResponse({'detail': '服务暂时无法完成请求，请稍后重试；持续失败请联系管理员。',
                                 'request_id': ident}, status_code=500,
                                headers={'Cache-Control': 'no-store', 'X-Request-ID': ident})
