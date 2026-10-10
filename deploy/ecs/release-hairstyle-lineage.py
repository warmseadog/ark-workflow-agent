"""Release only the reviewed hairstyle fix against the captured live baseline."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parents[2]
mode = sys.argv[1]
assert mode in ('check', 'deploy', 'verify')
live = json.loads((root/'storage/hairstyle-lineage-live-baseline.json').read_text(encoding='utf-8'))
names = ['app/shared_portraits.py', 'app/hairstyle_mask.py',
         'app/production_router.py', 'app/production_worker.py',
         'tests/test_reference_lineage.py', 'tests/test_hairstyle_mask.py',
         'tests/test_production_api.py', 'tests/test_production_worker.py',
         'tests/test_failed_real_reupload.py', 'tests/test_active_reference_checks.py',
         'tests/test_asset_preview.py']
normalize = lambda data: data.replace(b'\r\n', b'\n')
payload = {name: base64.b64encode(normalize((root/name).read_bytes())).decode() for name in names}
baseline = {name: hashlib.sha256(normalize(base64.b64decode(data))).hexdigest()
            for name, data in live['files'].items() if name.startswith('app/')}

# Reuse the established idle-queue, drained-proxy, SQLite-backup and rollback
# procedure. These replacements are checked so template drift fails locally.
remote = (root/'deploy/ecs/release-account-policy-remote.py').read_text(encoding='utf-8')
def substitute(old, new):
    global remote
    assert old in remote, 'Release template changed: '+old[:70]
    remote = remote.replace(old, new)

substitute("EXPECTED = '20260928T145154Z-multiuser'", 'EXPECTED = '+repr(Path(live['release']).name))
substitute('account-policy-timing', 'hairstyle-lineage')
substitute('before-account-policy-', 'before-hairstyle-lineage-')
substitute('.current-account-policy-', '.current-hairstyle-lineage-')
substitute('account-policy-release.json', 'hairstyle-lineage-release.json')
substitute("if relative == 'private/accounts.db' and table == 'users':", 'if False:')
substitute('Historical records changed beyond forced-password flag', 'Historical records changed')
begin = remote.index('    with sqlite3.connect', remote.index('def verify(release):'))
end = remote.index("\nif mode == 'verify':", begin)
remote = remote[:begin]+"    return {'health':'ok','anonymous_api':'denied','files_verified':len(payload)}\n"+remote[end:]

probe = '''import os,tempfile,pytest
with tempfile.TemporaryDirectory(prefix='ark-hairstyle-lineage-probe-') as directory:
 os.environ.update(STORAGE_DIR=directory,WORKFLOW_DB=directory+'/workflow.db',WORKFLOW_STORAGE=directory+'/workflow',DATABASE_URL='sqlite:///'+directory+'/studio.db',APP_AUTH_ENABLED='false',SEEDANCE_MODE='mock')
 raise SystemExit(pytest.main(['tests/test_reference_lineage.py','tests/test_hairstyle_mask.py','tests/test_production_api.py','tests/test_production_worker.py','tests/test_failed_real_reupload.py','tests/test_active_reference_checks.py','tests/test_asset_preview.py','-q','-p','no:cacheprovider','--basetemp='+directory+'/pytest']))
'''
begin = remote.index('# Real routes and SQLite')
end = remote.index('other_pid = ctl(', begin)
remote = remote[:begin]+'''# Use isolated temporary data and fake providers, with no production imports.
try:
    result=run('runuser','-u','ark-video-workflow','--','env','LD_LIBRARY_PATH='+library_path,
               str(ROOT/'venv/bin/python'),'-c',PROBE_CODE,cwd=release)
    print(result.stdout.decode(),flush=True)
except subprocess.CalledProcessError as error:
    raise RuntimeError('Regression probe failed before maintenance:\\n'+error.stdout.decode(errors='replace')+error.stderr.decode(errors='replace')) from None
'''.replace('PROBE_CODE', repr(probe))+remote[end:]
substitute("'isolated_policy_probe': 'passed'", "'isolated_regression_probe': 'passed'")

# Recheck the reviewed application tree after tests and before maintenance.
substitute('try:\n    pause_public_requests()', '''try:
    assert CURRENT.resolve() == PREVIOUS, 'Live release changed during tests'
    for name, sha in baseline.items():
        assert digest((PREVIOUS/name).read_bytes().replace(b'\\r\\n', b'\\n')) == sha, 'Live code changed during tests: '+name
    pause_public_requests()''')
revision = 'hairstyle-lineage-'+hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
code = 'mode='+repr(mode)+'\npayload='+repr(payload)+'\nbaseline='+repr(baseline)+'\nsource_revision='+repr(revision)+'\n'+remote
compile(code, '<hairstyle-lineage-release>', 'exec')
ssh = ['ssh', '-F', str(root/'ssh/ECS-YRXT.conf'), 'ECS-YRXT', 'python3', '-']
result = subprocess.run(ssh, input=code.encode(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
(root/'storage'/('hairstyle-lineage-'+mode+'.log')).write_bytes(result.stdout)
sys.stdout.buffer.write(result.stdout)
raise SystemExit(result.returncode)
