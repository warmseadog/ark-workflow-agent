"""Local SQLite persistence for prompt templates and TikHub credentials."""
import os
import sqlite3
from contextlib import contextmanager
from uuid import uuid4

PREVIOUS_PROMPTS = [
    ('动作保留', '保持@Video1原视频的动作、镜头和节奏；应用@Image1人物参考图中的脸、五官与身份；应用@Image2衣服参考图中的服装款式、颜色和材质。'),
    ('自然换装', '保持@Video1原视频的动作、镜头不变；使用@Image1人物参考图中的脸和身份；自然换上@Image2衣服参考图中的衣服，保持服装版型、颜色与材质。'),
    ('电商展示', '保持@Video1原视频的动作和镜头；使用@Image1人物参考图的脸、@Image2衣服参考图的衣服，突出服装细节、版型和材质，适合电商展示。'),
    ('稳定一致', '保持@Video1原视频的动作、镜头和节奏；应用@Image1人物参考图的脸和身份、@Image2衣服参考图的衣服，保持脸部、服装纹理和颜色在全片一致。'),
]

LEGACY_PROMPTS = [
    ('动作保留', '保持@Video1原视频的动作、镜头、场景和节奏；应用@Image1人物参考图中的脸、五官与身份；应用@Image2衣服参考图中的服装款式、颜色和材质。'),
    ('自然换装', '保持@Video1原视频的动作、镜头和场景不变；使用@Image1人物参考图中的脸和身份；自然换上@Image2衣服参考图中的衣服，保持服装版型、颜色与材质。'),
    ('电商展示', '保持@Video1原视频的动作、镜头和场景；使用@Image1人物参考图的脸、@Image2衣服参考图的衣服，突出服装细节、版型和材质，适合电商展示。'),
    ('稳定一致', '保持@Video1原视频的动作、镜头、场景和节奏；应用@Image1人物参考图的脸和身份、@Image2衣服参考图的衣服，保持脸部、服装纹理和颜色在全片一致。'),
]

DEFAULT_PROMPTS = [
    ('动作保留', '以@Video1为动作与运镜参考，复刻主体的姿态、步态、动作顺序、镜头运动、构图和节奏。以@Image1为主人物身份参考，保持脸型、五官和人物身份一致；以@Image2为主服装参考，准确还原服装款式、剪裁、颜色和面料质感。动作衔接自然，头发与衣物随动作合理运动，保持全片人物和穿着稳定。'),
    ('自然换装', '沿用@Video1的动作、姿态、运镜和节奏，将主体呈现为@Image1的人物身份，并自然穿着@Image2的服装。服装贴合身体，保留版型、材质、纹理和配饰细节；避免衣物穿模、脸部变形和画面闪烁，光影与环境协调。'),
    ('电商展示', '参考@Video1的动作和镜头，使用@Image1的人物身份展示@Image2的服装。突出领口、腰线、剪裁、面料和穿着效果，颜色准确，主体清晰；人物动作自然，保留参考视频的展示节奏，服装细节在镜头间一致。'),
    ('稳定一致', '按照@Video1的动作时序、姿态、构图和运镜生成连续视频。全片保持@Image1的人物身份、脸型和五官，以及@Image2服装的款式、颜色、纹理和材质一致。头发与衣服运动自然，减少脸部漂移、手部畸变、纹理跳变和闪烁。'),
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
            if cursor.rowcount and (not getattr(settings,'user_id','') or settings.storage_dir == getattr(settings,'config_root',None)):
                db.executemany('INSERT INTO prompt_templates VALUES (?, ?, ?)',
                    [(f'default-{i}', name, text) for i, (name, text) in enumerate(DEFAULT_PROMPTS)])
            # Upgrade only untouched bundled templates, once; never recreate deleted items.
            upgrade = db.execute("INSERT OR IGNORE INTO preferences VALUES ('prompts_material_roles_v1','1')")
            if upgrade.rowcount:
                for i, (name, text) in enumerate(DEFAULT_PROMPTS):
                    db.execute('UPDATE prompt_templates SET content=? WHERE id=? AND name=? AND content IN (?,?)',
                               (text,f'default-{i}',name,PREVIOUS_PROMPTS[i][1],LEGACY_PROMPTS[i][1]))
            yield db
    finally:
        db.close()

def list_templates(settings):
    with connection(settings) as db:
        personal = [dict(row) for row in db.execute('SELECT * FROM prompt_templates ORDER BY rowid')]
    from .tenancy import root_settings, config_root
    if settings.storage_dir != config_root(settings):
        shared=[{**item,'id':'system:'+item['id'],'read_only':True} for item in list_templates(root_settings(settings))]
        return shared+personal
    return personal

def save_template(settings, name, content, template_id=None):
    if template_id and template_id.startswith('system:'):
        raise ValueError('系统模板为只读，请另存为个人模板。')
    from .reference_prompt import strip_reference_rules
    name, content = name.strip(), strip_reference_rules(content)
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
    if template_id.startswith('system:'):
        raise ValueError('系统模板为只读，不能删除。')
    with connection(settings) as db:
        if not db.execute('DELETE FROM prompt_templates WHERE id=?', (template_id,)).rowcount:
            raise LookupError('模板不存在或已被删除。')

def get_tikhub_key(settings):
    from .tenancy import root_settings
    settings = root_settings(settings)
    with connection(settings) as db:
        row = db.execute("SELECT value FROM preferences WHERE key='tikhub_api_key'").fetchone()
    return row['value'] if row else os.getenv('TIKHUB_API_KEY', '').strip()

def save_tikhub_key(settings, api_key='', clear_api_key=False):
    from .tenancy import root_settings
    settings = root_settings(settings)
    key = api_key.strip()
    if len(key) > 2048 or any(ord(char) < 32 or ord(char) > 126 for char in key):
        raise ValueError('API Key 格式不正确。')
    if key or clear_api_key:
        with connection(settings) as db:
            db.execute('INSERT OR REPLACE INTO preferences VALUES (?, ?)', ('tikhub_api_key', '' if clear_api_key else key))
