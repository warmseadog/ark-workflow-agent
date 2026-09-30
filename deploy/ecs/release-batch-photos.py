"""Release committed photo-batch UI files over a captured live source baseline.

Run with check/deploy/verify. Capture source with the reviewed SSH connection into
storage/batch-upload-live-baseline.json before use; credentials/data are excluded.
"""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
mode = sys.argv[1]
assert mode in ('check', 'deploy', 'verify')
names = ['app/static/virtual-library.js', 'app/static/virtual-library.css',
         'app/templates/people.html', 'app/templates/admin_settings.html']
live = json.loads((root/'storage/batch-upload-live-baseline.json').read_text(encoding='utf-8'))
normalize = lambda value: value.replace(b'\r\n', b'\n')


def git(*args):
    return subprocess.run(['git', *args], cwd=root, check=True, capture_output=True).stdout


assert git('branch', '--show-current').strip() == b'main'
revision = git('rev-parse', 'HEAD').decode().strip()
contents = {name: normalize(git('show', 'HEAD:'+name)) for name in names}
for name, content in contents.items():
    assert content == normalize((root/name).read_bytes()), 'Uncommitted release file: '+name
# The committed runtime must reproduce the live baseline plus these four files.
for name, encoded in live['files'].items():
    if name.startswith('app/') and name not in names:
        assert normalize(git('show', 'HEAD:'+name)) == normalize(base64.b64decode(encoded)), name
payload = {name: base64.b64encode(content).decode() for name, content in contents.items()}
baseline = {name: hashlib.sha256(normalize(base64.b64decode(value))).hexdigest()
            for name, value in live['files'].items() if name.startswith('app/')}
remote = (root/'deploy/ecs/release-account-policy-remote.py').read_text(encoding='utf-8')
remote = remote.replace("EXPECTED = '20260928T145154Z-multiuser'", 'EXPECTED = '+repr(Path(live['release']).name))
remote = remote.replace("stamp + '-account-policy-timing'", "stamp + '-batch-photos-mobile'")
remote = remote.replace('before-account-policy-', 'before-batch-photos-mobile-')
remote = remote.replace('.current-account-policy-', '.current-batch-photos-mobile-')
remote = remote.replace('account-policy-release.json', 'batch-photos-mobile-release.json')
remote = remote.replace("if relative == 'private/accounts.db' and table == 'users':", 'if False:')
remote = remote.replace('Historical records changed beyond forced-password flag', 'Historical records changed')
begin = remote.index('    with sqlite3.connect', remote.index('def verify(release):'))
end = remote.index("\nif mode == 'verify':", begin)
remote = remote[:begin]+'''    for name in ('virtual-library.js','virtual-library.css'):
        status,body=http('/static/'+name+'?v=20260930-batch-mobile-1')
        assert status==200 and body==base64.b64decode(payload['app/static/'+name])
    for name in ('people.html','admin_settings.html'):
        assert b'20260930-batch-mobile-1' in (release/'app/templates'/name).read_bytes()
    return {'health':'ok','anonymous_api':'denied','files_verified':len(payload),'static_response':'verified'}
'''+remote[end:]
begin = remote.index('# Real routes and SQLite')
end = remote.index('other_pid = ctl(', begin)
probe_code = '''import os,tempfile,pytest
from jinja2 import Environment,FileSystemLoader
env=Environment(loader=FileSystemLoader('app/templates'),autoescape=True)
for name in ('people.html','admin_settings.html'):
 assert '20260930-batch-mobile-1' in env.get_template(name).render()
with tempfile.TemporaryDirectory(prefix='ark-batch-photos-probe-') as directory:
 os.environ.update(STORAGE_DIR=directory,WORKFLOW_DB=directory+'/workflow.db',WORKFLOW_STORAGE=directory+'/workflow',DATABASE_URL='sqlite:///'+directory+'/studio.db',APP_AUTH_ENABLED='false',SEEDANCE_MODE='mock')
 raise SystemExit(pytest.main(['tests/test_real_person_video_api.py','tests/test_person_video.py','-q','-p','no:cacheprovider','--basetemp='+directory+'/pytest']))
'''
remote = remote[:begin]+'''# Validate templates and existing media API with isolated data and mocked providers.
try:
    result=run('runuser','-u','ark-video-workflow','--','env','LD_LIBRARY_PATH='+library_path,
               str(ROOT/'venv/bin/python'),'-c',PROBE_CODE,cwd=release)
    print(result.stdout.decode(),flush=True)
except subprocess.CalledProcessError as error:
    raise RuntimeError('Batch-photo probe failed before maintenance:\\n'+error.stdout.decode(errors='replace')+error.stderr.decode(errors='replace')) from None
'''.replace('PROBE_CODE', repr(probe_code))+remote[end:]
remote = remote.replace("'isolated_policy_probe': 'passed'", "'isolated_media_probe': 'passed'")
code = 'mode='+repr(mode)+'\npayload='+repr(payload)+'\nbaseline='+repr(baseline)+'\nsource_revision='+repr(revision)+'\n'+remote
compile(code, '<batch-release-remote>', 'exec')
result = subprocess.run(['ssh','-F',str(root/'ssh/ECS-YRXT.conf'),'ECS-YRXT','python3','-'],
                        input=code.encode(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
(root/'storage'/('batch-photos-'+mode+'.log')).write_bytes(result.stdout)
sys.stdout.buffer.write(result.stdout)
raise SystemExit(result.returncode)
