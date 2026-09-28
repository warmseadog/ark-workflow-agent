"""SSH release body. The driver injects mode/payload/baseline/credentials in memory."""
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request

ROOT=Path('/opt/ark-video-workflow')
DATA=ROOT/'data'
CURRENT=ROOT/'current'
PREVIOUS=CURRENT.resolve()
SERVICE='ark-video-workflow.service'
ENV=ROOT/'config/app.env'
NGINX=Path('/etc/nginx/conf.d/ark-video-workflow.conf')
UNIT=Path('/etc/systemd/system/ark-video-workflow.service')
PUBLIC='https://118.196.7.195:8443'
assert PREVIOUS.is_relative_to(ROOT/'releases')

def digest(data):return hashlib.sha256(data).hexdigest()
def run(*args,**kw):return subprocess.run(args,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,**kw)
def ctl(*args):return run('systemctl',*args).stdout.decode().strip()

def http(path,method='GET',body=None,headers=None):
    request=urllib.request.Request('http://127.0.0.1:18080'+path,method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json','Origin':PUBLIC,**(headers or {})})
    try:response=urllib.request.urlopen(request,timeout=15)
    except urllib.error.HTTPError as error:response=error
    return response.code,response.headers,response.read()

def queues():
    counts={}
    for dbpath in [DATA/'production.db',*sorted((DATA/'users').glob('*/production.db'))]:
        if not dbpath.exists():continue
        with sqlite3.connect('file:'+str(dbpath)+'?mode=ro',uri=True) as db:
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table,states in {
                'production_runs':('queued','running'),
                'portrait_photos':('queued','uploading','submitting','processing','uncertain'),
                'portrait_group_requests':('submitting','uncertain'),
                'production_playbacks':('queued','processing'),
                'production_previews':('queued','running')}.items():
                if table in tables:
                    count=db.execute('SELECT COUNT(*) FROM '+table+' WHERE status IN ('+','.join('?' for _ in states)+')',states).fetchone()[0]
                    counts[str(dbpath.relative_to(DATA))+':'+table]=count
    for legacy in DATA.glob('*.db'):
        with sqlite3.connect('file:'+str(legacy)+'?mode=ro',uri=True) as db:
            tables={row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'video_jobs' in tables:
                counts[legacy.name+':legacy_jobs']=db.execute("SELECT COUNT(*) FROM video_jobs WHERE status IN ('queued','running')").fetchone()[0]
    return counts

def idle():
    counts=queues()
    assert not any(counts.values()),'Tasks active; deployment not started: '+json.dumps(counts)
    return counts

def fingerprint_data():
    with sqlite3.connect(DATA/'production.db') as db:
        result={table:digest(json.dumps(db.execute('SELECT * FROM '+table+' ORDER BY 1').fetchall(),sort_keys=True).encode())
            for table in ('production_runs','production_assets','production_drafts','portrait_people','production_portraits')}
    result['outputs']={path.name:digest(path.read_bytes()) for path in (DATA/'outputs').glob('*.mp4')}
    result['configs']={path.name:digest(path.read_bytes()) for path in (DATA/'private').glob('*.json')}
    return result

def verify(release,login=False):
    assert ctl('is-active',SERVICE)=='active'
    assert http('/healthz')[0]==200
    assert http('/api/production/drafts')[0]==401
    assert http('/api/admin/users')[0]==401
    status,headers,body=http('/login')
    assert status==200 and b'login-form' in body
    for name,encoded in payload.items():
        if name.startswith('app/'):
            assert (release/name).read_bytes()==base64.b64decode(encoded),'Release file mismatch: '+name
    if login:
        status,headers,body=http('/api/auth/login','POST',{'username':credentials['username'],'password':credentials['temporary_password']})
        assert status==200,'Initial administrator login failed'
        session=json.loads(body)
        assert session['user']['role']=='admin' and session['user']['legacy_owner'] and session['user']['must_change_password']
        cookie=headers['Set-Cookie']
        assert all(flag in cookie for flag in ('HttpOnly','Secure','SameSite=lax'))
        auth={'Cookie':cookie.split(';',1)[0],'X-CSRF-Token':session['csrf_token']}
        assert http('/api/auth/me',headers=auth)[0]==200
        assert http('/api/production/drafts',headers=auth)[0]==403
        assert http('/api/auth/logout','POST',headers=auth)[0]==204
        assert http('/api/auth/me',headers=auth)[0]==401
    return {'health':'ok','anonymous_api':'denied','login_page':'ok','files_verified':len(payload)}

if mode=='verify':
    print(json.dumps({'release':str(PREVIOUS),**verify(PREVIOUS)}));raise SystemExit()

assert PREVIOUS.name=='20260928T135707Z-inline-settings','Live release changed; refresh and review baseline'
assert all((PREVIOUS/name).is_file() and digest((PREVIOUS/name).read_bytes().replace(b'\r\n',b'\n'))==sha for name,sha in baseline.items()),'Live code changed'
assert NGINX.is_file() and UNIT.is_file() and ENV.is_file()
counts=idle()
if mode=='check':
    run(str(ROOT/'venv/bin/python'),'-m','ensurepip','--version')
    print(json.dumps({'current':str(PREVIOUS),'release_files':len(payload),'queues':counts,'dependency_bootstrap':'available','disk_free_bytes':shutil.disk_usage(ROOT).free}));raise SystemExit()

assert credentials and len(credentials['temporary_password'])>=24
stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
release=ROOT/'releases'/(stamp+'-multiuser')
backup=DATA/'backups'/('before-multiuser-'+stamp)
shutil.copytree(PREVIOUS,release,symlinks=True)
for name,encoded in payload.items():
    target=release/name
    assert target.is_relative_to(release) and not target.is_symlink()
    content=base64.b64decode(encoded)
    if name.endswith('.py'):compile(content,name,'exec')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(content)
has_pip=run(str(ROOT/'venv/bin/python'),'-c',"import importlib.util; print(bool(importlib.util.find_spec('pip')))").stdout.strip()==b'True'
if not has_pip:run(str(ROOT/'venv/bin/python'),'-m','ensurepip')
run(str(ROOT/'venv/bin/python'),'-m','pip','install','--disable-pip-version-check','argon2-cffi==25.1.0')
before=fingerprint_data()
other=ctl('show','director-prompt-h5.service','-p','MainPID','--value')
old_env,old_nginx,old_unit=ENV.read_bytes(),NGINX.read_bytes(),UNIT.read_bytes()
switched=False

def switch(target):
    temporary=ROOT/('.current-'+stamp)
    temporary.symlink_to(target)
    os.replace(temporary,CURRENT)

try:
    idle()
    run('systemctl','stop',SERVICE,timeout=110)
    idle()
    backup.mkdir(mode=0o700)
    for name,contents in [('app.env',old_env),('nginx.conf',old_nginx),('service.conf',old_unit)]:
        path=backup/name;path.write_bytes(contents);path.chmod(0o600)
    for path in [*sorted(DATA.glob('*.db')),*sorted((DATA/'private').glob('*.db'))]:
        dest=backup/path.relative_to(DATA);dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with sqlite3.connect(path) as src,sqlite3.connect(dest) as dst:src.backup(dst)
        dest.chmod(0o600)
    for path in (DATA/'private').glob('*.json'):
        dest=backup/'private'/path.name;dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        shutil.copyfile(path,dest);dest.chmod(0o600)
    code="""import json,sys
from pathlib import Path
from app.accounts import Accounts
data=json.load(sys.stdin)
store=Accounts(Path('/opt/ark-video-workflow/data'))
users=store.list_users()
if not users: store.init_admin(data['username'],data['temporary_password'])
else:
    assert len(users)==1 and users[0]['legacy_owner'] and users[0]['must_change_password'], 'Existing accounts require explicit migration review'
    session=store.login(data['username'],data['temporary_password'],'migration-local')
    store.logout(session['token'])
"""
    run(str(ROOT/'venv/bin/python'),'-c',code,input=json.dumps(credentials).encode(),cwd=release)
    owner=pwd.getpwnam('ark-video-workflow')
    for path in [DATA/'private',DATA/'private/accounts.db',DATA/'private/accounts.db-wal',DATA/'private/accounts.db-shm']:
        if path.exists():os.chown(path,owner.pw_uid,owner.pw_gid)
    lines=[line for line in old_env.decode().splitlines() if not line.startswith(('APP_AUTH_ENABLED=','APP_COOKIE_SECURE=','APP_PUBLIC_ORIGIN='))]
    ENV.write_text('\n'.join(lines)+f'\nAPP_AUTH_ENABLED=true\nAPP_COOKIE_SECURE=true\nAPP_PUBLIC_ORIGIN={PUBLIC}\n')
    ENV.chmod(0o600)
    UNIT.write_bytes(base64.b64decode(payload['deploy/ecs/ark-video-workflow.service']))
    run('systemctl','daemon-reload')
    switch(release);switched=True
    run('systemctl','start',SERVICE,timeout=110)
    for _ in range(30):
        try:
            if http('/healthz')[0]==200:break
        except Exception:pass
        time.sleep(1)
    else:raise RuntimeError('Startup health timed out')
    report=verify(release,login=True)
    assert fingerprint_data()==before,'Existing drafts/tasks/assets/people/configs changed'
    # Remove the shared Basic gate only AFTER individual session authentication passes.
    NGINX.write_bytes(base64.b64decode(payload['deploy/ecs/ark-video-workflow.nginx.conf']))
    run('nginx','-t');run('systemctl','reload','nginx')
    assert ctl('show','director-prompt-h5.service','-p','MainPID','--value')==other,'Unrelated service changed'
    manifest={'release':str(release),'previous':str(PREVIOUS),'backup':str(backup),'created_at':stamp,
        'legacy_data_preserved':True,'initial_admin':'admin','forced_password_change':True,**report}
    (release/'multiuser-release.json').write_text(json.dumps(manifest))
    print(json.dumps(manifest))
except Exception as error:
    if switched and any(queues().values()):
        print(json.dumps({'rollback':'deferred_active_tasks','release':str(CURRENT.resolve()),'error_type':type(error).__name__}))
    else:
        run('systemctl','stop',SERVICE,timeout=110)
        ENV.write_bytes(old_env);NGINX.write_bytes(old_nginx);UNIT.write_bytes(old_unit)
        if switched:switch(PREVIOUS)
        run('systemctl','daemon-reload');run('systemctl','start',SERVICE,timeout=110)
        run('nginx','-t');run('systemctl','reload','nginx')
        print(json.dumps({'rollback':'previous_code_and_auth_restored','error_type':type(error).__name__,'backup':str(backup)}))
    raise
