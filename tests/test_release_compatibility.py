import importlib
import json
import sqlite3
import pytest


def api():
    assert importlib.util.find_spec('app.release_compatibility'), 'Downgrade guard missing'
    return importlib.import_module('app.release_compatibility')


@pytest.mark.parametrize('mode',['random','guided'])
def test_old_reader_rejects_random_inspiration_tasks(tmp_path,mode):
    data=tmp_path/'data';data.mkdir();target=tmp_path/'old';target.mkdir()
    path=data/'state.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE production_runs(private TEXT)')
        db.execute('INSERT INTO production_runs VALUES (?)', (json.dumps({'variation':{'creation_mode':mode}}),))
    before=path.read_bytes()
    with pytest.raises(ValueError, match='camera-variation-'+mode+'-v1'):
        api().assert_rollback_compatible(target,data)
    assert path.read_bytes()==before


@pytest.mark.parametrize('source', ['config', 'snapshot'])
def test_old_reader_rejects_motion_profile_without_modifying_data(tmp_path, source):
    data=tmp_path/'data';data.mkdir();target=tmp_path/'old';target.mkdir()
    if source == 'config':
        path=data/'variation-settings.json'
        path.write_text(json.dumps({'prompt_mode':'motion','motion_skill':'approved'}))
    else:
        path=data/'state.db'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE production_runs(private TEXT)')
            db.execute('INSERT INTO production_runs VALUES (?)', (json.dumps({'variation':{'config':{'prompt_mode':'motion'}}}),))
    before=path.read_bytes()
    with pytest.raises(ValueError, match='camera-variation-motion-v1'):
        api().assert_rollback_compatible(target, data)
    assert path.read_bytes() == before


def test_old_reader_rejects_camera_variation_tasks(tmp_path):
    data=tmp_path/'data';data.mkdir();target=tmp_path/'old';target.mkdir()
    with sqlite3.connect(data/'state.db') as db:
        db.execute('CREATE TABLE production_variations(run_id TEXT, data TEXT)')
        db.execute("INSERT INTO production_variations VALUES ('run', '{}')")
    with pytest.raises(ValueError,match='camera-variation-v1'):
        api().assert_rollback_compatible(target,data)


@pytest.mark.parametrize('source',['config','snapshot'])
def test_old_reader_rejects_variation_reasoning_config(tmp_path,source):
    data=tmp_path/'data';data.mkdir();target=tmp_path/'old';target.mkdir()
    if source=='config':
        private=data/'private';private.mkdir()
        (private/'variation-settings.json').write_text(json.dumps({'thinking_enabled':True}))
    else:
        with sqlite3.connect(data/'state.db') as db:
            db.execute('CREATE TABLE production_runs(private TEXT)')
            db.execute('INSERT INTO production_runs VALUES (?)',(json.dumps({'variation':{'config':{'thinking_enabled':True}}}),))
    with pytest.raises(ValueError,match='camera-variation-reasoning-v1'):
        api().assert_rollback_compatible(target,data)


@pytest.mark.parametrize('table,state',[
    ('portrait_photos','stopped'),('portrait_photos','uncertain'),
    ('stage_tasks','uncertain'),('stage_tasks','restore_held'),('production_runs','restore_held')])
def test_old_reader_rejects_new_safety_states_without_mutating_database(tmp_path,table,state):
    guard=api(); data=tmp_path/'data';data.mkdir(); target=tmp_path/'old';target.mkdir()
    dbpath=data/'nested'/'tasks.sqlite3';dbpath.parent.mkdir()
    with sqlite3.connect(dbpath) as db:
        db.execute(f'CREATE TABLE {table}(status TEXT)'); db.execute(f'INSERT INTO {table} VALUES (?)',(state,))
    before=dbpath.read_bytes()
    with pytest.raises(ValueError,match='[Cc]ompatib|[Dd]owngrade'):
        guard.assert_rollback_compatible(target,data)
    assert dbpath.read_bytes()==before


def test_old_reader_rejects_durable_provider_state_even_on_failed_task(tmp_path):
    guard=api(); data=tmp_path/'data';data.mkdir(); target=tmp_path/'old';target.mkdir()
    with sqlite3.connect(data/'workflow.db') as db:
        db.execute('CREATE TABLE stage_tasks(status TEXT,provider_state_json TEXT)')
        db.execute('INSERT INTO stage_tasks VALUES (?,?)',('failed','{"remote_task_id":"paid"}'))
    with pytest.raises(ValueError): guard.assert_rollback_compatible(target,data)


def test_old_reader_rejects_query_window_records(tmp_path):
    guard=api(); data=tmp_path/'data';data.mkdir(); target=tmp_path/'old';target.mkdir()
    with sqlite3.connect(data/'no-extension') as db:
        db.execute('CREATE TABLE portrait_query_windows(photo_id TEXT)')
        db.execute("INSERT INTO portrait_query_windows VALUES ('photo')")
    with pytest.raises(ValueError): guard.assert_rollback_compatible(target,data)


def test_restore_marker_blocks_all_activation_even_with_capability(tmp_path):
    guard=api(); data=tmp_path/'data';data.mkdir(); target=tmp_path/'new';target.mkdir()
    (data/'.restore-hold.json').write_text('broken')
    with pytest.raises(ValueError,match='[Rr]estore'):
        guard.assert_rollback_compatible(target,data)


def test_empty_additive_schema_does_not_block_old_reader(tmp_path):
    guard=api(); data=tmp_path/'data';data.mkdir(); target=tmp_path/'old';target.mkdir()
    with sqlite3.connect(data/'data.db') as db:
        db.execute('CREATE TABLE portrait_query_windows(photo_id TEXT)')
        db.execute('CREATE TABLE stage_tasks(status TEXT,provider_state_json TEXT)')
        db.execute("INSERT INTO stage_tasks VALUES ('succeeded','{}')")
    guard.assert_rollback_compatible(target,data)


def test_explicit_missing_database_fails_closed(tmp_path):
    guard=api(); data=tmp_path/'data';data.mkdir(); target=tmp_path/'old';target.mkdir()
    with pytest.raises(ValueError):
        guard.assert_rollback_compatible(target,data,database_paths=[data/'missing.db'])


def test_old_reader_rejects_versioned_prompt_templates(tmp_path):
    data=tmp_path/'data';data.mkdir();target=tmp_path/'old';target.mkdir()
    with sqlite3.connect(data/'local-preferences.db') as db:
        db.execute('CREATE TABLE prompt_templates(id TEXT,rule_version TEXT)')
    with pytest.raises(ValueError,match='exclusive-prompts-v2'):
        api().assert_rollback_compatible(target,data)


@pytest.mark.parametrize('ddl,capability', [
    ('CREATE TABLE users(id TEXT, role TEXT, deleted_at REAL)', 'three-tier-accounts-v1'),
    ('CREATE TABLE production_run_phases(run_id TEXT)', 'explicit-run-phases-v1'),
    ('CREATE TABLE scheduling(id INTEGER, global_concurrency INTEGER)', 'global-scheduling-v1'),
])
def test_old_reader_rejects_new_admin_workflow_state(tmp_path, ddl, capability):
    data=tmp_path/'data';data.mkdir();target=tmp_path/'old';target.mkdir()
    with sqlite3.connect(data/'state.db') as db: db.execute(ddl)
    with pytest.raises(ValueError,match=capability): api().assert_rollback_compatible(target,data)


@pytest.mark.parametrize('table,field', [('prompt_templates','rule_version'),('production_drafts','data'),('production_runs','snapshot')])
def test_old_reader_rejects_yoyo_rules_even_without_template(tmp_path, table, field):
    data=tmp_path/'data';data.mkdir();target=tmp_path/'old';target.mkdir()
    with sqlite3.connect(data/'state.db') as db:
        db.execute('CREATE TABLE '+table+'('+field+' TEXT)')
        db.execute('INSERT INTO '+table+' VALUES (?)', ('yoyo-v3' if field=='rule_version' else '{"prompt_rule_version":"yoyo-v3"}',))
    with pytest.raises(ValueError,match='yoyo-prompts-v3'): api().assert_rollback_compatible(target,data)
