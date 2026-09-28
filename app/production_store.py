"""Durable local editing drafts and immutable production attempts."""
from __future__ import annotations
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path


def default_name(person='视频'):
    return (person[:80] or '视频') + '-' + datetime.now(timezone(timedelta(hours=8))).strftime('%m%d-%H%M%S')


def now():
    return datetime.now(timezone.utc).isoformat()


class Conflict(ValueError):
    pass


class ProductionStore:
    def __init__(self, storage):
        self.storage = Path(storage)
        self.storage.mkdir(parents=True, exist_ok=True)
        self.path = self.storage / 'production.db'
        with self.connection() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS production_playbacks (id TEXT PRIMARY KEY, status TEXT NOT NULL, signature TEXT NOT NULL, metadata TEXT, message TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS production_run_names (id TEXT PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS production_portraits (
                asset_id TEXT PRIMARY KEY, remote_asset_id TEXT NOT NULL,
                group_id TEXT NOT NULL, project TEXT NOT NULL,
                fingerprint TEXT NOT NULL, status TEXT NOT NULL,
                verified_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS production_deleted_runs (
                id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS production_assets (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL,
                path TEXT NOT NULL, size INTEGER NOT NULL, mime TEXT NOT NULL,
                sha256 TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS production_drafts (
                id TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS production_runs (
                id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, revision INTEGER NOT NULL,
                idempotency_key TEXT NOT NULL UNIQUE, snapshot TEXT NOT NULL,
                private TEXT NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL,
                message TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
                error TEXT, error_kind TEXT, request_id TEXT, provider_task_id TEXT,
                result_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS production_runs_order ON production_runs(created_at DESC,id DESC);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _draft(row):
        if row is None:
            raise LookupError('找不到草稿。')
        return {**json.loads(row['data']), 'id': row['id'], 'revision': row['revision'],
                'created_at': row['created_at'], 'updated_at': row['updated_at']}

    def create_draft(self, values):
        ident, stamp = uuid.uuid4().hex, now()
        data = {'name': default_name(), 'source_asset_id': None, 'face_asset_ids': [],
                'person_reference_mode':'image','person_video_asset_id':None,
                'clothing_asset_ids': [], 'hairstyle_asset_ids': [], 'scene_asset_ids': [],
                **{kind+suffix: ([] if suffix=='_asset_ids' else False) for kind in ('bag','hat','watch','shoes','necklace','glasses') for suffix in ('_asset_ids','_enabled')},
                'hairstyle_mask': {'mask_scale':1.0,'threshold':0.2}, 'hairstyle_enabled': False, 'scene_enabled': False, 'scene_description': '', 'prompt': '', 'mask': {}, 'model': {}, **values}
        with self.connection() as db:
            db.execute('INSERT INTO production_drafts VALUES (?,?,?,?,?)',
                       (ident, 1, json.dumps(data, ensure_ascii=False), stamp, stamp))
        return self.get_draft(ident)

    def get_draft(self, ident):
        with self.connection() as db:
            return self._draft(db.execute('SELECT * FROM production_drafts WHERE id=?', (ident,)).fetchone())

    def list_drafts(self):
        with self.connection() as db:
            return [self._draft(row) for row in db.execute('SELECT * FROM production_drafts ORDER BY updated_at DESC')]

    def save_draft(self, ident, revision, values):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM production_drafts WHERE id=?', (ident,)).fetchone()
            current = self._draft(row)
            if current['revision'] != revision:
                raise Conflict('草稿已在另一个页面更新，请重新打开草稿后再编辑。')
            data = {**json.loads(row['data']), **values}
            db.execute('UPDATE production_drafts SET data=?,revision=revision+1,updated_at=? WHERE id=?',
                       (json.dumps(data, ensure_ascii=False), now(), ident))
            saved = self._draft(db.execute('SELECT * FROM production_drafts WHERE id=?', (ident,)).fetchone())
        return saved

    def add_asset(self, ident, name, kind, path, size, mime, sha256):
        with self.connection() as db:
            db.execute('INSERT INTO production_assets VALUES (?,?,?,?,?,?,?,?)',
                       (ident, name, kind, str(path), size, mime, sha256, now()))
        return self.get_asset(ident)

    def get_asset(self, ident, private=False):
        with self.connection() as db:
            row = db.execute('SELECT * FROM production_assets WHERE id=?', (ident,)).fetchone()
        if row is None:
            raise LookupError('素材不存在，请重新选择。')
        value = dict(row)
        if not private:
            value.pop('path')
        value['url'] = '/api/production/assets/' + ident + '/file'
        portrait = self.portrait_binding(ident)
        if portrait:
            value['portrait'] = {k:v for k,v in portrait.items() if k not in {'asset_id','fingerprint'}}
        return value

    def portrait_binding(self, ident):
        with self.connection() as db:
            row = db.execute('SELECT * FROM production_portraits WHERE asset_id=?', (ident,)).fetchone()
            return dict(row) if row else None

    def find_portrait(self, remote_id, fingerprint):
        with self.connection() as db:
            row = db.execute('SELECT asset_id FROM production_portraits WHERE remote_asset_id=? AND fingerprint=?', (remote_id, fingerprint)).fetchone()
            return row['asset_id'] if row else None

    def register_portrait(self, ident, name, path, size, mime, sha256, remote, fingerprint):
        # Network/download work occurs before this short transaction. All API
        # processes converge on a single local identity for the official image.
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            existing=db.execute('SELECT p.asset_id,a.path FROM production_portraits p JOIN production_assets a ON a.id=p.asset_id WHERE p.remote_asset_id=? AND p.fingerprint=?',
                (remote['remote_asset_id'],fingerprint)).fetchone()
            if existing and Path(existing['path']).is_file():
                return existing['asset_id']
            if existing:
                db.execute('DELETE FROM production_portraits WHERE asset_id=?',(existing['asset_id'],))
            db.execute('INSERT INTO production_assets VALUES (?,?,?,?,?,?,?,?)',
                (ident,name,'face',str(path),size,mime,sha256,now()))
            db.execute('INSERT INTO production_portraits VALUES (?,?,?,?,?,?,?)',
                (ident,remote['remote_asset_id'],remote['group_id'],remote['project'],fingerprint,remote['status'],now()))
        return ident

    def bind_portrait(self, ident, remote, fingerprint):
        with self.connection() as db:
            db.execute('INSERT OR REPLACE INTO production_portraits VALUES (?,?,?,?,?,?,?)',
                (ident,remote['remote_asset_id'],remote['group_id'],remote['project'],fingerprint,remote['status'],now()))

    def _run(self, row, private=False):
        if row is None:
            raise LookupError('找不到生成任务。')
        value = dict(row)
        value['snapshot'] = json.loads(value['snapshot'])
        value['name'] = self.run_name(value['id'],value['snapshot'].get('name', '未命名视频'))
        has_remote = bool(value['provider_task_id'] or value['result_url'])
        value['can_resume'] = value['status'] == 'needs_attention' and has_remote
        value['can_cancel'] = value['status'] == 'queued' and not has_remote
        value['can_delete'] = value['status'] != 'running' and (value['status'] != 'queued' or not has_remote)
        if private:
            value['private'] = json.loads(value['private'])
        else:
            value.pop('private')
            value.pop('result_url')
            value.pop('idempotency_key')
        value['legacy'] = False
        return value

    def get_run(self, ident, private=False):
        with self.connection() as db:
            return self._run(db.execute('SELECT * FROM production_runs WHERE id=?', (ident,)).fetchone(), private)

    def run_by_key(self, key):
        with self.connection() as db:
            row = db.execute('SELECT * FROM production_runs WHERE idempotency_key=?', (key,)).fetchone()
            return self._run(row) if row else None

    def create_run(self, draft_id, revision, key, private):
        ident, stamp = uuid.uuid4().hex, now()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM production_runs WHERE idempotency_key=?', (key,)).fetchone()
            if row:
                if row['draft_id'] != draft_id or row['revision'] != revision:
                    raise Conflict('提交标识已用于另一份输入，请核对任务列表。')
                return self._run(row)
            draft = self._draft(db.execute('SELECT * FROM production_drafts WHERE id=?', (draft_id,)).fetchone())
            if draft['revision'] != revision:
                raise Conflict('草稿发生变化，请保存后重新提交。')
            db.execute('INSERT INTO production_runs\n                (id,draft_id,revision,idempotency_key,snapshot,private,status,stage,message,created_at,updated_at)\n                VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (ident,draft_id,revision,key,json.dumps(draft,ensure_ascii=False),json.dumps(private,ensure_ascii=False),
                 'queued','queued','等待处理',stamp,stamp))
        return self.get_run(ident)

    def run_name(self, ident, fallback):
        with self.connection() as db:
            row=db.execute('SELECT name FROM production_run_names WHERE id=?',(ident,)).fetchone()
            return row['name'] if row else fallback

    def rename_run(self, ident, name):
        if not isinstance(name,str) or not 1<=len(name.strip())<=120 or any(ord(c)<32 or ord(c)==127 for c in name):
            raise ValueError('任务名称请输入 1–120 个字符，不支持换行。')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM production_deleted_runs WHERE id=?',(ident,)).fetchone():
                raise LookupError('任务已删除。')
            if not ident.startswith('legacy-') and not db.execute('SELECT 1 FROM production_runs WHERE id=?',(ident,)).fetchone():
                raise LookupError('找不到任务。')
            db.execute('INSERT OR REPLACE INTO production_run_names VALUES (?,?,?)',(ident,name.strip(),now()))
        return {'id':ident,'name':name.strip()}

    def page_runs(self, page, page_size, legacy):
        # Summaries are selected by SQLite before any snapshot/media decoration.
        fields=['id','name','status','stage','message','progress','created_at','updated_at','error_kind','duration','download_url','has_remote','legacy']
        with self.connection() as db:
            db.execute('CREATE TEMP TABLE legacy_page (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.executemany('INSERT INTO legacy_page VALUES (?,?)',[(x['id'],json.dumps({k:x.get(k) for k in fields})) for x in legacy])
            legacy_sql=','.join("json_extract(l.data,'$."+k+"') AS "+k for k in fields)
            sql="""WITH combined AS (
                SELECT id,json_extract(snapshot,'$.name') AS name,status,stage,message,progress,created_at,updated_at,error_kind,
                json_extract(snapshot,'$.model.duration') AS duration,NULL AS download_url,
                (provider_task_id IS NOT NULL OR result_url IS NOT NULL) AS has_remote,0 AS legacy
                FROM production_runs
                UNION ALL SELECT """+legacy_sql+""" FROM legacy_page l
            ), visible AS (SELECT c.*,COALESCE(n.name,c.name) AS display_name FROM combined c
                LEFT JOIN production_run_names n ON c.id=n.id
                WHERE c.id NOT IN (SELECT id FROM production_deleted_runs)) """
            count=db.execute(sql+"SELECT count(*) AS total,COALESCE(sum(status IN ('running','queued')),0) AS active FROM visible").fetchone()
            pages=max(1,(count['total']+page_size-1)//page_size);page=min(page,pages)
            rows=db.execute(sql+'SELECT * FROM visible ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',(page_size,(page-1)*page_size)).fetchall()
        result=[]
        for row in rows:
            item=dict(row);item['name']=item.pop('display_name');item['legacy']=bool(item['legacy']);remote=item.pop('has_remote')
            item['can_resume']=not item['legacy'] and item['status']=='needs_attention' and bool(remote)
            item['can_cancel']=not item['legacy'] and item['status']=='queued' and not remote
            item['can_delete']=item['status']!='running' and (item['status']!='queued' or (not item['legacy'] and not remote))
            result.append(item)
        return {'items':result,'total':count['total'],'active_count':count['active'],'page':page,'pages':pages,'page_size':page_size}

    def list_runs(self):
        with self.connection() as db:
            return [self._run(row) for row in db.execute('SELECT * FROM production_runs WHERE id NOT IN (SELECT id FROM production_deleted_runs) ORDER BY created_at DESC')]

    def deleted_run_ids(self):
        with self.connection() as db:
            return {row['id'] for row in db.execute('SELECT id FROM production_deleted_runs')}

    def require_visible(self, ident):
        if ident in self.deleted_run_ids():
            raise LookupError('任务已删除。')

    def delete_run(self, ident, legacy_status=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM production_deleted_runs WHERE id=?', (ident,)).fetchone():
                return
            if ident.startswith('legacy-'):
                if legacy_status is None:
                    raise LookupError('找不到任务。')
                if legacy_status in {'running', 'queued'}:
                    raise Conflict('任务正在处理，请等待结束后删除。')
            else:
                row = db.execute('SELECT * FROM production_runs WHERE id=?', (ident,)).fetchone()
                run = self._run(row)
                if not run['can_delete']:
                    raise Conflict('任务正在处理，请等待结束后删除。')
                if run['status'] == 'queued':
                    db.execute("UPDATE production_runs SET status='cancelled',message='已撤销并删除任务',updated_at=? WHERE id=?", (now(), ident))
            db.execute('INSERT INTO production_deleted_runs VALUES (?,?)', (ident, now()))

    def update_run(self, ident, **changes):
        allowed = {'status','stage','message','progress','error','error_kind','request_id','provider_task_id','result_url'}
        if not set(changes) <= allowed:
            raise ValueError('Invalid task fields')
        changes['updated_at'] = now()
        with self.connection() as db:
            db.execute('UPDATE production_runs SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',
                       (*changes.values(), ident))
        return self.get_run(ident)

    def claim_next(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM production_runs WHERE status='queued' AND (stage!='authorizing' OR updated_at<?) ORDER BY created_at LIMIT 1", ((datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(),)).fetchone()
            if not row:
                return None
            db.execute("UPDATE production_runs SET status='running',updated_at=? WHERE id=?", (now(),row['id']))
        return self.get_run(row['id'], private=True)

    def cancel_run(self, ident):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,provider_task_id,result_url FROM production_runs WHERE id=?',(ident,)).fetchone()
            if not row:
                raise LookupError('找不到任务。')
            if row['status'] != 'queued' or row['provider_task_id'] or row['result_url']:
                raise Conflict('只能撤销尚未开始的排队任务。')
            db.execute("UPDATE production_runs SET status='cancelled',message='已撤销排队',updated_at=? WHERE id=?",(now(),ident))
        return self.get_run(ident)

    def resume_run(self, ident):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM production_runs WHERE id=?',(ident,)).fetchone()
            if not row:
                raise LookupError('找不到任务。')
            if row['status'] != 'needs_attention' or not (row['provider_task_id'] or row['result_url']):
                raise Conflict('此任务无法直接恢复，请核对详情后复制为新草稿。')
            db.execute("UPDATE production_runs SET status='queued',error=NULL,message='等待恢复查询',updated_at=? WHERE id=?",(now(),ident))
        return self.get_run(ident)

    def recover(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT * FROM production_runs WHERE status='running'").fetchall():
                uncertain = row['stage']=='submitting' and not row['provider_task_id']
                db.execute('UPDATE production_runs SET status=?,error_kind=?,message=?,updated_at=? WHERE id=?',
                    ('needs_attention' if uncertain else 'queued', 'submission_uncertain' if uncertain else None,
                     '提交结果待确认，请核对服务商记录，避免重复提交' if uncertain else '服务已恢复，等待继续处理',now(),row['id']))
