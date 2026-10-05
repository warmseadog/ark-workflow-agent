"""Operator-only backup orchestration; never enabled by the web application."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

from .backup import create_snapshot, _no_links
from .backup_transport import (read_key, pack_snapshot, unpack_snapshot, encrypt_file, decrypt_file,
                               upload_verified, download_verified, backup_health, retention_candidates,
                               _json_write, _prefix, _read_remote)


class SystemdService:
    def __init__(self, name):
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_.@-]+\.service',name):
            raise ValueError('Invalid application service unit')
        self.name=name

    def is_active(self):
        result=subprocess.run(['systemctl','show','--property=ActiveState','--value',self.name],
                              capture_output=True,text=True,timeout=30)
        state=result.stdout.strip()
        # is-active's exit code 3 also covers transitions with live writers.
        # Only a stable inactive state proves this service has stopped.
        if result.returncode!=0 or state not in {'active','inactive'}:
            raise RuntimeError('Application service state is transitional or unknown; snapshot refused')
        return state=='active'

    def stop(self):
        subprocess.run(['systemctl','stop',self.name],check=True,capture_output=True,timeout=180)

    def running_release_identity(self):
        from .release_identity import read_release_identity
        result=subprocess.run(['systemctl','show','--property=MainPID','--value',self.name],
                              check=True,capture_output=True,text=True,timeout=30)
        pid=result.stdout.strip()
        if not pid.isdigit() or pid=='0':
            raise ValueError('Cannot identify the running application release')
        root=(Path('/proc')/pid/'cwd').resolve(strict=True)
        return read_release_identity(root)

    def start(self):
        subprocess.run(['systemctl','start',self.name],check=True,capture_output=True,timeout=180)
        if not self.is_active():
            raise RuntimeError('Application service did not restart')


def _configuration(config):
    cfg=dict(config)
    from .release_identity import read_release_identity
    release_root=cfg.get('release_root')
    if release_root is not None and (not isinstance(release_root,str) or not Path(release_root).is_absolute()):
        raise ValueError('release_root must be an absolute deployed release path')
    identity=read_release_identity(release_root)
    if release_root is not None and identity is None:
        raise ValueError('Configured release_root has no verified release manifest')
    if identity is not None:
        if cfg.get('code_revision') and cfg['code_revision']!=identity['source_commit']:
            raise ValueError('Configured code revision differs from the deployed release')
        cfg['code_revision']=identity['source_commit']
        cfg['release_identity']=identity
    if not re.fullmatch(r'(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})',str(cfg.get('code_revision',''))):
        raise ValueError('code_revision must identify the deployed Git commit')
    for field in ('storage_root','snapshot_root','encryption_key_file'):
        value=cfg.get(field)
        if not isinstance(value,str) or not Path(value).is_absolute():
            raise ValueError(f'{field} must be an absolute path')
        cfg[field]=str(_no_links(value))
    storage, output, key = (Path(cfg[field]) for field in ('storage_root','snapshot_root','encryption_key_file'))
    if output==storage or output.is_relative_to(storage) or storage.is_relative_to(output):
        raise ValueError('Snapshot directory must be separate from application data')
    extras=dict(cfg.get('extra_roots') or {})
    app_env={}
    if cfg.get('application_env'):
        from dotenv import dotenv_values
        env_path=_no_links(cfg['application_env'])
        if not env_path.is_file():
            raise ValueError('Application environment file is missing')
        app_env=dotenv_values(env_path, interpolate=False)
        if app_env.get('STORAGE_DIR') and Path(app_env['STORAGE_DIR']).resolve()!=storage:
            raise ValueError('Backup storage root differs from application STORAGE_DIR')
        if not env_path.is_relative_to(storage):
            extras.setdefault('application_env',str(env_path))
    database_url=cfg.get('database_url') or app_env.get('DATABASE_URL') or 'sqlite:///'+(storage/'studio.db').as_posix()
    if not re.fullmatch(r'sqlite(?:\+pysqlite)?:///.+',database_url) or ':memory:' in database_url or '?' in database_url:
        raise ValueError('This backup backend supports file-backed SQLite only')
    runtime={name: app_env.get(name) or cfg.get(name.lower())
             for name in ('WORKFLOW_DB','WORKFLOW_STORAGE')}
    if any(not isinstance(value,str) or not Path(value).is_absolute() for value in runtime.values()):
        raise ValueError('Explicit absolute WORKFLOW_DB and WORKFLOW_STORAGE are required; do not infer them from STORAGE_DIR')
    for name,value in extras.items():
        if not isinstance(value,str) or not Path(value).is_absolute():
            raise ValueError('Extra backup roots must be absolute')
        extras[name]=str(_no_links(value))
    for source in [storage,*map(Path,extras.values())]:
        if key==source or (source.is_dir() and key.is_relative_to(source)):
            raise ValueError('The backup decryption key must be outside every captured source root')
    if key.is_relative_to(output):
        raise ValueError('Keep the encryption key outside the backup archive directory')
    cfg.update(extra_roots=extras,database_url=database_url,runtime=runtime)
    _prefix(cfg['offsite']['prefix'])
    return cfg


@contextmanager
def _environment(values):
    previous={key:os.environ.get(key) for key in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for key,value in previous.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value


@contextmanager
def _exclusive(root):
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    lock=root/'.backup-operation.lock'
    try:
        handle=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    except FileExistsError:
        raise RuntimeError('Another backup operation holds the lock; inspect stale locks before removal') from None
    try:
        os.write(handle,str(os.getpid()).encode())
        yield
    finally:
        os.close(handle)
        lock.unlink()


def make_client(config):
    from .secure_transport import validate_endpoint
    import tos
    remote=config['offsite']
    access=os.environ.get('ARK_BACKUP_ACCESS_KEY','')
    secret=os.environ.get('ARK_BACKUP_SECRET_KEY','')
    if not access or not secret:
        raise ValueError('Dedicated ARK_BACKUP_ACCESS_KEY/ARK_BACKUP_SECRET_KEY are required')
    return tos.TosClientV2(access,secret,endpoint=validate_endpoint(remote['endpoint']),
                           region=remote['region'],connection_time=10,socket_timeout=60,
                           max_retry_count=2)


def _notify(config, report):
    command=config.get('notify_command') or []
    if command:
        if not isinstance(command,list) or not all(isinstance(value,str) and value for value in command):
            raise ValueError('notify_command must be an argv list')
        subprocess.run(command,input=json.dumps(report),text=True,check=True,capture_output=True,timeout=30)


def recover_service(config, *, service=None):
    """systemd ExecStopPost hook after all backup processes have exited.

    A durable intent written before stop makes even SIGKILL recoverable. An
    originally stopped application is never started by this hook.
    """
    from .release_lock import maintenance_lock
    with maintenance_lock(config['snapshot_root']):
        if (Path(config['snapshot_root'])/'.release-maintenance.json').exists():
            raise RuntimeError('Release maintenance requires recovery before backup can restart the service')
        return _recover_service(config, service=service)


def _recover_service(config, *, service=None):
    journal=Path(config['snapshot_root'])/'service-recovery.json'
    if not journal.exists(): return {'recovered':False}
    state=json.loads(journal.read_text(encoding='utf-8'))
    if state.get('service')!=config['service']:
        raise ValueError('Recovery journal belongs to another service')
    if state.get('restart_required') is not True: return {'recovered':False}
    service=service or SystemdService(config['service'])
    # systemctl start is idempotent for an already active unit, and can recover
    # a failed unit too. Snapshot's stricter state predicate must not prevent it.
    service.start()
    state.update(restart_required=False,recovered_at=datetime.now(timezone.utc).isoformat())
    _json_write(journal,state)
    return {'recovered':True}


def run_backup(config, *, service=None, client=None):
    from .release_lock import maintenance_lock
    if not Path(config['snapshot_root']).is_absolute(): raise ValueError('snapshot_root must be absolute')
    root=_no_links(config['snapshot_root'])
    owned=client is None
    cfg=config
    try:
        with maintenance_lock(root), _exclusive(root):
            attempt={'started_at':datetime.now(timezone.utc).isoformat(),'ok':False}
            _json_write(root/'last-attempt.json',attempt)
            try:
                if (root/'.release-maintenance.json').exists():
                    raise RuntimeError('Release maintenance requires recovery before backup can control the service')
                cfg=_configuration(config)
                key=read_key(cfg['encryption_key_file'])
                service=service or SystemdService(cfg['service'])
                # Fail missing cloud credentials before disrupting the application.
                client=make_client(cfg) if owned else client
                active=service.is_active()
                if active and cfg.get('release_identity') is not None and hasattr(service,'running_release_identity'):
                    if service.running_release_identity()!=cfg['release_identity']:
                        raise ValueError('Current release differs from the running application; backup refused')
                journal=root/'service-recovery.json'
                if journal.exists():
                    pending=json.loads(journal.read_text(encoding='utf-8'))
                    if pending.get('restart_required') is True:
                        raise RuntimeError('Recover the interrupted service state before another backup')
                service_state={'service':cfg['service'],'restart_required':active,'started_at':attempt['started_at']}
                _json_write(journal,service_state)
                snapshot=root/('snapshot-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:12])
                try:
                    if active: service.stop()
                    if service.is_active():
                        raise RuntimeError('Application is still writing; snapshot refused')
                    if cfg.get('release_identity') is not None:
                        from .release_identity import read_release_identity
                        if read_release_identity(cfg.get('release_root'))!=cfg['release_identity']:
                            raise ValueError('Release changed while preparing the snapshot')
                    with _environment(cfg['runtime']):
                        manifest=create_snapshot(cfg['storage_root'],snapshot,extra_roots=cfg['extra_roots'],
                            code_revision=cfg.get('code_revision',''),database_url=cfg['database_url'],quiesced=True,
                            release_identity=cfg.get('release_identity'))
                finally:
                    if active:
                        service.start()
                        service_state['restart_required']=False
                        _json_write(journal,service_state)
                encrypted=root/(manifest['snapshot_id']+'.arkb')
                with tempfile.TemporaryDirectory(prefix='.archive-',dir=root) as temp:
                    archive=pack_snapshot(snapshot,Path(temp)/'snapshot.tar.gz')
                    encrypt_file(archive,encrypted,key)
                remote=cfg['offsite']
                receipt=upload_verified(encrypted,client,remote['bucket'],remote['prefix'],manifest,root/'last-success.json')
                _json_write(root/'receipts'/(manifest['snapshot_id']+'.json'),receipt)
                attempt.update(ok=True,snapshot_id=manifest['snapshot_id'],finished_at=datetime.now(timezone.utc).isoformat())
                _json_write(root/'last-attempt.json',attempt)
                return receipt
            except Exception as exc:
                attempt.update(error_type=type(exc).__name__,finished_at=datetime.now(timezone.utc).isoformat())
                _json_write(root/'last-attempt.json',attempt)
                try:
                    _notify(cfg,{'event':'backup_failed',**attempt})
                except Exception:
                    # Original failure remains authoritative; notification failure is visible too.
                    attempt['notification_failed']=True
                    _json_write(root/'last-attempt.json',attempt)
                raise
    finally:
        if owned and client is not None: client.close()


def health(config):
    root=Path(config['snapshot_root'])
    try:
        receipt=json.loads((root/'last-success.json').read_text(encoding='utf-8'))
    except (OSError,ValueError): receipt={}
    report=backup_health(receipt)
    try:
        last=json.loads((root/'last-attempt.json').read_text(encoding='utf-8'))
    except (OSError,ValueError): last={}
    if last and last.get('ok') is not True:
        report.update(ok=False,reason='Most recent backup attempt did not succeed')
    report['notification_configured']=bool(config.get('notify_command'))
    return report


def list_receipts(config,client):
    remote=config['offsite']; prefix=_prefix(remote['prefix']); token=None; seen=set(); receipts=[]
    while True:
        page=client.list_objects_type2(remote['bucket'],prefix=prefix,continuation_token=token,max_keys=1000)
        for item in page.contents:
            name=item.key[len(prefix):] if item.key.startswith(prefix) else ''
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}\.receipt\.json',name): continue
            stream=client.get_object(remote['bucket'],item.key)
            try: raw=stream.read(65537)
            finally:
                if callable(getattr(stream,'close',None)): stream.close()
            if len(raw)>65536: raise ValueError('Oversized offsite receipt')
            receipt=json.loads(raw)
            ident=name[:-len('.receipt.json')]
            if (receipt.get('schema')!='ark-offsite-backup' or receipt.get('version')!=1
                    or receipt.get('verified') is not True or receipt.get('snapshot_id')!=ident
                    or receipt.get('bucket')!=remote['bucket'] or receipt.get('key')!=prefix+ident+'.arkb'
                    or not re.fullmatch(r'[a-f0-9]{64}',str(receipt.get('sha256','')))
                    or type(receipt.get('size')) is not int or receipt['size']<0):
                raise ValueError('Invalid offsite receipt')
            receipts.append(receipt)
        if not page.is_truncated: return receipts
        token=page.next_continuation_token
        if not token or token in seen: raise ValueError('Invalid object listing pagination')
        seen.add(token)


def retrieve_snapshot(config,client,snapshot_id,destination):
    receipt=next((r for r in list_receipts(config,client) if r['snapshot_id']==snapshot_id),None)
    if receipt is None: raise ValueError('No verified remote snapshot has this identifier')
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.retrieve-',dir=destination.parent) as temp:
        encrypted=download_verified(receipt,client,Path(temp)/'snapshot.arkb')
        plaintext=decrypt_file(encrypted,Path(temp)/'snapshot.tar.gz',read_key(config['encryption_key_file']))
        return unpack_snapshot(plaintext,destination)


def prune(config,client,*,apply=False):
    """Dry-run by default; only verified archives under this dedicated prefix."""
    candidates=retention_candidates(list_receipts(config,client))
    if apply:
        for item in candidates:
            if _read_remote(client,item['bucket'],item['key'])!=(item['sha256'],item['size']):
                raise ValueError('Refusing to prune an unverified archive')
            # Remove its success marker first. Interrupted prune leaves an orphan,
            # never an apparently usable receipt pointing to a missing archive.
            client.delete_object(item['bucket'],item['key'][:-5]+'.receipt.json')
            client.delete_object(item['bucket'],item['key'])
    return {'applied':apply,'candidates':[item['snapshot_id'] for item in candidates]}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True,type=Path)
    commands=parser.add_subparsers(dest='command',required=True)
    commands.add_parser('backup'); commands.add_parser('check'); commands.add_parser('list')
    commands.add_parser('recover-service')
    get=commands.add_parser('retrieve'); get.add_argument('snapshot_id');get.add_argument('destination',type=Path)
    remove=commands.add_parser('prune');remove.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    try:
        config=json.loads(args.config.read_text(encoding='utf-8'))
        if args.command=='backup': result=run_backup(config)
        elif args.command=='recover-service': result=recover_service(config)
        elif args.command=='check':
            result=health(config)
            if not result['ok']: _notify(config,{'event':'backup_unhealthy',**result})
            print(json.dumps(result));return 0 if result['ok'] else 1
        else:
            _prefix(config['offsite']['prefix'])
            client=make_client(config)
            try:
                if args.command=='list': result=list_receipts(config,client)
                elif args.command=='retrieve': result={'snapshot':str(retrieve_snapshot(config,client,args.snapshot_id,args.destination))}
                else: result=prune(config,client,apply=args.apply)
            finally: client.close()
        print(json.dumps(result,ensure_ascii=False));return 0
    except Exception as exc:
        # SDK exception bodies can include request credentials; never print them.
        print(json.dumps({'ok':False,'error_type':type(exc).__name__,'message':'Backup operation failed; inspect configuration and private status files.'}))
        return 1


if __name__=='__main__':
    raise SystemExit(main())
