"""Read-only official Ark portrait assets and private local credentials.

API reference: https://docs.volcengine.com/docs/ark/list-assets-api?lang=zh
Signing reference: https://github.com/volcengine/volc-sdk-python/blob/main/volcengine/auth/SignerV4.py
Temporary asset URLs are private and must never appear in public API responses.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timezone
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import tempfile
from threading import RLock
from urllib.parse import urlencode, urlsplit
import warnings

import requests

_HOST = 'ark.cn-beijing.volcengineapi.com'
_ACTIONS = {'ListAssets', 'GetAsset', 'GetAssetGroup', 'CreateVisualValidateSession', 'GetVisualValidateResult', 'ListAssetGroups', 'CreateAsset', 'CreateAssetGroup'}
_IMAGE_HOSTS = {'ark-asset.cn-beijing.volcengine.com', 'ark-media-asset.tos-cn-beijing.volces.com'}
_lock = RLock()


class PortraitError(ValueError):
    """User-safe message without credential, signed URL, or upstream details."""


@dataclass(frozen=True)
class PortraitConfig:
    project_name: str = 'default'
    use_storage_credentials: bool = False
    access_key: str = field(default='', repr=False)
    secret_key: str = field(default='', repr=False)

    def problem(self):
        return '' if self.access_key and self.secret_key else '请配置火山 Access Key 和 Secret Key，或明确选择使用现有 TOS 凭据。'

    @property
    def ready(self):
        return not self.problem()


def config_path(settings):
    return settings.storage_dir / 'private' / 'portrait.json'


def _stored_config(settings):
    path = config_path(settings)
    if not path.exists():
        return PortraitConfig()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return _validate_config(data)
    except (OSError, ValueError, TypeError):
        raise PortraitError('真人素材配置无法读取，请重新保存配置。') from None


def _validate_config(values):
    if not isinstance(values, dict) or set(values) - set(PortraitConfig.__dataclass_fields__):
        raise PortraitError('包含不支持的真人素材配置字段。')
    values = {**asdict(PortraitConfig()), **values}
    if type(values['use_storage_credentials']) is not bool:
        raise PortraitError('请选择是否使用现有 TOS 凭据。')
    for key in ('project_name', 'access_key', 'secret_key'):
        value = values[key]
        if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise PortraitError('真人素材配置字段格式不正确。')
        values[key] = value.strip()
    if not re.fullmatch(r'[A-Za-z0-9_\-\u4e00-\u9fff]{1,128}', values['project_name']):
        raise PortraitError('请填写有效项目名称，例如 default。')
    return PortraitConfig(**values)


def _effective(settings, config):
    if config.use_storage_credentials:
        from .storage_settings import load_config as load_storage
        storage = load_storage(settings)
        return replace(config, access_key=storage.access_key, secret_key=storage.secret_key)
    return config


def load_config(settings):
    with _lock:
        return _effective(settings, _stored_config(settings))


def save_config(settings, payload):
    with _lock:
        current = _stored_config(settings)
        if not isinstance(payload, dict) or type(payload.get('clear_credentials', False)) is not bool:
            raise PortraitError('真人素材配置格式不正确。')
        values = asdict(current)
        values.update({k: v for k, v in payload.items() if k != 'clear_credentials'})
        candidate = _validate_config(values)
        values = asdict(candidate)
        for name in ('access_key', 'secret_key'):
            values[name] = getattr(candidate, name) or getattr(current, name)
        if candidate.access_key and candidate.access_key != current.access_key and not payload.get('secret_key', '').strip():
            values['secret_key'] = ''
        if payload.get('clear_credentials'):
            values.update(access_key='', secret_key='', use_storage_credentials=False)
        saved = PortraitConfig(**values)
        path = config_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.portrait-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(asdict(saved), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return _effective(settings, saved)


def public_config(settings):
    config = load_config(settings)
    return {'project_name': config.project_name, 'use_storage_credentials': config.use_storage_credentials,
            'has_access_key': bool(config.access_key), 'has_secret_key': bool(config.secret_key),
            'has_credentials': config.ready, 'ready': config.ready, 'message': config.problem()}


def fingerprint(config):
    value = json.dumps([config.project_name, config.access_key, config.secret_key], ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(value.encode()).hexdigest()


def signed_headers(config, action, body, timestamp=None):
    if action not in _ACTIONS:
        raise PortraitError('不支持的真人素材操作。')
    stamp = timestamp or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    body_hash = hashlib.sha256(body).hexdigest()
    headers = {'Content-Type': 'application/json', 'Host': _HOST, 'X-Date': stamp, 'X-Content-Sha256': body_hash}
    names = 'content-type;host;x-content-sha256;x-date'
    canonical_headers = ''.join(f'{key.lower()}:{value}\n' for key, value in sorted(headers.items(), key=lambda item: item[0].lower()))
    query = urlencode({'Action': action, 'Version': '2024-01-01'})
    canonical = '\n'.join(['POST', '/', query, canonical_headers, names, body_hash])
    scope = f'{stamp[:8]}/cn-beijing/ark/request'
    to_sign = '\n'.join(['HMAC-SHA256', stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = config.secret_key.encode()
    for value in (stamp[:8], 'cn-beijing', 'ark', 'request'):
        key = hmac.new(key, value.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers['Authorization'] = f'HMAC-SHA256 Credential={config.access_key}/{scope}, SignedHeaders={names}, Signature={signature}'
    return headers


def _identifier(value, prefix):
    return isinstance(value, str) and bool(re.fullmatch(prefix + r'-[A-Za-z0-9-]{1,114}', value))


class ArkPortraitClient:
    def __init__(self, config, person_type='LivenessFace', asset_type='Image'):
        if person_type not in {'LivenessFace', 'AIGC'}:
            raise PortraitError('不支持的人物类型。')
        if asset_type not in {'Image','Video'}: raise PortraitError('不支持的素材类型。')
        self.asset_type = asset_type
        self.config = config
        self.person_type = person_type

    def _request(self, action, payload):
        if not self.config.ready:
            raise PortraitError(self.config.problem())
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        headers = signed_headers(self.config, action, body)
        response = None
        try:
            response = requests.post(f'https://{_HOST}/?Action={action}&Version=2024-01-01',
                                     data=body, headers=headers, timeout=(10, 30), allow_redirects=False)
            envelope = response.json()
            if not isinstance(envelope, dict) or not isinstance(envelope.get('ResponseMetadata', {}), dict):
                raise ValueError()
            error = envelope.get('ResponseMetadata', {}).get('Error')
            if response.status_code != 200 or error:
                code = (error or {}).get('Code', '') if isinstance(error, dict) else ''
                code = code if isinstance(code, str) and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', code) else str(response.status_code)
                raise PortraitError(f'官方人物素材接口未完成请求（{code}），请检查 Ark 权限、项目或稍后重试。')
            result = envelope.get('Result')
            if not isinstance(result, dict) and action in {'CreateVisualValidateSession', 'GetVisualValidateResult'}:
                result = envelope
            if not isinstance(result, dict): raise ValueError()
            return result
        except PortraitError:
            raise
        except (requests.RequestException, ValueError, TypeError):
            raise PortraitError('官方人物素材查询失败，请检查 AK/SK、项目、网络和 Ark 素材访问权限。') from None
        finally:
            if response is not None:
                response.close()

    def create_session(self, callback_url):
        result = self._request('CreateVisualValidateSession', {'CallbackURL': callback_url, 'ProjectName': self.config.project_name})
        token, url = result.get('BytedToken'), result.get('H5Link')
        try:
            parsed = urlsplit(url)
            allowed = {'ark.volcengine.com', 'console.volcengine.com', 'h5-v2.kych5.com'}
            if (not isinstance(token, str) or not 1 <= len(token) <= 2048
                    or any(ord(c) < 32 for c in token) or not isinstance(url, str) or len(url) > 16384
                    or any(ord(c) < 32 for c in url) or parsed.scheme != 'https' or parsed.hostname not in allowed
                    or parsed.username or parsed.password or parsed.port not in {None, 443}):
                raise ValueError()
        except (ValueError, TypeError, AttributeError):
            raise PortraitError('官方认证链接格式异常，请重新获取二维码。') from None
        return {'token': token, 'url': url}

    def validation_result(self, token):
        result = self._request('GetVisualValidateResult', {'BytedToken': token, 'ProjectName': self.config.project_name})
        group = result.get('GroupId')
        if not group: return None
        if not _identifier(group, 'group'): raise PortraitError('官方认证结果格式异常，请稍后重试。')
        return group

    def create_group(self, name, on_created=None):
        if self.person_type != 'AIGC':
            raise PortraitError('真人素材组必须通过官方本人认证创建。')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 60 or any(ord(c) < 32 for c in name):
            raise PortraitError('虚拟人物名称请输入 1–60 个字符。')
        result = self._request('CreateAssetGroup', {'Name': name.strip(), 'GroupType': 'AIGC',
                                                   'ProjectName': self.config.project_name})
        group_id = result.get('Id')
        if not _identifier(group_id, 'group'):
            raise PortraitError('创建人物结果待确认，请稍后重新检查。')
        # Commit the creation receipt before another network request can fail.
        if on_created is not None:
            on_created(group_id)
        return self.get_group(group_id)

    def get_group(self, group_id):
        if not _identifier(group_id, 'group'):
            raise PortraitError('人物编号不正确，请重新选择。')
        item = self._request('GetAssetGroup', {'Id': group_id, 'ProjectName': self.config.project_name})
        if (item.get('Id') != group_id or item.get('GroupType') != self.person_type
                or item.get('ProjectName') != self.config.project_name):
            raise PortraitError('人物授权不可用，请重新认证或检查账号和项目。')
        return item

    def list_groups(self):
        result, tokens, token = [], set(), None
        for _ in range(10):
            payload = {'Filter': {'GroupType': self.person_type}, 'ProjectName': self.config.project_name, 'MaxResults': 100}
            if token: payload['NextToken'] = token
            page = self._request('ListAssetGroups', payload)
            if not isinstance(page.get('Items'), list): raise PortraitError('人物列表格式异常。')
            result.extend(x for x in page['Items'] if isinstance(x, dict) and x.get('GroupType') == self.person_type
                          and x.get('ProjectName') == self.config.project_name and _identifier(x.get('Id'), 'group'))
            token = page.get('NextToken')
            if not token: return result
            if not isinstance(token, str) or token in tokens: raise PortraitError('人物列表分页异常。')
            tokens.add(token)
        raise PortraitError('人物数量超过查询上限，请缩小项目范围。')

    def create_asset(self, group_id, url, name):
        result = self._request('CreateAsset', {'GroupId': group_id, 'URL': url, 'Name': name,
                                               'AssetType': self.asset_type, 'ProjectName': self.config.project_name})
        ident = result.get('Id')
        if not _identifier(ident, 'asset'): raise PortraitError('创建照片结果待确认，请稍后查看状态。')
        return ident

    def find_created_asset(self, group_id, name):
        result = self._request('ListAssets', {'Filter': {'GroupType': self.person_type, 'GroupIds': [group_id], 'Name': name},
                                            'ProjectName': self.config.project_name, 'MaxResults': 100})
        for item in result.get('Items', []):
            if (item.get('Name') == name and item.get('GroupId') == group_id
                    and item.get('ProjectName') == self.config.project_name and item.get('AssetType') == self.asset_type
                    and _identifier(item.get('Id'), 'asset')):
                return item['Id']
        return None

    def asset_state(self, asset_id, group_id):
        item = self._request('GetAsset', {'Id': asset_id, 'ProjectName': self.config.project_name})
        if (item.get('Id') != asset_id or item.get('GroupId') != group_id or item.get('AssetType') != self.asset_type
                or item.get('ProjectName') != self.config.project_name or item.get('Status') not in {'Active','Processing','Failed'}):
            raise PortraitError('照片与所选人物或项目不匹配，请重新选择。')
        error = item.get('Error') or {}
        return {'status': item['Status'], 'error_code': error.get('Code', '') if isinstance(error, dict) else ''}

    def _normalize(self, item):
        if (not isinstance(item, dict) or item.get('Status') != 'Active' or item.get('AssetType') != self.asset_type
                or item.get('ProjectName') != self.config.project_name or not _identifier(item.get('Id'), 'asset')
                or not _identifier(item.get('GroupId'), 'group')):
            raise PortraitError('素材不可用：必须是当前项目中已就绪且类型匹配的人物素材。')
        return {'remote_asset_id': item['Id'], 'name': str(item.get('Name', ''))[:128],
                'group_id': item['GroupId'], 'project': item['ProjectName'], 'status': 'Active',
                'asset_type': self.asset_type, 'person_type': self.person_type, 'url': item.get('URL', '')}

    def list_assets(self):
        items, tokens, seen_ids = [], set(), set()
        token = None
        examined = 0
        for _ in range(100):
            payload = {'Filter': {'GroupType': self.person_type, 'Statuses': ['Active']},
                       'ProjectName': self.config.project_name, 'MaxResults': 100,
                       'SortBy': 'CreateTime', 'SortOrder': 'Desc'}
            if token:
                payload['NextToken'] = token
            result = self._request('ListAssets', payload)
            page = result.get('Items')
            if not isinstance(page, list):
                raise PortraitError('官方素材列表格式异常，请稍后重试。')
            for item in page:
                examined += 1
                if examined > 1000:
                    raise PortraitError('素材数量超过本次查询上限，请在官方控制台缩小项目范围。')
                try:
                    normalized = self._normalize(item)
                except PortraitError:
                    continue
                if normalized['remote_asset_id'] not in seen_ids:
                    items.append(normalized)
                    seen_ids.add(normalized['remote_asset_id'])
            token = result.get('NextToken')
            if not token:
                return items
            if not isinstance(token, str) or len(token) > 16384 or token in tokens:
                raise PortraitError('官方素材分页异常，请稍后重试。')
            tokens.add(token)
        raise PortraitError('官方素材查询页数超过上限，请在官方控制台缩小项目范围。')

    def get_asset(self, remote_asset_id):
        if not _identifier(remote_asset_id, 'asset'):
            raise PortraitError('官方素材 ID 格式不正确。')
        item = self._request('GetAsset', {'Id': remote_asset_id, 'ProjectName': self.config.project_name})
        normalized = self._normalize(item)
        if normalized['remote_asset_id'] != remote_asset_id:
            raise PortraitError('官方素材 ID 不一致。')
        group = self._request('GetAssetGroup', {'Id': normalized['group_id'], 'ProjectName': self.config.project_name})
        if (group.get('Id') != normalized['group_id'] or group.get('GroupType') != self.person_type
                or group.get('ProjectName') != self.config.project_name):
            raise PortraitError('该素材不属于当前项目中所选类型的人物素材组。')
        return normalized

    get_authorized_asset = get_asset


def download_image(asset, destination):
    """Download bytes from a freshly verified asset; callers must first get_asset()."""
    from PIL import Image
    from . import tikhub
    destination = Path(destination)
    response = None
    temporary = None
    try:
        url = asset.get('url', '')
        if not isinstance(url, str) or len(url) > 16384 or any(ord(c) < 32 for c in url):
            raise ValueError()
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in _IMAGE_HOSTS or parsed.username
                or parsed.password or parsed.port not in {None, 443} or parsed.fragment):
            raise ValueError()
        resolved = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        addresses = tuple(dict.fromkeys(str(ipaddress.ip_address(item[4][0])) for item in resolved))
        if not addresses:
            raise ValueError()
        fake_range = ipaddress.ip_network('198.18.0.0/15')
        if any(ipaddress.ip_address(address) in fake_range for address in addresses):
            if any(not ipaddress.ip_address(address).is_global and ipaddress.ip_address(address) not in fake_range for address in addresses):
                raise ValueError()
            addresses = tikhub._resolve_fake_ip(parsed.hostname)
        if any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise ValueError()
        response = tikhub._get_pinned_video(url, addresses, {'Accept': 'image/png,image/jpeg,image/webp'})
        if response.status_code != 200:
            raise ValueError()
        limit = 20 * 1024 * 1024
        if int(response.headers.get('Content-Length', '0')) > limit:
            raise ValueError()
        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix='.portrait-image-', suffix='.tmp')
        length = 0
        with os.fdopen(fd, 'wb') as stream:
            for chunk in response.iter_content(64 * 1024):
                length += len(chunk)
                if length > limit:
                    raise ValueError()
                stream.write(chunk)
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(temporary) as image:
                if image.format not in {'PNG', 'JPEG', 'WEBP'} or image.width * image.height > 40000000:
                    raise ValueError()
                image.verify()
        os.replace(temporary, destination)
        return destination
    except Exception:
        raise PortraitError('官方授权图片下载失败：请检查网络、素材有效期或图片格式。') from None
    finally:
        if response is not None:
            response.close()
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)
