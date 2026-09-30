"""Publish administrator task records with user filtering against a live baseline."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
mode = sys.argv[1]
assert mode in ('capture', 'package', 'check', 'probe', 'deploy', 'verify')
baseline_path = root/'storage/admin-records-live-baseline.json'
package = root/'storage/admin-records-release-package'
ssh = ['ssh', '-F', str(root/'ssh/ECS-YRXT.conf'), 'ECS-YRXT', 'python3', '-']
names = ['app/task_records.py', 'app/user_admin.py', 'app/production_router.py', 'app/static/production-runs.js', 'app/static/production.js', 'app/static/production.css', 'app/templates/production_panel.html', 'app/templates/production.html', 'app/templates/studio.html', 'tests/test_admin_task_records.py', 'tests/test_admin_task_records_browser.py', 'tests/test_access_control.py', 'tests/test_admin_task_timing.py', 'tests/test_admin_audit_pagination.py', 'tests/test_production_api.py', 'tests/test_person_video.py', 'tests/test_playback.py', 'tests/test_run_timing.py', 'tests/test_account_frontend.py', 'tests/conftest.py']

normalize = lambda data: data.replace(b'\r\n', b'\n')
if mode == 'capture':
    assert not baseline_path.exists(), 'Keep the original captured baseline.'
    code = '''import base64,json
from pathlib import Path
p=Path('/opt/ark-video-workflow/current').resolve()
files={}
for directory in ('app','tests'):
 for f in (p/directory).rglob('*'):
  if f.is_file() and f.suffix in ('.py','.html','.js','.css','.svg') and '__pycache__' not in f.parts:
   files[f.relative_to(p).as_posix()]=base64.b64encode(f.read_bytes()).decode()
print(json.dumps({'release':str(p),'files':files}))
'''
    result = subprocess.run(ssh, input=code.encode(), check=True, capture_output=True)
    live = json.loads(result.stdout)
    baseline_path.write_text(json.dumps(live), encoding='utf-8')
    print(json.dumps({'release':live['release'], 'source_files':len(live['files'])}))
    raise SystemExit()

live = json.loads(baseline_path.read_text(encoding='utf-8'))
contents = {name:normalize((root/name).read_bytes()) for name in names}

if mode == 'package':
    for name, encoded in live['files'].items():
        target = package/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(normalize(base64.b64decode(encoded)))
    for name, data in contents.items():
        target = package/name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for source in (root/'tests').glob('*.py'):
        target = package/'tests'/source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    print(json.dumps({'package':str(package), 'overlaid_files':len(contents)}))
    raise SystemExit()

payload = {name:base64.b64encode(data).decode() for name,data in contents.items()}
baseline = {name:hashlib.sha256(normalize(base64.b64decode(data))).hexdigest()
            for name,data in live['files'].items() if name.startswith('app/')}
remote = (root/'deploy/ecs/release-account-policy-remote.py').read_text(encoding='utf-8')
remote = remote.replace("EXPECTED = '20260928T145154Z-multiuser'", 'EXPECTED = '+repr(Path(live['release']).name))
remote = remote.replace('account-policy-timing', 'admin-records')
remote = remote.replace('before-account-policy-', 'before-admin-records-')
remote = remote.replace('.current-account-policy-', '.current-admin-records-')
remote = remote.replace('account-policy-release.json', 'admin-records-release.json')
remote = remote.replace("if relative == 'private/accounts.db' and table == 'users':", 'if False:')
remote = remote.replace('Historical records changed beyond forced-password flag', 'Historical records changed')
begin = remote.index('    with sqlite3.connect', remote.index('def verify(release):'))
end = remote.index("\nif mode == 'verify':", begin)
remote = remote[:begin]+'''    assert http('/api/admin/task-records')[0] == 401
    for name,encoded in payload.items():
        if name.startswith('app/static/'):
            status,body=http('/static/'+Path(name).name+'?v=20260930-admin-records-1')
            assert status==200 and body==base64.b64decode(encoded), name
    return {'health':'ok','anonymous_api':'denied','files_verified':len(payload),'static_response':'verified'}
'''+remote[end:]
begin = remote.index('# Real routes and SQLite')
end = remote.index('other_pid = ctl(', begin)
probe = 'import os,tempfile,pytest\nfrom jinja2 import Environment,FileSystemLoader\nenv=Environment(loader=FileSystemLoader(\'app/templates\'),autoescape=True)\nfor template in (\'production.html\',\'studio.html\'):\n html=env.get_template(template).render()\n assert \'id="runs-user-filter"\' in html\n assert \'production-runs.js?v=20260930-admin-records-1\' in html\nwith tempfile.TemporaryDirectory(prefix=\'ark-admin-records-probe-\') as directory:\n os.environ.update(STORAGE_DIR=directory,WORKFLOW_DB=directory+\'/workflow.db\',WORKFLOW_STORAGE=directory+\'/workflow\',DATABASE_URL=\'sqlite:///\'+directory+\'/studio.db\',APP_AUTH_ENABLED=\'false\',SEEDANCE_MODE=\'mock\')\n raise SystemExit(pytest.main([\'tests/test_admin_task_records.py\',\'tests/test_access_control.py\',\'tests/test_admin_task_timing.py\',\'tests/test_admin_audit_pagination.py\',\'tests/test_production_api.py\',\'tests/test_playback.py\',\'tests/test_run_timing.py\',\'-k\',\'not browser\',\'-q\',\'-p\',\'no:cacheprovider\',\'--basetemp=\'+directory+\'/pytest\']))\n'
remote = remote[:begin]+'''# Verify staged application against isolated storage before maintenance.
try:
    result=run('runuser','-u','ark-video-workflow','--','env','LD_LIBRARY_PATH='+library_path,
               str(ROOT/'venv/bin/python'),'-c',PROBE_CODE,cwd=release)
    print(result.stdout.decode(),flush=True)
except subprocess.CalledProcessError as error:
    raise RuntimeError('Admin records probe failed before maintenance:\n'+error.stdout.decode(errors='replace')+error.stderr.decode(errors='replace')) from None
'''.replace('PROBE_CODE', repr(probe)).replace('maintenance:\n', 'maintenance:\\n')+remote[end:]
remote = remote.replace("'isolated_policy_probe': 'passed'", "'isolated_admin_records_probe': 'passed'")
remote = remote.replace('other_pid = ctl(', "if mode == 'probe':\n    print(json.dumps({'isolated_admin_records_probe':'passed','staged_release':str(release)}))\n    raise SystemExit()\n\nother_pid = ctl(")
revision = 'admin-records-'+hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:12]
code = 'mode='+repr(mode)+'\npayload='+repr(payload)+'\nbaseline='+repr(baseline)+'\nsource_revision='+repr(revision)+'\n'+remote
compile(code, '<admin-records-release>', 'exec')
result = subprocess.run(ssh, input=code.encode(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
(root/'storage'/('admin-records-'+mode+'.log')).write_bytes(result.stdout)
sys.stdout.buffer.write(result.stdout)
raise SystemExit(result.returncode)
