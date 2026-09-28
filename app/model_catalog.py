"""Server-owned model capabilities and account-scoped editor availability."""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
from .tenancy import config_root
import tempfile

SD25 = 'doubao-seedance-2-5-260628'
SD20 = 'doubao-seedance-2-0-260128'
MODELS = {
    SD25: 'Seedance 2.5',
    SD20: 'Seedance 2.0',
    'doubao-seedance-2-0-fast-260128': 'Seedance 2.0 Fast',
    'doubao-seedance-2-0-mini-260615': 'Seedance 2.0 Mini',
}
TASK_FIELDS = {'model', 'duration', 'resolution'}
LEGACY_FIELDS = {'provider', 'protocol', 'mode', 'base_url', 'fps', 'public_base_url'}


def capabilities(model: str, protocol: str = 'ark') -> dict:
    editing = protocol == 'ark' and model == SD25
    fast = protocol != 'adapter' and ('seedance-2-fast' in model or 'seedance-2-mini' in model
                                     or 'seedance-2-0-fast' in model or 'seedance-2-0-mini' in model)
    return {'resolutions': ['480p', '720p'] if fast else ['480p', '720p', '1080p'] if editing else ['480p', '720p', '1080p', '4k'],
            'max_duration': 30 if editing else 15, 'follow_source': editing,
            'max_images': None if protocol == 'adapter' else 30 if editing else 9,
            'max_video_seconds': 30 if editing else 15,
            'person_video': protocol == 'ark' and (model in MODELS or model.startswith(('doubao-seedance-2-0', 'doubao-seedance-2.0')))}


def task_values(config) -> dict:
    return {name: (-1 if name == 'duration' and capabilities(config.model, config.protocol)['follow_source'] else getattr(config, name))
            for name in TASK_FIELDS}


def _scope(config) -> str:
    # A different credential or destination must not inherit account acceptance.
    return hashlib.sha256(json.dumps([config.provider, config.protocol, config.base_url, config.api_key]).encode()).hexdigest()


def catalog(settings) -> dict:
    from .generation_settings import load_config, PRESETS
    config = load_config(settings)
    path = config_root(settings) / 'private' / 'model-catalog.json'
    document = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    same_account = document.get('scope') == _scope(config)
    overrides = document.get('items', {}) if same_account else {}
    inherited = document.get('legacy_model') if same_account else (config.model if not document else None)
    ids = list(MODELS) if config.protocol == 'ark' else list(PRESETS.get(config.provider, {}).get('models', []))
    if config.model not in ids:
        ids.append(config.model)
    items = []
    for index, ident in enumerate(ids):
        # Preserve the existing configured route during migration, without claiming new models were tested.
        existing = ident == inherited and ident != SD25
        row = {'id': ident, 'label': MODELS.get(ident, ident), 'enabled': existing,
               'verified': False, 'existing': existing, 'order': index}
        row.update(overrides.get(ident, {}))
        row.update(capabilities(ident, config.protocol))
        items.append(row)
    return {'items': sorted(items, key=lambda row: (row['order'], row['id'])), 'default_model': config.model}


def save_catalog(settings, payload: dict) -> dict:
    from .generation_settings import load_config, _lock
    with _lock:
        current = catalog(settings)
        if set(payload) != {'items'} or not isinstance(payload['items'], list):
            raise ValueError('模型目录格式不正确。')
        known = {row['id']: row for row in current['items']}
        if len(payload['items']) != len(known):
            raise ValueError('模型目录发生变化，请刷新后保存。')
        entries = {}
        for item in payload['items']:
            if not isinstance(item, dict) or item.get('id') not in known or item['id'] in entries:
                raise ValueError('模型目录含未知或重复模型。')
            if type(item.get('enabled')) is not bool or type(item.get('verified')) is not bool:
                raise ValueError('模型启用和验收状态不正确。')
            label, order = item.get('label'), item.get('order')
            if not isinstance(label, str) or not 1 <= len(label.strip()) <= 60 or type(order) is not int or not 0 <= order <= 100:
                raise ValueError('模型名称或排序不正确。')
            if item['enabled'] and not item['verified'] and not known[item['id']]['existing']:
                raise ValueError('新模型请先完成实际生成验收，再勾选已验收并启用。连接测试不验证生成权限。')
            entries[item['id']] = {name: item[name] for name in ('enabled', 'verified', 'order')}
            entries[item['id']]['label'] = label.strip()
        inherited = next((row['id'] for row in current['items'] if row['existing']), None)
        _write(settings, {'scope': _scope(load_config(settings)), 'items': entries, 'legacy_model': inherited})
        return catalog(settings)


def _write(settings, document):
    path = config_root(settings) / 'private' / 'model-catalog.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.catalog-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(document, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def preserve_existing(settings):
    """Freeze migration eligibility before an administrator changes the default."""
    from .generation_settings import load_config, config_path
    path = config_root(settings) / 'private' / 'model-catalog.json'
    if not path.exists() and config_path(settings).exists():
        current = load_config(settings)
        _write(settings, {'scope':_scope(current), 'items':{}, 'legacy_model':current.model})


def editor_options(settings) -> dict:
    from .generation_settings import load_config
    config = load_config(settings)
    items = [{key: value for key, value in row.items() if key not in {'existing', 'verified', 'enabled', 'order'}}
             for row in catalog(settings)['items'] if row['enabled']]
    defaults = task_values(config)
    if items and defaults['model'] not in {row['id'] for row in items}:
        defaults = task_values(replace(config, model=items[0]['id'], duration=8, resolution='720p'))
    return {'items': items, 'defaults': defaults, 'status': config.public()['status']}


def resolve_task_config(settings, payload: dict, *, require_enabled: bool = False):
    from .generation_settings import load_config, resolve_config
    current = load_config(settings)
    if not isinstance(payload, dict) or set(payload) - TASK_FIELDS - LEGACY_FIELDS:
        raise ValueError('任务只接受模型、时长和清晰度设置。')
    for name in LEGACY_FIELDS & payload.keys():
        if payload[name] != getattr(current, name):
            raise ValueError('任务中的旧连接配置与后台不一致，请重新选择模型；连接配置请在后台修改。')
    values = {name: payload[name] for name in TASK_FIELDS & payload.keys()}
    model = values.get('model', current.model)
    known = {row['id']: row for row in catalog(settings)['items']}
    if not isinstance(model, str) or model not in known:
        raise ValueError('此模型尚未在后台配置，请选择可用模型。')
    if require_enabled and not known[model]['enabled']:
        raise ValueError('此模型尚未启用或未通过账号验收，请在后台检查模型目录。')
    limits = capabilities(model, current.protocol)
    if 'duration' in values:
        duration = values['duration']
        if type(duration) is not int or not (4 <= duration <= limits['max_duration'] or (limits['follow_source'] and duration == -1)):
            raise ValueError(f"当前模型时长应为 4–{limits['max_duration']} 秒，视频编辑可跟随原视频。")
    if limits['follow_source']:
        values['duration'] = -1
    return resolve_config(settings, values)
