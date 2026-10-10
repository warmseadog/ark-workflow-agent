"""Admin-only prompt records. Never consulted by drafts or generation workers."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from . import local_preferences
from .prompt_templates import DEFAULT_TEMPLATE_ID
from .reference_prompt import compose_exclusive_prompt, strict_reference_rules, strip_reference_rules


RULE_LABELS = {
    'legacy-v1': '早期素材联动',
    'exclusive-v2': '独立参考优先',
    'yoyo-v3': '独立参考优先，其余配饰跟随穿搭图',
}
EXAMPLE_ROLES = ['人物', '衣服', '围巾', '手饰']


def _rules_fingerprint():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ('reference_prompt.py', 'reference_roles.py', 'video_provider.py', 'editor_assets/legacy-prompt-rules.js'):
        digest.update(name.encode())
        digest.update((root/name).read_bytes().replace(b'\r\n', b'\n'))
    return digest.hexdigest()


def _example(content, version, settings=None):
    if settings is not None:
        from .prompt_config import load, scope
        with scope(load(settings)):
            return _example(content,version)
    if version == 'legacy-v1':
        # Legacy provider also adds model/protocol-specific wrappers; name this
        # explicitly as an illustration, not a promise of an exact submission.
        return content + '\n\n' + strict_reference_rules([(None, role) for role in EXAMPLE_ROLES])
    return compose_exclusive_prompt(content, EXAMPLE_ROLES, rule_version=version)


def _path(root):
    return root.storage_dir/'private'/'prompt-standby.db'


def overview(settings):
    root = local_preferences._shared_settings(settings)
    templates = local_preferences.list_templates(root)
    current = next((item for item in templates if item['id'] == DEFAULT_TEMPLATE_ID), None)
    fingerprint = _rules_fingerprint()
    if current:
        current = {**current, 'rule_label': RULE_LABELS.get(current['rule_version'], current['rule_version']),
                   'rules_fingerprint': fingerprint, 'example_prompt': _example(current['content'], current['rule_version'],settings)}
    path = _path(root)
    items = []
    if path.is_file():
        with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=15) as db:
            # A first writer may have created the file but not the table yet.
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='prompt_standby'").fetchone():
                items = [json.loads(row[0]) for row in db.execute('SELECT data FROM prompt_standby ORDER BY rowid DESC LIMIT 100')]
    return {'current': current, 'templates': templates, 'items': items,
            'activation_available': False, 'rule_labels': RULE_LABELS,
            'example_roles': EXAMPLE_ROLES, 'rules_fingerprint': fingerprint}


def save(settings, *, name, content, source_template_id, note=''):
    root = local_preferences._shared_settings(settings)
    name, content, note = name.strip(), strip_reference_rules(content), note.strip()
    if not name or len(name) > 60 or not content or len(content) > 10000 or len(note) > 1000:
        raise ValueError('名称需为 1–60 字，正文需为 1–10000 字，备注最多 1000 字。')
    source = next((item for item in local_preferences.list_templates(root) if item['id'] == source_template_id), None)
    if source is None:
        raise LookupError('来源共享模板不存在，请刷新后重新选择。')
    item = {'id': uuid4().hex, 'name': name, 'content': content, 'note': note, 'status': 'standby',
            'created_at': datetime.now(timezone.utc).isoformat(), 'source_template_id': source['id'],
            'source_name': source['name'], 'source_content': source['content'], 'rule_version': source['rule_version'],
            'rule_label': RULE_LABELS[source['rule_version']], 'rules_fingerprint': _rules_fingerprint(),
            'example_prompt': _example(content, source['rule_version'],settings)}
    path = _path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=15) as db:
        db.execute('CREATE TABLE IF NOT EXISTS prompt_standby (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        db.execute('INSERT INTO prompt_standby VALUES (?,?)', (item['id'], json.dumps(item, ensure_ascii=False)))
    return item
