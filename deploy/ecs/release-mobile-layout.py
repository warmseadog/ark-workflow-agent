"""Incremental mobile/landscape release on a captured live baseline.

Capture reads source only. Check/deploy reuse the established queue, database
backup, atomic symlink and rollback guards. No credentials enter the package.
"""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
mode = sys.argv[1]
assert mode in ('capture', 'package', 'check', 'deploy', 'verify')
names = [
    'app/static/production.css', 'app/static/production.js',
    'app/static/workspace.css', 'app/static/workspace.js',
    'app/templates/generation_options_panel.html', 'app/templates/production.html',
    'app/templates/production_panel.html', 'app/templates/studio.html',
    'app/templates/workspace_head.html',
]
baseline_path = root/'storage/mobile-layout-live-baseline.json'
package = root/'storage/mobile-layout-release-package'
ssh = ['ssh', '-F', str(root/'ssh/ECS-YRXT.conf'), 'ECS-YRXT', 'python3', '-']
normalize = lambda data: data.replace(b'\r\n', b'\n')
if mode == 'capture':
    assert not baseline_path.exists(), 'Preserve the first captured baseline; review changes explicitly.'
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
    differences = []
    for name in names:
        if not name.startswith('app/'):
            continue
        original = subprocess.run(['git', 'show', 'HEAD:'+name], cwd=root, check=True, capture_output=True).stdout
        if normalize(original) != normalize(base64.b64decode(live['files'][name])):
            differences.append(name)
    print(json.dumps({'release':live['release'], 'source_files':len(live['files']), 'live_differs_from_HEAD':differences}))
    raise SystemExit()

live = json.loads(baseline_path.read_text(encoding='utf-8'))
contents = {name:normalize((root/name).read_bytes()) for name in names}
for name, encoded in live['files'].items():
    target = package/name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(normalize(base64.b64decode(encoded)))
for name, data in contents.items():
    target = package/name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
# Local regression harness only; these tests are not part of the live payload.
for source in (root/'tests').glob('*.py'):
    target = package/'tests'/source.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
if mode == 'package':
    print(json.dumps({'package':str(package), 'overlaid_files':len(contents)}))
    raise SystemExit()

payload = {name:base64.b64encode(data).decode() for name,data in contents.items()}
baseline = {name:hashlib.sha256(normalize(base64.b64decode(encoded))).hexdigest()
            for name,encoded in live['files'].items() if name.startswith('app/')}
remote = (root/'deploy/ecs/release-account-policy-remote.py').read_text(encoding='utf-8')
remote = remote.replace("EXPECTED = '20260928T145154Z-multiuser'", 'EXPECTED = '+repr(Path(live['release']).name))
remote = remote.replace("stamp + '-account-policy-timing'", "stamp + '-mobile-landscape'")
remote = remote.replace('before-account-policy-', 'before-mobile-landscape-')
remote = remote.replace('.current-account-policy-', '.current-mobile-landscape-')
remote = remote.replace('account-policy-release.json', 'mobile-landscape-release.json')
remote = remote.replace("if relative == 'private/accounts.db' and table == 'users':", 'if False:')
remote = remote.replace('Historical records changed beyond forced-password flag', 'Historical records changed')
begin = remote.index('    with sqlite3.connect', remote.index('def verify(release):'))
end = remote.index("\nif mode == 'verify':", begin)
remote = remote[:begin]+'''    for name in ('production.css','production.js','workspace.css','workspace.js'):
        status,body=http('/static/'+name+'?v=20260930-mobile-landscape-1')
        assert status==200 and body==base64.b64decode(payload['app/static/'+name]), name
    return {'health':'ok','anonymous_api':'denied','files_verified':len(payload),'static_response':'verified'}
'''+remote[end:]
begin = remote.index('# Real routes and SQLite')
end = remote.index('other_pid = ctl(', begin)
probe = '''import os,tempfile,pytest
from jinja2 import Environment,FileSystemLoader
env=Environment(loader=FileSystemLoader('app/templates'),autoescape=True)
for name in ('production.html','studio.html'):
 html=env.get_template(name).render()
 assert html.count('id="studio-generate-submit"')==1
 assert 'generation-dock' in html and 'scene-custom-fields' in html
 assert '20260930-mobile-landscape-1' in html
with tempfile.TemporaryDirectory(prefix='ark-mobile-layout-probe-') as directory:
 os.environ.update(STORAGE_DIR=directory,WORKFLOW_DB=directory+'/workflow.db',WORKFLOW_STORAGE=directory+'/workflow',DATABASE_URL='sqlite:///'+directory+'/studio.db',APP_AUTH_ENABLED='false',SEEDANCE_MODE='mock')
 raise SystemExit(pytest.main(['tests/test_generation_settings.py','tests/test_source_clip.py','tests/test_active_reference_checks.py','-q','-p','no:cacheprovider','--basetemp='+directory+'/pytest']))
'''
remote = remote[:begin]+'''# Isolated template and API regression before pausing the live service.
try:
    result=run('runuser','-u','ark-video-workflow','--','env','LD_LIBRARY_PATH='+library_path,
               str(ROOT/'venv/bin/python'),'-c',PROBE_CODE,cwd=release)
    print(result.stdout.decode(),flush=True)
except subprocess.CalledProcessError as error:
    raise RuntimeError('Mobile probe failed before maintenance:\\n'+error.stdout.decode(errors='replace')+error.stderr.decode(errors='replace')) from None
'''.replace('PROBE_CODE', repr(probe))+remote[end:]
remote = remote.replace("'isolated_policy_probe': 'passed'", "'isolated_mobile_probe': 'passed'")
revision = 'mobile-landscape-'+hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:12]
code = 'mode='+repr(mode)+'\npayload='+repr(payload)+'\nbaseline='+repr(baseline)+'\nsource_revision='+repr(revision)+'\n'+remote
compile(code, '<mobile-release-remote>', 'exec')
result = subprocess.run(ssh, input=code.encode(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
(root/'storage'/('mobile-layout-'+mode+'.log')).write_bytes(result.stdout)
sys.stdout.buffer.write(result.stdout)
raise SystemExit(result.returncode)
