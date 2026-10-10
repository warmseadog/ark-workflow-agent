"""Shared outbound transport policy. Errors never contain supplied URLs."""
import ipaddress
import os
from urllib.parse import urlsplit


def validate_endpoint(value: str, *, optional: bool = False, allow_local: bool = True,
                      allow_query: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError('接口地址必须是有效的 HTTPS URL。')
    value = value.strip()
    if not allow_query:
        value = value.rstrip('/')
    if optional and not value:
        return ''
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        raise ValueError('HTTPS 接口地址或端口格式不正确。') from None
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or (parsed.query and not allow_query) or parsed.fragment
            or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value)
            or '\\' in value):
        raise ValueError('地址必须是有效的 HTTPS URL，不可包含账号或片段，接口地址不可包含查询参数。')
    if parsed.scheme == 'http':
        from .tenancy import enabled
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname == 'localhost'
        if not (allow_local and loopback and not enabled()
                and os.getenv('APP_ALLOW_INSECURE_LOCAL_HTTP', '').strip().lower() == 'true'):
            raise ValueError('接口和媒体地址必须使用 HTTPS；仅显式启用且关闭认证的本机开发可使用回环 HTTP。')
    return value
