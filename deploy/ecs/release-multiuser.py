"""Scoped SSH release driver. Handoff credentials are written only to ignored storage."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

root=Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser()
parser.add_argument('mode',choices=['check','deploy','verify'])
args=parser.parse_args()
ssh=['ssh','-F',str(root/'ssh/ECS-YRXT.conf'),'-o','BatchMode=yes','ECS-YRXT','python3 -']
storage=root/'storage'
baseline_file=storage/'multiuser-baseline.json'
credentials_file=storage/'multiuser-admin-handoff.json'
allowed={'.py','.html','.js','.css','.svg'}
paths=[str(path.relative_to(root)).replace('\\','/') for path in (root/'app').rglob('*')
       if path.is_file() and path.suffix in allowed and '__pycache__' not in path.parts]
paths+=['requirements.txt','deploy/ecs/ark-video-workflow.nginx.conf','deploy/ecs/ark-video-workflow.service']
payload={name:base64.b64encode((root/name).read_bytes()).decode() for name in paths}
if args.mode=='check':
    source="""import pathlib,hashlib,json
root=pathlib.Path('/opt/ark-video-workflow/current').resolve()
assert root.name=='20260928T135707Z-inline-settings', 'Unexpected live baseline'
print(json.dumps({str(p.relative_to(root)):hashlib.sha256(p.read_bytes().replace(b'\\r\\n',b'\\n')).hexdigest() for p in (root/'app').rglob('*') if p.is_file() and '__pycache__' not in p.parts}))
"""
    result=subprocess.run(ssh,input=source.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
    baseline_file.write_bytes(result.stdout)
baseline=json.loads(baseline_file.read_text(encoding='utf-8'))
if args.mode=='deploy' and not credentials_file.exists():
    credentials_file.write_text(json.dumps({'url':'https://118.196.7.195:8443/login','username':'admin',
        'temporary_password':secrets.token_urlsafe(24),'notice':'首次登录必须修改密码；改密后请删除此本地交付文件。'},ensure_ascii=False,indent=2),encoding='utf-8')
    if os.name=='nt':
        owner=os.environ['USERDOMAIN']+'\\'+os.environ['USERNAME']
        subprocess.run(['icacls',str(credentials_file),'/inheritance:r','/grant:r',owner+':F','SYSTEM:F'],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
    else:credentials_file.chmod(0o600)
credentials=json.loads(credentials_file.read_text(encoding='utf-8')) if credentials_file.exists() else None
source='mode='+repr(args.mode)+'\npayload='+repr(payload)+'\nbaseline='+repr(baseline)+'\ncredentials='+repr(credentials)+'\n'
source+=(root/'deploy/ecs/release-multiuser-remote.py').read_text(encoding='utf-8')
result=subprocess.run(ssh,input=source.encode(),stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
log=result.stdout
if credentials:
    log=log.replace(credentials['temporary_password'].encode(),b'[REDACTED]')
(storage/('multiuser-deploy-'+args.mode+'.log')).write_bytes(log)
sys.stdout.buffer.write(log)
raise SystemExit(result.returncode)
