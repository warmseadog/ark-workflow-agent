"""Validated server defaults for new production drafts."""
import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from .media import BlurOptions

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


def load_config(settings):
    with _lock:
        path = config_path(settings)
        if path.exists():
            return validate(json.loads(path.read_text(encoding='utf-8')))
        values = BlurOptions.from_settings(settings).model_dump(exclude={'replace_image'})
        values['blur_style'] = values.pop('style')
        return validate(values)


def save_config(settings, payload):
    with _lock:
        current = load_config(settings)
        if set(payload) - set(current):
            raise ValueError('包含不支持的打码配置字段。')
        values = validate({**current, **payload})
        path = config_path(settings)
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
        return values
