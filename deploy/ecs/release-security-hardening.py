"""Publish the reviewed security delta with baseline checks and automatic rollback.

Uses the existing maintenance/backup/atomic-switch procedure. No live user,
credential or task records are changed. Baseline is captured separately, read-only.
"""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
MODE = sys.argv[1]
assert MODE in {'render', 'check', 'probe', 'deploy', 'verify'}
BASELINE = ROOT/'storage/security-hardening-baseline/baseline.json'
live = json.loads(BASELINE.read_text(encoding='utf-8'))
normalize = lambda value: value.replace(b'\r\n', b'\n')
changed = set()
for path in (ROOT/'app').rglob('*'):
    if not path.is_file() or path.suffix not in {'.py', '.js', '.css', '.html'} or '__pycache__' in path.parts:
        continue
    name = path.relative_to(ROOT).as_posix()
    if name not in live['files'] or normalize(path.read_bytes()) != normalize(base64.b64decode(live['files'][name])):
        changed.add(name)
names = sorted(changed | {path.relative_to(ROOT).as_posix() for path in (ROOT/'tests').glob('*.py')})
assert all(Path(name).suffix in {'.py', '.js', '.css', '.html'} for name in names)
contents = {name: normalize((ROOT/name).read_bytes()) for name in names}
payload = {name: base64.b64encode(data).decode() for name, data in contents.items()}
baseline = {name: hashlib.sha256(normalize(base64.b64decode(value))).hexdigest()
            for name, value in live['files'].items() if name.startswith('app/')}
assert any(name.startswith('app/') for name in names), 'No reviewed application delta'

remote = (ROOT/'deploy/ecs/release-account-policy-remote.py').read_text(encoding='utf-8')
remote = remote.replace("EXPECTED = '20260928T145154Z-multiuser'", 'EXPECTED = '+repr(Path(live['release']).name))
remote = remote.replace('account-policy-timing', 'security-hardening')
remote = remote.replace('before-account-policy-', 'before-security-hardening-')
remote = remote.replace('.current-account-policy-', '.current-security-hardening-')
remote = remote.replace('account-policy-release.json', 'security-hardening-release.json')
remote = remote.replace("if relative == 'private/accounts.db' and table == 'users':", 'if False:')
remote = remote.replace('Historical records changed beyond forced-password flag', 'Historical records changed')
remote = remote.replace('counts = idle()\nif mode', '''for name in payload:
    if name.startswith('app/') and name not in baseline:
        assert not (PREVIOUS/name).exists(), 'New live file appeared after capture: '+name
counts = idle()
if mode''')
remote = remote.replace("del service_env", '''
assert service_env.get(b'APP_AUTH_ENABLED', b'').lower() == b'true', 'Production authentication must be enabled'
assert service_env.get(b'APP_COOKIE_SECURE', b'true').lower() == b'true', 'Production cookies must be secure'
assert service_env.get(b'APP_ALLOW_INSECURE_LOCAL_HTTP', b'false').lower() != b'true', 'Production HTTP opt-in must be disabled'
from urllib.parse import urlsplit
for filename, fields in {
    'generation-settings.json': ('base_url', 'public_base_url'),
    'continuation-settings.json': ('base_url',),
    'redaction-service.json': ('endpoint',),
    'storage-settings.json': ('endpoint',),
}.items():
    config_file = DATA/'private'/filename
    if config_file.is_file():
        config = json.loads(config_file.read_text())
        for field in fields:
            if config.get(field):
                assert urlsplit(config[field]).scheme == 'https', 'Non-HTTPS live configuration: '+filename+':'+field
for key in (b'SEEDANCE_API_URL', b'TOS_ENDPOINT', b'PORTRAIT_PUBLIC_BASE_URL', b'APP_PUBLIC_ORIGIN'):
    if service_env.get(key):
        assert urlsplit(os.fsdecode(service_env[key])).scheme == 'https', 'Non-HTTPS environment URL: '+key.decode()
del service_env
''')

begin = remote.index('    with sqlite3.connect', remote.index('def verify(release):'))
end = remote.index("\nif mode == 'verify':", begin)
remote = remote[:begin]+'''    assert http('/api/model-settings')[0] == http('/api/link-settings')[0] == 401
    return {'health': 'ok', 'anonymous_api': 'denied', 'files_verified': len(payload)}
'''+remote[end:]

tests = [
    'tests/test_security_errors.py', 'tests/test_secure_transport.py', 'tests/test_media_upload_validation.py',
    'tests/test_authentication.py', 'tests/test_access_control.py', 'tests/test_tenant_link_security.py',
    'tests/test_generation_settings.py', 'tests/test_admin_settings.py', 'tests/test_production_api.py',
    'tests/test_production_worker.py', 'tests/test_video_provider.py', 'tests/test_reference_media.py',
    'tests/test_redaction_service.py', 'tests/test_continuation_llm.py', 'tests/test_continuation_settings.py',
    'tests/test_workflow_api.py', 'tests/test_workflow_worker.py', 'tests/test_video_link_import.py',
]
# The security regression test names are part of the payload and must exist.
assert all((ROOT/name).is_file() for name in tests), 'Regression test manifest is incomplete'
probe = '''import os,tempfile,pytest
with tempfile.TemporaryDirectory(prefix='ark-security-probe-') as directory:
 os.environ.update(STORAGE_DIR=directory,WORKFLOW_DB=directory+'/workflow.db',WORKFLOW_STORAGE=directory+'/workflow',DATABASE_URL='sqlite:///'+directory+'/studio.db',APP_AUTH_ENABLED='false',SEEDANCE_MODE='mock',PYTHON_DOTENV_DISABLED='1',PYTHONDONTWRITEBYTECODE='1',SEEDANCE_API_KEY='',SEEDANCE_API_URL='',TIKHUB_API_KEY='')
 os.environ.pop('APP_ALLOW_INSECURE_LOCAL_HTTP',None)
 raise SystemExit(pytest.main(TESTS+['-q','-rs','-p','no:cacheprovider','--basetemp='+directory+'/pytest']))
'''.replace('TESTS', repr(tests))
begin = remote.index('# Real routes and SQLite')
end = remote.index('other_pid = ctl(', begin)
remote = remote[:begin]+'''# Verify the staged app as the service user, against isolated data and fake credentials.
try:
    result=run('runuser','-u','ark-video-workflow','--','env','LD_LIBRARY_PATH='+library_path,
               str(ROOT/'venv/bin/python'),'-c',PROBE,cwd=release)
    print(result.stdout.decode(),flush=True)
except subprocess.CalledProcessError as error:
    raise RuntimeError('Security regression probe failed before maintenance:\\n'+error.stdout.decode(errors='replace')+error.stderr.decode(errors='replace')) from None
if mode == 'probe':
    print(json.dumps({'isolated_security_probe':'passed','staged_release':str(release)}))
    raise SystemExit()
'''.replace('PROBE', repr(probe))+remote[end:]

# Add HSTS only to this application's TLS virtual host; leave the separate 443 site intact.
remote = remote.replace('old_nginx = NGINX.read_bytes()', '''old_nginx = NGINX.read_bytes()
assert digest(old_nginx) == EXPECTED_NGINX, 'Proxy configuration changed since capture'
assert b'Strict-Transport-Security' not in old_nginx, 'Review existing HSTS policy before replacing'
new_nginx = old_nginx.replace(b'    ssl_protocols TLSv1.2 TLSv1.3;', b'    ssl_protocols TLSv1.2 TLSv1.3;\\n    add_header Strict-Transport-Security "max-age=86400" always;', 1)
assert new_nginx != old_nginx
deployed_ok = False
'''.replace('EXPECTED_NGINX', repr(hashlib.sha256(base64.b64decode(live['nginx'])).hexdigest())))
remote = remote.replace('assert new_nginx != old_nginx', '''assert new_nginx != old_nginx
MEDIA_NGINX = Path('/etc/nginx/conf.d/ark-video-media.conf')
old_media_nginx = MEDIA_NGINX.read_bytes()
assert digest(old_media_nginx) == EXPECTED_MEDIA_NGINX, 'Media proxy changed since capture'
assert old_media_nginx.count(b'server {') == 1 and b'listen 8444 ' in old_media_nginx
assert b'Strict-Transport-Security' not in old_media_nginx
new_media_nginx = old_media_nginx.replace(b'    ssl_protocols TLSv1.2 TLSv1.3;', b'    ssl_protocols TLSv1.2 TLSv1.3;\\n    add_header Strict-Transport-Security "max-age=86400" always;', 1)
# Location-specific CORS headers disable add_header inheritance in this listener.
new_media_nginx = new_media_nginx.replace(b'        add_header X-Content-Type-Options', b'        add_header Strict-Transport-Security "max-age=86400" always;\\n        add_header X-Content-Type-Options', 1)
assert new_media_nginx.count(b'Strict-Transport-Security') == 2
'''.replace('EXPECTED_MEDIA_NGINX', repr(hashlib.sha256(base64.b64decode(live['nginx_media'])).hexdigest())))
remote = remote.replace("NGINX.write_bytes(old_nginx.replace(b'server {', b'server {\\n    return 503;', 1))",
    "NGINX.write_bytes(old_nginx.replace(b'server {', b'server {\\n    return 503;', 1))\n    MEDIA_NGINX.write_bytes(old_media_nginx.replace(b'server {', b'server {\\n    return 503;', 1))")
remote = remote.replace('NGINX.write_bytes(old_nginx)\n', 'NGINX.write_bytes(new_nginx if deployed_ok else old_nginx)\n')
remote = remote.replace('NGINX.write_bytes(new_nginx if deployed_ok else old_nginx)\n',
                       'NGINX.write_bytes(new_nginx if deployed_ok else old_nginx)\n        MEDIA_NGINX.write_bytes(new_media_nginx if deployed_ok else old_media_nginx)\n')
remote = remote.replace("(backup/'config/nginx.conf').chmod(0o600)",
                       "(backup/'config/nginx.conf').chmod(0o600)\n    (backup/'config/nginx-media.conf').write_bytes(old_media_nginx)\n    (backup/'config/nginx-media.conf').chmod(0o600)")
remote = remote.replace("assert snapshot(before) == before, 'Historical records changed'", "assert snapshot(before) == before, 'Historical records changed'\n    deployed_ok = True")
remote = remote.replace('except Exception as error:\n    if switched', 'except Exception as error:\n    deployed_ok = False\n    if switched')
remote = remote.replace("'isolated_policy_probe': 'passed'", "'isolated_security_probe': 'passed'")
revision = 'security-hardening-'+hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]
code = 'mode='+repr(MODE)+'\npayload='+repr(payload)+'\nbaseline='+repr(baseline)+'\nsource_revision='+repr(revision)+'\n'+remote
compile(code, '<security-hardening-release>', 'exec')
if MODE == 'render':
    (ROOT/'storage/security-hardening-candidate.py').write_text(code, encoding='utf-8')
    print(json.dumps({'revision': revision, 'files': len(payload), 'compiled': True}))
    raise SystemExit()
log = ROOT/'storage'/('security-hardening-'+MODE+'.log')
result = subprocess.run(['ssh', '-F', str(ROOT/'ssh/ECS-YRXT.conf'), 'ECS-YRXT', 'python3', '-'],
                        input=code.encode(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
log.write_bytes(result.stdout)
sys.stdout.buffer.write(result.stdout)
raise SystemExit(result.returncode)
