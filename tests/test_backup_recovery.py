"""Offline disaster recovery contracts, using real SQLite/WAL and media bytes."""
import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest


def core():
    assert importlib.util.find_spec('app.backup') is not None, 'Offline backup engine is not implemented'
    return importlib.import_module('app.backup')


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    for key in ('DATABASE_URL', 'WORKFLOW_DB', 'WORKFLOW_STORAGE'):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def durable(tmp_path):
    root = tmp_path/'source'
    tenant = root/'users'/'alice'
    (tenant/'assets').mkdir(parents=True)
    (tenant/'assets'/'source.mp4').write_bytes(b'original-video')
    (tenant/'work'/'running').mkdir(parents=True)
    (tenant/'work'/'running'/'base.mp4').write_bytes(b'continuation-base')
    (tenant/'outputs').mkdir()
    (tenant/'outputs'/'done.mp4').write_bytes(b'finished-video')
    (root/'private').mkdir()
    (root/'private'/'settings.json').write_text('{"api_key":"synthetic-test-secret"}')
    (tenant/'cache').mkdir()
    (tenant/'cache'/'rebuild.mp4').write_bytes(b'rebuildable')
    db = sqlite3.connect(tenant/'production.db')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
        CREATE TABLE production_assets(id TEXT PRIMARY KEY,path TEXT,size INTEGER,sha256 TEXT);
        CREATE TABLE production_runs(id TEXT PRIMARY KEY,status TEXT,provider_task_id TEXT,result_url TEXT,snapshot TEXT);
        CREATE TABLE production_continuations(run_id TEXT PRIMARY KEY,data TEXT);
        CREATE TABLE portrait_photos(id TEXT PRIMARY KEY,status TEXT,remote_id TEXT);
        CREATE TABLE production_playbacks(id TEXT PRIMARY KEY,status TEXT);
    ''')
    media = tenant/'assets'/'source.mp4'
    db.execute('INSERT INTO production_assets VALUES (?,?,?,?)', ('asset',str(media),14,hashlib.sha256(b'original-video').hexdigest()))
    db.execute('INSERT INTO production_runs VALUES (?,?,?,?,?)', ('running','queued','accepted-remote-id','https://provider.invalid/result',json.dumps({'prompt':str(media)})))
    db.execute('INSERT INTO production_runs VALUES (?,?,?,?,?)', ('done','succeeded','finished-remote-id',None,'{}'))
    db.execute('INSERT INTO production_continuations VALUES (?,?)', ('running',json.dumps({'base_ready':True,'provider_task_id':'continuation-remote'})))
    db.execute('INSERT INTO portrait_photos VALUES (?,?,?)', ('photo','uncertain','official-asset-id'))
    db.execute('INSERT INTO production_playbacks VALUES (?,?)', ('running','queued'))
    db.commit()
    # Keep this connection open: committed rows can still live in the WAL.
    yield root
    db.close()


def create(durable, tmp_path, **kwargs):
    destination = tmp_path/'snapshot'
    manifest = core().create_snapshot(durable,destination,quiesced=True,code_revision='revision-test',**kwargs)
    return destination, manifest


def test_roundtrip_keeps_wal_media_configs_and_quarantines_old_work(durable,tmp_path):
    backup = core()
    snapshot, manifest = create(durable,tmp_path)
    assert manifest['code_revision'] == 'revision-test'
    assert backup.verify_snapshot(snapshot)['snapshot_id'] == manifest['snapshot_id']
    restored = tmp_path/'restored'
    receipt = backup.restore_snapshot(snapshot,restored)
    tenant = restored/'users'/'alice'
    assert (tenant/'assets'/'source.mp4').read_bytes() == b'original-video'
    assert (tenant/'work'/'running'/'base.mp4').read_bytes() == b'continuation-base'
    assert (tenant/'outputs'/'done.mp4').read_bytes() == b'finished-video'
    assert json.loads((restored/'private'/'settings.json').read_text())['api_key'] == 'synthetic-test-secret'
    assert not (tenant/'cache').exists()
    hold = json.loads((restored/'.restore-hold.json').read_text())
    assert hold['snapshot_id'] == manifest['snapshot_id'] == receipt['snapshot_id']
    with sqlite3.connect(tenant/'production.db') as db:
        assert db.execute('SELECT path FROM production_assets').fetchone()[0] == str(tenant/'assets'/'source.mp4')
        assert db.execute("SELECT status,provider_task_id,result_url FROM production_runs WHERE id='running'").fetchone() == ('restore_held','accepted-remote-id','https://provider.invalid/result')
        assert db.execute("SELECT status FROM production_runs WHERE id='done'").fetchone()[0] == 'succeeded'
        assert db.execute('SELECT status,remote_id FROM portrait_photos').fetchone() == ('restore_held','official-asset-id')
        assert db.execute('SELECT status FROM production_playbacks').fetchone()[0] == 'restore_held'
        original = json.loads(db.execute("SELECT original_json FROM restore_quarantine WHERE table_name='production_runs' AND record_id='running'").fetchone()[0])
        assert original['status'] == 'queued'
        # Prompts are user data, even when they happen to equal a filesystem path.
        assert json.loads(db.execute("SELECT snapshot FROM production_runs WHERE id='running'").fetchone()[0])['prompt'] == str(durable/'users'/'alice'/'assets'/'source.mp4')
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'


def test_snapshot_requires_explicit_quiescence(durable,tmp_path):
    with pytest.raises(ValueError,match='quiesc'):
        core().create_snapshot(durable,tmp_path/'snapshot')
    assert not (tmp_path/'snapshot').exists()


@pytest.mark.parametrize('mutation',['damage','missing','extra'])
def test_verify_rejects_corruption_missing_and_unlisted_files(durable,tmp_path,mutation):
    snapshot,_ = create(durable,tmp_path)
    media = snapshot/'roots'/'storage'/'users'/'alice'/'assets'/'source.mp4'
    if mutation == 'damage': media.write_bytes(b'corrupt')
    elif mutation == 'missing': media.unlink()
    else: (snapshot/'unexpected').write_bytes(b'not-listed')
    with pytest.raises(ValueError): core().verify_snapshot(snapshot)


def test_reference_validation_rejects_media_removed_from_manifest_too(durable,tmp_path):
    snapshot,_ = create(durable,tmp_path)
    path = snapshot/'manifest.json'
    manifest = json.loads(path.read_text())
    manifest['files'] = [item for item in manifest['files'] if not item['path'].endswith('assets/source.mp4')]
    path.write_text(json.dumps(manifest))
    (snapshot/'roots'/'storage'/'users'/'alice'/'assets'/'source.mp4').unlink()
    with pytest.raises(ValueError,match='referenc|media'): core().verify_snapshot(snapshot)


def test_missing_source_media_never_publishes_success(durable,tmp_path):
    (durable/'users'/'alice'/'assets'/'source.mp4').unlink()
    with pytest.raises(ValueError,match='referenc|media'): create(durable,tmp_path)
    assert not (tmp_path/'snapshot').exists()


def test_postgres_and_uncovered_external_paths_fail_closed(durable,tmp_path,monkeypatch):
    with pytest.raises(ValueError,match='SQLite|backend|PostgreSQL'):
        create(durable,tmp_path,database_url='postgresql://example.invalid/db')
    outside = tmp_path/'external-workflow.db'
    with sqlite3.connect(outside) as db: db.execute('CREATE TABLE records(value TEXT)')
    monkeypatch.setenv('WORKFLOW_DB',str(outside))
    with pytest.raises(ValueError,match='cover|root|external'):
        create(durable,tmp_path)


def test_named_database_and_workflow_media_roundtrip(tmp_path,monkeypatch):
    storage = tmp_path/'storage'; storage.mkdir()
    workflow = tmp_path/'workflow'; workflow.mkdir()
    media = workflow/'source.mp4'; media.write_bytes(b'workflow-source')
    database = tmp_path/'external.db'
    with sqlite3.connect(database) as db:
        db.executescript('CREATE TABLE source_assets(id TEXT,kind TEXT,uri TEXT); CREATE TABLE stage_tasks(id TEXT,status TEXT,output_json TEXT,provider_task_id TEXT);')
        db.execute('INSERT INTO source_assets VALUES (?,?,?)',('source','upload',str(media)))
        db.execute('INSERT INTO stage_tasks VALUES (?,?,?,?)',('task','running',json.dumps({'artifact':{'path':str(media)}}),'remote-workflow-id'))
    monkeypatch.setenv('WORKFLOW_DB',str(database)); monkeypatch.setenv('WORKFLOW_STORAGE',str(workflow))
    backup = core(); snapshot = tmp_path/'snapshot'
    backup.create_snapshot(storage,snapshot,extra_roots={'workflow_db':database,'workflow':workflow},quiesced=True,database_url='sqlite:///'+database.as_posix())
    new_db,new_media = tmp_path/'new.db',tmp_path/'new-workflow'
    backup.restore_snapshot(snapshot,tmp_path/'restored',extra_destinations={'workflow_db':new_db,'workflow':new_media})
    with sqlite3.connect(new_db) as db:
        assert db.execute('SELECT uri FROM source_assets').fetchone()[0] == str(new_media/'source.mp4')
        row = db.execute('SELECT status,output_json,provider_task_id FROM stage_tasks').fetchone()
        assert row[0] == 'restore_held' and row[2] == 'remote-workflow-id'
        assert json.loads(row[1])['artifact']['path'] == str(new_media/'source.mp4')


def test_restore_refuses_existing_and_overlapping_targets(durable,tmp_path):
    snapshot,_ = create(durable,tmp_path)
    existing=tmp_path/'existing'; existing.mkdir()
    with pytest.raises(ValueError,match='exist|fresh'):
        core().restore_snapshot(snapshot,existing)
    with pytest.raises(ValueError,match='overlap|inside|root'):
        core().restore_snapshot(snapshot,snapshot/'restored')
    assert not (snapshot/'restored').exists()


def test_overlapping_source_roots_and_snapshot_destination_are_refused(durable,tmp_path):
    with pytest.raises(ValueError,match='overlap|inside|root'):
        core().create_snapshot(durable,durable/'backups'/'snapshot',quiesced=True)
    with pytest.raises(ValueError,match='overlap|root'):
        create(durable,tmp_path,extra_roots={'nested':durable/'private'})


def test_manifest_traversal_cannot_escape_restore(durable,tmp_path):
    snapshot,_ = create(durable,tmp_path)
    manifest_path=snapshot/'manifest.json'; manifest=json.loads(manifest_path.read_text())
    manifest['files'][0]['path']='../../outside'
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError): core().restore_snapshot(snapshot,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()
    assert not (tmp_path/'outside').exists()


def test_symlink_is_rejected_without_following_it(durable,tmp_path):
    outside=tmp_path/'outside'; outside.write_bytes(b'do-not-copy')
    link=durable/'linked'
    try: link.symlink_to(outside)
    except OSError: pytest.skip('Symlink privilege unavailable')
    with pytest.raises(ValueError,match='link|reparse'): create(durable,tmp_path)


def test_interrupted_copy_does_not_publish_partial_snapshot(durable,tmp_path,monkeypatch):
    backup=core()
    def interrupted(*args,**kwargs): raise OSError('simulated disk failure')
    monkeypatch.setattr(backup.shutil,'copy2',interrupted)
    with pytest.raises((OSError,ValueError)): create(durable,tmp_path)
    assert not (tmp_path/'snapshot').exists()
    assert not list(tmp_path.glob('.snapshot.partial-*'))


def test_restore_preserves_empty_directories_and_private_directory_permissions(durable,tmp_path):
    (durable/'private').chmod(0o700)
    (durable/'empty').mkdir()
    snapshot,manifest=create(durable,tmp_path)
    restored=tmp_path/'restored'; core().restore_snapshot(snapshot,restored)
    assert (restored/'empty').is_dir()
    assert stat.S_IMODE((restored/'private').stat().st_mode) == stat.S_IMODE((durable/'private').stat().st_mode)


def test_restore_detects_corruption_during_copy_before_publication(durable,tmp_path,monkeypatch):
    backup=core(); snapshot,_=create(durable,tmp_path)
    original=backup.shutil.copy2
    def corrupt(source,target,*args,**kwargs):
        result=original(source,target,*args,**kwargs)
        if Path(target).name=='source.mp4': Path(target).write_bytes(b'damaged-in-transfer')
        return result
    monkeypatch.setattr(backup.shutil,'copy2',corrupt)
    with pytest.raises(ValueError,match='corrupt|hash|copy'):
        backup.restore_snapshot(snapshot,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


def test_database_integrity_is_checked_even_if_manifest_hash_matches(durable,tmp_path):
    snapshot,manifest=create(durable,tmp_path)
    entry=next(item for item in manifest['files'] if item['sqlite'])
    path=snapshot/'roots'/entry['root']/entry['path']
    data=bytearray(path.read_bytes()); data[100:110]=b'\xff'*10; path.write_bytes(data)
    entry['sha256']=hashlib.sha256(data).hexdigest()
    (snapshot/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='SQLite|integrity'): core().verify_snapshot(snapshot)


def test_media_root_prefix_does_not_authorize_adjacent_directory(durable,tmp_path):
    foreign=tmp_path/'source-neighbor'/'media.mp4'; foreign.parent.mkdir(); foreign.write_bytes(b'original-video')
    with sqlite3.connect(durable/'users'/'alice'/'production.db') as db:
        db.execute('UPDATE production_assets SET path=?',(str(foreign),))
    with pytest.raises(ValueError,match='outside|root|media'): create(durable,tmp_path)


def test_offline_cli_create_verify_restore_and_failure_exit(tmp_path):
    storage=tmp_path/'storage'; storage.mkdir(); (storage/'data.txt').write_text('durable')
    snapshot=tmp_path/'snapshot'; restored=tmp_path/'restored'
    command=[sys.executable,'-B','-m','app.backup']
    denied=subprocess.run(command+['create',str(storage),str(snapshot)],capture_output=True,text=True)
    assert denied.returncode!=0 and not snapshot.exists()
    made=subprocess.run(command+['create',str(storage),str(snapshot),'--quiesced'],capture_output=True,text=True)
    assert made.returncode==0,made.stderr
    checked=subprocess.run(command+['verify',str(snapshot)],capture_output=True,text=True)
    assert checked.returncode==0,checked.stderr
    result=subprocess.run(command+['restore',str(snapshot),str(restored)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    assert (restored/'data.txt').read_text()=='durable'
    assert (restored/'.restore-hold.json').exists()


def test_readonly_media_restore_with_original_permissions(durable,tmp_path):
    media=durable/'users'/'alice'/'assets'/'source.mp4'
    media.chmod(0o444)
    try:
        snapshot,_=create(durable,tmp_path)
        restored=tmp_path/'restored'; core().restore_snapshot(snapshot,restored)
        actual=restored/'users'/'alice'/'assets'/'source.mp4'
        assert actual.read_bytes()==b'original-video'
        assert not (actual.stat().st_mode & stat.S_IWUSR)
        actual.chmod(0o666)
        (snapshot/'roots'/'storage'/'users'/'alice'/'assets'/'source.mp4').chmod(0o666)
    finally: media.chmod(0o666)


def test_overlapping_manifest_source_roots_fail_closed(durable,tmp_path):
    extra=tmp_path/'extra'; extra.mkdir()
    snapshot,manifest=create(durable,tmp_path,extra_roots={'extra':extra})
    manifest['roots']['extra']['source']=str(durable/'private')
    (snapshot/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='overlap|root'): core().verify_snapshot(snapshot)


def test_restore_quarantines_legacy_jobs_and_pending_portrait_sessions(tmp_path):
    storage=tmp_path/'source'; storage.mkdir()
    with sqlite3.connect(storage/'state.db') as db:
        db.executescript('CREATE TABLE video_jobs(id TEXT PRIMARY KEY,status TEXT,provider_task_id TEXT); CREATE TABLE sessions(id TEXT PRIMARY KEY,data TEXT);')
        db.execute('INSERT INTO video_jobs VALUES (?,?,?)',('job','running','remote-job'))
        db.execute('INSERT INTO sessions VALUES (?,?)',('portrait-session',json.dumps({'status':'pending','token':'synthetic'})))
    snapshot,_=create(storage,tmp_path)
    restored=tmp_path/'restored'; core().restore_snapshot(snapshot,restored)
    with sqlite3.connect(restored/'state.db') as db:
        assert db.execute('SELECT status,provider_task_id FROM video_jobs').fetchone()==('restore_held','remote-job')
        assert json.loads(db.execute('SELECT data FROM sessions').fetchone()[0])['status']=='restore_held'
        assert db.execute('SELECT COUNT(*) FROM restore_quarantine').fetchone()[0]==2


@pytest.mark.parametrize('state',['waiting','resolving','needs_attention','future_pending_state'])
def test_restore_quarantines_every_unfinished_person_preparation(tmp_path,state):
    storage=tmp_path/'source';storage.mkdir()
    with sqlite3.connect(storage/'production.db') as db:
        db.execute('CREATE TABLE production_person_preparations(run_id TEXT PRIMARY KEY,data TEXT)')
        db.execute('INSERT INTO production_person_preparations VALUES (?,?)',('run',json.dumps({'state':state})))
        db.execute('INSERT INTO production_person_preparations VALUES (?,?)',('done',json.dumps({'state':'ready'})))
    snapshot,_=create(storage,tmp_path)
    restored=tmp_path/'restored';core().restore_snapshot(snapshot,restored)
    with sqlite3.connect(restored/'production.db') as db:
        assert json.loads(db.execute("SELECT data FROM production_person_preparations WHERE run_id='run'").fetchone()[0])['state']=='restore_held'
        assert json.loads(db.execute("SELECT data FROM production_person_preparations WHERE run_id='done'").fetchone()[0])['state']=='ready'
        assert db.execute('SELECT COUNT(*) FROM restore_quarantine').fetchone()[0]==1


def test_named_media_file_can_restore_to_a_new_filename(tmp_path):
    storage=tmp_path/'source'; storage.mkdir()
    media=tmp_path/'external.mp4'; media.write_bytes(b'external-media')
    with sqlite3.connect(storage/'production.db') as db:
        db.execute('CREATE TABLE production_assets(id TEXT,path TEXT)')
        db.execute('INSERT INTO production_assets VALUES (?,?)',('a',str(media)))
    snapshot,_=create(storage,tmp_path,extra_roots={'media':media})
    restored=tmp_path/'restored'; target=tmp_path/'renamed.mp4'
    core().restore_snapshot(snapshot,restored,extra_destinations={'media':target})
    with sqlite3.connect(restored/'production.db') as db:
        assert db.execute('SELECT path FROM production_assets').fetchone()[0]==str(target)
    assert target.read_bytes()==b'external-media'
