"""Local provider settings. Secrets are never included in public responses."""
from __future__ import annotations

import ipaddress
import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from .config import Settings

PRESETS = {
    'ark': {'label': '火山方舟官方', 'protocol': 'ark',
            'base_url': 'https://ark.cn-beijing.volces.com/api/v3',
            'model': 'doubao-seedance-2-0-260128',
            'models': ['doubao-seedance-2-5-260628', 'doubao-seedance-2-0-260128', 'doubao-seedance-2-0-fast-260128', 'doubao-seedance-2-0-mini-260615']},
    'toapis': {'label': 'toapis.cn', 'protocol': 'toapis',
               'base_url': 'https://toapis.cn/v1', 'model': 'seedance-2',
               'models': ['seedance-2', 'seedance-2-fast', 'seedance-2-mini']},
    'custom': {'label': '自定义', 'protocol': 'adapter', 'base_url': '', 'model': '', 'models': []},
}
_lock = RLock()


def validate_url(value: str, *, optional: bool = False) -> str:
    value = value.strip().rstrip('/')
    if optional and not value:
        return ''
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Accessing the property also validates the port range.
    except ValueError:
        raise ValueError('接口地址或端口格式不正确。') from None
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or any(c.isspace() for c in value)):
        raise ValueError('地址必须是 HTTP(S) URL，可包含端口和路径，不可包含账号、查询参数或片段。')
    return value


@dataclass(frozen=True)
class GenerationConfig:
    provider: str = 'ark'
    protocol: str = 'ark'
    mode: str = 'mock'
    base_url: str = PRESETS['ark']['base_url']
    model: str = PRESETS['ark']['model']
    api_key: str = field(default='', repr=False)
    duration: int = 5
    fps: int = 0
    resolution: str = '720p'
    ratio: str = 'adaptive'
    public_base_url: str = ''
    # Missing values in historical jobs/settings preserve their original audio behavior.
    generate_audio: bool = True

    def problem(self) -> str:
        if self.mode == 'mock':
            return ''
        if not self.base_url or not self.model or not self.api_key:
            return '请在后台配置视频模型的接口地址、API Key 和模型 ID。'
        return ''

    def generation_problem(self, video_available: bool = False) -> str:
        if self.problem():
            return self.problem()
        if self.mode == 'http' and self.protocol == 'ark' and not self.public_base_url and not video_available:
            return '火山方舟需要读取打码视频的公网地址。请在后台启用 TOS，或在视频模型高级配置中填写工作台公网地址，也可以选择支持视频上传的服务商。'
        return ''

    def public(self) -> dict:
        result = asdict(self)
        result.pop('api_key')
        result['has_api_key'] = bool(self.api_key)
        result['status'] = 'demo' if self.mode == 'mock' else ('incomplete' if self.problem() else 'configured')
        result['message'] = self.problem()
        result['generation_message'] = self.generation_problem()
        return result


def config_path(settings: Settings) -> Path:
    from .tenancy import config_root
    return config_root(settings) / 'private' / 'generation-settings.json'


def load_config(settings: Settings) -> GenerationConfig:
    with _lock:
        path = config_path(settings)
        if path.exists():
            return GenerationConfig(**json.loads(path.read_text(encoding='utf-8')))
        # Preserve existing .env integrations until the user saves a UI configuration.
        if settings.seedance_mode == 'http':
            return GenerationConfig(provider='custom', protocol='adapter', mode='http',
                                    base_url=settings.seedance_api_url,
                                    api_key=settings.seedance_api_key, model='seedance-default', fps=24)
        return GenerationConfig()


def resolve_config(settings: Settings, payload: dict) -> GenerationConfig:
    """Validate current form fields without saving; retain keys only for the same endpoint."""
    current = load_config(settings)
    values = asdict(current)
    allowed = set(values) - {'api_key'}
    if set(payload) - allowed - {'api_key', 'clear_api_key'}:
        raise ValueError('包含不支持的配置字段。')
    for name in allowed & payload.keys():
        values[name] = payload[name]
    for name in ('provider', 'protocol', 'mode', 'base_url', 'model', 'resolution', 'ratio', 'public_base_url'):
        if not isinstance(values[name], str) or len(values[name]) > 2048:
            raise ValueError('配置字段格式不正确。')
        values[name] = values[name].strip()
    if values['provider'] not in PRESETS or values['protocol'] not in {'ark', 'toapis', 'adapter'}:
        raise ValueError('请选择支持的服务商和接口格式。')
    if values['mode'] not in {'mock', 'http'}:
        raise ValueError('请选择真实生成或本地演示模式。')
    values['base_url'] = validate_url(values['base_url'], optional=True)
    values['public_base_url'] = validate_url(values['public_base_url'], optional=True)
    if values['public_base_url']:
        hostname = urlsplit(values['public_base_url']).hostname
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if hostname == 'localhost' or hostname.endswith('.localhost') or (address and not address.is_global):
            raise ValueError('工作台公网地址不能使用本机或局域网 IP，请填写公网域名或公网 IP。')
    if not 1 <= len(values['model']) <= 160:
        raise ValueError('请填写有效的模型 ID（不超过 160 字符）。')
    from .model_catalog import capabilities
    limits = capabilities(values['model'], values['protocol'])
    if values['ratio'] not in limits['ratios']:
        raise ValueError('当前模型不支持所选画面比例，请选择支持的比例。')
    if type(values['generate_audio']) is not bool:
        raise ValueError('生成声音请选择开启或关闭。')
    if not values['generate_audio'] and not limits['audio_control']:
        raise ValueError('当前模型不支持关闭声音，请选择支持声音设置的模型。')
    if type(values['duration']) is not int or not (4 <= values['duration'] <= limits['max_duration'] or (limits['auto_duration'] and values['duration'] == -1)):
        raise ValueError(f"生成时长应为 4–{limits['max_duration']} 秒。")
    if type(values['fps']) is not int or values['fps'] not in {0, 24, 25, 30}:
        raise ValueError('帧率应为自动、24、25 或 30 fps。')
    if values['protocol'] != 'adapter' and values['fps'] != 0:
        raise ValueError('方舟和 toapis 预设不传自定义帧率，请使用模型默认帧率。')
    if values['resolution'] not in {'480p', '720p', '1080p', '4k'}:
        raise ValueError('不支持的输出分辨率。')
    if values['resolution'] not in limits['resolutions']:
        raise ValueError('当前模型支持的清晰度为：' + '、'.join(limits['resolutions']) + '。')
    key = payload.get('api_key', '')
    if not isinstance(key, str) or len(key) > 4096 or '\n' in key or '\r' in key:
        raise ValueError('API Key 格式不正确。')
    same_destination = (values['base_url'], values['protocol']) == (current.base_url, current.protocol)
    values['api_key'] = '' if payload.get('clear_api_key') else (key.strip() or (current.api_key if same_destination else ''))
    return GenerationConfig(**values)


def save_config(settings: Settings, payload: dict) -> GenerationConfig:
    with _lock:
        config = resolve_config(settings, payload)
        from .model_catalog import preserve_existing
        preserve_existing(settings)
        path = config_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.settings-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as output:
                json.dump(asdict(config), output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return config
