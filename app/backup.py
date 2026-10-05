"""Offline, quiesced SQLite/media snapshots. Never imports application runtime.

Cache, thumbnail/preview derivatives, backup folders and transient lock/partial
files are excluded; durable work (including continuation bases) is retained.
The caller must stop every writer, including independent workflow writers.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import sys
import uuid


class BackupError(ValueError):
    """A snapshot is incomplete, unsafe, or unsupported."""


SCHEMA = 'ark-backup'
VERSION = 1
_CACHES = {'cache', 'asset-thumbs', 'asset-previews', 'portrait-thumbs', 'backups'}
_PATH_KEYS = {'path', 'uri', 'source_uri', 'source_path', 'video_path', 'face_path',
              'garment_path', 'output_path', 'input_path', 'local_path', 'replace_image'}
_MEDIA_JSON_TABLES = {'production_drafts','production_runs','production_continuations',
                     'production_person_preparations','stage_tasks','run_snapshots',
                     'source_assets','reference_assets'}
_TERMINAL = {
    'production_runs': {'succeeded', 'failed', 'cancelled'},
    'stage_tasks': {'succeeded', 'failed', 'cancelled'},
    'portrait_photos': {'active', 'failed', 'removed'},
    'portrait_group_requests': {'ready', 'failed'},
    'production_playbacks': {'ready', 'failed'},
    'video_jobs': {'succeeded', 'failed', 'cancelled', 'completed'},
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _no_links(path):
    """Reject symlinks and Windows junction/reparse points, including ancestors."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise BackupError(f'Symlink/reparse path is not supported: {part}')
    return path


def _overlap(a, b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _relative(value):
    if (not isinstance(value, str) or not value or '\\' in value or ':' in value
            or any(ord(c) < 32 for c in value) or value.endswith('/')
            or any(part in {'', '.', '..'} for part in value.split('/'))
            or PurePosixPath(value).is_absolute()):
        raise BackupError('Invalid manifest relative path')
    # Reject Windows aliases/alternate streams even when verifying on Linux.
    for part in value.split('/'):
        if part.rstrip(' .') != part or part.split('.')[0].upper() in {
                'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}:
            raise BackupError('Unsafe manifest filename')
    return value


def _root_specs(storage, extras):
    if not isinstance(extras or {}, dict) or 'storage' in (extras or {}):
        raise BackupError('extra_roots must be named roots; storage is reserved')
    paths = {'storage': storage, **(extras or {})}
    result = {}
    for name, value in paths.items():
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}', name):
            raise BackupError('Invalid root name')
        path = _no_links(value)
        if not path.exists() or not (path.is_dir() or path.is_file()):
            raise BackupError(f'Source root does not exist: {name}')
        if name == 'storage' and not path.is_dir():
            raise BackupError('Storage root must be a directory')
        if any(_overlap(path, Path(old['source'])) for old in result.values()):
            raise BackupError('Source roots overlap')
        result[name] = {'source': str(path), 'kind': 'directory' if path.is_dir() else 'file',
                        'mode': stat.S_IMODE(path.stat().st_mode)}
    return result


def _configured_paths(roots, database_url):
    url = database_url if database_url is not None else os.environ.get('DATABASE_URL')
    paths = []
    if url:
        match = re.fullmatch(r'sqlite(?:\+pysqlite)?:///(.+)', url)
        if not match or match[1] == ':memory:' or '?' in match[1] or '#' in match[1]:
            raise BackupError('Only file-backed SQLite is supported; unsupported database backend')
        paths.append(('DATABASE_URL', Path(match[1])))
    for key in ('WORKFLOW_DB', 'WORKFLOW_STORAGE'):
        if os.environ.get(key):
            paths.append((key, Path(os.environ[key])))
    for label, value in paths:
        path = _no_links(value)
        if not any(path == Path(spec['source']) or
                   (spec['kind'] == 'directory' and path.is_relative_to(Path(spec['source'])))
                   for spec in roots.values()):
            raise BackupError(f'{label} is external and must be covered by an explicit named root')
        if not path.exists():
            raise BackupError(f'Configured path is missing: {label}')


def _excluded(relative):
    parts = relative.parts
    # Only application root/tenant root cache directories, never arbitrary nested media names.
    local = parts[2:] if len(parts) >= 3 and parts[0] == 'users' else parts
    return (bool(local) and local[0] in _CACHES) or relative.name in {
        '.production-worker.lock', '.workflow-worker.lock'} or relative.name.endswith(('.part', '.tmp'))


def _walk(root, *, exclude=False):
    for current, dirs, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in list(dirs):
            path = _no_links(base/name)
            if exclude and _excluded(path.relative_to(root)):
                dirs.remove(name)
        for name in sorted(files):
            path = _no_links(base/name)
            if not path.is_file():
                raise BackupError(f'Non-regular file in snapshot: {path}')
            if not exclude or not _excluded(path.relative_to(root)):
                yield path


def _directories(root, *, exclude=False):
    for current, dirs, _ in os.walk(root, followlinks=False):
        for name in list(dirs):
            path=_no_links(Path(current)/name)
            if exclude and _excluded(path.relative_to(root)):
                dirs.remove(name)
            else:
                yield path


def _is_sqlite(path):
    with path.open('rb') as stream:
        header = stream.read(16)
    detected = header == b'SQLite format 3\x00'
    if path.suffix.lower() in {'.db', '.sqlite', '.sqlite3'} and not detected:
        raise BackupError(f'Invalid SQLite database: {path.name}')
    return detected


def _open_readonly(path, *, immutable=False):
    return sqlite3.connect(path.as_uri() + ('?mode=ro&immutable=1' if immutable else '?mode=ro'), uri=True, timeout=30)


def _integrity(db):
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise BackupError('SQLite integrity check failed')


def _backup_database(source, target):
    try:
        with closing(_open_readonly(source)) as src, closing(sqlite3.connect(target)) as dst:
            _integrity(src)
            src.backup(dst)
            dst.execute('PRAGMA journal_mode=DELETE')
            dst.commit()
            _integrity(dst)
    except sqlite3.Error as exc:
        raise BackupError(f'SQLite backup failed: {source.name}') from exc


def _write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != 'nt': path.chmod(0o600)


def _fsync_directory(path):
    if os.name != 'nt':
        descriptor = os.open(path, os.O_RDONLY)
        try: os.fsync(descriptor)
        finally: os.close(descriptor)


def _publish(source, destination):
    """Atomic publication with NO replacement, including concurrent destinations."""
    if os.name=='nt':
        source.rename(destination)  # Windows rename fails when the destination exists.
    elif source.is_file():
        os.link(source,destination)
        source.unlink()
    elif sys.platform.startswith('linux'):
        import ctypes
        libc=ctypes.CDLL(None,use_errno=True)
        rename=getattr(libc,'renameat2',None)
        if rename is None: raise BackupError('Atomic no-replace directory publication is unsupported on this host')
        if rename(-100,os.fsencode(source),-100,os.fsencode(destination),1):
            error=ctypes.get_errno()
            raise OSError(error,os.strerror(error),str(destination))
    else:
        raise BackupError('Atomic no-replace directory publication requires Windows or Linux')


def _remove_owned(path):
    # Call only on a directory created in this operation; validate absolute path before recursive removal.
    path = _no_links(path)
    def retry(function, failed, error):
        item=_no_links(failed)
        if not item.is_relative_to(path) or not isinstance(error[1],PermissionError):
            raise error[1]
        item.chmod(stat.S_IMODE(item.stat().st_mode)|0o700)
        function(item)
    if path.is_dir(): shutil.rmtree(path,onerror=retry)
    elif path.exists():
        path.chmod(stat.S_IMODE(path.stat().st_mode)|0o600)
        path.unlink()


def create_snapshot(storage_root, destination, *, extra_roots=None, code_revision='', database_url=None, quiesced=False,
                    release_identity=None):
    """Publish a verified directory snapshot; caller attests all writers stopped."""
    if quiesced is not True:
        raise BackupError('A quiesced snapshot requires quiesced=True after stopping all writers')
    if release_identity is not None:
        from .release_identity import validate_release_identity, ReleaseIdentityError
        try:
            validate_release_identity(release_identity,code_revision=str(code_revision))
        except ReleaseIdentityError as error:
            raise BackupError('Release identity must match the snapshot code revision') from error
        release_identity=json.loads(json.dumps(release_identity))
    roots = _root_specs(storage_root, extra_roots)
    destination = _no_links(destination)
    if any(_overlap(destination, Path(spec['source'])) for spec in roots.values()):
        raise BackupError('Snapshot destination overlaps a source root; use independent backup storage')
    if destination.exists(): raise BackupError('Snapshot destination already exists')
    _configured_paths(roots, database_url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = destination.with_name('.'+destination.name+'.partial-'+uuid.uuid4().hex)
    stage.mkdir(mode=0o700)
    manifest = {'schema':SCHEMA,'version':VERSION,'snapshot_id':uuid.uuid4().hex,
                'created_at':_now(),'code_revision':str(code_revision),'source_cwd':str(Path.cwd()),
                'roots':roots,'files':[],'databases':[],'directories':[],
                'exclusions':{'directories':sorted(_CACHES),'transient_suffixes':['.part','.tmp'],
                              'sqlite_sidecars':'WAL and SHM incorporated using SQLite backup'}}
    if release_identity is not None:
        manifest['release_identity']=release_identity
    try:
        for name, spec in roots.items():
            source = Path(spec['source'])
            if spec['kind']=='directory':
                for directory in _directories(source,exclude=name=='storage'):
                    relative=_relative(directory.relative_to(source).as_posix())
                    (stage/'roots'/name/relative).mkdir(parents=True,exist_ok=True)
                    manifest['directories'].append({'root':name,'path':relative,'mode':stat.S_IMODE(directory.stat().st_mode)})
            paths = list(_walk(source, exclude=name=='storage')) if spec['kind']=='directory' else [source]
            for path in paths:
                if path.name.endswith(('-wal','-shm')):
                    original = path.with_name(path.name[:-4])
                    if original.is_file() and _is_sqlite(original): continue
                relative = path.relative_to(source).as_posix() if source.is_dir() else source.name
                _relative(relative)
                target = stage/'roots'/name/relative
                target.parent.mkdir(parents=True, exist_ok=True)
                is_db = _is_sqlite(path)
                if is_db: _backup_database(path,target)
                else: shutil.copy2(path,target)
                mode = stat.S_IMODE(path.stat().st_mode)
                target.chmod(mode|0o600)
                with target.open('r+b') as stream: os.fsync(stream.fileno())
                target.chmod(mode)
                manifest['files'].append({'root':name,'path':relative,'size':target.stat().st_size,
                                          'sha256':_hash(target),'mode':mode,'sqlite':is_db})
                if is_db: manifest['databases'].append({'root':name,'path':relative})
            (stage/'roots'/name).mkdir(parents=True, exist_ok=True)
        _write_json(stage/'manifest.json',manifest)
        verify_snapshot(stage)
        for directory in sorted(_directories(stage),key=lambda item:len(item.parts),reverse=True):
            _fsync_directory(directory)
        _fsync_directory(stage)
        if destination.exists(): raise BackupError('Snapshot destination already exists')
        _publish(stage,destination)
        _fsync_directory(destination.parent)
        return manifest
    except BaseException:
        if stage.exists(): _remove_owned(stage)
        raise


def _load_manifest(snapshot):
    try:
        manifest = json.loads((snapshot/'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise BackupError('Missing or invalid manifest; incomplete snapshot') from exc
    if (not isinstance(manifest,dict) or manifest.get('schema') != SCHEMA
            or type(manifest.get('version')) is not int or manifest['version'] != VERSION
            or not re.fullmatch('[a-f0-9]{32}',str(manifest.get('snapshot_id','')))
            or not isinstance(manifest.get('files'),list) or not isinstance(manifest.get('roots'),dict)
            or 'storage' not in manifest['roots'] or not isinstance(manifest.get('databases'),list)
            or not isinstance(manifest.get('directories'),list)):
        raise BackupError('Unsupported or malformed backup manifest')
    source_paths=[]
    for name,spec in manifest['roots'].items():
        if (not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',name) or not isinstance(spec,dict)
                or spec.get('kind') not in {'file','directory'} or not _absolute(spec.get('source',''))):
            raise BackupError('Invalid manifest root')
        if type(spec.get('mode')) is not int or not 0<=spec['mode']<=0o777:
            raise BackupError('Invalid root permissions')
        normalized=spec['source'].replace('\\','/').rstrip('/')
        if any(part in {'.','..'} for part in normalized.split('/')):
            raise BackupError('Invalid source root traversal')
        key=normalized.casefold() if PureWindowsPath(normalized).is_absolute() and not normalized.startswith('/') else normalized
        if any(key==old or key.startswith(old+'/') or old.startswith(key+'/') for old in source_paths):
            raise BackupError('Manifest source roots overlap')
        source_paths.append(key)
    if manifest['roots']['storage']['kind']!='directory': raise BackupError('Storage root must be a directory')
    if 'release_identity' in manifest:
        from .release_identity import validate_release_identity, ReleaseIdentityError
        try:
            validate_release_identity(manifest['release_identity'],code_revision=manifest.get('code_revision',''))
        except ReleaseIdentityError as error:
            raise BackupError('Invalid snapshot release identity') from error
    return manifest


def _absolute(value):
    return isinstance(value,str) and (PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute())


def _location(value, manifest):
    """Match only complete path components; never prefix-substitute arbitrary text."""
    if not isinstance(value,str) or not value or '://' in value: return None
    normalized = value.replace('\\','/')
    if not _absolute(normalized):
        normalized = manifest.get('source_cwd','').replace('\\','/').rstrip('/')+'/'+normalized
    if '..' in normalized.split('/'):
        raise BackupError('Media reference contains path traversal')
    for name,spec in manifest['roots'].items():
        root = spec['source'].replace('\\','/').rstrip('/')
        windows = PureWindowsPath(root).is_absolute() and not root.startswith('/')
        a,b = (normalized.casefold(),root.casefold()) if windows else (normalized,root)
        if a == b:
            if spec['kind']=='file':
                return name,next(item['path'] for item in manifest['files'] if item['root']==name)
            return name,''
        if spec['kind']=='directory' and a.startswith(b+'/'):
            return name, _relative(normalized[len(root)+1:])
    raise BackupError('Local media reference is outside declared source roots')


def _json_paths(value):
    if isinstance(value,dict):
        for key,item in value.items():
            if key in _PATH_KEYS and isinstance(item,str) and item and '://' not in item:
                yield item
            elif isinstance(item,(dict,list)):
                yield from _json_paths(item)
    elif isinstance(value,list):
        for item in value: yield from _json_paths(item)


def _quote(name):
    return '"'+name.replace('"','""')+'"'


def _tables(db):
    return [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]


def _rows(db,table):
    cursor=db.execute('SELECT * FROM '+_quote(table))
    names=[item[0] for item in cursor.description]
    for row in cursor: yield dict(zip(names,row))


def _check_references(db, entry, manifest, inventory):
    def check(value, expected_hash=None, expected_size=None):
        location = _location(value,manifest)
        if location is None: return
        item=inventory.get(location)
        if not item: raise BackupError(f'Missing referenced media in snapshot: {location}')
        if expected_hash and item['sha256'] != expected_hash:
            raise BackupError('Referenced media hash differs from database')
        if expected_size is not None and item['size'] != expected_size:
            raise BackupError('Referenced media size differs from database')
    root_name,relative = entry['root'],PurePosixPath(entry['path'])
    source_spec = manifest['roots'][root_name]
    db_parent = (source_spec['source'].replace('\\','/') if source_spec['kind']=='directory'
                 else str(PurePosixPath(source_spec['source'].replace('\\','/')).parent))
    if source_spec['kind']=='directory' and str(relative.parent) != '.': db_parent += '/'+str(relative.parent)
    for table in _tables(db):
        if table == 'restore_quarantine': continue
        for row in _rows(db,table):
            if table == 'production_assets':
                check(row['path'],row.get('sha256'),row.get('size'))
            if table in {'source_assets','reference_assets'} and row.get('kind')!='url':
                check(row.get('uri'),row.get('sha256'),row.get('size_bytes'))
            if table == 'production_runs' and row.get('status')=='succeeded':
                check(db_parent+'/outputs/'+row['id']+'.mp4')
            if table == 'video_jobs':
                for column,folder in [('output_name','outputs'),('defaced_name','work')]:
                    if row.get(column): check(db_parent+'/'+folder+'/'+row[column])
            for column,value in row.items() if table in _MEDIA_JSON_TABLES else ():
                if not isinstance(value,str) or not value.lstrip().startswith(('{','[')): continue
                try: data=json.loads(value)
                except ValueError: continue
                for path in _json_paths(data): check(path)
                if table=='production_continuations' and isinstance(data,dict) and data.get('base_ready'):
                    check(db_parent+'/work/'+row['run_id']+'/base.mp4')


def verify_snapshot(snapshot):
    """Verify all bytes, SQLite integrity and actual local media references."""
    snapshot = _no_links(snapshot)
    if not snapshot.is_dir(): raise BackupError('Snapshot directory is missing')
    # Walk first so a manifest symlink is not read before link validation.
    actual={path.relative_to(snapshot).as_posix() for path in _walk(snapshot)}
    actual_dirs={path.relative_to(snapshot).as_posix() for path in _directories(snapshot)}
    manifest = _load_manifest(snapshot)
    expected={'manifest.json'}; inventory={}; names=set(); databases=[]
    expected_dirs={'roots',*('roots/'+name for name in manifest['roots'])}
    for entry in manifest['directories']:
        if not isinstance(entry,dict) or entry.get('root') not in manifest['roots']:
            raise BackupError('Invalid directory inventory')
        relative=_relative(entry.get('path')); logical='roots/'+entry['root']+'/'+relative
        if (manifest['roots'][entry['root']]['kind']!='directory' or logical.casefold() in names
                or type(entry.get('mode')) is not int or not 0<=entry['mode']<=0o777):
            raise BackupError('Invalid directory metadata')
        names.add(logical.casefold()); expected_dirs.add(logical)
    for entry in manifest['files']:
        if not isinstance(entry,dict): raise BackupError('Invalid file manifest entry')
        name,relative=entry.get('root'),_relative(entry.get('path'))
        if name not in manifest['roots']: raise BackupError('Unknown file root')
        spec=manifest['roots'][name]
        if spec['kind']=='file' and relative!=PurePosixPath(spec['source'].replace('\\','/')).name:
            raise BackupError('Invalid file-root inventory')
        logical=f'roots/{name}/{relative}'
        if logical.casefold() in names: raise BackupError('Duplicate manifest path')
        names.add(logical.casefold()); expected.add(logical)
        if (type(entry.get('size')) is not int or entry['size']<0 or type(entry.get('sqlite')) is not bool
                or not re.fullmatch('[a-f0-9]{64}',str(entry.get('sha256','')))
                or type(entry.get('mode')) is not int or not 0<=entry['mode']<=0o777):
            raise BackupError('Invalid file metadata')
        path=snapshot/logical
        if not path.is_file() or path.stat().st_size!=entry['size'] or _hash(path)!=entry['sha256']:
            raise BackupError(f'Missing or corrupt snapshot file: {logical}')
        if _is_sqlite(path)!=entry['sqlite']: raise BackupError('SQLite inventory mismatch')
        inventory[name,relative]=entry
        if entry['sqlite']: databases.append({'root':name,'path':relative})
    if actual!=expected: raise BackupError('Snapshot contains missing or extra unlisted files')
    if actual_dirs!=expected_dirs: raise BackupError('Snapshot contains missing or extra unlisted directories')
    for name,spec in manifest['roots'].items():
        if spec['kind']=='file' and sum(entry['root']==name for entry in manifest['files'])!=1:
            raise BackupError('File root must contain exactly one file')
    if databases!=manifest['databases']: raise BackupError('Database inventory mismatch')
    for entry in databases:
        try:
            with closing(_open_readonly(snapshot/'roots'/entry['root']/entry['path'],immutable=True)) as db:
                _integrity(db)
                _check_references(db,entry,manifest,inventory)
        except sqlite3.Error as exc: raise BackupError('Invalid snapshot SQLite database') from exc
    return manifest


def _mapped(value,manifest,targets):
    if not isinstance(value,str) or not value or '://' in value: return value
    location=_location(value,manifest)
    if location is None: return value
    name,relative=location
    return str(targets[name]/relative) if manifest['roots'][name]['kind']=='directory' else str(targets[name])


def _remap_json(value,manifest,targets):
    if isinstance(value,dict):
        return {key:(_mapped(item,manifest,targets) if key in _PATH_KEYS and isinstance(item,str)
                    else _remap_json(item,manifest,targets)) for key,item in value.items()}
    if isinstance(value,list): return [_remap_json(item,manifest,targets) for item in value]
    return value


def _restore_database(path,manifest,targets):
    count=0
    with closing(sqlite3.connect(path)) as db:
        db.execute('PRAGMA journal_mode=DELETE')
        db.execute('CREATE TABLE IF NOT EXISTS restore_quarantine(table_name TEXT,record_id TEXT,original_json TEXT,quarantined_at TEXT,PRIMARY KEY(table_name,record_id))')
        for table in _tables(db):
            if table=='restore_quarantine': continue
            columns=[row[1] for row in db.execute('PRAGMA table_info('+_quote(table)+')')]
            pk=[row[1] for row in db.execute('PRAGMA table_info('+_quote(table)+')') if row[5]]
            # The real application tables all have primary keys; tolerate rowid-only offline fixtures.
            rows=db.execute('SELECT rowid,* FROM '+_quote(table)).fetchall()
            for values in rows:
                row=dict(zip(columns,values[1:])); changes={}; original=dict(row)
                for column,value in row.items():
                    if column in _PATH_KEYS and isinstance(value,str):
                        changes[column]=_mapped(value,manifest,targets)
                    elif table in _MEDIA_JSON_TABLES and isinstance(value,str) and value.lstrip().startswith(('{','[')):
                        try: data=json.loads(value)
                        except ValueError: continue
                        mapped=_remap_json(data,manifest,targets)
                        if mapped!=data: changes[column]=json.dumps(mapped,ensure_ascii=False)
                quarantine=table in _TERMINAL and row.get('status') not in _TERMINAL[table]
                if quarantine: changes['status']='restore_held'
                # Embedded work/session states also require explicit reconciliation.
                if table in {'production_person_preparations','sessions'} and isinstance(row.get('data'),str):
                    try: data=json.loads(changes.get('data',row['data']))
                    except ValueError: data={}
                    field='state' if table=='production_person_preparations' else 'status'
                    unfinished=(data.get(field) not in {'ready','failed','cancelled'} if table=='production_person_preparations'
                                else data.get(field) in {'pending','creating','queued','running','processing','uploading','submitting','uncertain'})
                    if unfinished:
                        data[field]='restore_held'; changes['data']=json.dumps(data,ensure_ascii=False); quarantine=True
                if quarantine:
                    identity=str(row.get('id',row.get('run_id',json.dumps([row.get(key) for key in pk]) if pk else values[0])))
                    db.execute('INSERT OR REPLACE INTO restore_quarantine VALUES (?,?,?,?)',
                               (table,identity,json.dumps(original,ensure_ascii=False),_now()))
                    count+=1
                if changes:
                    db.execute('UPDATE '+_quote(table)+' SET '+','.join(_quote(k)+'=?' for k in changes)+' WHERE rowid=?',(*changes.values(),values[0]))
        db.commit(); _integrity(db)
    return count


def restore_snapshot(snapshot,destination,*,extra_destinations=None):
    """Restore only to absent targets; old pending work remains held after activation."""
    snapshot=_no_links(snapshot); manifest=verify_snapshot(snapshot)
    extras=extra_destinations or {}
    if not isinstance(extras,dict) or set(extras)!=set(manifest['roots'])-{'storage'}:
        raise BackupError('Explicit destinations are required for every named extra root')
    targets={name:_no_links(value) for name,value in {'storage':destination,**extras}.items()}
    for name,target in targets.items():
        if _overlap(target,snapshot) or any(_overlap(target,other) for key,other in targets.items() if key!=name):
            raise BackupError('Restore destinations overlap another root or snapshot')
        if target.exists(): raise BackupError('Restore requires fresh, nonexistent destinations')
    stages={}; published=[]; quarantined=0
    try:
        for name,target in targets.items():
            target.parent.mkdir(parents=True,exist_ok=True)
            stage=target.with_name('.'+target.name+'.restore-'+uuid.uuid4().hex)
            stage.mkdir(mode=0o700); stages[name]=stage
        for entry in manifest['files']:
            name=entry['root']; path=stages[name]/entry['path']
            path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(snapshot/'roots'/name/entry['path'],path)
            if path.stat().st_size!=entry['size'] or _hash(path)!=entry['sha256']:
                raise BackupError('Restored copy is corrupt; hash verification failed')
            path.chmod(entry['mode']|0o600)
            if entry['sqlite']: quarantined+=_restore_database(path,manifest,targets)
            with path.open('r+b') as stream: os.fsync(stream.fileno())
            path.chmod(entry['mode'])
        restored_manifest={**manifest,'roots':{name:{**spec,'source':str(targets[name])} for name,spec in manifest['roots'].items()}}
        inventory={(entry['root'],entry['path']):entry for entry in manifest['files']}
        for entry in manifest['databases']:
            with closing(_open_readonly(stages[entry['root']]/entry['path'],immutable=True)) as db:
                _integrity(db); _check_references(db,entry,restored_manifest,inventory)
        for entry in sorted(manifest['directories'],key=lambda value:len(value['path'].split('/')),reverse=True):
            path=stages[entry['root']]/entry['path']
            path.mkdir(parents=True,exist_ok=True); path.chmod(entry['mode'])
        receipt={'schema':'ark-restore','version':1,'snapshot_id':manifest['snapshot_id'],
                 'restored_at':_now(),'quarantined_records':quarantined,
                 'roots':{name:str(path) for name,path in targets.items()},'status':'restore_held'}
        hold=stages['storage']/'.restore-hold.json'
        if hold.exists(): hold.unlink()
        _write_json(hold,receipt)
        for stage in stages.values():
            for directory in sorted(_directories(stage),key=lambda item:len(item.parts),reverse=True):
                _fsync_directory(directory)
            _fsync_directory(stage)
        # All validation happened before publication; every published storage tree already has its hold.
        for name,target in targets.items():
            if target.exists(): raise BackupError('Restore destination was created concurrently')
            stage=stages[name]
            if manifest['roots'][name]['kind']=='file':
                entries=[item for item in manifest['files'] if item['root']==name]
                if len(entries)!=1: raise BackupError('File root must contain exactly one file')
                _publish(stage/entries[0]['path'],target)
            else:
                stage.chmod(manifest['roots'][name].get('mode',0o700))
                _publish(stage,target)
            published.append(target); _fsync_directory(target.parent)
        return receipt
    except BaseException:
        for target in reversed(published): _remove_owned(target)
        raise
    finally:
        for stage in stages.values():
            if stage.exists(): _remove_owned(stage)


def _named(values):
    result={}
    for value in values:
        name,separator,path=value.partition('=')
        if not separator or not path or name in result: raise BackupError('Named roots use unique NAME=PATH')
        result[name]=path
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest='command',required=True)
    create=commands.add_parser('create'); create.add_argument('storage_root'); create.add_argument('destination')
    create.add_argument('--extra-root',action='append',default=[]); create.add_argument('--code-revision',default='')
    create.add_argument('--database-url'); create.add_argument('--quiesced',action='store_true')
    verify=commands.add_parser('verify'); verify.add_argument('snapshot')
    restore=commands.add_parser('restore'); restore.add_argument('snapshot'); restore.add_argument('destination')
    restore.add_argument('--extra-destination',action='append',default=[])
    args=parser.parse_args(argv)
    try:
        if args.command=='create':
            result=create_snapshot(args.storage_root,args.destination,extra_roots=_named(args.extra_root),
                                   code_revision=args.code_revision,database_url=args.database_url,quiesced=args.quiesced)
        elif args.command=='verify': result=verify_snapshot(args.snapshot)
        else: result=restore_snapshot(args.snapshot,args.destination,extra_destinations=_named(args.extra_destination))
        print(json.dumps(result,ensure_ascii=False)); return 0
    except (BackupError,OSError) as exc:
        print('Backup operation failed: '+str(exc),file=sys.stderr); return 1


if __name__=='__main__':
    raise SystemExit(main())
