"""Local SQLite persistence for prompt templates and TikHub credentials."""
import os
import sqlite3
from contextlib import contextmanager
from uuid import uuid4
from .prompt_templates import (DEFAULT_TEMPLATE_ID, EXCLUSIVE_TEMPLATE_ID, EXCLUSIVE_PROMPT,
    EXCLUSIVE_RULE_VERSION, YOYO_RULE_VERSION, LEGACY_RULE_VERSION, RULE_VERSIONS, yoyo_prompt)

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
            # Serialize schema inspection and one-time migration across requests.
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS prompt_templates (id TEXT PRIMARY KEY, name TEXT NOT NULL, content TEXT NOT NULL)')
            if 'rule_version' not in {r[1] for r in db.execute('PRAGMA table_info(prompt_templates)')}:
                db.execute("ALTER TABLE prompt_templates ADD COLUMN rule_version TEXT NOT NULL DEFAULT 'legacy-v1'")
            cursor = db.execute("INSERT OR IGNORE INTO preferences VALUES ('prompts_initialized', '1')")
            if cursor.rowcount and (not getattr(settings,'user_id','') or settings.storage_dir == getattr(settings,'config_root',None)):
                db.executemany('INSERT INTO prompt_templates (id,name,content) VALUES (?, ?, ?)',
                    [(f'default-{i}', name, text) for i, (name, text) in enumerate(DEFAULT_PROMPTS[:1])])
            # Upgrade only untouched bundled templates, once; never recreate deleted items.
            upgrade = db.execute("INSERT OR IGNORE INTO preferences VALUES ('prompts_material_roles_v1','1')")
            if upgrade.rowcount:
                for i, (name, text) in enumerate(DEFAULT_PROMPTS):
                    db.execute('UPDATE prompt_templates SET content=? WHERE id=? AND name=? AND content IN (?,?)',
                               (text,f'default-{i}',name,PREVIOUS_PROMPTS[i][1],LEGACY_PROMPTS[i][1]))
            migration = db.execute("INSERT OR IGNORE INTO preferences VALUES ('prompts_exclusive_v2','1')")
            from .tenancy import config_root
            if migration.rowcount and settings.storage_dir == config_root(settings):
                db.execute('INSERT OR IGNORE INTO prompt_templates (id,name,content,rule_version) VALUES (?,?,?,?)',
                           (EXCLUSIVE_TEMPLATE_ID, '默认提示词', EXCLUSIVE_PROMPT, EXCLUSIVE_RULE_VERSION))
                db.execute("UPDATE prompt_templates SET name='默认提示词2' WHERE id='default-0' AND name='动作保留'")
            yoyo_migration = db.execute("INSERT OR IGNORE INTO preferences VALUES ('prompts_yoyo_v3','1')")
            if yoyo_migration.rowcount and settings.storage_dir == config_root(settings):
                previous = db.execute('SELECT content FROM prompt_templates WHERE id=?', (EXCLUSIVE_TEMPLATE_ID,)).fetchone()
                db.execute('INSERT OR IGNORE INTO prompt_templates (id,name,content,rule_version) VALUES (?,?,?,?)',
                           (DEFAULT_TEMPLATE_ID, 'yoyo提示词', yoyo_prompt(previous['content'] if previous else EXCLUSIVE_PROMPT), YOYO_RULE_VERSION))
            yield db
    finally:
        db.close()

def _personal_settings(settings):
    """Even the legacy owner has private templates; its old root rows stay shared."""
    from .tenancy import user_settings
    owner = getattr(settings, 'user_id', '')
    return user_settings(settings, {'id': owner, 'legacy_owner': False}) if owner else settings


def _shared_settings(settings):
    from . import tenancy
    if tenancy.enabled():
        from .accounts import Accounts, AccountError
        try:
            user = Accounts(tenancy.config_root(settings)).get_user(getattr(settings, 'user_id', ''))
        except AccountError:
            raise PermissionError('共享模板仅管理员可管理。') from None
        if not user['enabled'] or user['role'] not in {'admin', 'super_admin'}:
            raise PermissionError('共享模板仅管理员可管理。')
    return tenancy.root_settings(settings)


def list_shared_templates(settings):
    return list_templates(_shared_settings(settings))


def save_shared_template(settings, name, content, template_id=None, rule_version=None):
    return save_template(_shared_settings(settings), name, content, template_id, rule_version)


def delete_shared_template(settings, template_id):
    delete_template(_shared_settings(settings), template_id)


def list_templates(settings):
    settings = _personal_settings(settings)
    with connection(settings) as db:
        personal = [{**dict(row), 'is_default':row['id'] == DEFAULT_TEMPLATE_ID} for row in db.execute(
            'SELECT * FROM prompt_templates ORDER BY (id=?) DESC,(id=?) DESC,rowid', (DEFAULT_TEMPLATE_ID, EXCLUSIVE_TEMPLATE_ID))]
    from .tenancy import root_settings, config_root
    if settings.storage_dir != config_root(settings):
        shared=[{**item,'id':'system:'+item['id'],'scope':'shared','read_only':True} for item in list_templates(root_settings(settings))]
        return shared+[{**item, 'scope':'personal', 'read_only':False} for item in personal]
    return personal


def _template_write_settings(settings, template_id):
    if template_id and template_id.startswith('system:'):
        raise PermissionError('系统模板为只读，请另存为个人模板；管理员可在后台管理共享模板。')
    personal = _personal_settings(settings)
    if template_id and getattr(settings, 'user_id', ''):
        from .tenancy import root_settings
        # Also reject old, unprefixed shared IDs retained by an existing browser.
        with connection(root_settings(settings)) as db:
            if db.execute('SELECT 1 FROM prompt_templates WHERE id=?', (template_id,)).fetchone():
                raise PermissionError('共享模板请由管理员在后台管理。')
    return personal


def save_template(settings, name, content, template_id=None, rule_version=None):
    settings = _template_write_settings(settings, template_id)
    from .reference_prompt import strip_reference_rules
    name, content = name.strip(), strip_reference_rules(content)
    if not name or len(name) > 60 or not content or len(content) > 10000:
        raise ValueError('模板名称需为 1–60 个字符，提示词需为 1–10000 个字符。')
    if rule_version is not None and rule_version not in RULE_VERSIONS:
        raise ValueError('提示词规则版本不正确。')
    with connection(settings) as db:
        if template_id:
            old = db.execute('SELECT rule_version FROM prompt_templates WHERE id=?', (template_id,)).fetchone()
            rule_version = rule_version or (old['rule_version'] if old else LEGACY_RULE_VERSION)
            if not db.execute('UPDATE prompt_templates SET name=?, content=?, rule_version=? WHERE id=?', (name, content, rule_version, template_id)).rowcount:
                raise LookupError('模板不存在或已被删除，请刷新列表。')
        else:
            template_id = uuid4().hex
            rule_version = rule_version or LEGACY_RULE_VERSION
            db.execute('INSERT INTO prompt_templates (id,name,content,rule_version) VALUES (?, ?, ?, ?)', (template_id, name, content, rule_version))
    return {'id': template_id, 'name': name, 'content': content, 'rule_version':rule_version, 'is_default':template_id == DEFAULT_TEMPLATE_ID}


def default_prompt_values(settings):
    selected = next((x for x in list_templates(settings) if x['is_default']), None)
    if selected:
        return {'prompt':selected['content'], 'prompt_template_id':selected['id'], 'prompt_rule_version':selected['rule_version']}
    # Respect deletion: do not silently recreate or apply the removed template.
    return {'prompt':'', 'prompt_template_id':None, 'prompt_rule_version':LEGACY_RULE_VERSION}

def delete_template(settings, template_id):
    settings = _template_write_settings(settings, template_id)
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
