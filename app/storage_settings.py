"""Local TOS configuration and redacted-video uploads using the official SDK."""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
import json
import os
from pathlib import Path
import re
import tempfile
from threading import RLock
from uuid import uuid4
from .secure_transport import validate_endpoint

_lock = RLock()


@dataclass(frozen=True)
class StorageConfig:
    enabled: bool = False
    region: str = 'cn-beijing'
    endpoint: str = 'https://tos-cn-beijing.volces.com'
    bucket: str = ''
    prefix: str = 'ark/redacted/'
    expires_seconds: int = 86400
    access_key: str = field(default='', repr=False)
    secret_key: str = field(default='', repr=False)

    def problem(self):
        if not all((self.region, self.endpoint, self.bucket, self.access_key, self.secret_key)):
            return '请填写 TOS Bucket、地域、Endpoint、Access Key 和 Secret Key。'
        return ''

    @property
    def ready(self):
        return self.enabled and not self.problem()

    def public(self):
        values = asdict(self)
        for name in ('access_key', 'secret_key'):
            values['has_' + name] = bool(values.pop(name))
        values.update(ready=self.ready, message=self.problem() if self.enabled else '未启用 TOS，使用工作台公网地址或服务商上传。')
        return values


def config_path(settings):
    from .tenancy import config_root
    return config_root(settings) / 'private' / 'storage-settings.json'


def load_config(settings):
    with _lock:
        path = config_path(settings)
        if path.exists():
            return StorageConfig(**json.loads(path.read_text(encoding='utf-8')))
        region = os.getenv('TOS_REGION', 'cn-beijing').strip()
        return StorageConfig(
            enabled=os.getenv('TOS_ENABLED', '').lower() in {'true', '1', 'yes'},
            region=region, endpoint=os.getenv('TOS_ENDPOINT', f'https://tos-{region}.volces.com').strip(),
            bucket=os.getenv('TOS_BUCKET', '').strip(), access_key=os.getenv('TOS_ACCESS_KEY', '').strip(),
            secret_key=os.getenv('TOS_SECRET_KEY', '').strip())


def resolve_config(settings, payload):
    current = load_config(settings)
    values = asdict(current)
    if set(payload) - set(values) - {'clear_credentials'}:
        raise ValueError('包含不支持的存储配置字段。')
    values.update({k: v for k, v in payload.items() if k in values})
    if type(values['enabled']) is not bool or type(payload.get('clear_credentials', False)) is not bool:
        raise ValueError('请选择是否启用 TOS。')
    for name in ('region', 'endpoint', 'bucket', 'prefix', 'access_key', 'secret_key'):
        value = values[name]
        if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
            raise ValueError('存储配置字段格式不正确。')
        values[name] = value.strip()
    if not re.fullmatch(r'[a-z]{2}(?:-[a-z0-9]+)+', values['region']):
        raise ValueError('地域格式不正确，例如 cn-beijing。')
    values['endpoint'] = values['endpoint'].rstrip('/')
    if values['endpoint'] != f"https://tos-{values['region']}.volces.com":
        raise ValueError('请使用与地域一致的 TOS 官方公网 HTTPS Endpoint，例如 https://tos-cn-beijing.volces.com。')
    if values['bucket'] and not re.fullmatch(r'[a-z0-9][a-z0-9-]{1,61}[a-z0-9]', values['bucket']):
        raise ValueError('Bucket 名称应为 3–63 位小写字母、数字或连字符，不要填写 URL。')
    prefix = values['prefix'].strip('/')
    if not re.fullmatch(r'[a-zA-Z0-9/_-]{1,160}', prefix):
        raise ValueError('对象目录请使用字母、数字、斜杠、下划线或连字符。')
    values['prefix'] = prefix + '/'
    if type(values['expires_seconds']) is not int or not 3600 <= values['expires_seconds'] <= 604800:
        raise ValueError('下载链接有效期应为 1 小时到 7 天。')
    destination_same = values['endpoint'] == current.endpoint
    for name in ('access_key', 'secret_key'):
        supplied = payload.get(name, '').strip()
        values[name] = '' if payload.get('clear_credentials') else (supplied or (getattr(current, name) if destination_same else ''))
    if payload.get('access_key', '').strip() and values['access_key'] != current.access_key and not payload.get('secret_key', '').strip():
        values['secret_key'] = ''
    return StorageConfig(**values)


def save_config(settings, payload):
    with _lock:
        config = resolve_config(settings, payload)
        path = config_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.storage-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(asdict(config), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return config


def make_client(config):
    validate_endpoint(config.endpoint, allow_local=False)
    if config.endpoint != f'https://tos-{config.region}.volces.com':
        raise ValueError('请使用与地域一致的 TOS 官方公网 HTTPS Endpoint。')
    import tos
    return tos.TosClientV2(config.access_key, config.secret_key, config.endpoint, config.region,
                           connection_time=10, socket_timeout=60, max_retry_count=1)


def test_connection(config):
    if config.problem():
        return {'ok': False, 'message': config.problem()}
    try:
        client = make_client(config)
        try:
            client.head_bucket(config.bucket)
        finally:
            client.close()
        return {'ok': True, 'message': 'TOS Bucket 访问验证通过。此测试不上传文件；上传和下载权限将在实际任务中验证。'}
    except Exception:
        return {'ok': False, 'message': 'TOS 连接失败，请检查 Bucket、地域、AK/SK、网络及桶访问权限。测试需要 HeadBucket 权限。'}


def upload_redacted_video(path, settings, config):
    resolved = Path(path).resolve()
    from .reference_media import _scoped_video
    if not _scoped_video(resolved,settings.storage_dir):
        raise ValueError('只能上传当前任务的打码视频或已生成的基础片。')
    if not config.ready:
        raise ValueError(config.problem() or '请先启用 TOS。')
    if not 0 < resolved.stat().st_size <= 50 * 1024 * 1024:
        raise ValueError('参考视频需大于 0 且不超过 50 MB，请压缩或缩短视频后重试。')
    try:
        import tos
        client = make_client(config)
        try:
            key = f'{config.prefix}{uuid4().hex}/{resolved.name}'
            client.put_object_from_file(config.bucket, key, str(resolved),
                                        content_type='video/mp4', acl=tos.ACLType.ACL_Private)
            return client.pre_signed_url(tos.HttpMethodType.Http_Method_Get, config.bucket, key,
                                         expires=config.expires_seconds).signed_url
        finally:
            client.close()
    except Exception:
        raise RuntimeError('上传打码视频到 TOS 失败，请检查密钥、Bucket、地域、网络及 PutObject/GetObject 权限。') from None
