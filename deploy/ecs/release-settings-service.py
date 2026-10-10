"""Publish simplified settings and configurable redaction against a live baseline."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
mode = sys.argv[1]
assert mode in ('capture', 'package', 'check', 'probe', 'deploy', 'verify')
baseline_path = root/'storage/settings-service-live-baseline.json'
package = root/'storage/settings-service-release-package'
ssh = ['ssh', '-F', str(root/'ssh/ECS-YRXT.conf'), 'ECS-YRXT', 'python3', '-']
names = [
    'app/config.py', 'app/main.py', 'app/media.py', 'app/redaction_service.py',
    'app/preview_router.py', 'app/production_worker.py',
    'app/static/admin-settings.css', 'app/static/admin-settings.js',
    'app/static/model-catalog.js', 'app/static/model-settings.js',
    'app/static/portrait-people.js', 'app/static/production.css',
    'app/static/production.js', 'app/static/virtual-library.js',
    'app/templates/admin_settings.html', 'app/templates/generation_options_panel.html',
    'app/templates/model_settings_panel.html', 'app/templates/people.html',
    'app/templates/production.html', 'app/templates/production_panel.html',
    'app/templates/studio.html', 'app/templates/workspace_sidebar.html',
    'tests/test_redaction_service.py', 'tests/test_tenant_preview.py',
    'tests/test_access_control.py',
    'tests/test_redaction_settings.py', 'tests/test_blur_settings.py',
    'tests/test_admin_settings.py', 'tests/test_production_worker.py',
    'tests/test_person_video.py', 'tests/test_production_api.py', 'tests/conftest.py',
]
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

# These four files have newer live fixes outside this change. Preserve them,
# applying only the newly added API routes and the three reduced hint strings.
name = 'app/main.py'
current = contents[name].decode()
start = current.index("\n\n@app.get('/api/redaction-service')")
end = current.index("\n\n@app.put('/api/redaction-settings')", start)
original = normalize(base64.b64decode(live['files'][name])).decode()
anchor = "\n\n@app.put('/api/redaction-settings')"
assert original.count(anchor) == 1 and "@app.get('/api/redaction-service')" not in original
contents[name] = original.replace(anchor, current[start:end]+anchor, 1).encode()
for name, marker in [
    ('app/static/portrait-people.js', '    if (!matches.length)'),
    ('app/static/model-settings.js', '    if (sourceHint) sourceHint.textContent'),
    ('app/static/model-catalog.js', '    try { render(await request());'),
]:
    original = normalize(base64.b64decode(live['files'][name])).decode()
    replacement = [line for line in contents[name].decode().splitlines() if line.startswith(marker)]
    old = [line for line in original.splitlines() if line.startswith(marker)]
    assert len(old) == len(replacement) == 1, name
    if name.endswith('model-catalog.js'):
        replacement[0] = replacement[0].replace('render(await request());', 'render(await request()); loaded = true;')
    contents[name] = original.replace(old[0], replacement[0], 1).encode()

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
remote = remote.replace('account-policy-timing', 'settings-service')
remote = remote.replace('before-account-policy-', 'before-settings-service-')
remote = remote.replace('.current-account-policy-', '.current-settings-service-')
remote = remote.replace('account-policy-release.json', 'settings-service-release.json')
remote = remote.replace("if relative == 'private/accounts.db' and table == 'users':", 'if False:')
remote = remote.replace('Historical records changed beyond forced-password flag', 'Historical records changed')
begin = remote.index('    with sqlite3.connect', remote.index('def verify(release):'))
end = remote.index("\nif mode == 'verify':", begin)
remote = remote[:begin]+'''    assert http('/api/redaction-service')[0] == 401
    for name,encoded in payload.items():
        if name.startswith('app/static/'):
            status,body=http('/static/'+Path(name).name+'?v=20260930-settings-simple-1')
            assert status==200 and body==base64.b64decode(encoded), name
    service_file=DATA/'private/redaction-service.json'
    redaction_mode=json.loads(service_file.read_text()).get('mode','local') if service_file.exists() else 'local'
    return {'health':'ok','anonymous_api':'denied','files_verified':len(payload),'static_response':'verified','redaction_mode':redaction_mode}
'''+remote[end:]
begin = remote.index('# Real routes and SQLite')
end = remote.index('other_pid = ctl(', begin)
probe = '''import os,tempfile,pytest
from jinja2 import Environment,FileSystemLoader
env=Environment(loader=FileSystemLoader('app/templates'),autoescape=True)
html=env.get_template('admin_settings.html').render()
assert 'id="redaction-service-form"' in html
assert 'id="admin-intro"' not in html and 'id="redaction-defaults"' not in html
html=env.get_template('production.html').render()
assert 'id="material-progress"' not in html
assert html.count('id="studio-generate-submit"')==1
with tempfile.TemporaryDirectory(prefix='ark-settings-service-probe-') as directory:
 os.environ.update(STORAGE_DIR=directory,WORKFLOW_DB=directory+'/workflow.db',WORKFLOW_STORAGE=directory+'/workflow',DATABASE_URL='sqlite:///'+directory+'/studio.db',APP_AUTH_ENABLED='false',SEEDANCE_MODE='mock')
 raise SystemExit(pytest.main(['tests/test_redaction_service.py','tests/test_redaction_settings.py','tests/test_blur_settings.py','tests/test_admin_settings.py','tests/test_production_worker.py','tests/test_tenant_preview.py','tests/test_access_control.py','-q','-p','no:cacheprovider','--basetemp='+directory+'/pytest']))
'''
remote = remote[:begin]+'''# Verify staged application against isolated storage before maintenance.
try:
    result=run('runuser','-u','ark-video-workflow','--','env','LD_LIBRARY_PATH='+library_path,
               str(ROOT/'venv/bin/python'),'-c',PROBE_CODE,cwd=release)
    print(result.stdout.decode(),flush=True)
except subprocess.CalledProcessError as error:
    raise RuntimeError('Settings service probe failed before maintenance:\n'+error.stdout.decode(errors='replace')+error.stderr.decode(errors='replace')) from None
'''.replace('PROBE_CODE', repr(probe)).replace('maintenance:\n', 'maintenance:\\n')+remote[end:]
remote = remote.replace("'isolated_policy_probe': 'passed'", "'isolated_settings_probe': 'passed'")
remote = remote.replace('other_pid = ctl(', "if mode == 'probe':\n    print(json.dumps({'isolated_settings_probe':'passed','staged_release':str(release)}))\n    raise SystemExit()\n\nother_pid = ctl(")
revision = 'settings-service-'+hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:12]
code = 'mode='+repr(mode)+'\npayload='+repr(payload)+'\nbaseline='+repr(baseline)+'\nsource_revision='+repr(revision)+'\n'+remote
compile(code, '<settings-service-release>', 'exec')
result = subprocess.run(ssh, input=code.encode(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
(root/'storage'/('settings-service-'+mode+'.log')).write_bytes(result.stdout)
sys.stdout.buffer.write(result.stdout)
raise SystemExit(result.returncode)
