"""Offline, deterministic full release artifacts. No application/runtime import."""
from __future__ import annotations
import gzip
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tarfile


class ReleaseError(ValueError):
    pass


FIELDS = ('schema','version','source_commit','capabilities','files','dependencies','source_dirty','untracked_files')
ASSET_ROOT = 'storage/attached_local_face_mosaic_v3/local-face-mosaic-tracking/'
ASSET_FILES = tuple(ASSET_ROOT+name for name in (
    'scripts/batch_face_mosaic.py','scripts/hair_mosaic.py','scripts/process_all_faces_mosaic.py',
    'scripts/process_primary_face_mosaic.py','models/face_detection_yunet_2023mar.onnx',
    'models/selfie_multiclass_256x256.tflite','requirements.txt'))
ROOTS = ('app','tests','ui','deploy/ecs','requirements.txt',*ASSET_FILES)


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':')).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def safe_path(name):
    if (not isinstance(name,str) or not name or '\\' in name or ':' in name or
            any(ord(c)<32 for c in name) or any(p in {'','.','..'} for p in name.split('/'))):
        raise ReleaseError('Unsafe release path')
    if any(p.rstrip(' .')!=p or p.split('.')[0].upper() in
           {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))}
           for p in name.split('/')):
        raise ReleaseError('Unsafe release filename')
    return name


def no_links(path):
    path=Path(os.path.abspath(path))
    for part in (path,*path.parents):
        if part.exists() or part.is_symlink():
            info=part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
                raise ReleaseError('Release paths cannot contain symlinks/reparse points')
    return path


def allowed(name):
    safe_path(name)
    if not (name=='requirements.txt' or name in ASSET_FILES or name.startswith(('app/','tests/','ui/','deploy/ecs/'))):
        raise ReleaseError('File outside release source roots')
    parts=Path(name).parts
    if any(p in {'__pycache__','.git','node_modules'} for p in parts):
        raise ReleaseError('Generated directory in release')
    if (Path(name).name in {'.env','credentials.json','id_rsa','id_ed25519'} or
            Path(name).suffix.lower() in {'.pem','.key','.p12','.pfx','.db','.sqlite','.sqlite3'}):
        raise ReleaseError('Secret or database file cannot enter a source release')


def _git(repo,*args):
    return subprocess.check_output(['git','-C',str(repo),*args])


def parse_dependencies(text):
    result={}
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('#'): continue
        match=re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+!-]+)',line)
        if not match: raise ReleaseError('Dependencies must be exact, unconditional lock entries')
        name=re.sub(r'[-_.]+','-',match[1]).lower()
        if name in result: raise ReleaseError('Duplicate dependency lock entry')
        result[name]=match[2]
    if not result: raise ReleaseError('Dependency lock is empty')
    return result


def runtime_dependencies():
    return {re.sub(r'[-_.]+','-',item.metadata['Name']).lower():item.version
            for item in importlib.metadata.distributions() if item.metadata.get('Name')}


def assert_declared_dependencies(text,locked):
    from packaging.requirements import Requirement, InvalidRequirement
    environment={'sys_platform':'linux','platform_system':'Linux','os_name':'posix'}
    for line in text.splitlines():
        line=line.strip()
        if not line or line.startswith('#'): continue
        try: requirement=Requirement(line)
        except InvalidRequirement as exc: raise ReleaseError('Unsupported dependency declaration') from exc
        if requirement.url: raise ReleaseError('URL dependency declarations require a reviewed lock')
        if requirement.marker and not requirement.marker.evaluate(environment): continue
        name=re.sub(r'[-_.]+','-',requirement.name).lower()
        if name not in locked or not requirement.specifier.contains(locked[name],prereleases=True):
            raise ReleaseError('Declared dependency missing or incompatible in Linux lock: '+name)


def assert_dependencies(manifest,installed=None):
    installed=runtime_dependencies() if installed is None else installed
    bad=[name for name,version in manifest['dependencies'].items() if installed.get(name)!=version]
    if bad: raise ReleaseError('Dependency mismatch; use a reviewed compatible runtime: '+', '.join(sorted(bad)))


def validate_manifest(manifest):
    if not isinstance(manifest,dict) or manifest.get('schema')!='ark-release' or manifest.get('version')!=1:
        raise ReleaseError('Invalid release manifest schema')
    if set(manifest)!={*FIELDS,'content_sha256','revision'}:
        raise ReleaseError('Invalid release manifest fields')
    if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',str(manifest['source_commit'])):
        raise ReleaseError('Invalid release source commit')
    if not isinstance(manifest['files'],dict) or not manifest['files']:
        raise ReleaseError('Empty release manifest')
    for name,entry in manifest['files'].items():
        allowed(name)
        if (not isinstance(entry,dict) or set(entry)!={'sha256','size','mode'} or
                not re.fullmatch('[0-9a-f]{64}',str(entry['sha256'])) or
                type(entry['size']) is not int or entry['size']<0 or entry['mode'] not in (0o644,0o755)):
            raise ReleaseError('Invalid release file metadata')
    if (not isinstance(manifest['capabilities'],list) or
            any(not isinstance(x,str) for x in manifest['capabilities']) or
            not isinstance(manifest['dependencies'],dict) or not manifest['dependencies'] or
            type(manifest['source_dirty']) is not bool or not isinstance(manifest['untracked_files'],list)):
        raise ReleaseError('Invalid release metadata')
    if any(name not in manifest['files'] for name in manifest['untracked_files']):
        raise ReleaseError('Untracked selection outside release')
    expected=digest(canonical({key:manifest[key] for key in FIELDS}))
    if manifest['content_sha256']!=expected or manifest['revision']!=manifest['source_commit']+'+'+expected[:16]:
        raise ReleaseError('Release content identity mismatch')
    return manifest


def build_bundle(repo,destination,*,include_untracked=()):
    repo=no_links(repo); destination=no_links(destination)
    if destination.exists(): raise ReleaseError('Bundle destination exists')
    tracked=set(_git(repo,'ls-files','-z','--',*ROOTS).decode().split('\0'))-{''}
    untracked=set(_git(repo,'ls-files','--others','--exclude-standard','-z','--',*ROOTS).decode().split('\0'))-{''}
    selected=set(include_untracked)
    for name in selected: allowed(name)
    if selected-untracked: raise ReleaseError('Approved untracked selection does not match working tree')
    if selected&set(ASSET_FILES): raise ReleaseError('Bundled runtime resources must be tracked')
    if untracked-selected: raise ReleaseError('Untracked source files require explicit approval: '+', '.join(sorted(untracked-selected)))
    payload={}
    modes={}
    for name in sorted(tracked|selected):
        allowed(name); path=no_links(repo/name)
        if not path.exists(): continue  # A full release faithfully represents deletions.
        if not path.is_file(): raise ReleaseError('Release source must be a regular file')
        payload[name]=path.read_bytes()
        modes[name]=0o755 if path.stat().st_mode&0o111 else 0o644
    lock='deploy/ecs/requirements-linux.lock.txt'
    if lock not in payload or 'requirements.txt' not in payload:
        raise ReleaseError('Release must include requirements and Linux dependency lock')
    if 'app/local_mosaic.py' in payload and not set(ASSET_FILES)<=set(payload):
        raise ReleaseError('Bundled mosaic runtime resources are incomplete')
    dependencies=parse_dependencies(payload[lock].decode())
    assert_declared_dependencies(payload['requirements.txt'].decode(),dependencies)
    if ASSET_ROOT+'requirements.txt' in payload:
        assert_declared_dependencies(payload[ASSET_ROOT+'requirements.txt'].decode(),dependencies)
    caps=json.loads(payload.get('deploy/ecs/release-capabilities.json',b'[]'))
    manifest={'schema':'ark-release','version':1,'source_commit':_git(repo,'rev-parse','HEAD').decode().strip(),
              'capabilities':sorted(set(caps)),
              'files':{name:{'sha256':digest(data),'size':len(data),'mode':modes[name]} for name,data in payload.items()},
              'dependencies':dependencies,
              'source_dirty':bool(_git(repo,'status','--porcelain','--untracked-files=all','--',*ROOTS).strip()),
              'untracked_files':sorted(selected)}
    manifest['content_sha256']=digest(canonical(manifest))
    manifest['revision']=manifest['source_commit']+'+'+manifest['content_sha256'][:16]
    validate_manifest(manifest)
    payload['release-manifest.json']=canonical(manifest)+b'\n'
    payload['REVISION']=(manifest['revision']+'\n').encode()
    destination.parent.mkdir(parents=True,exist_ok=True)
    with destination.open('xb') as output, gzip.GzipFile(fileobj=output,filename='',mtime=0,mode='wb') as compressed:
        with tarfile.open(fileobj=compressed,mode='w',format=tarfile.USTAR_FORMAT) as archive:
            for name,data in sorted(payload.items()):
                info=tarfile.TarInfo(name);info.size=len(data);info.mode=modes.get(name,0o644)
                archive.addfile(info,io.BytesIO(data))
    return manifest


def verify_release(root):
    root=no_links(root)
    try: manifest=validate_manifest(json.loads((root/'release-manifest.json').read_text(encoding='utf-8')))
    except (OSError,ValueError,KeyError,TypeError) as exc: raise ReleaseError('Invalid release manifest') from exc
    if (root/'REVISION').read_text().strip()!=manifest['revision']:
        raise ReleaseError('REVISION does not match release manifest')
    actual=set()
    for item in root.rglob('*'):
        no_links(item)
        relative=item.relative_to(root)
        if '__pycache__' in relative.parts: continue
        if item.is_file() and relative.as_posix() not in {'REVISION','release-manifest.json','release-receipt.json'}:
            actual.add(relative.as_posix())
    if actual!=set(manifest['files']): raise ReleaseError('Release content file set mismatch')
    for name,entry in manifest['files'].items():
        data=no_links(root/name).read_bytes()
        if len(data)!=entry['size'] or digest(data)!=entry['sha256']:
            raise ReleaseError('Release content hash mismatch: '+name)
    locked=parse_dependencies((root/'deploy/ecs/requirements-linux.lock.txt').read_text())
    if locked!=manifest['dependencies']: raise ReleaseError('Dependency manifest differs from packaged lock')
    assert_declared_dependencies((root/'requirements.txt').read_text(),locked)
    if (root/(ASSET_ROOT+'requirements.txt')).exists():
        assert_declared_dependencies((root/(ASSET_ROOT+'requirements.txt')).read_text(),locked)
    return manifest


def unpack_bundle(bundle,destination):
    destination=no_links(destination)
    if destination.exists(): raise ReleaseError('Release destination exists')
    with tarfile.open(no_links(bundle),'r:gz') as archive:
        members=archive.getmembers(); names=[safe_path(m.name) for m in members]
        if len(names)!=len(set(names)) or any(not m.isfile() for m in members):
            raise ReleaseError('Archive must contain unique regular files')
        if 'release-manifest.json' not in names: raise ReleaseError('Archive manifest missing')
        manifest=validate_manifest(json.loads(archive.extractfile('release-manifest.json').read()))
        if set(names)!=set(manifest['files'])|{'REVISION','release-manifest.json'}:
            raise ReleaseError('Archive content file set mismatch')
        payload={}
        for member in members:
            data=archive.extractfile(member).read()
            if member.name in manifest['files']:
                entry=manifest['files'][member.name]
                if digest(data)!=entry['sha256'] or len(data)!=entry['size']:
                    raise ReleaseError('Archive content hash mismatch')
            payload[member.name]=data
        if payload['REVISION']!=(manifest['revision']+'\n').encode():
            raise ReleaseError('Archive revision mismatch')
    destination.mkdir(parents=True)
    for name,data in payload.items():
        path=destination/name; path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        path.chmod(manifest['files'].get(name,{}).get('mode',0o644))
    for directory in (destination,*(p for p in destination.rglob('*') if p.is_dir())):
        directory.chmod(0o755)
    verify_release(destination)
    return manifest
