"""Deploy the continuation feature over a captured production source baseline."""
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[2]
mode = sys.argv[1]
assert mode in ('capture','check','deploy','verify')
baseline_path = root/'storage/continuation-live-baseline.json'
ssh = ['ssh','-F',str(root/'ssh/ECS-YRXT.conf'),'ECS-YRXT','python3','-']
if mode == 'capture':
    code = '''import base64,json
from pathlib import Path
release=Path('/opt/ark-video-workflow/current').resolve()
assert release.is_relative_to(Path('/opt/ark-video-workflow/releases'))
files={str(p.relative_to(release)):base64.b64encode(p.read_bytes()).decode() for p in (release/'app').rglob('*') if p.is_file() and p.suffix in {'.py','.js','.css','.html'}}
print(json.dumps({'release':str(release),'files':files}))
'''
    result=subprocess.run(ssh,input=code.encode(),capture_output=True,check=True)
    live=json.loads(result.stdout)
    baseline_path.write_text(json.dumps(live),encoding='utf-8')
    print(json.dumps({'release':live['release'],'source_files':len(live['files'])}))
    raise SystemExit()

live=json.loads(baseline_path.read_text(encoding='utf-8'))
names = [
    'app/continuation.py','app/continuation_media.py','app/continuation_llm.py','app/continuation_settings.py',
    'app/main.py','app/production_router.py','app/production_store.py','app/production_worker.py',
    'app/video_provider.py','app/reference_media.py','app/storage_settings.py',
    'app/static/continuation-settings.js','app/static/admin-settings.js','app/static/generation-options.js',
    'app/static/production.js','app/static/production-runs.js','app/templates/admin_settings.html',
    'app/templates/workspace_sidebar.html','app/templates/production_panel.html',
    'app/templates/production.html','app/templates/studio.html',
    'tests/test_continuation.py','tests/test_continuation_api.py','tests/test_continuation_llm.py',
    'tests/test_continuation_media.py','tests/test_continuation_provider.py','tests/test_continuation_settings.py',
    'tests/test_continuation_transport.py','tests/test_continuation_worker.py',
    'tests/test_production_worker.py','tests/test_production_api.py','tests/test_video_provider.py',
    'tests/test_duration_slider_browser.py',
]
normalize=lambda data:data.replace(b'\r\n',b'\n')
payload={name:base64.b64encode(normalize((root/name).read_bytes())).decode() for name in names}
# These three files also contain unrelated local changes. Merge only the
# continuation additions into production, retaining its authorization/routes.
def live_text(name):
    return normalize(base64.b64decode(live['files'][name])).decode('utf-8')
def local_text(name):
    return (root/name).read_text(encoding='utf-8')
def replace_once(text,old,new):
    assert text.count(old)==1,'Ambiguous production merge anchor: '+old[:80]
    return text.replace(old,new,1)
main=live_text('app/main.py')
main=replace_once(main,'from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile',
                  'from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile')
local_main=local_text('app/main.py')
start=local_main.index("@app.get('/api/continuation-settings')")
end=local_main.index("@app.put('/api/redaction-service')",start)
main=replace_once(main,"@app.put('/api/redaction-service')",local_main[start:end]+"@app.put('/api/redaction-service')")
payload['app/main.py']=base64.b64encode(main.encode()).decode()
admin=live_text('app/static/admin-settings.js')
admin=replace_once(admin,'  const sectionRequests = {',"  const sectionRequests = {\n    continuation: ['continuation-status', async () => window.ContinuationSettings.load()],")
payload['app/static/admin-settings.js']=base64.b64encode(admin.encode()).decode()
template=live_text('app/templates/admin_settings.html')
local_template=local_text('app/templates/admin_settings.html')
start=local_template.index('      <section id="section-continuation"')
end=local_template.index('      <section id="section-tikhub"',start)
template=replace_once(template,'      <section id="section-tikhub"',local_template[start:end]+'      <section id="section-tikhub"')
template=replace_once(template,'  <script src="/static/admin-settings.js?v=20260930-settings-simple-1"></script>',
    '  <script src="/static/continuation-settings.js?v=20260930-continuation-1"></script>\n  <script src="/static/admin-settings.js?v=20260930-continuation-1"></script>')
payload['app/templates/admin_settings.html']=base64.b64encode(template.encode()).decode()
baseline={name:hashlib.sha256(normalize(base64.b64decode(data))).hexdigest() for name,data in live['files'].items()}
remote=(root/'deploy/ecs/release-account-policy-remote.py').read_text(encoding='utf-8')
def substitute(old,new):
    global remote
    assert old in remote,'Release template changed: '+old[:70]
    remote=remote.replace(old,new)
substitute("EXPECTED = '20260928T145154Z-multiuser'",'EXPECTED = '+repr(Path(live['release']).name))
substitute('account-policy-timing','video-continuation')
substitute('before-account-policy-','before-video-continuation-')
substitute('.current-account-policy-','.current-video-continuation-')
substitute('account-policy-release.json','video-continuation-release.json')
substitute("if relative == 'private/accounts.db' and table == 'users':",'if False:')
substitute('Historical records changed beyond forced-password flag','Historical records changed')
begin=remote.index('    with sqlite3.connect',remote.index('def verify(release):'))
end=remote.index("\nif mode == 'verify':",begin)
remote=remote[:begin]+'''    assert http('/api/continuation-settings')[0] == 401
    assert b'continuation-settings.js' in (release/'app/templates/admin_settings.html').read_bytes()
    return {'health':'ok','anonymous_api':'denied','files_verified':len(payload)}
'''+remote[end:]
probe='''import os,tempfile,pytest
with tempfile.TemporaryDirectory(prefix='ark-continuation-probe-') as directory:
 os.environ.update(STORAGE_DIR=directory,WORKFLOW_DB=directory+'/workflow.db',WORKFLOW_STORAGE=directory+'/workflow',DATABASE_URL='sqlite:///'+directory+'/studio.db',APP_AUTH_ENABLED='false',SEEDANCE_MODE='mock')
 raise SystemExit(pytest.main(['tests/test_continuation.py','tests/test_continuation_api.py','tests/test_continuation_llm.py','tests/test_continuation_media.py','tests/test_continuation_provider.py','tests/test_continuation_settings.py','tests/test_continuation_transport.py','tests/test_continuation_worker.py','tests/test_production_worker.py','tests/test_production_api.py','tests/test_video_provider.py','-k','not test_admin_continuation_form','-q','-p','no:cacheprovider','--basetemp='+directory+'/pytest']))
'''
begin=remote.index('# Real routes and SQLite')
end=remote.index('other_pid = ctl(',begin)
remote=remote[:begin]+'''# Probe the candidate release using isolated data and mocked providers.
try:
    result=run('runuser','-u','ark-video-workflow','--','env','LD_LIBRARY_PATH='+library_path,
               str(ROOT/'venv/bin/python'),'-c',PROBE_CODE,cwd=release)
    print(result.stdout.decode(),flush=True)
except subprocess.CalledProcessError as error:
    raise RuntimeError('Regression probe failed before maintenance:\\n'+error.stdout.decode(errors='replace')+error.stderr.decode(errors='replace')) from None
'''.replace('PROBE_CODE',repr(probe))+remote[end:]
substitute("'isolated_policy_probe': 'passed'","'isolated_regression_probe': 'passed'")
substitute('try:\n    pause_public_requests()',"""try:
    assert CURRENT.resolve() == PREVIOUS, 'Live release changed during tests'
    for name, sha in baseline.items():
        assert digest((PREVIOUS/name).read_bytes().replace(b'\\r\\n', b'\\n')) == sha, 'Live code changed during tests: '+name
    pause_public_requests()""")
revision='video-continuation-'+hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()[:12]
code='mode='+repr(mode)+'\npayload='+repr(payload)+'\nbaseline='+repr(baseline)+'\nsource_revision='+repr(revision)+'\n'+remote
compile(code,'<video-continuation-release>','exec')
result=subprocess.run(ssh,input=code.encode(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
(root/'storage'/('video-continuation-'+mode+'.log')).write_bytes(result.stdout)
sys.stdout.buffer.write(result.stdout)
raise SystemExit(result.returncode)
