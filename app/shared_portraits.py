"""Server-owned cross-tenant real portrait catalogue, grants and provenance.

Paths are derived exclusively from the account database. AIGC is never searched
outside the caller's library. Mirrors carry immutable source-photo provenance.
"""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import uuid

from . import tenancy
from .accounts import Accounts, AccountError
from .portrait_library import PortraitLibrary, validate_photo
from .production_store import ProductionStore


def policy_store(settings):
    store = ProductionStore(tenancy.config_root(settings))
    with store.connection() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS shared_portrait_policies (person_id TEXT PRIMARY KEY, mode TEXT NOT NULL, user_ids TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS shared_portrait_removed (kind TEXT NOT NULL, ident TEXT NOT NULL, PRIMARY KEY(kind,ident));
        ''')
    return store


def photo_key(person_id, sha256):
    return hashlib.sha256((person_id+':'+sha256).encode()).hexdigest()


def assert_available(settings, person_id, sha256=None):
    scoped=hashlib.sha256(str(settings.storage_dir.resolve()).encode()).hexdigest()+':'+person_id
    with policy_store(settings).connection() as db:
        if db.execute('SELECT 1 FROM shared_portrait_removed WHERE (kind=? AND ident IN (?,?)) OR (kind=? AND ident IN (?,?))',
                      ('person',person_id,scoped,'photo',photo_key(person_id,sha256 or ''),photo_key(scoped,sha256 or ''))).fetchone():
            raise LookupError('人物或照片已从素材库移除。')


class SharedPortraitCatalog:
    def __init__(self, settings):
        self.settings = settings
        self.local = PortraitLibrary(settings)
        self.policies = policy_store(settings)
        self.user_id = getattr(settings,'user_id','')
        self.accounts = Accounts(tenancy.config_root(settings)) if tenancy.enabled() else None
        self.user = self.accounts.get_user(self.user_id) if self.accounts and self.user_id else None
        self.admin = not tenancy.enabled() or bool(self.user and self.user['enabled'] and self.user['role']=='admin')
        self._libraries=None
        self._index=None
        self._photos_index={}
        with self.policies.connection() as db:
            self._policies={row['person_id']:{'id':row['person_id'],'mode':row['mode'],'user_ids':json.loads(row['user_ids'])}
                for row in db.execute('SELECT * FROM shared_portrait_policies')}
            self._removed={(row['kind'],row['ident']) for row in db.execute('SELECT * FROM shared_portrait_removed')}

    def _available(self, lib, ident, sha256=None):
        scoped=hashlib.sha256(str(lib.settings.storage_dir.resolve()).encode()).hexdigest()+':'+ident
        return not any(key in self._removed for key in [('person',ident),('person',scoped),
            ('photo',photo_key(ident,sha256 or '')),('photo',photo_key(scoped,sha256 or ''))])

    def _refresh_access(self):
        with self.policies.connection() as db:
            self._policies={row['person_id']:{'id':row['person_id'],'mode':row['mode'],'user_ids':json.loads(row['user_ids'])}
                for row in db.execute('SELECT * FROM shared_portrait_policies')}
            self._removed={(row['kind'],row['ident']) for row in db.execute('SELECT * FROM shared_portrait_removed')}
        self._index=None

    def libraries(self):
        if self._libraries is not None:
            yield from self._libraries
            return
        result=[self.local]
        if not tenancy.enabled():
            self._libraries=result
            yield from result
            return
        # Include root even before a legacy administrator has signed in.
        roots = {self.settings.storage_dir.resolve()}
        candidates = [tenancy.root_settings(self.settings)]
        candidates += [tenancy.user_settings(self.settings,user) for user in self.accounts.list_users()]
        for settings in candidates:
            root = settings.storage_dir.resolve()
            if root in roots or not (root/'production.db').is_file(): continue
            roots.add(root)
            result.append(PortraitLibrary(settings))
        self._libraries=result
        yield from result

    def _build_index(self):
        if self._index is not None: return
        self._index={}
        for lib in self.libraries():
            if not lib.config.ready: continue
            lib.import_verified()
            with lib.store.connection() as db:
                rows=db.execute('SELECT pe.*,h.person_id AS hidden FROM portrait_people pe LEFT JOIN portrait_hidden_people h ON h.person_id=pe.id WHERE pe.account=?',(lib.account,)).fetchall()
                photos=db.execute('SELECT ph.*,a.name,a.kind FROM portrait_photos ph JOIN production_assets a ON a.id=ph.asset_id WHERE ph.account=? ORDER BY ph.created DESC,ph.id',(lib.account,)).fetchall()
                generated=dict(db.execute("""SELECT COALESCE(json_extract(r.snapshot,'$.person_id'),json_extract(p.data,'$.person_id')),count(*)
                    FROM production_runs r LEFT JOIN production_person_preparations p ON p.run_id=r.id
                    WHERE r.status='succeeded' AND COALESCE(json_extract(r.snapshot,'$.model.mode'),'')!='mock'
                    GROUP BY COALESCE(json_extract(r.snapshot,'$.person_id'),json_extract(p.data,'$.person_id'))""").fetchall())
            for row in rows:
                if row['person_type']=='AIGC' and lib is not self.local: continue
                self._index.setdefault(row['id'],[]).append((lib,{**dict(row),'generated_count':generated.get(row['id'],0)}))
            by_person={}
            for row in photos: by_person.setdefault(row['person_id'],[]).append(dict(row))
            self._photos_index[str(lib.settings.storage_dir.resolve())]=by_person

    def policy(self, ident):
        return self._policies.get(ident,{'id':ident,'mode':'all','user_ids':[]})

    def allowed(self, ident):
        if self.admin: return True
        if tenancy.enabled() and (not self.user or not self.user['enabled']): return False
        policy=self.policy(ident)
        return policy['mode']=='all' or self.user_id in policy['user_ids']

    def sources(self, ident, *, removed=False):
        if not getattr(self,'_batch',False): self._refresh_access()
        self._build_index()
        found=[]
        for lib,person in self._index.get(ident,[]):
            if person['person_type']=='LivenessFace' and not self.allowed(ident): continue
            if not removed:
                if not self._available(lib,ident) or person['hidden']: continue
            found.append((lib,person))
        if not found: raise LookupError('所选人物不可用或未获授权。')
        # Canonical metadata is the oldest real-person registration. A later
        # mirror/import in a recipient must not shadow its name with a stub.
        if found[0][1]['person_type']=='LivenessFace':
            found.sort(key=lambda source:(source[1]['created'],str(source[0].settings.storage_dir.resolve())))
        return found

    def person(self, ident, *, removed=False):
        sources=self.sources(ident,removed=removed)
        person={key:value for key,value in sources[0][1].items() if key not in {'account','group_id','hidden'}}
        real=person['person_type']=='LivenessFace'
        person['verified']=real
        photos=[] if removed else self._photos(sources)
        counts={}
        for _,photo in photos: counts[photo['status']]=counts.get(photo['status'],0)+1
        person.update(shared=real,can_manage=self.admin if real else True,
            photo_counts=counts,photo_count=sum(p['kind']=='face' and p['status']=='active' for _,p in photos),
            video_count=sum(p['kind']=='person_video' and p['status']=='active' for _,p in photos))
        person['usable']=person['photo_count']+person['video_count']>0
        person['thumbnail_url']=next(('/api/portrait/photos/'+photo['id']+'/thumbnail' for _,photo in photos if photo['kind']=='face'),None)
        return person

    def people(self, removed=False):
        self._refresh_access()
        self._build_index()
        ids=list(self._index)
        result=[]
        self._batch=True
        try:
            for ident in ids:
                try:
                    item=self.person(ident)
                    if not removed: result.append(item)
                except LookupError:
                    if removed:
                        try: result.append(self.person(ident,removed=True))
                        except LookupError: pass
        finally: self._batch=False
        return result

    def _photos(self, sources):
        result=[]; seen=set()
        for lib,person in sources:
            for raw in self._photos_index[str(lib.settings.storage_dir.resolve())].get(person['id'],[]):
                key=photo_key(person['id'],raw['sha256'])
                if key in seen: continue
                if not self._available(lib,person['id'],raw['sha256']): continue
                photo={k:raw[k] for k in ('id','asset_id','name','kind','status','message')}
                photo.update(url='/api/production/assets/'+raw['asset_id']+'/file',remote_asset_id=raw['remote_id'] if raw['status']=='active' else None)
                seen.add(key); result.append((lib,photo))
        return result

    def photos_for_person(self, ident):
        sources=self.sources(ident); real=sources[0][1]['person_type']=='LivenessFace'
        result=[]
        for lib,photo in self._photos(sources):
            local=lib.settings.storage_dir.resolve()==self.settings.storage_dir.resolve()
            result.append({**photo,'url':photo['url'] if local else '/api/portrait/photos/'+photo['id']+'/file',
                'thumbnail_url':'/api/portrait/photos/'+photo['id']+'/thumbnail',
                'can_manage':self.admin if real else True})
        return result

    def photo_source(self, ident):
        for lib in self.libraries():
            try: photo=lib.get_photo(ident,private=True)
            except LookupError: continue
            sources=self.sources(photo['person_id'])
            if not any(source.settings.storage_dir.resolve()==lib.settings.storage_dir.resolve() for source,_ in sources): continue
            assert_available(self.settings,photo['person_id'],photo['sha256'])
            return lib,photo
        raise LookupError('照片不可用或未获授权。')

    def require_manage(self, ident, *, removed=False):
        sources=self.sources(ident,removed=removed)
        if sources[0][1]['person_type']=='LivenessFace' and not self.admin:
            raise PermissionError('共享真人素材仅管理员可修改或移除。')
        return sources[0][0]

    def restore_person(self, ident):
        lib=self.require_manage(ident,removed=True)
        key=ident if lib.person(ident)['person_type']=='LivenessFace' else hashlib.sha256(str(lib.settings.storage_dir.resolve()).encode()).hexdigest()+':'+ident
        with self.policies.connection() as db:
            db.execute('DELETE FROM shared_portrait_removed WHERE kind=? AND ident=?',('person',key))
        self._removed.discard(('person',key))
        for source,_ in self.sources(ident,removed=True): source.set_removed(ident,False)
        self._index=None
        return {'id':ident,'removed':False}

    def remove_person(self, ident):
        lib=self.require_manage(ident)
        key=ident if lib.person(ident)['person_type']=='LivenessFace' else hashlib.sha256(str(lib.settings.storage_dir.resolve()).encode()).hexdigest()+':'+ident
        with self.policies.connection() as db:
            db.execute('INSERT OR IGNORE INTO shared_portrait_removed VALUES (?,?)',('person',key))
        self._removed.add(('person',key))
        return {'id':ident,'removed':True,'retained_history':True,'remote_deleted':False}

    def remove_photo(self, ident):
        lib,photo=self.photo_source(ident); self.require_manage(photo['person_id'])
        key=photo['person_id'] if lib.person(photo['person_id'])['person_type']=='LivenessFace' else hashlib.sha256(str(lib.settings.storage_dir.resolve()).encode()).hexdigest()+':'+photo['person_id']
        with self.policies.connection() as db:
            db.execute('INSERT OR IGNORE INTO shared_portrait_removed VALUES (?,?)',('photo',photo_key(key,photo['sha256'])))
        self._removed.add(('photo',photo_key(key,photo['sha256'])))
        return {'id':ident,'removed':True,'retained_history':True,'remote_deleted':False}

    def set_policy(self, ident, mode, user_ids):
        if not self.admin: raise PermissionError('仅管理员可分配真人权限。')
        person=self.sources(ident)[0][1]
        if person['person_type']!='LivenessFace': raise ValueError('虚拟人物保持账号私有。')
        if mode not in {'all','selected'} or not isinstance(user_ids,list) or any(not isinstance(x,str) for x in user_ids):
            raise ValueError('人物授权配置无效。')
        users={user['id'] for user in self.accounts.list_users()} if self.accounts else set()
        if any(ident not in users for ident in user_ids): raise ValueError('请选择存在的用户。')
        user_ids=sorted(set(user_ids)) if mode=='selected' else []
        with self.policies.connection() as db:
            db.execute('INSERT OR REPLACE INTO shared_portrait_policies VALUES (?,?,?)',(ident,mode,json.dumps(user_ids)))
        self._policies[ident]={'id':ident,'mode':mode,'user_ids':user_ids}
        if self.accounts:
            self.accounts.audit(self.user_id,'portrait.access',ident,{'reason':mode+':'+','.join(user_ids)})
        return {**self.policy(ident),'name':person['name']}

    def use_reference(self, ident):
        from .portrait_router import _use_local_reference
        lib,photo=self.photo_source(ident)
        selected=_use_local_reference(lib,ident)
        if lib.settings.storage_dir.resolve()==self.settings.storage_dir.resolve(): return {**selected,'person_id':photo['person_id']}
        asset=lib.store.get_asset(photo['asset_id'],private=True)
        mirror_id=hashlib.sha256(('shared:'+photo['person_id']+':'+photo['sha256']).encode()).hexdigest()[:32]
        root=self.settings.storage_dir/'assets'; root.mkdir(parents=True,exist_ok=True)
        path=root/(mirror_id+Path(asset['path']).suffix)
        provenance_store(self.local.store)
        try: self.local.store.get_asset(mirror_id)
        except LookupError:
            shutil.copyfile(asset['path'],path)
            self.local.store.add_asset(mirror_id,asset['name'],asset['kind'],path,asset['size'],asset['mime'],asset['sha256'])
        with self.local.store.connection() as db:
            db.execute('INSERT OR REPLACE INTO shared_portrait_provenance VALUES (?,?,?)',(mirror_id,photo['person_id'],ident))
        self.local.store.bind_portrait(mirror_id,lib.store.portrait_binding(asset['id']),lib.account)
        return {**self.local.store.get_asset(mirror_id),'person_id':photo['person_id'],'person_type':'LivenessFace'}


def provenance_store(store):
    with store.connection() as db:
        db.execute('CREATE TABLE IF NOT EXISTS shared_portrait_provenance (asset_id TEXT PRIMARY KEY, person_id TEXT NOT NULL, photo_id TEXT NOT NULL)')


def authorize_asset(settings, ident):
    """Validate source access for local mirrors, registered photos and bindings.

    Return source (library, photo), or None for an ordinary uploaded asset.
    Never invoke this for completed run output playback.
    """
    cat=SharedPortraitCatalog(settings); store=cat.local.store
    binding=store.portrait_binding(ident)
    if binding and binding['fingerprint']!=cat.local.account:
        raise ValueError('人物素材的账号或项目配置已变更，请重新选择。')
    provenance_store(store)
    with store.connection() as db:
        origins=db.execute('SELECT * FROM shared_portrait_provenance WHERE asset_id=? OR asset_id IN (SELECT id FROM production_assets WHERE sha256=(SELECT sha256 FROM production_assets WHERE id=?))',(ident,ident)).fetchall()
        rows=db.execute('SELECT id,person_id,sha256 FROM portrait_photos WHERE account=? AND (asset_id=? OR sha256=(SELECT sha256 FROM production_assets WHERE id=?))',(cat.local.account,ident,ident)).fetchall()
    provenance_source=None
    for origin in origins:
        lib,photo=cat.photo_source(origin['photo_id'])
        if photo['person_id']!=origin['person_id']: raise LookupError('共享来源记录不匹配。')
        provenance_source=(lib,photo)
    for row in rows:
        cat.sources(row['person_id'])
        assert_available(settings,row['person_id'],row['sha256'])
    local_source=cat.photo_source(rows[0]['id']) if rows else None
    if binding:
        pid=hashlib.sha256((binding['fingerprint']+binding['group_id']).encode()).hexdigest()[:32]
        cat.sources(pid)
        assert_available(settings,pid,store.get_asset(ident,private=True)['sha256'])
    # Prevent a second uploaded/local ID from stripping a known shared real
    # identity. Content provenance survives a change of kind or an attempted
    # relabel as AIGC; every known real restriction must still be satisfied.
    asset=store.get_asset(ident,private=True)
    cat._build_index()
    for source in cat.libraries():
        photos=cat._photos_index.get(str(source.settings.storage_dir.resolve()),{})
        for pid,entries in photos.items():
            real=any(person['person_type']=='LivenessFace' for _,person in cat._index.get(pid,[]))
            if real and any(photo['sha256']==asset['sha256'] for photo in entries):
                cat.sources(pid)
                assert_available(settings,pid,asset['sha256'])
    return provenance_source or local_source
