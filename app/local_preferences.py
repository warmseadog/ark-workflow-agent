"""Local SQLite persistence for prompt templates and TikHub credentials."""
import os
import sqlite3
from contextlib import contextmanager
from uuid import uuid4

DEFAULT_PROMPTS = [
    ('动作保留', '保持@Video1原视频的动作、镜头和节奏；应用@Image1人物参考图中的脸、五官与身份；应用@Image2衣服参考图中的服装款式、颜色和材质。'),
    ('自然换装', '保持@Video1原视频的动作、镜头不变；使用@Image1人物参考图中的脸和身份；自然换上@Image2衣服参考图中的衣服，保持服装版型、颜色与材质。'),
    ('电商展示', '保持@Video1原视频的动作和镜头；使用@Image1人物参考图的脸、@Image2衣服参考图的衣服，突出服装细节、版型和材质，适合电商展示。'),
    ('稳定一致', '保持@Video1原视频的动作、镜头和节奏；应用@Image1人物参考图的脸和身份、@Image2衣服参考图的衣服，保持脸部、服装纹理和颜色在全片一致。'),
]

@contextmanager
def connection(settings):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.storage_dir / 'local-preferences.db', timeout=15)
    db.row_factory = sqlite3.Row
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS prompt_templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, content TEXT NOT NULL)')
            cursor = db.execute("INSERT OR IGNORE INTO preferences VALUES ('prompts_initialized', '1')")
            if cursor.rowcount:
                db.executemany('INSERT INTO prompt_templates VALUES (?, ?, ?)',
                    [(f'default-{i}', name, text) for i, (name, text) in enumerate(DEFAULT_PROMPTS)])
            yield db
    finally:
        db.close()

def list_templates(settings):
    with connection(settings) as db:
        return [dict(row) for row in db.execute('SELECT * FROM prompt_templates ORDER BY rowid')]

def save_template(settings, name, content, template_id=None):
    name, content = name.strip(), content.strip()
    if not name or len(name) > 60 or not content or len(content) > 10000:
        raise ValueError('模板名称需为 1–60 个字符，提示词需为 1–10000 个字符。')
    with connection(settings) as db:
        if template_id:
            if not db.execute('UPDATE prompt_templates SET name=?, content=? WHERE id=?', (name, content, template_id)).rowcount:
                raise LookupError('模板不存在或已被删除，请刷新列表。')
        else:
            template_id = uuid4().hex
            db.execute('INSERT INTO prompt_templates VALUES (?, ?, ?)', (template_id, name, content))
    return {'id': template_id, 'name': name, 'content': content}

def delete_template(settings, template_id):
    with connection(settings) as db:
        if not db.execute('DELETE FROM prompt_templates WHERE id=?', (template_id,)).rowcount:
            raise LookupError('模板不存在或已被删除。')

def get_tikhub_key(settings):
    with connection(settings) as db:
        row = db.execute("SELECT value FROM preferences WHERE key='tikhub_api_key'").fetchone()
    return row['value'] if row else os.getenv('TIKHUB_API_KEY', '').strip()

def save_tikhub_key(settings, api_key='', clear_api_key=False):
    key = api_key.strip()
    if len(key) > 2048 or any(ord(char) < 32 or ord(char) > 126 for char in key):
        raise ValueError('API Key 格式不正确。')
    if key or clear_api_key:
        with connection(settings) as db:
            db.execute('INSERT OR REPLACE INTO preferences VALUES (?, ?)', ('tikhub_api_key', '' if clear_api_key else key))
