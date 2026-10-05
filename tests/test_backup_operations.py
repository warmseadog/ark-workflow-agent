import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3

import pytest

from tests.test_backup_transport import ObjectStore


class Service:
    def __init__(self, active=True):
        self.active=active
        self.events=[]
    def is_active(self):
        return self.active
    def stop(self):
        self.events.append('stop'); self.active=False
    def start(self):
        self.events.append('start'); self.active=True


def config(tmp_path):
    root=tmp_path/'data'; root.mkdir()
    (root/'assets').mkdir()
    (root/'assets'/'face.jpg').write_bytes(b'audit media')
    (root/'workflow').mkdir()
    for filename in ('studio.db','workflow.db'):
        with sqlite3.connect(root/filename) as db:
            db.execute('CREATE TABLE sample(value TEXT)')
            db.execute("INSERT INTO sample VALUES ('kept')")
    key=tmp_path/'backup.key'
    key.write_bytes(base64.urlsafe_b64encode(b'k'*32))
    key.chmod(0o600)
    return {'storage_root':str(root), 'snapshot_root':str(tmp_path/'backups'),
            'code_revision':'a'*40,
            'encryption_key_file':str(key), 'service':'ark-video-workflow.service',
            'workflow_db':str(root/'workflow.db'),'workflow_storage':str(root/'workflow'),
            'offsite':{'bucket':'dedicated-test-backups', 'prefix':'ark-backups/',
                       'endpoint':'https://tos-cn-beijing.volces.com', 'region':'cn-beijing'},
            'extra_roots':{}, 'notify_command':[]}


def test_backup_restores_original_service_before_network_and_records_verified_success(tmp_path):
    from app.backup_operations import run_backup
    cfg=config(tmp_path); service=Service()
    class Store(ObjectStore):
        def put_object_from_file(self, *args, **kwargs):
            assert service.active, 'service should restart before offsite upload'
            super().put_object_from_file(*args, **kwargs)
    receipt=run_backup(cfg, service=service, client=Store())
    assert service.events == ['stop','start']
    assert receipt['verified'] is True
    state=Path(cfg['snapshot_root'])/'last-success.json'
    assert json.loads(state.read_text())['snapshot_id'] == receipt['snapshot_id']


def test_snapshot_failure_restarts_service_without_false_success(tmp_path, monkeypatch):
    from app import backup_operations as operations
    cfg=config(tmp_path); service=Service()
    def fail(*args, **kwargs):
        assert not service.active
        raise OSError('simulated snapshot failure')
    monkeypatch.setattr(operations,'create_snapshot',fail)
    with pytest.raises(OSError):
        operations.run_backup(cfg,service=service,client=ObjectStore())
    assert service.active and service.events == ['stop','start']
    assert not (Path(cfg['snapshot_root'])/'last-success.json').exists()
    assert json.loads((Path(cfg['snapshot_root'])/'last-attempt.json').read_text())['ok'] is False


def test_backup_of_stopped_service_does_not_start_it_and_key_cannot_be_in_snapshot(tmp_path):
    from app.backup_operations import run_backup
    cfg=config(tmp_path); service=Service(active=False)
    run_backup(cfg,service=service,client=ObjectStore())
    assert not service.active and service.events == []
    key=Path(cfg['storage_root'])/'key'
    key.write_bytes(base64.urlsafe_b64encode(b'k'*32));key.chmod(0o600)
    cfg['encryption_key_file']=str(key)
    with pytest.raises(ValueError):
        run_backup(cfg,service=service,client=ObjectStore())


def test_health_fails_immediately_after_bad_attempt_even_with_recent_success(tmp_path):
    from app.backup_operations import health
    cfg=config(tmp_path); state=Path(cfg['snapshot_root']);state.mkdir()
    (state/'last-success.json').write_text(json.dumps({'verified':True,'created_at':datetime.now(timezone.utc).isoformat()}))
    (state/'last-attempt.json').write_text(json.dumps({'ok':False,'error_type':'NetworkFailure'}))
    assert health(cfg)['ok'] is False


def test_configured_external_database_must_be_part_of_backup(tmp_path):
    from app.backup_operations import run_backup
    cfg=config(tmp_path); service=Service()
    env=tmp_path/'app.env'
    env.write_text('STORAGE_DIR='+cfg['storage_root']+'\nDATABASE_URL=postgresql://redacted-placeholder\n')
    cfg['application_env']=str(env)
    with pytest.raises(ValueError):
        run_backup(cfg,service=service,client=ObjectStore())
    assert service.events == []


def test_preflight_failure_is_recorded_and_health_cannot_keep_previous_success(tmp_path):
    from app.backup_operations import run_backup, health
    cfg=config(tmp_path); root=Path(cfg['snapshot_root']);root.mkdir()
    (root/'last-success.json').write_text(json.dumps({'verified':True,'created_at':datetime.now(timezone.utc).isoformat()}))
    Path(cfg['encryption_key_file']).write_bytes(b'invalid key')
    service=Service()
    with pytest.raises(ValueError):
        run_backup(cfg,service=service,client=ObjectStore())
    assert service.events==[]
    assert health(cfg)['ok'] is False


def test_key_inside_lexically_disguised_extra_root_is_rejected(tmp_path):
    from app.backup_operations import run_backup
    cfg=config(tmp_path)
    (tmp_path/'safe').mkdir();(tmp_path/'secrets').mkdir()
    key=tmp_path/'secrets'/'backup.key'
    key.write_bytes(base64.urlsafe_b64encode(b'k'*32));key.chmod(0o600)
    cfg['encryption_key_file']=str(key)
    cfg['extra_roots']={'secrets':str(tmp_path/'safe'/'..'/'secrets')}
    service=Service()
    with pytest.raises(ValueError):
        run_backup(cfg,service=service,client=ObjectStore())
    assert service.events==[]


def test_custom_storage_cannot_guess_workflow_database_defaults(tmp_path):
    from app.backup_operations import run_backup
    cfg=config(tmp_path);cfg.pop('workflow_db');cfg.pop('workflow_storage')
    service=Service()
    with pytest.raises(ValueError):
        run_backup(cfg,service=service,client=ObjectStore())
    assert service.events==[]


def test_backup_refuses_missing_deployed_code_revision(tmp_path):
    from app.backup_operations import run_backup
    cfg=config(tmp_path);cfg.pop('code_revision')
    service=Service()
    with pytest.raises(ValueError):run_backup(cfg,service=service,client=ObjectStore())
    assert service.events==[]


@pytest.mark.parametrize('state',['activating','deactivating','reloading','failed','unknown'])
def test_systemd_transition_is_not_treated_as_quiesced(monkeypatch,state):
    from types import SimpleNamespace
    from app import backup_operations as operations
    def command(args,**kwargs):
        if 'is-active' in args:return SimpleNamespace(returncode=3,stdout='')
        return SimpleNamespace(returncode=0,stdout=state+'\n')
    monkeypatch.setattr(operations.subprocess,'run',command)
    with pytest.raises(RuntimeError):operations.SystemdService('ark-video-workflow.service').is_active()


def test_interrupted_backup_recovers_only_originally_running_service(tmp_path):
    from app.backup_operations import recover_service
    cfg=config(tmp_path);root=Path(cfg['snapshot_root']);root.mkdir()
    journal=root/'service-recovery.json'
    journal.write_text(json.dumps({'service':cfg['service'],'restart_required':True}))
    service=Service(active=False)
    recover_service(cfg,service=service)
    assert service.events==['start']
    assert json.loads(journal.read_text())['restart_required'] is False
    service=Service(active=False)
    recover_service(cfg,service=service)
    assert service.events==[]


def test_service_recovery_can_start_a_failed_originally_active_unit(tmp_path):
    from app.backup_operations import recover_service
    cfg=config(tmp_path);root=Path(cfg['snapshot_root']);root.mkdir()
    (root/'service-recovery.json').write_text(json.dumps({'service':cfg['service'],'restart_required':True}))
    class FailedService(Service):
        def is_active(self):raise RuntimeError('failed is not safe for snapshot')
    service=FailedService(active=False)
    recover_service(cfg,service=service)
    assert service.events==['start']


def test_verified_remote_retrieve_and_fresh_restore_drill(tmp_path):
    from types import SimpleNamespace
    from app.backup_operations import run_backup, retrieve_snapshot, list_receipts
    from app.backup import restore_snapshot
    cfg=config(tmp_path)
    with sqlite3.connect(Path(cfg['storage_root'])/'workflow.db') as db:
        db.execute('CREATE TABLE stage_tasks(id TEXT PRIMARY KEY,status TEXT,provider_state_json TEXT)')
        db.execute('INSERT INTO stage_tasks VALUES (?,?,?)',('task-old','queued','{"task_id":"accepted-remote-123"}'))
    class Store(ObjectStore):
        def list_objects_type2(self,bucket,**kwargs):
            return SimpleNamespace(contents=[SimpleNamespace(key=key) for key in self.objects],is_truncated=False)
    store=Store();receipt=run_backup(cfg,service=Service(),client=store)
    assert list_receipts(cfg,store)==[receipt]
    snapshot=retrieve_snapshot(cfg,store,receipt['snapshot_id'],tmp_path/'downloaded-snapshot')
    destination=tmp_path/'recovered'
    report=restore_snapshot(snapshot,destination)
    assert report['quarantined_records']==1
    assert (destination/'assets'/'face.jpg').read_bytes()==b'audit media'
    assert (destination/'workflow').is_dir()
    assert (destination/'.restore-hold.json').is_file()
    with sqlite3.connect(destination/'workflow.db') as db:
        row=db.execute('SELECT status,provider_state_json FROM stage_tasks').fetchone()
        assert row==('restore_held','{"task_id":"accepted-remote-123"}')
        original=json.loads(db.execute('SELECT original_json FROM restore_quarantine').fetchone()[0])
        assert original['status']=='queued'
    with pytest.raises(ValueError):
        retrieve_snapshot(cfg,store,receipt['snapshot_id'],snapshot)
