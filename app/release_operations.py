"""Explicit ECS release/rollback commands; no SSH, dependency installation or DB restore."""
from __future__ import annotations
import argparse
from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from .release_bundle import (ReleaseError, assert_dependencies, build_bundle, canonical, digest,
                             no_links, unpack_bundle, verify_release)
from .release_compatibility import assert_rollback_compatible, discover_databases
from .release_identity import read_release_identity
from .release_lock import maintenance_lock


def _write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    with temporary.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
    temporary.chmod(0o600);os.replace(temporary,path)


@contextmanager
def _environment(values):
    old={key:os.environ.get(key) for key in values}
    try:
        os.environ.update(values);yield
    finally:
        for key,value in old.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value


def release_description(root):
    root=no_links(root)
    identity=read_release_identity(root)
    if identity:
        verify_release(root)
        return identity
    # One-time transition from historical releases: identify their actual bytes.
    files={}
    for folder in ('app','ui'):
        if (root/folder).is_dir():
            for path in (root/folder).rglob('*'):
                no_links(path)
                if path.is_file() and '__pycache__' not in path.parts:
                    files[path.relative_to(root).as_posix()]=digest(path.read_bytes())
    if not files: raise ReleaseError('Previous release has no identifiable application files')
    return {'revision':'legacy-sha256:'+digest(canonical(files)), 'capabilities':[], 'dependencies':{}}


class ECSHost:
    def __init__(self,cfg):
        self.cfg=cfg;self.root=Path(cfg['root']);self.service=cfg['service']
        if self.service!='ark-video-workflow.service': raise ReleaseError('Only the reviewed ECS service is supported')
        self.python=self.root/'venv/bin/python'
        self.proxy_original=Path(cfg['snapshot_root'])/'.release-nginx-original'
    def run(self,*args,**kwargs):
        return subprocess.run(args,check=True,capture_output=True,text=True,timeout=180,**kwargs)
    def ctl(self,*args): return self.run('systemctl',*args).stdout.strip()
    def current_release(self):
        current=(self.root/'current').resolve(strict=True)
        if current.parent!=self.root/'releases': raise ReleaseError('Current release is outside managed releases')
        return current
    def is_active(self):
        state=self.ctl('show',self.service,'-p','ActiveState','--value')
        if state not in {'active','inactive','failed'}: raise ReleaseError('Service state is transitional')
        return state=='active'
    def runtime_env(self):
        pid=self.ctl('show',self.service,'-p','MainPID','--value')
        if not pid.isdigit() or pid=='0': raise ReleaseError('A running service is required for configuration preflight')
        if (Path('/proc')/pid/'cwd').resolve(strict=True)!=self.current_release():
            raise ReleaseError('Running process directory differs from current release; reconcile before deployment')
        values=dict(item.split(b'=',1) for item in (Path('/proc')/pid/'environ').read_bytes().split(b'\0') if b'=' in item)
        self.library_path=os.fsdecode(values.get(b'LD_LIBRARY_PATH',b''))
        return {key:os.fsdecode(values[os.fsencode(key)]) for key in
                ('STORAGE_DIR','DATABASE_URL','WORKFLOW_DB','WORKFLOW_STORAGE') if os.fsencode(key) in values}
    def dependencies(self):
        code="import importlib.metadata as m,json,re; print(json.dumps({re.sub('[-_.]+','-',d.metadata['Name']).lower():d.version for d in m.distributions() if d.metadata.get('Name')}))"
        return json.loads(self.run(str(self.python),'-I','-c',code).stdout)
    def stop(self):
        self.run('systemctl','stop',self.service)
        if self.is_active(): raise ReleaseError('Service still active; cannot snapshot')
    def start(self): self.run('systemctl','start',self.service)
    def switch(self,target):
        target=no_links(target)
        if target.parent!=self.root/'releases': raise ReleaseError('Target outside managed releases')
        temporary=self.root/('.current-release-'+uuid.uuid4().hex)
        temporary.symlink_to(target);os.replace(temporary,self.root/'current')
    def ready(self):
        for _ in range(30):
            try:
                pid=self.ctl('show',self.service,'-p','MainPID','--value')
                process_root=(Path('/proc')/pid/'cwd').resolve(strict=True)
                with urllib.request.urlopen('http://127.0.0.1:18080/healthz',timeout=2) as response:
                    if self.is_active() and process_root==self.current_release() and response.status==200: return
            except (OSError,subprocess.CalledProcessError,ReleaseError): pass
            time.sleep(1)
        raise ReleaseError('Service readiness failed; maintenance remains enabled')
    def maintenance(self,enabled):
        nginx=no_links(self.cfg['nginx_config'])
        if enabled:
            if not self.proxy_original.exists():
                original=nginx.read_bytes()
                if original.count(b'server {')!=1 or b'listen 8443 ' not in original:
                    raise ReleaseError('Unsupported nginx configuration')
                self.proxy_original.write_bytes(original);self.proxy_original.chmod(0o600)
            original=self.proxy_original.read_bytes()
            pid=self.ctl('show','nginx','-p','MainPID','--value')
            workers=[line.split()[0] for line in self.run('ps','--ppid',pid,'-o','pid=,args=').stdout.splitlines()
                     if 'nginx: worker process' in line]
            nginx.write_bytes(original.replace(b'server {',b'server {\n    return 503;',1))
        else:
            if not self.proxy_original.exists(): raise ReleaseError('Original nginx configuration is missing')
            nginx.write_bytes(self.proxy_original.read_bytes())
        self.run('nginx','-t');self.run('systemctl','reload','nginx')
        if enabled:
            deadline=time.monotonic()+45
            while any((Path('/proc')/pid).exists() for pid in workers):
                if time.monotonic()>=deadline: raise ReleaseError('Requests did not drain; maintenance retained')
                time.sleep(.2)
        else: self.proxy_original.unlink()
    def probe(self,target):
        # No credentials, inherited DB paths, lifespan or external network in preflight.
        with tempfile.TemporaryDirectory(prefix='ark-release-probe-') as directory:
            env={key:os.environ[key] for key in ('PATH','LD_LIBRARY_PATH') if key in os.environ}
            env['LD_LIBRARY_PATH']=getattr(self,'library_path','')
            env.update(PYTHON_DOTENV_DISABLED='1',PYTHONDONTWRITEBYTECODE='1',STORAGE_DIR=directory,
                       DATABASE_URL='sqlite:///'+directory+'/studio.db',WORKFLOW_DB=directory+'/workflow.db',
                       WORKFLOW_STORAGE=directory+'/workflow',SEEDANCE_MODE='mock',APP_AUTH_ENABLED='false')
            code="import socket; socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('network disabled')); from app.main import app; from fastapi.testclient import TestClient; c=TestClient(app); assert c.get('/healthz').status_code==200; c.close()"
            self.run('chown','ark-video-workflow:ark-video-workflow',directory)
            self.run('runuser','-u','ark-video-workflow','--',str(self.python),'-c',code,cwd=target,env=env)


class ReleaseManager:
    def __init__(self,config,*,host=None):
        self.cfg=dict(config)
        for key in ('root','storage_root','snapshot_root','application_env','nginx_config','workflow_db','workflow_storage'):
            value=self.cfg.get(key)
            if not isinstance(value,str) or not Path(value).is_absolute(): raise ReleaseError('Explicit absolute configuration required: '+key)
            self.cfg[key]=str(no_links(value))
        self.root=Path(self.cfg['root']);self.storage=Path(self.cfg['storage_root']);self.backups=Path(self.cfg['snapshot_root'])
        if self.backups==self.storage or self.backups.is_relative_to(self.storage) or self.storage.is_relative_to(self.backups):
            raise ReleaseError('Snapshot root must be independent of application storage')
        self.host=host or ECSHost(self.cfg)
        self.marker=self.backups/'.release-maintenance.json'
        self.extras={name:str(no_links(path)) for name,path in self.cfg.get('extra_roots',{}).items()}
        for name,key in (('application_env','application_env'),('nginx_config','nginx_config')):
            path=Path(self.cfg[key])
            covered=path.is_relative_to(self.storage) or any(path==Path(p) or (Path(p).is_dir() and path.is_relative_to(Path(p))) for p in self.extras.values())
            if not covered:self.extras[name]=str(path)
        self.database_url=self.cfg.get('database_url') or 'sqlite:///'+(self.storage/'studio.db').as_posix()
        match=re.fullmatch(r'sqlite(?:\+pysqlite)?:///(.+)',self.database_url)
        if not match or ':memory:' in self.database_url or '?' in self.database_url or '#' in self.database_url:
            raise ReleaseError('Only configured file-backed SQLite deployments are supported')
        self.databases=[no_links(match[1]),Path(self.cfg['workflow_db'])]
        self.runtime={'WORKFLOW_DB':self.cfg['workflow_db'],'WORKFLOW_STORAGE':self.cfg['workflow_storage']}
    def _compatibility(self,target):
        return assert_rollback_compatible(target,self.storage,database_paths=self.databases,extra_roots=self.extras.values())
    def _idle(self):
        active={'production_runs':('queued','running'),'video_jobs':('queued','running'),
                'stage_tasks':('queued','running'),'portrait_photos':('queued','uploading','submitting','processing','uncertain'),
                'portrait_group_requests':('submitting','uncertain'),'production_playbacks':('queued','processing'),
                'production_previews':('queued','running')}
        for path in discover_databases(self.storage,database_paths=self.databases,extra_roots=self.extras.values()):
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table,states in active.items():
                    if table in tables and db.execute('SELECT 1 FROM '+table+' WHERE status IN ('+','.join('?' for _ in states)+') LIMIT 1',states).fetchone():
                        raise ReleaseError('Active tasks prevent release/rollback; keep submissions paused until they finish')
    def _config_matches_runtime(self):
        actual=self.host.runtime_env()
        expected={'STORAGE_DIR':str(self.storage),'DATABASE_URL':self.database_url,**self.runtime}
        for key,value in expected.items():
            if key=='DATABASE_URL': match=actual.get(key,'sqlite:///'+(self.storage/'studio.db').as_posix())==value
            else: match=key in actual and Path(actual[key]).resolve()==Path(value).resolve()
            if not match: raise ReleaseError('Configured storage differs from running service: '+key)
        for path in [*self.databases,Path(self.cfg['workflow_storage'])]:
            if not path.exists() or not (path==self.storage or path.is_relative_to(self.storage) or
                    any(path==Path(p) or (Path(p).is_dir() and path.is_relative_to(Path(p))) for p in self.extras.values())):
                raise ReleaseError('Configured data is missing or outside snapshot coverage')
    def inspect(self):
        current=self.host.current_release();self._config_matches_runtime()
        return {'release':str(current),'identity':release_description(current),'dependencies':self.host.dependencies()}
    def _snapshot(self,previous,nginx_original):
        from .backup import create_snapshot
        identity=read_release_identity(previous)
        commit=identity['source_commit'] if identity else self.cfg.get('legacy_source_commit','')
        if not re.fullmatch('[0-9a-f]{40}|[0-9a-f]{64}',commit):
            raise ReleaseError('Legacy transition requires a verified legacy_source_commit')
        path=self.backups/('before-release-'+uuid.uuid4().hex)
        extras={**self.extras,'nginx_before_release':str(nginx_original)}
        with _environment(self.runtime):
            manifest=create_snapshot(self.storage,path,extra_roots=extras,code_revision=commit,
                                     release_identity=identity,database_url=self.database_url,quiesced=True)
        return {'snapshot':str(path),'snapshot_id':manifest['snapshot_id'],
                'snapshot_manifest_sha256':digest((path/'manifest.json').read_bytes()),
                'nginx_restore_root':'nginx_before_release','nginx_restore_destination':self.cfg['nginx_config']}
    def _activate(self,target,*,expected_revision):
        with maintenance_lock(self.backups):
            if self.marker.exists(): raise ReleaseError('Unfinished release maintenance; use recover after inspection')
            previous=self.host.current_release();before=release_description(previous)
            if before['revision']!=expected_revision: raise ReleaseError('Live revision changed; inspect again')
            target_description=release_description(target)
            self._config_matches_runtime();installed=self.host.dependencies()
            if target_description['dependencies']: assert_dependencies(target_description,installed)
            if before['dependencies']: assert_dependencies(before,installed)
            self._compatibility(target);self._idle()
            self.host.probe(target)
            if self.host.current_release()!=previous or release_description(previous)['revision']!=before['revision']:
                raise ReleaseError('Live revision changed during preflight')
            self._config_matches_runtime()
            nginx_original=self.backups/('nginx-before-'+uuid.uuid4().hex+'.conf')
            with nginx_original.open('xb') as stream:
                stream.write(Path(self.cfg['nginx_config']).read_bytes());stream.flush();os.fsync(stream.fileno())
            nginx_original.chmod(0o600)
            state={'previous':str(previous),'previous_revision':before['revision'],'target':str(target),
                   'revision':target_description['revision'],'runtime_dependencies':installed,'phase':'maintenance'}
            _write(self.marker,state)
            stopped=False
            try:
                self.host.maintenance(True);self._idle()
                self.host.stop();stopped=True;self._idle()
                state.update(self._snapshot(previous,nginx_original));_write(self.marker,state)
                if self.host.dependencies()!=installed: raise ReleaseError('Shared runtime changed during release')
                self._compatibility(target);self.host.switch(target);self.host.start();self.host.ready()
                if release_description(target)['revision']!=state['revision']: raise ReleaseError('Activated release content changed')
                state['phase']='verified';_write(self.marker,state)
                _write(self.backups/'release-receipts'/(digest(state['revision'].encode())+'.json'),state)
                if read_release_identity(target) is not None:
                    _write(target/'release-receipt.json',state)
                self.host.maintenance(False);self.marker.unlink()
                return state
            except Exception:
                # No automatic database restore. New paid IDs and business writes survive.
                try:
                    try:
                        self.host.maintenance(True)
                    except Exception:
                        self.host.stop()
                        raise
                    self._idle()
                    if self.host.dependencies()!=installed: raise ReleaseError('Shared runtime changed; rollback denied')
                    self._compatibility(previous)
                    if release_description(previous)['revision']!=before['revision']: raise ReleaseError('Previous release content changed')
                    if stopped:
                        self.host.stop();self.host.switch(previous);self.host.start()
                    self.host.ready();self.host.maintenance(False);self.marker.unlink()
                except Exception as rollback_error:
                    state['phase']='recovery_required';state['failure_type']=type(rollback_error).__name__
                    _write(self.marker,state)
                    raise RuntimeError('Release failed and safe rollback was not verified; maintenance retained') from rollback_error
                raise
    def deploy(self,bundle,*,expected_revision,check_only=False):
        # A full new directory prevents deleted or unknown old code surviving overlays.
        temporary=self.root/'releases'/('.stage-'+uuid.uuid4().hex)
        manifest=unpack_bundle(bundle,temporary)
        target=self.root/'releases'/manifest['revision']
        if target.exists():
            if verify_release(target)!=manifest: raise ReleaseError('Existing release differs from bundle')
            from .backup import _remove_owned
            _remove_owned(temporary)
        else: temporary.rename(target)
        if check_only:
            previous=self.host.current_release()
            if release_description(previous)['revision']!=expected_revision: raise ReleaseError('Live revision changed')
            self._config_matches_runtime();assert_dependencies(manifest,self.host.dependencies())
            self._compatibility(target);self._idle();self.host.probe(target)
            return {'revision':manifest['revision'],'staged_release':str(target),'checks':'passed'}
        return self._activate(target,expected_revision=expected_revision)
    def rollback(self,*,expected_revision):
        current=self.host.current_release()
        current_revision=release_description(current)['revision']
        receipt=json.loads((self.backups/'release-receipts'/(digest(current_revision.encode())+'.json')).read_text())
        target=no_links(receipt['previous'])
        if target.parent!=self.root/'releases' or release_description(target)['revision']!=receipt['previous_revision']:
            raise ReleaseError('Rollback release does not match recorded predecessor')
        if self.host.dependencies()!=receipt['runtime_dependencies']:
            raise ReleaseError('Shared dependency runtime changed since deployment; rollback denied')
        return self._activate(target,expected_revision=expected_revision)
    def recover(self,*,expected_revision):
        with maintenance_lock(self.backups):
            state=json.loads(self.marker.read_text());target=no_links(state['previous'])
            if target.parent!=self.root/'releases' or release_description(target)['revision']!=expected_revision or expected_revision!=state['previous_revision']:
                raise ReleaseError('Recovery target differs from recorded previous revision')
            if self.host.dependencies()!=state['runtime_dependencies']: raise ReleaseError('Shared runtime changed; recovery denied')
            self._idle();self._compatibility(target)
            self.host.stop();self.host.switch(target);self.host.start();self.host.ready()
            self.host.maintenance(False);self.marker.unlink()
            return {'recovered_revision':expected_revision}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='mode',required=True)
    build=sub.add_parser('build');build.add_argument('--repo',default=str(Path(__file__).resolve().parents[1]));build.add_argument('--output',required=True)
    build.add_argument('--include-untracked',action='append',default=[])
    for name in ('inspect','check','deploy','rollback','recover','verify'):
        item=sub.add_parser(name);item.add_argument('--config',required=True)
        if name in {'check','deploy'}: item.add_argument('--bundle',required=True)
        if name in {'check','deploy','rollback','recover'}: item.add_argument('--expected-revision',required=True)
    args=parser.parse_args(argv)
    try:
        if args.mode=='build': result=build_bundle(args.repo,args.output,include_untracked=args.include_untracked)
        else:
            manager=ReleaseManager(json.loads(Path(args.config).read_text()))
            if args.mode=='inspect': result=manager.inspect()
            elif args.mode in {'check','deploy'}: result=manager.deploy(args.bundle,expected_revision=args.expected_revision,check_only=args.mode=='check')
            elif args.mode in {'rollback','recover'}: result=getattr(manager,args.mode)(expected_revision=args.expected_revision)
            else:
                result=manager.inspect();manager.host.ready()
                assert_dependencies(result['identity'],result['dependencies'])
        print(json.dumps(result,ensure_ascii=False,sort_keys=True));return 0
    except (ValueError,RuntimeError,OSError,subprocess.CalledProcessError) as error:
        print(f'Release refused: {error}',file=sys.stderr);return 1
