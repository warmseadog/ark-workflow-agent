"""Durable local editing drafts and immutable production attempts."""
from __future__ import annotations
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager, nullcontext
from datetime import datetime, timezone, timedelta
from pathlib import Path
from threading import RLock
from .audio_policy import decorate_audio_failure
from .model_catalog import capabilities
from .queue_admission import queued_write, check_capacity, positive_setting
from . import run_phases

_connection_setup_lock = RLock()


def default_name(person=None):
    return datetime.fromisoformat(now()).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


def now():
    return datetime.now(timezone.utc).isoformat()


def unknown_timing():
    """No historical durations are inferred from mutable updated_at values."""
    return dict(available=False, started_at=None, finished_at=None, paused_at=None,
                queue_seconds=None, execution_seconds=None, paused_seconds=None,
                total_seconds=None, is_live=False, interrupted=False, phases=run_phases.unknown())


def _elapsed(start, end):
    return max(0.0, (datetime.fromisoformat(end)-datetime.fromisoformat(start)).total_seconds())


class Conflict(ValueError):
    pass


class ProductionStore:
    def __init__(self, storage, *, queue_root=None):
        self.storage = Path(storage)
        self.queue_root = Path(queue_root).resolve() if queue_root is not None else None
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
            CREATE TABLE IF NOT EXISTS production_person_preparations (
                run_id TEXT PRIMARY KEY, data TEXT NOT NULL, next_check REAL NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS production_variations (
                run_id TEXT PRIMARY KEY, group_key TEXT NOT NULL, data TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS variation_group ON production_variations(group_key);
            CREATE TABLE IF NOT EXISTS production_continuations (
                run_id TEXT PRIMARY KEY, data TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS production_run_timing (
                run_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
                started_at TEXT, finished_at TEXT, state TEXT NOT NULL, state_since TEXT NOT NULL,
                queue_seconds REAL NOT NULL DEFAULT 0, execution_seconds REAL DEFAULT 0,
                paused_seconds REAL NOT NULL DEFAULT 0, interrupted INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS production_run_phases (
                run_id TEXT PRIMARY KEY, data TEXT NOT NULL, phase TEXT,
                since TEXT NOT NULL, state TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS production_runs_order ON production_runs(created_at DESC,id DESC);
            CREATE INDEX IF NOT EXISTS production_runs_status ON production_runs(status);
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            # WAL is persistent. Serialize first-open transitions between local
            # request threads; SQLite can reject this PRAGMA before busy_timeout.
            with _connection_setup_lock:
                for attempt in range(5):
                    try:
                        if db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal':
                            db.execute('PRAGMA journal_mode=WAL').fetchone()
                        break
                    except sqlite3.OperationalError as exc:
                        if 'locked' not in str(exc).lower() or attempt == 4:
                            raise
                        time.sleep(0.05 * (attempt + 1))
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

    def run_timings(self, idents):
        """Return {id: timing}, including explicit unknowns for legacy/old rows.

        Seconds are wall-clock seconds, unrelated to video duration. Queue and
        execution accrue only in those states. Stopped states freeze; resuming
        includes the intervening wait in paused_seconds and total_seconds.
        An interrupted running interval has no trustworthy end, so execution
        stays unknown, even after recovery. The overall wall span remains known.
        """
        ids = list(dict.fromkeys(idents))
        result = {ident: unknown_timing() for ident in ids}
        stamp = now()
        with self.connection() as db:
            for offset in range(0, len(ids), 500):
                batch = ids[offset:offset+500]
                rows = db.execute('SELECT * FROM production_run_timing WHERE run_id IN ('+
                                  ','.join('?' for _ in batch)+')', batch).fetchall()
                for row in rows:
                    live = row['state'] in {'queued', 'running'}
                    end = stamp if live else row['state_since']
                    queue, execution = row['queue_seconds'], row['execution_seconds']
                    if row['state'] == 'queued':
                        queue += _elapsed(row['state_since'], end)
                    elif row['state'] == 'running' and execution is not None:
                        execution += _elapsed(row['state_since'], end)
                    result[row['run_id']] = dict(
                        available=True, started_at=row['started_at'], finished_at=row['finished_at'],
                        paused_at=row['state_since'] if row['state'] == 'needs_attention' else None,
                        queue_seconds=round(queue, 3),
                        execution_seconds=round(execution, 3) if execution is not None else None,
                        paused_seconds=round(row['paused_seconds'], 3),
                        total_seconds=round(_elapsed(row['created_at'], end), 3),
                        is_live=live, interrupted=bool(row['interrupted']))
            phases = run_phases.summaries(db, ids, stamp)
            for ident in ids:
                result[ident]['phases'] = phases[ident]
        return result

    def run_timing(self, ident):
        return self.run_timings([ident])[ident]

    @staticmethod
    def _transition_timing(db, ident, state, stamp, *, interrupted=False):
        # Call in the SAME write transaction as the state change. Old records
        # deliberately have no timing row, and are never silently backfilled.
        run_phases.state_transition(db, ident, state, stamp, interrupted=interrupted)
        row = db.execute('SELECT * FROM production_run_timing WHERE run_id=?', (ident,)).fetchone()
        if row is None or row['state'] == state:
            return
        queue, execution, paused = row['queue_seconds'], row['execution_seconds'], row['paused_seconds']
        delta = _elapsed(row['state_since'], stamp)
        if row['state'] == 'queued':
            queue += delta
        elif row['state'] == 'running':
            execution = None if interrupted or execution is None else execution + delta
        else:
            paused += delta
        started = row['started_at'] or (stamp if state == 'running' else None)
        finished = stamp if state in {'succeeded', 'failed', 'cancelled'} else None
        db.execute('''UPDATE production_run_timing SET started_at=?,finished_at=?,state=?,state_since=?,
                      queue_seconds=?,execution_seconds=?,paused_seconds=?,interrupted=? WHERE run_id=?''',
                   (started, finished, state, stamp, queue, execution, paused,
                    int(interrupted or row['interrupted']), ident))

    def create_draft(self, values, *, ident=None, connection=None):
        ident, stamp = ident or uuid.uuid4().hex, now()
        data = {'name': default_name(), 'source_clip': None, 'target_duration': None, 'source_asset_id': None, 'face_asset_ids': [],
                'person_reference_mode':'image','person_video_asset_id':None,
                'clothing_asset_ids': [], 'hairstyle_asset_ids': [], 'scene_asset_ids': [],
                **{kind+suffix: ([] if suffix=='_asset_ids' else False) for kind in ('bag','hat','watch','shoes','necklace','glasses','earrings') for suffix in ('_asset_ids','_enabled')},
                'hairstyle_mask': {'mask_scale':1.0,'threshold':0.2}, 'hairstyle_enabled': False, 'scene_enabled': False, 'scene_description': '', 'prompt': '', 'mask': {}, 'model': {}, **values}
        with (nullcontext(connection) if connection is not None else self.connection()) as db:
            db.execute('INSERT OR IGNORE INTO production_drafts VALUES (?,?,?,?,?)',
                       (ident, 1, json.dumps(data, ensure_ascii=False), stamp, stamp))
            return self._draft(db.execute('SELECT * FROM production_drafts WHERE id=?', (ident,)).fetchone())

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
        value['thumbnail_url'] = '/api/production/assets/' + ident + '/thumbnail'
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
        generation = json.loads(value['private']).get('generation', {})
        audio = value['snapshot'].get('model', {}).get('generate_audio', generation.get('generate_audio', True))
        decorate_audio_failure(value, enabled=audio, supported=capabilities(generation.get('model') or '', generation.get('protocol') or 'ark')['audio_control'])
        value['name'] = self.run_name(value['id'],value['snapshot'].get('name', '未命名视频'))
        continuation = self.get_continuation(value['id'])
        value['continuation'] = {k:v for k,v in continuation.items() if k != 'result_url'}
        value['variation'] = self.get_variation(value['id'])
        if value['variation']:value['can_retry_without_audio']=False
        has_remote = bool(value['provider_task_id'] or value['result_url'] or continuation.get('base_ready'))
        value['can_resume'] = value['status'] == 'needs_attention' and has_remote and value['error_kind'] != 'submission_uncertain'
        if value['status']=='needs_attention' and value['stage']=='variation_planning' and value['variation'] and not value['variation'].get('plan',{}).get('blocked'):
            value['can_resume']=True
        value['can_cancel'] = value['status'] == 'queued' and not has_remote
        value['can_delete'] = value['status'] != 'running' and (value['status'] != 'queued' or not has_remote)
        preparation = self.get_preparation(value['id'])
        value['person_preparation'] = self.public_preparation(preparation)
        value['can_retry_preparation'] = bool(preparation and preparation.get('retryable')
            and value['status'] in {'failed', 'needs_attention'} and not has_remote)
        if private:
            value['private'] = json.loads(value['private'])
        else:
            from .security import safe_payload, secret_values
            value = safe_payload(value, secret_values(json.loads(value['private'])))
            value.pop('private')
            value.pop('result_url')
            value.pop('idempotency_key')
        value['legacy'] = False
        value['timing'] = self.run_timing(value['id'])
        return value

    def get_run(self, ident, private=False):
        with self.connection() as db:
            return self._run(db.execute('SELECT * FROM production_runs WHERE id=?', (ident,)).fetchone(), private)

    def run_by_key(self, key):
        with self.connection() as db:
            row = db.execute('SELECT * FROM production_runs WHERE idempotency_key=?', (key,)).fetchone()
            return self._run(row) if row else None

    def check_queue_limit(self, db, limit):
        check_capacity(self, limit)
        if limit is not None and db.execute("SELECT COUNT(*) FROM production_runs WHERE status='queued'").fetchone()[0] >= limit:
            raise Conflict('当前账户排队任务已达上限，请等待任务开始或撤销排队任务。')

    @queued_write
    def create_run(self, draft_id, revision, key, private, max_queued=None):
        ident, stamp = uuid.uuid4().hex, now()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM production_runs WHERE idempotency_key=?', (key,)).fetchone()
            if row:
                if row['draft_id'] != draft_id or row['revision'] != revision:
                    raise Conflict('提交标识已用于另一份输入，请核对任务列表。')
                old=json.loads(row['private']).get('variation');new=private.get('variation')
                if bool(old)!=bool(new) or (old and old['inspiration']!=new['inspiration']):
                    raise Conflict('提交标识已用于不同拍法或灵感，请核对上次提交。')
                return self._run(row)
            self.check_queue_limit(db,max_queued)
            draft = self._draft(db.execute('SELECT * FROM production_drafts WHERE id=?', (draft_id,)).fetchone())
            if draft['revision'] != revision:
                raise Conflict('草稿发生变化，请保存后重新提交。')
            if private.get('variation'):
                from .variation_llm import RECIPES
                intent=private['variation']
                recent=[json.loads(row[0])['recipe'] for row in db.execute('SELECT data FROM production_variations WHERE group_key=? ORDER BY rowid DESC LIMIT 4',(intent['group_key'],))]
                recipe=next((r for r in RECIPES if r not in recent),RECIPES[0])
                state={'recipe':recipe,'recent_recipes':recent,'inspiration':intent['inspiration'],'skill_version':intent['skill_version'],'llm_model':intent['config']['model']}
                db.execute('INSERT INTO production_variations VALUES (?,?,?)',(ident,intent['group_key'],json.dumps(state,ensure_ascii=False)))
            db.execute('INSERT INTO production_runs\n                (id,draft_id,revision,idempotency_key,snapshot,private,status,stage,message,created_at,updated_at)\n                VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (ident,draft_id,revision,key,json.dumps(draft,ensure_ascii=False),json.dumps(private,ensure_ascii=False),
                 'queued','queued','等待处理',stamp,stamp))
            db.execute('INSERT INTO production_run_timing (run_id,created_at,state,state_since) VALUES (?,?,?,?)',
                       (ident, stamp, 'queued', stamp))
            run_phases.create(db, ident, stamp)
            if private.get('person_preparation'):
                intent = private['person_preparation']
                data = {'state': 'pending', 'account': intent['account'],
                    'input_digest': intent['input_digest'], 'person_id': None,
                    'group_request_id': None, 'uploads': {}, 'started_at': None,
                    'deadline_at': None, 'retryable': False, 'attempts': 0,
                    'message': '等待执行名额，随后检查人物素材', 'updated_at': stamp}
                db.execute('INSERT INTO production_person_preparations VALUES (?,?,0)',
                    (ident, json.dumps(data, ensure_ascii=False)))
        return self.get_run(ident)

    def get_variation(self, ident):
        with self.connection() as db:
            row=db.execute('SELECT data FROM production_variations WHERE run_id=?',(ident,)).fetchone()
        return json.loads(row['data']) if row else {}

    def update_variation(self, ident, **changes):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT data FROM production_variations WHERE run_id=?',(ident,)).fetchone()
            if row is None:raise LookupError('换拍法任务不存在。')
            state={**json.loads(row['data']),**changes}
            db.execute('UPDATE production_variations SET data=? WHERE run_id=?',(json.dumps(state,ensure_ascii=False),ident))
        return state

    def get_preparation(self, ident):
        with self.connection() as db:
            row = db.execute('SELECT * FROM production_person_preparations WHERE run_id=?', (ident,)).fetchone()
        return {**json.loads(row['data']), 'next_check': row['next_check']} if row else None

    @staticmethod
    def public_preparation(data):
        return {key: data.get(key) for key in ('state', 'person_id', 'message')} if data else None

    def update_preparation(self, ident, **changes):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run = db.execute('SELECT status FROM production_runs WHERE id=?', (ident,)).fetchone()
            if not run or run['status'] == 'cancelled':
                return False
            row = db.execute('SELECT * FROM production_person_preparations WHERE run_id=?', (ident,)).fetchone()
            if not row:
                raise LookupError('找不到人物准备记录。')
            next_check = changes.pop('next_check', row['next_check'])
            data = {**json.loads(row['data']), **changes, 'updated_at': now()}
            db.execute('UPDATE production_person_preparations SET data=?,next_check=? WHERE run_id=?',
                (json.dumps(data, ensure_ascii=False), next_check, ident))
        return True

    @queued_write
    def retry_preparation(self, ident, max_queued=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM production_deleted_runs WHERE id=?', (ident,)).fetchone():
                raise LookupError('任务已删除。')
            row = db.execute('SELECT * FROM production_runs WHERE id=?', (ident,)).fetchone()
            prep = db.execute('SELECT data FROM production_person_preparations WHERE run_id=?', (ident,)).fetchone()
            if not row:
                raise LookupError('找不到生成任务。')
            data = json.loads(prep['data']) if prep else {}
            if (row['status'] not in {'failed', 'needs_attention'} or row['provider_task_id']
                    or row['result_url'] or not data.get('retryable')):
                raise Conflict('此任务不能重新检查人物，请核对详情后复制为新草稿。')
            data.update(state='pending', retryable=False, attempts=0, started_at=None,
                deadline_at=None, message='等待重新检查人物', updated_at=now(), retry_requested=True)
            self.check_queue_limit(db,max_queued)
            db.execute('UPDATE production_person_preparations SET data=?,next_check=0 WHERE run_id=?',
                (json.dumps(data, ensure_ascii=False), ident))
            stamp = now()
            self._transition_timing(db, ident, 'queued', stamp)
            db.execute("UPDATE production_runs SET status='queued',stage='queued',error=NULL,error_kind=NULL,message='等待重新检查人物',updated_at=? WHERE id=?", (stamp, ident))
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

    def page_runs(self, page, page_size, legacy, status_filter=None):
        # Summaries are selected by SQLite before any snapshot/media decoration.
        fields=['id','name','status','stage','message','progress','created_at','updated_at','error_kind','duration','model','source_clip','target_duration','download_url','has_remote','legacy','generate_audio','protocol','request_id','_private']
        with self.connection() as db:
            db.execute('CREATE TEMP TABLE legacy_page (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
            db.executemany('INSERT INTO legacy_page VALUES (?,?)',[(x['id'],json.dumps({k:x.get(k) for k in fields})) for x in legacy])
            legacy_sql=','.join("json_extract(l.data,'$."+k+"') AS "+k for k in fields)
            sql="""WITH combined AS (
                SELECT id,json_extract(snapshot,'$.name') AS name,status,stage,message,progress,created_at,updated_at,
                CASE WHEN instr(COALESCE(error,''),'OutputAudioSensitiveContentDetected.PolicyViolation')>0 THEN 'audio_copyright' ELSE error_kind END AS error_kind,
                json_extract(snapshot,'$.model.duration') AS duration,json_extract(snapshot,'$.model.model') AS model,json_extract(snapshot,'$.source_clip') AS source_clip,json_extract(snapshot,'$.target_duration') AS target_duration,NULL AS download_url,
                (provider_task_id IS NOT NULL OR result_url IS NOT NULL) AS has_remote,0 AS legacy,
                COALESCE(json_extract(snapshot,'$.model.generate_audio'),json_extract(private,'$.generation.generate_audio'),1) AS generate_audio,
                COALESCE(json_extract(private,'$.generation.protocol'),'ark') AS protocol,request_id,private AS _private
                FROM production_runs
                UNION ALL SELECT """+legacy_sql+""" FROM legacy_page l
            ), visible AS (SELECT c.*,COALESCE(n.name,c.name) AS display_name FROM combined c
                LEFT JOIN production_run_names n ON c.id=n.id
                WHERE c.id NOT IN (SELECT id FROM production_deleted_runs)) """
            where = ' WHERE status=?' if status_filter else ''
            parameters = (status_filter,) if status_filter else ()
            count=db.execute(sql+"SELECT count(*) AS total,COALESCE(sum(status IN ('running','queued')),0) AS active FROM visible"+where, parameters).fetchone()
            pages=max(1,(count['total']+page_size-1)//page_size);page=min(page,pages)
            rows=db.execute(sql+'SELECT * FROM visible'+where+' ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?',parameters+(page_size,(page-1)*page_size)).fetchall()
        result=[]
        timings = self.run_timings(row['id'] for row in rows)
        for row in rows:
            item=dict(row);item['name']=item.pop('display_name');item['legacy']=bool(item['legacy']);remote=item.pop('has_remote')
            from .security import safe_payload, secret_values
            private=item.pop('_private')
            item=safe_payload(item,secret_values(json.loads(private)) if private else ())
            item['generate_audio'] = bool(item['generate_audio'])
            decorate_audio_failure(item, enabled=item['generate_audio'], supported=not item['legacy'] and capabilities(item.get('model') or '', item.pop('protocol') or 'ark')['audio_control'])
            item['source_clip'] = json.loads(item['source_clip']) if item.get('source_clip') else None
            if item['name']:
                import re
                item['name']=re.sub(r'\s*(?:[（(]副本[）)]|副本)$','',item['name']).rstrip()
            continuation = {} if item['legacy'] else self.get_continuation(item['id'])
            remote = remote or continuation.get('base_ready')
            item['can_resume']=not item['legacy'] and item['status']=='needs_attention' and bool(remote) and item['error_kind'] != 'submission_uncertain'
            variation={} if item['legacy'] else self.get_variation(item['id'])
            item['variation'] = variation
            if variation:item['can_retry_without_audio']=False
            if variation and item['status']=='needs_attention' and item['stage']=='variation_planning' and not variation.get('plan',{}).get('blocked'):
                item['can_resume']=True
            item['can_cancel']=not item['legacy'] and item['status']=='queued' and not remote
            item['can_delete']=item['status']!='running' and (item['status']!='queued' or (not item['legacy'] and not remote))
            preparation = None if item['legacy'] else self.get_preparation(item['id'])
            item['person_preparation'] = self.public_preparation(preparation)
            item['can_retry_preparation'] = bool(preparation and preparation.get('retryable')
                and item['status'] in {'failed', 'needs_attention'} and not remote)
            item['timing'] = timings[item['id']]
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
                    stamp = now()
                    self._transition_timing(db, ident, 'cancelled', stamp)
                    db.execute("UPDATE production_runs SET status='cancelled',message='已撤销并删除任务',updated_at=? WHERE id=?", (stamp, ident))
            db.execute('INSERT INTO production_deleted_runs VALUES (?,?)', (ident, now()))

    def get_continuation(self, ident):
        with self.connection() as db:
            row = db.execute('SELECT data FROM production_continuations WHERE run_id=?', (ident,)).fetchone()
            return json.loads(row['data']) if row else {}

    def update_continuation(self, ident, **changes):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT 1 FROM production_runs WHERE id=?', (ident,)).fetchone():
                raise LookupError('找不到生成任务。')
            row = db.execute('SELECT data FROM production_continuations WHERE run_id=?', (ident,)).fetchone()
            state = json.loads(row['data']) if row else {}
            state.update(changes)
            db.execute('INSERT OR REPLACE INTO production_continuations VALUES (?,?)', (ident,json.dumps(state,ensure_ascii=False)))
        return state

    def set_phase(self, ident, phase, *, cached=False):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            run_phases.transition(db, ident, phase, now(), cached=cached)

    def update_run(self, ident, **changes):
        allowed = {'status','stage','message','progress','error','error_kind','request_id','provider_task_id','result_url'}
        if not set(changes) <= allowed:
            raise ValueError('Invalid task fields')
        changes['updated_at'] = now()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM production_runs WHERE id=?', (ident,)).fetchone()
            if row and row['status'] != 'cancelled' and 'status' in changes:
                self._transition_timing(db, ident, changes['status'], changes['updated_at'])
            db.execute('UPDATE production_runs SET '+','.join(k+'=?' for k in changes)+" WHERE id=? AND status!='cancelled'",
                       (*changes.values(), ident))
        return self.get_run(ident)

    def claim_next(self):
        self.expire_queued()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("""SELECT r.* FROM production_runs r
                LEFT JOIN production_person_preparations p ON p.run_id=r.id
                WHERE r.status='queued' AND (r.stage!='authorizing' OR r.updated_at<?)
                AND r.id NOT IN (SELECT id FROM production_deleted_runs)
                AND (p.run_id IS NULL OR p.next_check<=?) ORDER BY r.created_at LIMIT 1""",
                ((datetime.now(timezone.utc)-timedelta(seconds=10)).isoformat(), time.time())).fetchone()
            if not row:
                return None
            stamp = now()
            self._transition_timing(db, row['id'], 'running', stamp)
            db.execute("UPDATE production_runs SET status='running',updated_at=? WHERE id=?", (stamp,row['id']))
        return self.get_run(row['id'], private=True)

    def cancel_run(self, ident):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,provider_task_id,result_url FROM production_runs WHERE id=?',(ident,)).fetchone()
            if not row:
                raise LookupError('找不到任务。')
            if row['status'] != 'queued' or row['provider_task_id'] or row['result_url']:
                raise Conflict('只能撤销尚未开始的排队任务。')
            stamp = now()
            self._transition_timing(db, ident, 'cancelled', stamp)
            db.execute("UPDATE production_runs SET status='cancelled',message='已撤销排队',updated_at=? WHERE id=?",(stamp,ident))
        return self.get_run(ident)

    @queued_write
    def resume_run(self, ident, max_queued=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM production_runs WHERE id=?',(ident,)).fetchone()
            if not row:
                raise LookupError('找不到任务。')
            state = db.execute('SELECT data FROM production_continuations WHERE run_id=?', (ident,)).fetchone()
            base_ready = bool(state and json.loads(state['data']).get('base_ready'))
            variation=db.execute('SELECT data FROM production_variations WHERE run_id=?',(ident,)).fetchone()
            variation_retry=bool(variation and row['stage']=='variation_planning' and not json.loads(variation['data']).get('plan',{}).get('blocked'))
            if row['status'] != 'needs_attention' or row['error_kind'] == 'submission_uncertain' or not (row['provider_task_id'] or row['result_url'] or base_ready or variation_retry):
                raise Conflict('此任务无法直接恢复，请核对详情后复制为新草稿。')
            self.check_queue_limit(db,max_queued)
            continuation = json.loads(state['data']) if state else {}
            if continuation.get('terminal_failure') or continuation.get('quality_rejected'):
                # Only an explicit user resume after a failure or rejected seam
                # may submit a fresh continuation; the base and plan are reused.
                history=continuation.setdefault('previous_task_ids',[])
                if continuation.get('provider_task_id'):
                    history.append(continuation['provider_task_id'])
                continuation.update(provider_task_id=None,result_url=None,terminal_failure=False,quality_rejected=False)
                db.execute('UPDATE production_continuations SET data=? WHERE run_id=?',(json.dumps(continuation,ensure_ascii=False),ident))
            stamp = now()
            self._transition_timing(db, ident, 'queued', stamp)
            db.execute("UPDATE production_runs SET status='queued',error=NULL,message='等待恢复查询',updated_at=? WHERE id=?",(stamp,ident))
        return self.get_run(ident)

    def expire_queued(self):
        cutoff = (datetime.fromisoformat(now())-timedelta(
            seconds=positive_setting('APP_QUEUE_TIMEOUT_SECONDS',600))).isoformat()
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("""SELECT r.*,c.data AS continuation_data FROM production_runs r
                LEFT JOIN production_run_timing t ON t.run_id=r.id
                LEFT JOIN production_continuations c ON c.run_id=r.id
                WHERE r.status='queued' AND COALESCE(t.state_since,r.created_at)<?
                AND COALESCE(r.provider_task_id,'')='' AND COALESCE(r.result_url,'')=''
                AND r.stage NOT IN ('submitting','continuation_submitting')""",(cutoff,)).fetchall()
            expired = 0
            for row in rows:
                continuation = json.loads(row['continuation_data'] or '{}')
                if continuation.get('base_ready') or continuation.get('provider_task_id') or continuation.get('result_url'):
                    continue
                stamp = now()
                self._transition_timing(db,row['id'],'failed',stamp)
                db.execute("UPDATE production_runs SET status='failed',error_kind='queue_timeout',error=?,message=?,updated_at=? WHERE id=?",
                    ('排队等待超时，请重新提交。','排队等待超时，请重新提交。',stamp,row['id']))
                expired += 1
            return expired

    def recover(self):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in db.execute("SELECT * FROM production_runs WHERE status='running'").fetchall():
                uncertain = row['stage']=='submitting' and not row['provider_task_id']
                if row['stage']=='continuation_submitting':
                    state = db.execute('SELECT data FROM production_continuations WHERE run_id=?', (row['id'],)).fetchone()
                    uncertain = not (state and json.loads(state['data']).get('provider_task_id'))
                stamp = now()
                state = 'needs_attention' if uncertain else 'queued'
                self._transition_timing(db, row['id'], state, stamp, interrupted=True)
                db.execute('UPDATE production_runs SET status=?,error_kind=?,message=?,updated_at=? WHERE id=?',
                    (state, 'submission_uncertain' if uncertain else None,
                     '提交结果待确认，请核对服务商记录，避免重复提交' if uncertain else '服务已恢复，等待继续处理',stamp,row['id']))
