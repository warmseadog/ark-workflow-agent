"""Validated server defaults for new production drafts."""
import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from .media import BlurOptions, LocalMosaicOptions
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal

_lock = RLock()


def config_path(settings):
    from .tenancy import config_root
    return config_root(settings) / 'private' / 'redaction-settings.json'


def validate(values):
    values = dict(values)
    if values.get('blur_style') not in {'mosaic', 'blur', 'solid'}:
        raise ValueError('请选择马赛克、高斯模糊或黑色遮挡。')
    values['style'] = values.pop('blur_style')
    if 'replace_image' in values:
        raise ValueError('默认打码设置不支持图片覆盖。')
    result = BlurOptions.model_validate(values, strict=True).model_dump(exclude={'replace_image'})
    result['blur_style'] = result.pop('style')
    return result


def _load_local(settings):
    with _lock:
        path = config_path(settings)
        if path.exists():
            values = validate(json.loads(path.read_text(encoding='utf-8')))
            values['local_options'] = values.get('local_options') or LocalMosaicOptions().model_dump()
            return values
        values = BlurOptions.from_settings(settings).model_dump(exclude={'replace_image'})
        values['blur_style'] = values.pop('style')
        values['local_options'] = LocalMosaicOptions().model_dump()
        return validate(values)


class MediaKitOptions(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    mask_mode: Literal['mosaic', 'blur'] = 'mosaic'
    mask_strength: Literal['low', 'medium', 'high'] = 'medium'
    face_box_expand: float = Field(default=0.2, gt=0, le=1)
    face_confidence: float = Field(default=0.35, ge=0.1, le=1)

    @field_validator('face_box_expand')
    @classmethod
    def representable_expansion(cls, value):
        if 1 + value == 1:
            raise ValueError('人脸框扩展比例过小，请填写可实际执行的数值，例如 0.2。')
        return value


def active_profile(settings):
    from .redaction_service import load_config as load_service
    from .mediakit_redaction import matches
    service = load_service(settings)
    return 'local' if service.mode == 'local' else 'mediakit' if matches(service.endpoint) else 'http'


def _profile_path(settings, profile):
    if profile not in {'local', 'mediakit', 'http'}:
        raise ValueError('不支持的打码配置类型。')
    path = config_path(settings)
    return path if profile == 'local' else path.with_name(f'redaction-settings-{profile}.json')


def load_profiles(settings):
    with _lock:
        profiles = {'local': _load_local(settings)}
        for profile in ('mediakit', 'http'):
            path = _profile_path(settings, profile)
            values = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            if profile == 'http' and not path.exists():
                # One-time migration keeps existing generic adapters unchanged,
                # then isolates their values from later local-profile edits.
                values = dict(profiles['local'])
                values['mask_mode'] = 'face'
                values['local_options'] = None
                _write_config(path, values)
            profiles[profile] = (MediaKitOptions.model_validate(values).model_dump() if profile == 'mediakit'
                                 else validate(values or _defaults(settings)))
        return profiles


def _defaults(settings):
    values = BlurOptions.from_settings(settings).model_dump(exclude={'replace_image'})
    values['blur_style'] = values.pop('style')
    return values


def load_config(settings):
    """Keep draft snapshots compatible with the existing immutable mask schema."""
    profile = active_profile(settings)
    values = load_profiles(settings)[profile]
    return mask_values(profile, values)


def mask_values(profile, values):
    """Translate validated profile values for drafts and one-off tests alike."""
    if profile != 'mediakit':
        return values
    return validate({**BlurOptions().model_dump(exclude={'style', 'replace_image'}),
                     'blur_style': values['mask_mode'],
                     'mask_scale': 1 + values['face_box_expand'],
                     'mosaic_size': {'low': 8, 'medium': 20, 'high': 60}[values['mask_strength']],
                     'threshold': values['face_confidence']})


def public_config(settings):
    return {'config': load_config(settings), 'profile': active_profile(settings),
            'profiles': load_profiles(settings)}


def save_config(settings, payload):
    with _lock:
        # Flat payloads remain the legacy local-settings API. The new UI always
        # names the displayed profile, independent of the saved service mode.
        profile = payload.get('profile', 'local')
        path = _profile_path(settings, profile)
        if 'profile' in payload:
            if set(payload) != {'profile', 'values'} or not isinstance(payload['values'], dict):
                raise ValueError('打码配置格式不正确。')
            payload = payload['values']
        current = load_profiles(settings)[profile]
        if profile == 'mediakit':
            values = MediaKitOptions.model_validate({**current, **payload}).model_dump()
        else:
            if set(payload) - set(current):
                raise ValueError('包含不支持的打码配置字段。')
            if profile == 'http' and (payload.get('mask_mode', 'face') != 'face' or payload.get('local_options') is not None):
                raise ValueError('通用外部 API 仅支持人脸遮挡；本地高级参数请在本地配置中设置。')
            values = validate({**current, **payload})
            if profile == 'local':
                values['local_options'] = values.get('local_options') or LocalMosaicOptions().model_dump()
        _write_config(path, values)
        return values


def _write_config(path, values):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.redaction-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as output:
            json.dump(values, output, ensure_ascii=False, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
