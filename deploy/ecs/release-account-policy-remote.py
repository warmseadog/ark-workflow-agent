"""SSH body injected by release-account-policy.py. Only the named service is changed."""
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path('/opt/ark-video-workflow')
DATA = ROOT/'data'
CURRENT = ROOT/'current'
PREVIOUS = CURRENT.resolve()
SERVICE = 'ark-video-workflow.service'
EXPECTED = '20260928T145154Z-multiuser'
NGINX = Path('/etc/nginx/conf.d/ark-video-workflow.conf')
assert PREVIOUS.is_relative_to(ROOT/'releases')

def run(*args, **kwargs):
    return subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)

def ctl(*args):
    return run('systemctl', *args).stdout.decode().strip()

def digest(value):
    return hashlib.sha256(value).hexdigest()

service_pid = ctl('show', SERVICE, '-p', 'MainPID', '--value')
service_env = dict(item.split(b'=', 1) for item in (Path('/proc')/service_pid/'environ').read_bytes().split(b'\0') if b'=' in item)
WORKFLOW_DB = Path(os.fsdecode(service_env.get(b'WORKFLOW_DB', os.fsencode(PREVIOUS/'storage/workflow.db'))))
if not WORKFLOW_DB.is_absolute():
    WORKFLOW_DB = PREVIOUS/WORKFLOW_DB
WORKFLOW_DB = WORKFLOW_DB.resolve()
assert WORKFLOW_DB.is_relative_to(DATA), 'Workflow database is outside reviewed data root; stop for migration review'
del service_env

def databases():
    return sorted(set([*([WORKFLOW_DB] if WORKFLOW_DB.is_file() else []), *DATA.glob('*.db'), *DATA.glob('private/*.db'),
                       *DATA.glob('users/*/*.db'), *DATA.glob('users/*/private/*.db')]))

def queues():
    counts = {}
    for path in databases():
        with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table, states in {
                'production_runs': ('queued', 'running'), 'video_jobs': ('queued', 'running'),
                'stage_tasks': ('queued', 'running'),
                'portrait_photos': ('queued', 'uploading', 'submitting', 'processing', 'uncertain'),
                'portrait_group_requests': ('submitting', 'uncertain'),
                'production_playbacks': ('queued', 'processing'),
                'production_previews': ('queued', 'running')}.items():
                if table in tables:
                    counts[str(path.relative_to(DATA)) + ':' + table] = db.execute(
                        'SELECT COUNT(*) FROM ' + table + ' WHERE status IN ('
                        + ','.join('?' for _ in states) + ')', states).fetchone()[0]
    return counts

def idle():
    counts = queues()
    assert not any(counts.values()), 'Active tasks; release not switched: ' + json.dumps(counts)
    return counts

def snapshot(spec=None):
    """Hash existing rows/columns. New timing tables are additive, not historical edits."""
    result = {}
    for path in databases():
        relative = str(path.relative_to(DATA))
        if spec is not None and relative not in spec:
            continue
        with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True) as db:
            tables = ([row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                      if spec is None else spec[relative].keys())
            result[relative] = {}
            for table in tables:
                quote = lambda value: '"' + value.replace('"', '""') + '"'
                columns = ([row[1] for row in db.execute('PRAGMA table_info(' + quote(table) + ')')]
                           if spec is None else spec[relative][table]['columns'])
                # This is the single intended change to old account records.
                if relative == 'private/accounts.db' and table == 'users':
                    columns = [column for column in columns if column != 'must_change_password']
                rows = db.execute('SELECT ' + ','.join(map(quote, columns)) + ' FROM ' + quote(table)).fetchall()
                encoded = sorted(json.dumps(row, sort_keys=True, default=repr) for row in rows)
                result[relative][table] = {'columns': columns, 'hash': digest(json.dumps(encoded).encode())}
    return result

def http(path):
    try:
        response = urllib.request.urlopen('http://127.0.0.1:18080' + path, timeout=10)
    except urllib.error.HTTPError as error:
        response = error
    return response.code, response.read()

def verify(release):
    assert ctl('is-active', SERVICE) == 'active'
    assert http('/healthz')[0] == 200
    assert http('/api/admin/users')[0] == http('/api/production/drafts')[0] == 401
    status, body = http('/login')
    assert status == 200 and b'login-form' in body
    for name, encoded in payload.items():
        assert (release/name).read_bytes() == base64.b64decode(encoded), 'File mismatch: ' + name
    with sqlite3.connect('file:' + str(DATA/'private/accounts.db') + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT COUNT(*) FROM users WHERE must_change_password<>0').fetchone()[0] == 0
    return {'health': 'ok', 'anonymous_api': 'denied', 'forced_change_removed': True, 'files_verified': len(payload)}

if mode == 'verify':
    print(json.dumps({'release': str(PREVIOUS), **verify(PREVIOUS)}))
    raise SystemExit()

assert PREVIOUS.name == EXPECTED, 'Unexpected live release; review baseline before deployment'
for name, sha in baseline.items():
    assert (PREVIOUS/name).is_file() and digest((PREVIOUS/name).read_bytes().replace(b'\r\n', b'\n')) == sha, 'Live code changed: ' + name
counts = idle()
if mode == 'check':
    print(json.dumps({'release': str(PREVIOUS), 'files': len(payload), 'queues': counts,
                      'free_bytes': shutil.disk_usage(ROOT).free, 'databases': len(databases())}))
    raise SystemExit()

stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
release = ROOT/'releases'/(stamp + '-account-policy-timing')
backup = DATA/'backups'/('before-account-policy-' + stamp)
shutil.copytree(PREVIOUS, release, symlinks=True)
for name, encoded in payload.items():
    target = release/name
    assert target.is_relative_to(release) and not target.is_symlink()
    content = base64.b64decode(encoded)
    if name.endswith('.py'):
        compile(content, name, 'exec')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)

# Real routes and SQLite in an isolated temporary directory, with no lifespan or paid calls.
probe = r'''import os,tempfile,json
from pathlib import Path
from dataclasses import replace
from fastapi.testclient import TestClient
with tempfile.TemporaryDirectory(prefix='ark-policy-probe-') as directory:
    os.environ.update(STORAGE_DIR=directory,WORKFLOW_DB=directory+'/workflow.db',WORKFLOW_STORAGE=directory+'/workflow',APP_AUTH_ENABLED='true',APP_COOKIE_SECURE='true',APP_PUBLIC_ORIGIN='https://testserver',DATABASE_URL='sqlite:///'+directory+'/studio.db')
    from app import main
    from app.accounts import Accounts
    main.settings=replace(main.settings,storage_dir=Path(directory),config_root=None,user_id='')
    accounts=Accounts(Path(directory)); admin=accounts.init_admin('probe-admin','618429')
    with accounts._connect(write=True) as db:
        db.execute('UPDATE users SET must_change_password=1')
    client=TestClient(main.app,base_url='https://testserver')
    try:
        response=client.post('/api/auth/login',json={'username':'probe-admin','password':'618429'},headers={'Origin':'https://testserver'})
        assert response.status_code==200 and not response.json()['user']['must_change_password']
        client.headers.update({'Origin':'https://testserver','X-CSRF-Token':response.json()['csrf_token']})
        assert client.get('/api/production/drafts').status_code==200
        for role in ('admin','user'):
            created=client.post('/api/admin/users',json={'username':'probe-'+role+'-2','password':'782649','role':role})
            assert created.status_code==201 and created.json()['user']['role']==role
        from app.production_store import ProductionStore
        import re
        store=ProductionStore(Path(directory))
        draft=store.create_draft({})
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}',draft['name'])
        task=store.create_run(draft['id'],draft['revision'],'isolated-probe',{})
        assert task['timing']['available'] and task['timing']['is_live']
        store.claim_next()
        done=store.update_run(task['id'],status='succeeded')
        assert done['timing']['finished_at'] and not done['timing']['is_live']
        assert client.post('/api/admin/users',json={'username':'short','password':'12345'}).status_code==422
        assert client.post('/api/auth/logout').status_code==204
        assert client.get('/api/auth/me').status_code==401
    finally:
        client.close()
print(json.dumps({'isolated_policy_probe':'passed'}))
'''
run('runuser', '-u', 'ark-video-workflow', '--', str(ROOT/'venv/bin/python'), '-c', probe, cwd=release)
other_pid = ctl('show', 'director-prompt-h5.service', '-p', 'MainPID', '--value')
switched = False
stopped = False
public_paused = False
old_nginx = NGINX.read_bytes()
assert old_nginx.count(b'server {') == 1 and b'listen 8443 ' in old_nginx

def pause_public_requests():
    global public_paused
    nginx_pid = ctl('show', 'nginx', '-p', 'MainPID', '--value')
    children = run('ps', '--ppid', nginx_pid, '-o', 'pid=,args=').stdout.decode().splitlines()
    workers = [line.split()[0] for line in children if 'nginx: worker process' in line]
    public_paused = True
    # Only this 8443 server block is gated; the separate 443 site is unchanged.
    NGINX.write_bytes(old_nginx.replace(b'server {', b'server {\n    return 503;', 1))
    run('nginx', '-t')
    run('systemctl', 'reload', 'nginx')
    deadline = time.monotonic() + 45
    while any((Path('/proc')/pid).exists() for pid in workers):
        if time.monotonic() >= deadline:
            raise RuntimeError('Old proxy requests did not drain; service not stopped')
        time.sleep(0.2)

def restore_public_requests():
    global public_paused
    if public_paused:
        NGINX.write_bytes(old_nginx)
        run('nginx', '-t')
        run('systemctl', 'reload', 'nginx')
        public_paused = False

def switch(target):
    temporary = ROOT/('.current-account-policy-' + stamp)
    temporary.symlink_to(target)
    os.replace(temporary, CURRENT)

try:
    pause_public_requests()
    idle()
    stopped = True
    run('systemctl', 'stop', SERVICE, timeout=110)
    idle()
    before = snapshot()
    backup.mkdir(mode=0o700)
    for path in databases():
        dest = backup/path.relative_to(DATA)
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(path) as source, sqlite3.connect(dest) as destination:
            source.backup(destination)
        dest.chmod(0o600)
    for path in [ROOT/'config/app.env', *DATA.glob('private/*.json')]:
        dest = backup/'config'/path.name
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(path, dest)
        dest.chmod(0o600)
    (backup/'snapshot.json').write_text(json.dumps(before))
    (backup/'snapshot.json').chmod(0o600)
    (backup/'config/nginx.conf').write_bytes(old_nginx)
    (backup/'config/nginx.conf').chmod(0o600)
    switch(release)
    switched = True
    run('systemctl', 'start', SERVICE, timeout=110)
    for attempt in range(30):
        try:
            if http('/healthz')[0] == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    else:
        raise RuntimeError('Startup health timeout')
    report = verify(release)
    assert snapshot(before) == before, 'Historical records changed beyond forced-password flag'
    restore_public_requests()
    assert ctl('show', 'director-prompt-h5.service', '-p', 'MainPID', '--value') == other_pid
    manifest = {'release': str(release), 'previous': str(PREVIOUS), 'backup': str(backup),
                'revision': source_revision, 'historical_records_preserved': True,
                'password_hashes_unchanged': True, 'isolated_policy_probe': 'passed', **report}
    (release/'account-policy-release.json').write_text(json.dumps(manifest))
    print(json.dumps(manifest))
except Exception as error:
    if switched and any(queues().values()):
        print(json.dumps({'rollback': 'deferred_active_tasks', 'error_type': type(error).__name__}))
    else:
        if stopped:
            run('systemctl', 'stop', SERVICE, timeout=110)
            if switched:
                switch(PREVIOUS)
            run('systemctl', 'start', SERVICE, timeout=110)
        print(json.dumps({'rollback': 'previous_release_restored', 'error_type': type(error).__name__}))
    raise
finally:
    restore_public_requests()
