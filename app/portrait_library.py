"""Durable people and deduplicated official photo ingestion; one bounded worker."""
from __future__ import annotations
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
                CREATE TABLE IF NOT EXISTS portrait_photos (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, person_id TEXT NOT NULL,
                    asset_id TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL,
                    remote_id TEXT, message TEXT NOT NULL, next_check REAL NOT NULL,
                    created REAL NOT NULL, checked REAL NOT NULL DEFAULT 0,
                    UNIQUE(account,person_id,sha256));
                CREATE INDEX IF NOT EXISTS portrait_photos_pending ON portrait_photos(status,next_check);
            """)

            db.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in db.execute('PRAGMA table_info(portrait_people)')}
            if 'person_type' not in columns:
                db.execute("ALTER TABLE portrait_people ADD COLUMN person_type TEXT NOT NULL DEFAULT 'LivenessFace'")
            db.execute("""CREATE TABLE IF NOT EXISTS portrait_group_requests (
                account TEXT NOT NULL, request_id TEXT NOT NULL, name TEXT NOT NULL,
                status TEXT NOT NULL, person_id TEXT, message TEXT NOT NULL,
                PRIMARY KEY(account,request_id))""")

    def api(self, person_type):
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
            db.execute('INSERT INTO portrait_group_requests VALUES (?,?,?,?,?,?)',
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
            people=db.execute("SELECT id FROM portrait_people WHERE account=? AND person_type='AIGC'",(self.account,)).fetchall()
            counts=db.execute("SELECT ph.status,count(*) AS count FROM portrait_photos ph JOIN portrait_people pe ON pe.id=ph.person_id WHERE ph.account=? AND pe.person_type='AIGC' GROUP BY ph.status",(self.account,)).fetchall()
            failures=db.execute("SELECT pe.name,ph.message,ph.created FROM portrait_photos ph JOIN portrait_people pe ON pe.id=ph.person_id WHERE ph.account=? AND pe.person_type='AIGC' AND ph.status IN ('failed','uncertain') ORDER BY ph.created DESC LIMIT 5",(self.account,)).fetchall()
            runs=db.execute("SELECT snapshot,updated_at FROM production_runs WHERE status='succeeded' ORDER BY updated_at DESC").fetchall()
        ids={row['id'] for row in people}
        last=None
        for run in runs:
            snapshot=json.loads(run['snapshot'])
            if snapshot.get('person_id') in ids:
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
            value['photo_count'] = db.execute("SELECT count(*) FROM portrait_photos WHERE person_id=? AND status='active'",(ident,)).fetchone()[0]
        if not private: value.pop('account'); value.pop('group_id')
        value['verified'] = value['person_type']=='LivenessFace'
        value['usable'] = value['photo_count']>0
        with self.store.connection() as db:
            value['generated_count']=db.execute("SELECT count(*) FROM production_runs WHERE status='succeeded' AND COALESCE(json_extract(snapshot,'$.model.mode'),'')!='mock' AND json_extract(snapshot,'$.person_id')=?",(ident,)).fetchone()[0]
        thumb = self.settings.storage_dir/'portrait-thumbs'/(ident+'.jpg')
        value['thumbnail_url'] = '/api/portrait/people/'+ident+'/thumbnail' if thumb.is_file() else None
        return value

    def people(self):
        if not self.config.ready: return []
        self.import_verified()
        with self.store.connection() as db:
            ids = [x[0] for x in db.execute('SELECT id FROM portrait_people WHERE account=? ORDER BY created',(self.account,))]
        return [self.person(ident) for ident in ids]

    def rename(self, ident, name):
        self.person(ident)
        if not isinstance(name,str) or not 1 <= len(name.strip()) <= 60 or any(ord(c)<32 for c in name):
            raise ValueError('人物备注请输入 1–60 个字符。')
        with self.store.connection() as db:
            db.execute('UPDATE portrait_people SET name=? WHERE id=? AND account=?',(name.strip(),ident,self.account))
        return self.person(ident)

    def get_photo(self, ident, private=False):
        with self.store.connection() as db:
            row = db.execute('SELECT * FROM portrait_photos WHERE id=? AND account=?',(ident,self.account)).fetchone()
        if not row: raise LookupError('照片校验记录不可用，请重新选择人物和照片。')
        result = dict(row)
        if not private:
            result = {k:result[k] for k in ('id','person_id','asset_id','status','message')}
        return result

    def update(self, ident, **values):
        assert set(values) <= {'status','remote_id','message','next_check','checked'}
        with self.store.connection() as db:
            db.execute('UPDATE portrait_photos SET '+','.join(k+'=?' for k in values)+' WHERE id=?',(*values.values(),ident))

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
            api=self.api(person['person_type'])
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
                        self.update(ident,status='failed',message=messages.get(result.get('error_code'),'照片未通过官方检查，请更换照片或到官方控制台查看。'))
                    else:
                        self.update(ident,next_check=time.time()+10,message='官方正在校验照片，可继续准备其他视频')
            except Exception as exc:
                current=self.get_photo(ident,private=True)
                safe=str(exc) if isinstance(exc,(service.PortraitError,ValueError)) else '照片处理暂时失败，请稍后重试。'
                if current['status']=='submitting':
                    self.update(ident,status='uncertain',message='提交结果待确认，正在查找官方记录',next_check=time.time()+15)
                elif current['status'] in {'processing','uncertain'}:
                    self.update(ident,message=safe,next_check=time.time()+30)
                else: self.update(ident,status='failed',message=safe)
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
