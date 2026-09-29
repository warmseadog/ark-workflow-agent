"""Incremental account-policy/timing release; never reads or resets live passwords."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
BASE_REF = 'e7b5de3'
parser = argparse.ArgumentParser()
parser.add_argument('mode', choices=['check', 'deploy', 'verify'])
args = parser.parse_args()

def git(*arguments):
    return subprocess.run(['git', *arguments], cwd=ROOT, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

baseline = {}
for name in git('ls-tree', '-r', '--name-only', BASE_REF, 'app').decode().splitlines():
    content = git('show', BASE_REF + ':' + name).replace(b'\r\n', b'\n')
    baseline[name] = hashlib.sha256(content).hexdigest()
paths = [p for p in (ROOT/'app').rglob('*') if p.is_file()
         and p.suffix in {'.py', '.html', '.js', '.css', '.svg'} and '__pycache__' not in p.parts]
payload = {p.relative_to(ROOT).as_posix(): base64.b64encode(p.read_bytes()).decode() for p in paths}
revision = git('rev-parse', 'HEAD').decode().strip()
source = ('mode=' + repr(args.mode) + '\npayload=' + repr(payload)
          + '\nbaseline=' + repr(baseline) + '\nsource_revision=' + repr(revision) + '\n')
source += (ROOT/'deploy/ecs/release-account-policy-remote.py').read_text(encoding='utf-8')
result = subprocess.run(['ssh', '-F', str(ROOT/'ssh/ECS-YRXT.conf'), '-o', 'BatchMode=yes',
                         'ECS-YRXT', 'python3 -'], input=source.encode(),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
(ROOT/'storage'/('account-policy-' + args.mode + '.log')).write_bytes(result.stdout)
sys.stdout.buffer.write(result.stdout)
raise SystemExit(result.returncode)
