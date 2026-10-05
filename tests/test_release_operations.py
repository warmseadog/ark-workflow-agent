import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import pytest
from app.release_bundle import build_bundle, unpack_bundle


def api():
    assert importlib.util.find_spec('app.release_operations'), 'Release activation implementation missing'
    return importlib.import_module('app.release_operations')


class Host:
    """Only systemd, nginx and the current symlink are replaced; data is real."""
    def __init__(self,current,env):
        self.current=current;self.env=env;self.open=True;self.active=True;self.events=[]
        self.fail_new=False;self.fail_old=False;self.previous=current
    def runtime_env(self): return self.env
    def dependencies(self): return {'example':'1.2'}
    def current_release(self): return self.current
    def is_active(self): return self.active
    def probe(self,target): pass
    def maintenance(self,enabled): self.open=not enabled; self.events.append('close' if enabled else 'open')
    def stop(self): self.active=False
    def start(self):
        self.active=True
        if self.fail_old and self.current==self.previous: raise RuntimeError('old service failed')
    def switch(self,target): self.current=target
    def ready(self):
        self.events.append('ready')
        if self.fail_new and self.current!=self.previous: raise RuntimeError('new service failed')


@pytest.fixture
def setup(tmp_path):
    root=tmp_path/'root';root.mkdir();repo=tmp_path/'repo';repo.mkdir()
    for name,data in {'app/__init__.py':'','app/main.py':'value=1', 'requirements.txt':'example==1.2',
                      'deploy/ecs/requirements-linux.lock.txt':'example==1.2'}.items():
        path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(data)
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Test','-c','user.email=t@example.invalid','commit','-qm','test'],check=True)
    old=build_bundle(repo,tmp_path/'old.tar.gz');previous=root/'releases'/old['revision']
    unpack_bundle(tmp_path/'old.tar.gz',previous)
    (repo/'app/main.py').write_text('value=2')
    new=build_bundle(repo,tmp_path/'new.tar.gz')
    storage=root/'data';storage.mkdir();(storage/'workflow').mkdir()
    for name in ('studio.db','workflow.db','users/u/nested.sqlite3'):
        path=storage/name;path.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as db: db.execute('CREATE TABLE records(value TEXT)');db.execute("INSERT INTO records VALUES ('preserve')")
    (storage/'media.mp4').write_bytes(b'media')
    envfile=root/'config'/'app.env';envfile.parent.mkdir();envfile.write_text('DUMMY=value')
    nginx=root/'nginx.conf';nginx.write_text('server { listen 8443 ssl; }')
    cfg={'root':str(root),'service':'ark-video-workflow.service','storage_root':str(storage),
         'snapshot_root':str(tmp_path/'snapshots'),'application_env':str(envfile),'nginx_config':str(nginx),
         'database_url':'sqlite:///'+(storage/'studio.db').as_posix(),
         'workflow_db':str(storage/'workflow.db'),'workflow_storage':str(storage/'workflow'),'extra_roots':{}}
    env={'STORAGE_DIR':str(storage),'DATABASE_URL':cfg['database_url'],
         'WORKFLOW_DB':cfg['workflow_db'],'WORKFLOW_STORAGE':cfg['workflow_storage']}
    return cfg,Host(previous,env),tmp_path/'new.tar.gz',old,new


def test_deploy_keeps_complete_verified_snapshot_and_opens_only_after_readiness(setup):
    operations=api();cfg,host,bundle,old,new=setup
    report=operations.ReleaseManager(cfg,host=host).deploy(bundle,expected_revision=old['revision'])
    assert host.current.name==new['revision'] and host.open
    assert host.events[-2:]==['ready','open']
    snapshot=Path(report['snapshot']); manifest=json.loads((snapshot/'manifest.json').read_text())
    captured={(row['root'],row['path']) for row in manifest['files']}
    assert ('storage','media.mp4') in captured
    assert ('storage','users/u/nested.sqlite3') in captured
    assert any(path=='app.env' for _,path in captured)
    assert any(path=='nginx.conf' for _,path in captured)
    assert report['snapshot_id']==manifest['snapshot_id']
    assert report['previous_revision']==old['revision']
    assert not (Path(cfg['snapshot_root'])/'.release-maintenance.json').exists()


def test_failed_new_and_old_start_keep_public_closed_and_journal(setup):
    operations=api();cfg,host,bundle,old,new=setup;host.fail_new=True;host.fail_old=True
    with pytest.raises(RuntimeError):
        operations.ReleaseManager(cfg,host=host).deploy(bundle,expected_revision=old['revision'])
    assert not host.open
    assert (Path(cfg['snapshot_root'])/'.release-maintenance.json').exists()


def test_new_failure_can_reopen_only_after_old_readiness(setup):
    operations=api();cfg,host,bundle,old,new=setup;host.fail_new=True
    with pytest.raises(RuntimeError):
        operations.ReleaseManager(cfg,host=host).deploy(bundle,expected_revision=old['revision'])
    assert host.current.name==old['revision'] and host.open
    assert host.events[-2:]==['ready','open']


def test_actual_service_storage_mismatch_blocks_before_maintenance(setup):
    operations=api();cfg,host,bundle,old,new=setup;host.env['STORAGE_DIR']+='-different'
    with pytest.raises(ValueError,match='[Cc]onfig|[Ss]torage'):
        operations.ReleaseManager(cfg,host=host).deploy(bundle,expected_revision=old['revision'])
    assert host.events==[] and host.open


def test_missing_dependency_blocks_before_maintenance(setup):
    operations=api();cfg,host,bundle,old,new=setup;host.dependencies=lambda:{}
    with pytest.raises(ValueError,match='[Dd]ependenc'):
        operations.ReleaseManager(cfg,host=host).deploy(bundle,expected_revision=old['revision'])
    assert host.events==[] and host.open


def test_rollback_preserves_new_business_data_instead_of_restoring_old_database(setup):
    operations=api();cfg,host,bundle,old,new=setup;manager=operations.ReleaseManager(cfg,host=host)
    manager.deploy(bundle,expected_revision=old['revision'])
    with sqlite3.connect(Path(cfg['storage_root'])/'studio.db') as db: db.execute("INSERT INTO records VALUES ('new-business')")
    manager.rollback(expected_revision=new['revision'])
    assert host.current.name==old['revision'] and host.open
    with sqlite3.connect(Path(cfg['storage_root'])/'studio.db') as db:
        assert db.execute('SELECT value FROM records').fetchall()==[('preserve',),('new-business',)]


def test_downgrade_with_new_paused_record_is_denied_before_switch(setup):
    operations=api();cfg,host,bundle,old,new=setup;manager=operations.ReleaseManager(cfg,host=host)
    manager.deploy(bundle,expected_revision=old['revision'])
    with sqlite3.connect(Path(cfg['storage_root'])/'studio.db') as db:
        db.execute('CREATE TABLE portrait_photos(status TEXT)');db.execute("INSERT INTO portrait_photos VALUES ('stopped')")
    with pytest.raises(ValueError,match='[Dd]owngrade'):
        manager.rollback(expected_revision=new['revision'])
    assert host.current.name==new['revision']


def test_stale_expected_revision_is_rejected_without_closing_traffic(setup):
    operations=api();cfg,host,bundle,old,new=setup
    with pytest.raises(ValueError,match='[Rr]evision'):
        operations.ReleaseManager(cfg,host=host).deploy(bundle,expected_revision='stale')
    assert host.events==[]


def test_recover_requires_recorded_predecessor_and_waits_before_reopening(setup):
    operations=api();cfg,host,bundle,old,new=setup;host.fail_new=True;host.fail_old=True
    manager=operations.ReleaseManager(cfg,host=host)
    with pytest.raises(RuntimeError):manager.deploy(bundle,expected_revision=old['revision'])
    host.fail_new=False;host.fail_old=False
    with pytest.raises(ValueError,match='[Rr]ecovery'):
        manager.recover(expected_revision='wrong')
    assert not host.open
    manager.recover(expected_revision=old['revision'])
    assert host.current.name==old['revision'] and host.events[-2:]==['ready','open']


def test_release_refuses_to_replace_an_active_backup_lock(setup):
    operations=api();cfg,host,bundle,old,new=setup
    from app.release_lock import maintenance_lock
    with maintenance_lock(cfg['snapshot_root']):
        with pytest.raises(RuntimeError):
            operations.ReleaseManager(cfg,host=host).deploy(bundle,expected_revision=old['revision'])
    assert host.events==[]


def test_snapshot_preserves_pre_maintenance_nginx_content(setup):
    operations=api();cfg,host,bundle,old,new=setup;nginx=Path(cfg['nginx_config'])
    original=nginx.read_bytes();maintenance=host.maintenance
    def change_proxy(enabled):
        maintenance(enabled)
        nginx.write_bytes(b'server { return 503; listen 8443 ssl; }' if enabled else original)
    host.maintenance=change_proxy
    report=operations.ReleaseManager(cfg,host=host).deploy(bundle,expected_revision=old['revision'])
    manifest=json.loads((Path(report['snapshot'])/'manifest.json').read_text())
    entries=[entry for entry in manifest['files'] if entry['root']=='nginx_before_release']
    assert len(entries)==1
    saved=Path(report['snapshot'])/'roots'/'nginx_before_release'/entries[0]['path']
    assert saved.read_bytes()==original
    assert report['nginx_restore_destination']==cfg['nginx_config']


def test_proxy_cleanup_failure_recloses_traffic_before_rollback(setup):
    operations=api();cfg,host,bundle,old,new=setup;maintenance=host.maintenance;failed=False
    def fail_after_open(enabled):
        nonlocal failed
        maintenance(enabled)
        if not enabled and not failed:
            failed=True;raise OSError('reload succeeded, cleanup failed')
    host.maintenance=fail_after_open
    def start():
        assert not host.open, 'rollback must not restart under open traffic'
        host.active=True
    host.start=start
    with pytest.raises(OSError):
        operations.ReleaseManager(cfg,host=host).deploy(bundle,expected_revision=old['revision'])
    assert host.current.name==old['revision'] and host.open


def test_ecs_probe_runs_as_application_user_in_an_isolated_environment(setup,monkeypatch):
    operations=api();cfg,_,_,_,_=setup;host=operations.ECSHost(cfg);calls=[]
    monkeypatch.setenv('SEEDANCE_API_KEY','must-not-inherit')
    def run(*args,**kwargs):
        calls.append((args,kwargs))
    host.run=run
    host.probe(Path(cfg['root'])/'candidate')
    invocation=next((args,kwargs) for args,kwargs in calls if '-c' in args)
    args,kwargs=invocation
    assert args[:4]==('runuser','-u','ark-video-workflow','--')
    assert 'SEEDANCE_API_KEY' not in kwargs['env']
    assert kwargs['env']['PYTHON_DOTENV_DISABLED']=='1'


def test_one_time_legacy_transition_can_roll_back_without_forging_new_identity(setup):
    operations=api();cfg,host,bundle,old,new=setup
    (host.current/'release-manifest.json').unlink();(host.current/'REVISION').write_text('historical-label\n')
    cfg['legacy_source_commit']=old['source_commit']
    legacy=operations.release_description(host.current)['revision']
    manager=operations.ReleaseManager(cfg,host=host)
    manager.deploy(bundle,expected_revision=legacy)
    manager.rollback(expected_revision=new['revision'])
    assert operations.release_description(host.current)['revision']==legacy
    assert not (host.current/'release-receipt.json').exists()


def test_inspect_refuses_symlink_version_that_is_not_the_running_process(setup,monkeypatch):
    operations=api();cfg,old_host,_,_,_=setup;host=operations.ECSHost(cfg)
    host.current_release=lambda:old_host.current
    host.ctl=lambda *args:'123'
    host.dependencies=lambda:{'example':'1.2'}
    read_bytes=Path.read_bytes;resolve=Path.resolve
    def fake_bytes(path):
        if path==Path('/proc/123/environ'):
            return b'\0'.join((key+'='+value).encode() for key,value in old_host.env.items())
        return read_bytes(path)
    def fake_resolve(path,*args,**kwargs):
        if path==Path('/proc/123/cwd'):return Path(cfg['root'])/'releases'/'different-running-version'
        return resolve(path,*args,**kwargs)
    monkeypatch.setattr(Path,'read_bytes',fake_bytes);monkeypatch.setattr(Path,'resolve',fake_resolve)
    with pytest.raises(ValueError,match='[Rr]unning|[Pp]rocess'):
        operations.ReleaseManager(cfg,host=host).inspect()


@pytest.mark.skipif(__import__('os').name=='nt',reason='POSIX directory permissions')
def test_unpack_directories_are_accessible_to_service_under_private_umask(setup,tmp_path):
    import os,stat
    _,_,bundle,_,_=setup;old=os.umask(0o077)
    try: unpack_bundle(bundle,tmp_path/'private-release')
    finally: os.umask(old)
    assert stat.S_IMODE((tmp_path/'private-release'/'app').stat().st_mode)==0o755
