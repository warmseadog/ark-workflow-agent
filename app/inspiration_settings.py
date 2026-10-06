"""Independent short-text assistant settings; never mutate the camera planner."""
from dataclasses import asdict, dataclass, field, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from functools import lru_cache
from threading import RLock

from .secure_transport import validate_endpoint
from .tenancy import config_root

SKILL_PATH = Path(__file__).parent / 'skills' / 'shooting-inspiration' / 'SKILL.md'
_lock = RLock()


@lru_cache(maxsize=2)
def skill_text(prompt_mode='strict'):
    if prompt_mode not in ('strict', 'user_priority'):
        raise ValueError('提示词版本不正确。')
    path = SKILL_PATH.with_name('USER_PRIORITY.md') if prompt_mode == 'user_priority' else SKILL_PATH
    return path.read_text(encoding='utf-8').split('---', 2)[-1].strip()


def prompt_mode(settings):
    from .variation_settings import load_config as planner_config
    return planner_config(settings).prompt_mode


@dataclass(frozen=True)
class InspirationConfig:
    enabled: bool = True
    inherit_provider: bool = True
    base_url: str = 'https://ark.cn-beijing.volces.com/api/v3'
    model: str = ''
    api_key: str = field(default='', repr=False)
    timeout_seconds: int = 15
    max_tokens: int = 512

    def resolved(self, settings):
        if not self.inherit_provider:
            return self
        from .variation_settings import load_config as planner_config
        provider = planner_config(settings)
        return replace(self, base_url=provider.base_url, api_key=provider.api_key,
                       model=self.model or provider.model)

    def problem(self):
        if not self.enabled:
            return 'AI 灵感辅助暂不可用，可直接填写拍摄想法。'
        if not self.model or not self.api_key:
            return 'AI 灵感辅助尚未配置，请联系管理员；也可直接填写拍摄想法。'
        return ''

    def public(self, settings):
        values = asdict(self)
        values.pop('api_key')
        effective = self.resolved(settings)
        mode = prompt_mode(settings)
        return {**values, 'has_api_key': bool(effective.api_key),
                'effective_model': effective.model, 'problem': effective.problem(),
                'prompt_mode': mode, 'skill_version': hashlib.sha256(skill_text(mode).encode()).hexdigest()}


def config_path(settings):
    return config_root(settings) / 'private' / 'inspiration-settings.json'


def _validate(values):
    for name in ('enabled', 'inherit_provider'):
        if type(values[name]) is not bool:
            raise ValueError('灵感辅助开关格式不正确。')
    for name in ('base_url', 'model', 'api_key'):
        value = values[name]
        if not isinstance(value, str) or len(value) > 2048 or any(ord(c) < 32 for c in value):
            raise ValueError('灵感辅助接口配置格式不正确。')
        values[name] = value.strip()
    values['base_url'] = validate_endpoint(values['base_url'])
    if not values['inherit_provider'] and not values['model']:
        raise ValueError('独立接口需填写模型 ID。')
    if any(ord(c) < 33 or ord(c) > 126 for c in values['api_key']):
        raise ValueError('API Key 应为不含空格的英文字符。')
    if type(values['timeout_seconds']) is not int or not 5 <= values['timeout_seconds'] <= 30:
        raise ValueError('灵感辅助超时需为 5–30 秒。')
    if type(values['max_tokens']) is not int or not 128 <= values['max_tokens'] <= 1024:
        raise ValueError('灵感辅助输出预算需为 128–1024 tokens。')
    return InspirationConfig(**values)


def load_config(settings):
    with _lock:
        path = config_path(settings)
        if not path.exists():
            return InspirationConfig()
        try:
            values = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(values, dict) or set(values) - set(asdict(InspirationConfig())):
                raise ValueError()
            return _validate({**asdict(InspirationConfig()), **values})
        except (ValueError, TypeError, KeyError):
            raise ValueError('灵感辅助配置无法读取，请由管理员检查。') from None


def save_config(settings, values):
    with _lock:
        current = load_config(settings)
        if not isinstance(values, dict) or set(values) - set(asdict(current)) - {'clear_api_key'}:
            raise ValueError('包含不支持的灵感辅助配置字段。')
        if type(values.get('clear_api_key', False)) is not bool:
            raise ValueError('清除密钥选项不正确。')
        merged = {**asdict(current), **{k: v for k, v in values.items() if k != 'clear_api_key'}}
        candidate = _validate(merged)
        key = '' if values.get('clear_api_key') else (candidate.api_key if values.get('api_key') else
              (current.api_key if candidate.base_url == current.base_url else ''))
        # Inherited credentials are resolved at request time and are never copied to this file.
        config = replace(candidate, api_key=key)
        path = config_path(settings)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.inspiration-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(asdict(config), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return config
