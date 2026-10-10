"""Release the failed-real reupload fix against a captured live baseline."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parents[2]
mode = sys.argv[1]
assert mode in ('capture', 'check', 'probe', 'deploy', 'verify')
baseline_path = root / 'storage/failed-real-reupload-live-baseline.json'
ssh = ['ssh', '-F', str(root / 'ssh/ECS-YRXT.conf'), 'ECS-YRXT', 'python3', '-']
names = ['app/portrait_library.py', 'app/shared_portraits.py',
         'tests/test_failed_real_reupload.py', 'tests/test_asset_preview.py',
         'tests/test_auto_virtual_library.py', 'tests/test_person_preparation.py',
         'tests/test_person_video.py']
normalize = lambda data: data.replace(b'\r\n', b'\n')


def resolver(data):
    start = data.index(b'    def resolve_virtual_assets(')
    end = data.index(b'\n    def ', start + 5)
    return data[start:end]


if mode == 'capture':
    assert not baseline_path.exists(), 'Keep the original release baseline.'
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
    for name in names[:2]:
        original = subprocess.run(['git', 'show', 'HEAD:' + name], cwd=root,
                                  check=True, capture_output=True).stdout
        original, deployed = normalize(original), normalize(base64.b64decode(live['files'][name]))
        if name == 'app/portrait_library.py':
            assert resolver(original) == resolver(deployed), 'Live resolver differs'
        else:
            assert original == deployed, 'Live differs: ' + name
    baseline_path.write_text(json.dumps(live), encoding='utf-8')
    print(json.dumps({'release': live['release'], 'baseline_files': len(live['files'])}))
    raise SystemExit()

live = json.loads(baseline_path.read_text(encoding='utf-8'))
payload = {name: base64.b64encode(normalize((root / name).read_bytes())).decode() for name in names}
# Preserve the live availability/removal fixes outside the resolver. Only the
# reviewed function is replaced; its captured old body was checked above.
deployed = normalize(base64.b64decode(live['files']['app/portrait_library.py']))
updated = deployed.replace(resolver(deployed), resolver(normalize((root / 'app/portrait_library.py').read_bytes())), 1)
payload['app/portrait_library.py'] = base64.b64encode(updated).decode()
baseline = {name: hashlib.sha256(normalize(base64.b64decode(data))).hexdigest()
            for name, data in live['files'].items() if name.startswith('app/')}
remote = (root / 'deploy/ecs/release-account-policy-remote.py').read_text(encoding='utf-8')
remote = remote.replace("EXPECTED = '20260928T145154Z-multiuser'", 'EXPECTED = ' + repr(Path(live['release']).name))
remote = remote.replace('account-policy-timing', 'failed-real-reupload')
remote = remote.replace('before-account-policy-', 'before-failed-real-reupload-')
remote = remote.replace('.current-account-policy-', '.current-failed-real-reupload-')
remote = remote.replace('account-policy-release.json', 'failed-real-reupload-release.json')
remote = remote.replace("if relative == 'private/accounts.db' and table == 'users':", 'if False:')
remote = remote.replace('Historical records changed beyond forced-password flag', 'Historical records changed')
begin = remote.index('    with sqlite3.connect', remote.index('def verify(release):'))
end = remote.index("\nif mode == 'verify':", begin)
remote = remote[:begin] + "    return {'health':'ok','anonymous_api':'denied','files_verified':len(payload)}\n" + remote[end:]
begin = remote.index('# Real routes and SQLite')
end = remote.index('other_pid = ctl(', begin)
probe = '''import os,tempfile,pytest
with tempfile.TemporaryDirectory(prefix='ark-failed-real-probe-') as directory:
 os.environ.update(STORAGE_DIR=directory,WORKFLOW_DB=directory+'/workflow.db',WORKFLOW_STORAGE=directory+'/workflow',DATABASE_URL='sqlite:///'+directory+'/studio.db',APP_AUTH_ENABLED='false',SEEDANCE_MODE='mock')
 raise SystemExit(pytest.main(['tests/test_failed_real_reupload.py','tests/test_auto_virtual_library.py','tests/test_asset_preview.py','tests/test_person_preparation.py','-q','-p','no:cacheprovider','--basetemp='+directory+'/pytest']))
'''
remote = remote[:begin] + '''# No real provider calls: tests use isolated storage and provider doubles.
try:
    result=run('runuser','-u','ark-video-workflow','--','env','LD_LIBRARY_PATH='+library_path,
               str(ROOT/'venv/bin/python'),'-c',PROBE_CODE,cwd=release)
    print(result.stdout.decode(),flush=True)
except subprocess.CalledProcessError as error:
    raise RuntimeError('Regression probe failed before maintenance:\\n'+error.stdout.decode(errors='replace')+error.stderr.decode(errors='replace')) from None
'''.replace('PROBE_CODE', repr(probe)) + remote[end:]
remote = remote.replace("'isolated_policy_probe': 'passed'", "'isolated_regression_probe': 'passed'")
remote = remote.replace('other_pid = ctl(', "if mode == 'probe':\n    print(json.dumps({'isolated_regression_probe':'passed','staged_release':str(release)}))\n    raise SystemExit()\n\nother_pid = ctl(")
revision = 'failed-real-reupload-' + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
code = 'mode=' + repr(mode) + '\npayload=' + repr(payload) + '\nbaseline=' + repr(baseline) + '\nsource_revision=' + repr(revision) + '\n' + remote
compile(code, '<failed-real-reupload-release>', 'exec')
result = subprocess.run(ssh, input=code.encode(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
(root / 'storage' / ('failed-real-reupload-' + mode + '.log')).write_bytes(result.stdout)
sys.stdout.buffer.write(result.stdout)
raise SystemExit(result.returncode)
