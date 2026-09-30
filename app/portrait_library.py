"""Durable people and deduplicated official photo ingestion; one bounded worker."""
from __future__ import annotations
import base64
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import threading
import time
import uuid
import warnings
from . import portrait_service as service, storage_settings
from .production_store import ProductionStore, Conflict

_process_lock = threading.Lock()


def validate_photo(settings, asset, *, require_upload_dimensions=True):
    if asset['kind']=='person_video':
        from .person_video import validate_asset
        return validate_asset(settings,asset)
    from PIL import Image
    path = Path(asset['path']).resolve()
    if asset['kind'] != 'face' or not path.is_relative_to((settings.storage_dir/'assets').resolve()) or not path.is_file():
        raise ValueError('请选择已上传的人物照片。')
    if not 0 < path.stat().st_size <= 20*1024*1024:
        raise ValueError('人物照片需小于 20 MB。')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(path) as image:
                if image.format not in {'PNG','JPEG','WEBP'} or getattr(image,'n_frames',1) != 1:
                    raise ValueError('请使用静态 PNG、JPEG 或 WebP 人物照片。')
                if require_upload_dimensions and not (300 < image.width < 6000 and 300 < image.height < 6000 and .4 < image.width/image.height < 2.5):
                    raise ValueError('人物照片宽高需大于 300、小于 6000 像素，宽高比在 0.4–2.5 之间。')
                image.verify()
    except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError('图片无法读取或尺寸过大，请更换清晰的正面照片。') from None
    if hashlib.sha256(path.read_bytes()).hexdigest() != asset['sha256']:
        raise ValueError('照片文件发生变化，请重新上传。')
    return path


def upload_photo(settings, asset, key):
    path = validate_photo(settings, asset)
    config = storage_settings.load_config(settings)
    if not config.ready: raise ValueError('请在后台启用并配置 TOS，以便提交人物照片校验。')
    try:
        import tos
        client = storage_settings.make_client(config)
        try:
            object_key = config.prefix + 'portraits/' + key + path.suffix.lower()
            client.put_object_from_file(config.bucket, object_key, str(path), content_type=asset['mime'], acl=tos.ACLType.ACL_Private)
            return client.pre_signed_url(tos.HttpMethodType.Http_Method_Get, config.bucket, object_key,
                                         expires=config.expires_seconds).signed_url
        finally: client.close()
    except Exception:
        raise ValueError('人物照片上传失败，请检查 TOS 配置和网络后重试。') from None


class PortraitLibrary:
    def __init__(self, settings):
        self.settings = settings
        self.store = ProductionStore(settings.storage_dir)
        self.config = service.load_config(settings)
        self.account = service.fingerprint(self.config)
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS portrait_people (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, group_id TEXT NOT NULL,
                    name TEXT NOT NULL, created REAL NOT NULL, UNIQUE(account,group_id));
                CREATE TABLE IF NOT EXISTS portrait_hidden_people (
                    person_id TEXT PRIMARY KEY, account TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS portrait_photos (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, person_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
                    remote_id TEXT, message TEXT NOT NULL, next_check REAL NOT NULL,
                    created REAL NOT NULL, checked REAL NOT NULL DEFAULT 0,
                    UNIQUE(account,person_id,sha256));
                CREATE INDEX IF NOT EXISTS portrait_photos_pending ON portrait_photos(status,next_check);
                CREATE TABLE IF NOT EXISTS portrait_photo_errors (
                    photo_id TEXT PRIMARY KEY, retryable INTEGER NOT NULL);
            """)

            db.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in db.execute('PRAGMA table_info(portrait_people)')}
            if 'person_type' not in columns:
                db.execute("ALTER TABLE portrait_people ADD COLUMN person_type TEXT NOT NULL DEFAULT 'LivenessFace'")
            db.execute("""CREATE TABLE IF NOT EXISTS portrait_group_requests (
                account TEXT NOT NULL, request_id TEXT NOT NULL, name TEXT NOT NULL,
                status TEXT NOT NULL, person_id TEXT, message TEXT NOT NULL,
                PRIMARY KEY(account,request_id))""")
            request_columns = {row[1] for row in db.execute('PRAGMA table_info(portrait_group_requests)')}
            for column in ('remote_group_id', 'auto_kind', 'auto_sha256'):
                if column not in request_columns:
                    db.execute(f'ALTER TABLE portrait_group_requests ADD COLUMN {column} TEXT')

    def api(self, person_type, asset_type='Image'):
        if asset_type=='Video': return service.ArkPortraitClient(self.config,person_type=person_type,asset_type='Video')
        return (service.ArkPortraitClient(self.config) if person_type == 'LivenessFace'
                else service.ArkPortraitClient(self.config, person_type=person_type))

    def create_virtual(self, name, request_id):
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 60 or any(ord(c)<32 for c in name):
            raise ValueError('虚拟人物名称请输入 1–60 个字符。')
        if not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9-]{8,100}',request_id):
            raise ValueError('请提供有效的创建请求编号。')
        if not self.config.ready: raise ValueError(self.config.problem())
        name=name.strip()
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT * FROM portrait_group_requests WHERE account=? AND request_id=?',(self.account,request_id)).fetchone()
            if not old:
                old=db.execute("SELECT * FROM portrait_group_requests WHERE account=? AND name=? AND status IN ('submitting','uncertain')",(self.account,name)).fetchone()
            if old:
                if old['name']!=name: raise Conflict('该创建请求已用于其他人物。')
                return self.group_request(dict(old))
            db.execute('INSERT INTO portrait_group_requests (account,request_id,name,status,person_id,message) VALUES (?,?,?,?,?,?)',
                       (self.account,request_id,name,'submitting',None,'正在创建官方虚拟素材组'))
        try:
            group=self.api('AIGC').create_group(name)
            person=self.add_person(group['Id'],name,'AIGC')
            status,message,person_id='ready','虚拟人物已创建；请上传照片并等待官方检查',person['id']
        except Exception as exc:
            status,person_id='uncertain',None
            message='创建结果待确认，暂不重复提交。请同步官方虚拟库或在控制台核实。'
            if isinstance(exc,service.PortraitError): message += ' '+str(exc)
        with self.store.connection() as db:
            db.execute('UPDATE portrait_group_requests SET status=?,person_id=?,message=? WHERE account=? AND request_id=?',
                       (status,person_id,message,self.account,request_id))
        return {'request_id':request_id,'status':status,'person_id':person_id,'message':message}

    def _auto_assets(self, asset_ids):
        if not isinstance(asset_ids, list) or not asset_ids:
            raise ValueError('请先上传虚拟人物参考素材。')
        assets = []
        for ident in asset_ids:
            if not isinstance(ident, str) or not ident:
                raise ValueError('人物参考素材编号不正确。')
            asset = self.store.get_asset(ident, private=True)
            if asset['kind'] not in {'face', 'person_video'} or not re.fullmatch(r'[a-f0-9]{64}', asset['sha256']):
                raise ValueError('请选择有效的人物照片或人物视频。')
            assets.append(asset)
        return assets

    def resolve_virtual_assets(self, asset_ids):
        """Read exact content associations only; never infer a face or query cloud."""
        assets = self._auto_assets(asset_ids)
        people = set()
        with self.store.connection() as db:
            for asset in assets:
                rows = db.execute("""
                    SELECT pe.id,pe.person_type,h.person_id AS hidden
                    FROM portrait_photos ph
                    JOIN production_assets a ON a.id=ph.asset_id
                    LEFT JOIN portrait_people pe ON pe.id=ph.person_id AND pe.account=ph.account
                    LEFT JOIN portrait_hidden_people h ON h.person_id=pe.id AND h.account=pe.account
                    WHERE ph.account=? AND ph.sha256=? AND a.kind=?
                    AND NOT (ph.status='failed' AND ph.checked=0 AND ph.asset_id<>?
                             AND COALESCE(pe.person_type,'')='LivenessFace')
                    UNION ALL
                    SELECT pe.id,pe.person_type,h.person_id AS hidden
                    FROM production_portraits p
                    JOIN production_assets a ON a.id=p.asset_id
                    LEFT JOIN portrait_people pe ON pe.group_id=p.group_id AND pe.account=p.fingerprint
                    LEFT JOIN portrait_hidden_people h ON h.person_id=pe.id AND h.account=pe.account
                    WHERE p.fingerprint=? AND a.sha256=? AND a.kind=?
                    """, (self.account, asset['sha256'], asset['kind'], asset['id'],
                          self.account, asset['sha256'], asset['kind'])).fetchall()
                for row in rows:
                    if not row['id']:
                        raise ValueError('人物素材已有官方归属，请从人物库选择并核实所属人物。')
                    if row['person_type'] != 'AIGC':
                        raise ValueError('素材已关联真人，请从人物库选择已有真人，不能作为虚拟人物自动入库。')
                    if row['hidden']:
                        raise ValueError('素材关联的人物已移除，请先在人物库中确认或恢复。')
                    people.add(row['id'])
        if len(people) > 1:
            raise ValueError('参考素材关联了不同人物，请从人物库明确选择同一人物的素材。')
        return self.person(next(iter(people)), private=True) if people else None

    def _visible_virtual(self, person_id):
        person = self.person(person_id, private=True)
        if person['person_type'] != 'AIGC':
            raise ValueError('该人物属于真人，请从人物库选择。')
        with self.store.connection() as db:
            hidden = db.execute('SELECT 1 FROM portrait_hidden_people WHERE person_id=? AND account=?',
                                (person_id, self.account)).fetchone()
        if hidden:
            raise ValueError('素材关联的人物已移除，请先在人物库中确认或恢复。')
        return person

    def ensure_auto_virtual(self, asset_ids):
        """Create once, then reconcile the same durable request after uncertainty."""
        scope = ['auto-virtual-v1']
        root = getattr(self.settings, 'config_root', None)
        owner = getattr(self.settings, 'user_id', '')
        try:
            if not isinstance(owner, str) or (owner and not re.fullmatch(r'[a-f0-9]{32}', owner)):
                raise ValueError('Invalid tenant ID')
            storage = Path(self.settings.storage_dir).resolve()
            if root is None:
                # Pre-auth callers have neither a root nor an owner. A partial
                # tenant context must never silently use the legacy namespace.
                if owner:
                    raise ValueError('Missing tenant root')
            else:
                if not isinstance(root, (str, Path)) or not str(root).strip():
                    raise ValueError('Invalid tenant root')
                root = Path(root).resolve()
                if storage != root:
                    if (not owner or not storage.is_relative_to(root)
                            or storage != (root / 'users' / owner).resolve()):
                        raise ValueError('Mismatched tenant storage')
                    # Stable across storage moves; never recover a new tenant
                    # using the old account/content-only name or request ID.
                    scope = ['auto-virtual-tenant-v1', owner]
                # The initial admin keeps root storage AND its old namespace,
                # even after authentication adds a non-empty user_id.
        except (TypeError, ValueError, OSError, RuntimeError):
            raise ValueError('租户上下文无效，无法准备虚拟人物。') from None
        if not self.config.ready:
            raise ValueError(self.config.problem())
        primary = self._auto_assets(asset_ids)[0]
        resolved = self.resolve_virtual_assets(asset_ids)
        digest = hashlib.sha256(json.dumps(
            scope + [self.account, primary['kind'], primary['sha256']],
            separators=(',', ':')).encode()).digest()
        request_id = 'auto-' + digest.hex()
        # Base32 retains the full digest while respecting the 60-character limit.
        cloud_name = 'auto-v-' + base64.b32encode(digest).decode().rstrip('=').lower()
        message = '正在准备虚拟人物'
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM portrait_group_requests WHERE account=? AND request_id=?',
                             (self.account, request_id)).fetchone()
            fresh = old is None
            if fresh:
                db.execute('''INSERT INTO portrait_group_requests
                    (account,request_id,name,status,person_id,message,remote_group_id,auto_kind,auto_sha256)
                    VALUES (?,?,?,?,?,?,?,?,?)''',
                    (self.account, request_id, cloud_name, 'ready' if resolved else 'submitting',
                     resolved['id'] if resolved else None, message,
                     resolved['group_id'] if resolved else None, primary['kind'], primary['sha256']))
                old = db.execute('SELECT * FROM portrait_group_requests WHERE account=? AND request_id=?',
                                 (self.account, request_id)).fetchone()
            row = dict(old)
        if (row['name'] != cloud_name or row['auto_kind'] != primary['kind']
                or row['auto_sha256'] != primary['sha256']):
            raise Conflict('自动人物请求归属不一致，请重新核实人物素材。')
        if row['status'] == 'ready':
            person = self._visible_virtual(row['person_id'])
            if resolved and person['id'] != resolved['id']:
                raise ValueError('参考素材关联了不同人物，请重新选择。')
            return self.group_request(row)

        def checkpoint(group_id):
            with self.store.connection() as db:
                db.execute('BEGIN IMMEDIATE')
                changed = db.execute('''UPDATE portrait_group_requests SET remote_group_id=?
                    WHERE account=? AND request_id=? AND (remote_group_id IS NULL OR remote_group_id=?)''',
                    (group_id, self.account, request_id, group_id)).rowcount
                if not changed:
                    raise Conflict('自动人物请求出现不同官方组，请核实后再使用。')

        try:
            api = self.api('AIGC')
            if fresh:
                group = api.create_group(cloud_name, on_created=checkpoint)
            elif row['remote_group_id']:
                group = api.get_group(row['remote_group_id'])
            else:
                # list_groups returns only after complete bounded pagination. A
                # partial/error/ambiguous listing must never authorize a create.
                matches = {g['Id']: g for g in api.list_groups() if g.get('Name') == cloud_name}
                if len(matches) != 1:
                    raise service.PortraitError('未找到唯一可确认的官方人物组，请稍后重新检查。')
                group_id = next(iter(matches))
                checkpoint(group_id)
                group = api.get_group(group_id)
            person = self.add_person(group['Id'], '虚拟人物 ' + digest.hex()[:8], 'AIGC')
            person = self._visible_virtual(person['id'])
            latest = self.resolve_virtual_assets(asset_ids)
            if latest and latest['id'] != person['id']:
                raise ValueError('参考素材关联了不同人物，请重新选择。')
            with self.store.connection() as db:
                db.execute('''UPDATE portrait_group_requests SET status='ready',person_id=?,message=?
                    WHERE account=? AND request_id=?''',
                    (person['id'], '虚拟人物已准备，正在检查参考素材', self.account, request_id))
        except Exception as exc:
            message = '人物入库结果待确认，暂不重复创建；请重新检查原请求。'
            if isinstance(exc, ValueError):
                message += ' ' + str(exc)
            with self.store.connection() as db:
                # A slower failed observer must not overwrite a completed peer.
                db.execute('''UPDATE portrait_group_requests SET status='uncertain',message=?
                    WHERE account=? AND request_id=? AND status!='ready' ''',
                    (message, self.account, request_id))
        with self.store.connection() as db:
            result = dict(db.execute('SELECT * FROM portrait_group_requests WHERE account=? AND request_id=?',
                                     (self.account, request_id)).fetchone())
        if result['status'] == 'ready':
            self._visible_virtual(result['person_id'])
        return self.group_request(result)

    def group_request(self, row):
        if row['status']=='submitting': row['message']='创建结果待确认，暂不重复提交；请同步官方虚拟库或核实控制台。'
        return {k:('uncertain' if k=='status' and row[k]=='submitting' else row[k])
                for k in ('request_id','status','person_id','message')}

    def virtual_requests(self):
        with self.store.connection() as db:
            rows=db.execute('SELECT * FROM portrait_group_requests WHERE account=?',(self.account,)).fetchall()
        return [self.group_request(dict(row)) for row in rows]

    def virtual_status(self):
        with self.store.connection() as db:
            people=db.execute("SELECT id FROM portrait_people WHERE account=? AND person_type='AIGC' AND id NOT IN (SELECT person_id FROM portrait_hidden_people WHERE account=?)",(self.account,self.account)).fetchall()
            counts=db.execute("SELECT ph.status,count(*) AS count FROM portrait_photos ph JOIN portrait_people pe ON pe.id=ph.person_id WHERE ph.account=? AND pe.person_type='AIGC' AND pe.id NOT IN (SELECT person_id FROM portrait_hidden_people) GROUP BY ph.status",(self.account,)).fetchall()
            failures=db.execute("SELECT pe.name,ph.message,ph.created FROM portrait_photos ph JOIN portrait_people pe ON pe.id=ph.person_id WHERE ph.account=? AND pe.person_type='AIGC' AND pe.id NOT IN (SELECT person_id FROM portrait_hidden_people) AND ph.status IN ('failed','uncertain') ORDER BY ph.created DESC LIMIT 5",(self.account,)).fetchall()
            runs=db.execute("""SELECT r.snapshot,r.updated_at,
                COALESCE(json_extract(r.snapshot,'$.person_id'),json_extract(p.data,'$.person_id')) AS resolved_person_id
                FROM production_runs r LEFT JOIN production_person_preparations p ON p.run_id=r.id
                WHERE r.status='succeeded' AND COALESCE(json_extract(r.snapshot,'$.model.mode'),'')!='mock'
                ORDER BY r.updated_at DESC""").fetchall()
        ids={row['id'] for row in people}
        last=None
        for run in runs:
            snapshot=json.loads(run['snapshot'])
            if run['resolved_person_id'] in ids:
                last={'time':run['updated_at'],'model':snapshot.get('model',{}).get('model',''),
                      'mode':snapshot.get('model',{}).get('mode','')}
                break
        return {'people_count':len(ids),'photo_counts':{row['status']:row['count'] for row in counts},
                'recent_failures':[dict(row) for row in failures],'last_success':last}

    def add_person(self, group_id, name='', person_type='LivenessFace'):
        if person_type not in {'LivenessFace','AIGC'}: raise ValueError('不支持的人物类型。')
        if not service._identifier(group_id, 'group'): raise ValueError('人物编号不正确。')
        ident = hashlib.sha256((self.account+group_id).encode()).hexdigest()[:32]
        with self.store.connection() as db:
            count = db.execute('SELECT count(*) FROM portrait_people WHERE account=?',(self.account,)).fetchone()[0]
            db.execute('INSERT OR IGNORE INTO portrait_people (id,account,group_id,name,created,person_type) VALUES (?,?,?,?,?,?)',
                       (ident,self.account,group_id,name[:60].strip() or f'人物 {count+1}',time.time(),person_type))
            old=db.execute('SELECT person_type FROM portrait_people WHERE id=?',(ident,)).fetchone()
            if old['person_type']!=person_type: raise ValueError('人物类型不一致，请重新同步官方素材。')
        return self.person(ident)

    def import_verified(self):
        path = self.settings.storage_dir/'private'/'portrait-sessions.db'
        if path.is_file():
            with sqlite3.connect(path) as db:
                rows = db.execute('SELECT data FROM sessions WHERE account=?',(self.account,)).fetchall()
            for row in rows:
                data = json.loads(row[0])
                if data.get('status') == 'verified' and data.get('group_id'):
                    self.add_person(data['group_id'])
        with self.store.connection() as db:
            rows = db.execute('SELECT DISTINCT group_id FROM production_portraits WHERE fingerprint=?',(self.account,)).fetchall()
        for row in rows:
            with self.store.connection() as db:
                existing=db.execute('SELECT id FROM portrait_people WHERE account=? AND group_id=?',(self.account,row['group_id'])).fetchone()
            if not existing: self.add_person(row['group_id'])

    def person(self, ident, private=False):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM portrait_people WHERE id=? AND account=?',(ident,self.account)).fetchone()
            if not row: raise LookupError('所选人物不可用，请重新选择或认证。')
            value = dict(row)
            value['photo_counts'] = {r['status']: r['count'] for r in db.execute('SELECT status,count(*) AS count FROM portrait_photos WHERE person_id=? AND account=? GROUP BY status',(ident,self.account))}
            counts = dict(db.execute("SELECT a.kind,count(*) FROM portrait_photos ph JOIN production_assets a ON a.id=ph.asset_id WHERE ph.person_id=? AND ph.status='active' GROUP BY a.kind",(ident,)).fetchall())
            value['photo_count'] = counts.get('face',0)
            value['video_count'] = counts.get('person_video',0)
        if not private: value.pop('account'); value.pop('group_id')
        value['verified'] = value['person_type']=='LivenessFace'
        value['usable'] = value['photo_count']+value['video_count']>0
        with self.store.connection() as db:
            value['generated_count']=db.execute("""SELECT count(*) FROM production_runs r
                LEFT JOIN production_person_preparations p ON p.run_id=r.id
                WHERE r.status='succeeded' AND COALESCE(json_extract(r.snapshot,'$.model.mode'),'')!='mock'
                AND COALESCE(json_extract(r.snapshot,'$.person_id'),json_extract(p.data,'$.person_id'))=?""",(ident,)).fetchone()[0]
        thumb = self.settings.storage_dir/'portrait-thumbs'/(ident+'.jpg')
        value['thumbnail_url'] = '/api/portrait/people/'+ident+'/thumbnail' if thumb.is_file() else None
        return value

    def people(self, removed=False):
        if not self.config.ready: return []
        self.import_verified()
        with self.store.connection() as db:
            membership = 'IN' if removed else 'NOT IN'
            ids = [x[0] for x in db.execute(f'SELECT id FROM portrait_people WHERE account=? AND id {membership} (SELECT person_id FROM portrait_hidden_people WHERE account=?) ORDER BY created',(self.account,self.account))]
        return [self.person(ident) for ident in ids]

    def set_removed(self, ident, removed):
        person = self.person(ident)
        with self.store.connection() as db:
            if removed:
                db.execute('INSERT OR IGNORE INTO portrait_hidden_people VALUES (?,?)',(ident,self.account))
            else:
                db.execute('DELETE FROM portrait_hidden_people WHERE person_id=? AND account=?',(ident,self.account))
        return {'id':ident,'removed':removed}

    def photos_for_person(self, ident):
        self.person(ident)
        with self.store.connection() as db:
            rows = db.execute("""SELECT ph.id,ph.asset_id,a.name,a.kind,ph.status,ph.message,ph.remote_id
                FROM portrait_photos ph JOIN production_assets a ON a.id=ph.asset_id
                WHERE ph.person_id=? AND ph.account=? ORDER BY ph.created DESC,ph.id""",
                (ident,self.account)).fetchall()
        return [{'id':row['id'],'asset_id':row['asset_id'],'name':row['name'],'kind':row['kind'],
                 'url':'/api/production/assets/'+row['asset_id']+'/file',
                 'status':row['status'],'message':row['message'],
                 'remote_asset_id':row['remote_id'] if row['status']=='active' else None}
                for row in rows]

    def use_video(self, ident):
        job=self.get_photo(ident,private=True)
        if job['status']!='active' or not job['remote_id']:
            raise ValueError('人物视频仍未通过官方检查，请稍后刷新。')
        person=self.person(job['person_id'],private=True)
        asset=self.store.get_asset(job['asset_id'],private=True)
        if asset['kind']!='person_video': raise ValueError('请选择人物视频。')
        validate_photo(self.settings,asset)
        remote=self.api(person['person_type'],'Video').get_asset(job['remote_id'])
        if remote['group_id']!=person['group_id']:
            raise ValueError('视频与所选人物不匹配，请重新选择。')
        self.store.bind_portrait(asset['id'],remote,self.account)
        return {**self.store.get_asset(asset['id']),'person_type':person['person_type']}

    def reference(self, ident):
        person = self.person(ident)
        with self.store.connection() as db:
            photo = db.execute("SELECT ph.remote_id FROM portrait_photos ph JOIN production_assets a ON a.id=ph.asset_id WHERE ph.person_id=? AND ph.account=? AND ph.status='active' AND ph.remote_id IS NOT NULL AND a.kind='face' ORDER BY ph.checked DESC,ph.created DESC LIMIT 1",(ident,self.account)).fetchone()
        if not photo:
            raise ValueError('还没有可用照片，请先添加照片并等待检查通过。')
        return {'remote_asset_id':photo['remote_id']}

    def rename(self, ident, name):
        self.person(ident)
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 60 or any(ord(c)<32 for c in name):
            raise ValueError('人物备注请输入 1–60 个字符。')
        with self.store.connection() as db:
            db.execute('UPDATE portrait_people SET name=? WHERE id=? AND account=?',(name.strip(),ident,self.account))
        return self.person(ident)

    def get_photo(self, ident, private=False):
        with self.store.connection() as db:
            row = db.execute('''SELECT ph.*,COALESCE(e.retryable,0) AS retryable
                FROM portrait_photos ph LEFT JOIN portrait_photo_errors e ON e.photo_id=ph.id
                WHERE ph.id=? AND ph.account=?''',(ident,self.account)).fetchone()
        if not row: raise LookupError('照片校验记录不可用，请重新选择人物和照片。')
        result = dict(row)
        result['retryable'] = bool(result['retryable'])
        if not private:
            result = {k:result[k] for k in ('id','person_id','asset_id','status','message')}
        return result

    def update(self, ident, **values):
        retryable = values.pop('retryable', None)
        assert set(values) <= {'status','remote_id','message','next_check','checked'}
        with self.store.connection() as db:
            db.execute('UPDATE portrait_photos SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),ident))
            if retryable is not None:
                db.execute('INSERT OR REPLACE INTO portrait_photo_errors VALUES (?,?)', (ident, int(bool(retryable))))
            elif values.get('status') and values['status'] != 'failed':
                db.execute('DELETE FROM portrait_photo_errors WHERE photo_id=?', (ident,))

    def record_verified_import(self, person_id, asset_id, remote_id):
        """Reconcile a freshly verified official import with any existing upload job.

        The worker owns this lock across provider I/O. Wait outside DB transactions
        so its final write cannot overwrite the verified import or deadlock us.
        """
        if not service._identifier(remote_id,'asset'): raise ValueError('官方素材 ID 格式不正确。')
        with _process_lock:
            self.person(person_id)
            asset=self.store.get_asset(asset_id,private=True)
            # Official imports were already checked remotely and can have sizes
            # outside upload recommendations; still verify local bytes and format.
            validate_photo(self.settings,asset,require_upload_dimensions=False)
            checked=time.time()
            with self.store.connection() as db:
                db.execute("""INSERT INTO portrait_photos VALUES (?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(account,person_id,sha256) DO UPDATE SET
                    status='active',remote_id=excluded.remote_id,message=excluded.message,
                    next_check=0,checked=excluded.checked""",
                    (uuid.uuid4().hex,self.account,person_id,asset_id,asset['sha256'],'active',
                     remote_id,'官方照片可用',0,checked,checked))
                db.execute('''DELETE FROM portrait_photo_errors WHERE photo_id IN
                    (SELECT id FROM portrait_photos WHERE account=? AND person_id=? AND sha256=?)''',
                    (self.account, person_id, asset['sha256']))
            self.thumbnail({'asset_id':asset_id,'person_id':person_id})

    def enqueue(self, person_id, asset_id):
        person = self.person(person_id,private=True)
        asset = self.store.get_asset(asset_id,private=True)
        validate_photo(self.settings,asset)
        with self.store.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT id FROM portrait_photos WHERE account=? AND person_id=? AND sha256=?',
                             (self.account,person_id,asset['sha256'])).fetchone()
            if old: ident=old['id']
            else:
                count = db.execute("SELECT count(*) FROM portrait_photos WHERE status IN ('queued','uploading','submitting','processing','uncertain')").fetchone()[0]
                if count >= 50: raise Conflict('照片校验队列已满，请稍后上传。')
                ident = uuid.uuid4().hex
                binding = self.store.portrait_binding(asset_id)
                reusable = binding and binding['fingerprint']==self.account and binding['group_id']==person['group_id']
                db.execute('INSERT INTO portrait_photos VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                           (ident,self.account,person_id,asset_id,asset['sha256'],'processing' if reusable else 'queued',
                            binding['remote_asset_id'] if reusable else None,'等待校验人物照片',0,time.time(),0))
        return self.get_photo(ident)

    def retry(self, ident):
        job=self.get_photo(ident,private=True)
        if job['status'] not in {'failed','uncertain'}: return self.get_photo(ident)
        # Never repeat CreateAsset after an ambiguous response. Existing remote IDs are polled again.
        state='processing' if job['remote_id'] else ('uncertain' if job['status']=='uncertain' else 'queued')
        self.update(ident,status=state,next_check=0,message='等待重新核实照片状态')
        return self.get_photo(ident)

    def recover(self):
        with self.store.connection() as db:
            db.execute("UPDATE portrait_photos SET status='queued',next_check=0 WHERE status='uploading'")
            db.execute("UPDATE portrait_photos SET status='uncertain',next_check=0 WHERE status='submitting'")

    def process_one(self):
        if not _process_lock.acquire(blocking=False): return
        try:
            with self.store.connection() as db:
                row = db.execute("SELECT * FROM portrait_photos WHERE account=? AND status IN ('queued','processing','uncertain') AND next_check<=? ORDER BY next_check,created LIMIT 1",(self.account,time.time())).fetchone()
            if not row: return
            job=dict(row); ident=job['id']
            person=self.person(job['person_id'],private=True)
            asset=self.store.get_asset(job['asset_id'],private=True)
            api=self.api(person['person_type'],'Video' if asset['kind']=='person_video' else 'Image')
            group=person['group_id']
            try:
                if job['status']=='queued':
                    self.update(ident,status='uploading',message='正在上传人物照片')
                    api.get_group(group)
                    asset=self.store.get_asset(job['asset_id'],private=True)
                    url=upload_photo(self.settings,asset,ident)
                    self.update(ident,status='submitting',message='正在提交官方照片校验')
                    remote=api.create_asset(group,url,'portrait-'+ident)
                    self.update(ident,status='processing',remote_id=remote,next_check=time.time()+5,message='正在核对是否为所选人物')
                elif job['status']=='uncertain':
                    remote=api.find_created_asset(group,'portrait-'+ident)
                    if remote:
                        self.update(ident,status='processing',remote_id=remote,next_check=0,message='已找回照片记录，正在核实')
                    else:
                        self.update(ident,next_check=time.time()+60,message='提交结果待确认，暂不重复上传；可在官方控制台核对')
                else:
                    result=api.asset_state(job['remote_id'],group)
                    status=result['status']
                    if status=='Active':
                        api.get_group(group)
                        self.update(ident,status='active',checked=time.time(),message='照片可用')
                        self.thumbnail(job)
                    elif status=='Failed':
                        messages={'FaceMismatch':'照片与所选人物不一致，请切换人物或更换清晰正面照片。',
                                  'ContentRestricted':'照片未通过官方内容检查，请更换照片。',
                                  'DownloadFailed':'官方读取照片失败，请检查 TOS 后更换照片重试。'}
                        self.update(ident,status='failed',retryable=False,message=messages.get(result.get('error_code'),'照片未通过官方检查，请更换照片或到官方控制台查看。'))
                    else:
                        self.update(ident,next_check=time.time()+10,message='官方正在校验照片，可继续准备其他视频')
            except Exception as exc:
                current=self.get_photo(ident,private=True)
                safe=str(exc) if isinstance(exc,(service.PortraitError,ValueError)) else '照片处理暂时失败，请稍后重试。'
                if current['status']=='submitting':
                    self.update(ident,status='uncertain',message='提交结果待确认，正在查找官方记录',next_check=time.time()+15)
                elif current['status'] in {'processing','uncertain'}:
                    self.update(ident,message=safe,next_check=time.time()+30)
                else: self.update(ident,status='failed',retryable=True,message=safe)
            if asset['kind']=='person_video':
                current=self.get_photo(ident,private=True)
                self.update(ident,message=current['message'].replace('照片','视频'))
        finally: _process_lock.release()

    def thumbnail(self, job):
        from PIL import Image, ImageOps
        asset=self.store.get_asset(job['asset_id'],private=True)
        root=self.settings.storage_dir/'portrait-thumbs';root.mkdir(exist_ok=True)
        try:
            with Image.open(asset['path']) as image:
                image=ImageOps.exif_transpose(image).convert('RGB');image.thumbnail((96,96))
                image.save(root/(job['person_id']+'.jpg'),quality=80)
        except (OSError,ValueError): pass
