"""Offline HTTP admission benchmark; run directly, never against an existing server.

Creates fresh local data and a loopback server; real API/auth/SQLite/scheduler,
synthetic worker execution. No dotenv, credentials, or paid provider calls.
Usage: python tests/bench_queue_admission.py --output storage/admission-bench-UNIQUE
"""
import argparse
import asyncio
from collections import Counter
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def offline(folder):
    import dotenv
    dotenv.load_dotenv = lambda *a, **kw: False
    os.environ.update(STORAGE_DIR=str(folder/'data'), APP_AUTH_ENABLED='true',
        APP_COOKIE_SECURE='false', SEEDANCE_MODE='mock', SEEDANCE_API_KEY='',
        SEEDANCE_API_URL='', APP_VIDEO_WORKERS='10', APP_SUBMISSION_WORKERS='4',
        APP_MAX_QUEUED_TOTAL='30', APP_QUEUE_TIMEOUT_SECONDS='600',
        WORKFLOW_DB=str(folder/'data/workflow.db'),
        WORKFLOW_STORAGE=str(folder/'data/workflow'),
        DATABASE_URL='sqlite:///'+(folder/'data/studio.db').as_posix())
    def guard(event, args):
        if event == 'socket.connect' and isinstance(args[1], tuple) and args[1][0] not in ('127.0.0.1', '::1', 'localhost'):
            raise RuntimeError('Offline benchmark blocked external network')
    sys.addaudithook(guard)


def server(folder, port):
    offline(folder)
    os.environ['APP_PUBLIC_ORIGIN'] = f'http://127.0.0.1:{port}'
    from app import main, production_worker
    from app.accounts import Accounts
    accounts = Accounts(main.settings.storage_dir)
    admin = accounts.init_admin('benchadmin', 'local-benchmark-only')
    for i in range(12):
        accounts.create_user(f'bench{i}', 'local-benchmark-only', admin['id'], max_concurrent=1, max_queued=3)
    def simulated(settings, store, run):
        # Exercise the real scheduler, keeping slots busy deterministically.
        store.update_run(run['id'], stage='generating', provider_task_id='fake-'+run['id'])
        deadline = time.monotonic()+180
        while (folder/'hold').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('benchmark hold deadline')
            time.sleep(.05)
        time.sleep(.05)
        store.update_run(run['id'], status='succeeded', stage='done', progress=100)
    production_worker.execute_run = simulated
    import uvicorn
    uvicorn.run(main.app, host='127.0.0.1', port=port, access_log=False, log_level='warning')


async def measure(folder, port):
    import httpx
    from PIL import Image
    import imageio_ffmpeg
    source = folder/'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-y', '-v', 'error', '-f', 'lavfi',
        '-i', 'testsrc2=s=640x360:r=24:d=3', '-threads', '2', '-c:v', 'libx264',
        '-pix_fmt', 'yuv420p', str(source)], check=True, capture_output=True, timeout=30)
    for name in ('face', 'clothes'):
        Image.new('RGB', (256, 256), (100, 120, 140)).save(folder/(name+'.png'))
    base = f'http://127.0.0.1:{port}'
    actors, raw, summary = [], [], []
    async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=15,
            limits=httpx.Limits(max_connections=120, max_keepalive_connections=40)) as client:
        for i in range(12):
            r = await client.post('/api/auth/login', json={'username':f'bench{i}', 'password':'local-benchmark-only'}, headers={'Origin':base})
            assert r.status_code == 200, r.text
            headers = {'Origin':base, 'X-CSRF-Token':r.json()['csrf_token'], 'Cookie':'ark_session='+r.cookies['ark_session']}
            client.cookies.clear()
            assets = {}
            for kind, name in [('video','source.mp4'), ('face','face.png'), ('clothing','clothes.png')]:
                r = await client.post('/api/production/assets', headers=headers, data={'kind':kind},
                    files={'file':(name,(folder/name).read_bytes(), 'video/mp4' if kind=='video' else 'image/png')})
                assert r.status_code == 200, r.text
                assets[kind] = r.json()['id']
            r = await client.post('/api/production/drafts', headers=headers, json={'person_input_policy':'legacy_raw'})
            assert r.status_code == 200, r.text
            draft = r.json()
            r = await client.put('/api/production/drafts/'+draft['id'], headers=headers, json={
                'revision':draft['revision'], 'source_asset_id':assets['video'],
                'face_asset_ids':[assets['face']], 'clothing_asset_ids':[assets['clothing']], 'prompt':'offline admission benchmark'})
            assert r.status_code == 200, r.text
            actors.append((headers, r.json()))
        async def call(stage, method, path, i=0, body=None):
            start = time.perf_counter()
            try:
                r = await client.request(method, path, headers=actors[i%12][0], json=body)
                code, data = r.status_code, r.json()
            except Exception as exc:
                code, data = 0, {'exception':str(exc)}
            raw.append({'stage':stage,'code':code,'ms':round((time.perf_counter()-start)*1000,2)})
            return code, data
        def body(i, key):
            draft = actors[i%12][1]
            return {'draft_id':draft['id'], 'revision':draft['revision'], 'idempotency_key':key}
        def report(stage):
            rows = [r for r in raw if r['stage']==stage]
            timings = sorted(r['ms'] for r in rows)
            entry = {'stage':stage,'requests':len(rows),'status':dict(Counter(r['code'] for r in rows)),
                'p95_ms':timings[math.ceil(len(timings)*.95)-1], 'max_ms':max(timings)}
            summary.append(entry)
            print(json.dumps(entry), flush=True)
        async def states():
            result = []
            for i in range(12):
                code, data = await call('observe','GET','/api/production/runs?page=1&page_size=50',i)
                assert code==200, data
                result.extend((i,r) for r in data['items'])
            return result
        # Saturate execution slots before the burst, then exercise global waiting cap.
        (folder/'hold').write_text('hold')
        for i in range(10):
            code, data = await call('warmup','POST','/api/production/runs',i,body(i,'warmup'))
            assert code==200, data
        for _ in range(50):
            if sum(r['status']=='running' for _,r in await states())==10:break
            await asyncio.sleep(.1)
        else:raise AssertionError('worker slots not occupied')
        burst = await asyncio.gather(*(call('burst120','POST','/api/production/runs',i,body(i,f'burst-{i}')) for i in range(120)))
        assert all(code in (200,429) for code,_ in burst), Counter(code for code,_ in burst)
        report('burst120')
        # Controlled retries fill the queue; ingress rejection is intentional.
        for i in range(60):
            code, data = await call('fill','POST','/api/production/runs',i,body(i,f'fill-{i}'))
            assert code in (200,429), data
        rows = await states()
        checks = {'running':sum(r['status']=='running' for _,r in rows),
            'queued':sum(r['status']=='queued' for _,r in rows)}
        assert checks == {'running':10,'queued':30}, checks
        full = await asyncio.gather(*(call('full120','POST','/api/production/runs',i,body(i,f'full-{i}')) for i in range(120)))
        assert all(code==429 for code,_ in full), Counter(code for code,_ in full)
        report('full120')
        duplicates = await asyncio.gather(*(call('replay20','POST','/api/production/runs',0,body(0,'warmup')) for _ in range(20)))
        assert all(code==200 for code,_ in duplicates)
        assert len({r['id'] for _,r in duplicates})==1
        report('replay20')
        owner, queued = next((i,r) for i,r in rows if r['status']=='queued')
        code, data = await call('cancel','POST',f"/api/production/runs/{queued['id']}/cancel",owner)
        assert code==200, data
        code, data = await call('replacement','POST','/api/production/runs',owner,body(owner,'replacement'))
        assert code==200, data
        checks['cancel_releases_slot'] = True
        start = time.perf_counter()
        tasks = []
        for i in range(600):
            await asyncio.sleep(max(0,start+i/60-time.perf_counter()))
            tasks.append(asyncio.create_task(call('reads60rps','GET','/api/production/runs?page=1&page_size=10',i)))
        assert all(code==200 for code,_ in await asyncio.gather(*tasks))
        report('reads60rps')
        (folder/'hold').unlink()
        start = time.perf_counter()
        while time.perf_counter()-start < 30:
            rows = await states()
            if all(r['status'] in ('succeeded','cancelled') for _,r in rows):break
            await asyncio.sleep(.2)
        else:raise AssertionError('queue failed to drain')
        checks['drain_seconds'] = round(time.perf_counter()-start,2)
        checks['final_status'] = dict(Counter(r['status'] for _,r in rows))
        (folder/'results.json').write_text(json.dumps({'summary':summary,'checks':checks,'raw':raw},indent=2),encoding='utf-8')
        print(json.dumps(checks),flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--server', type=int)
    args = parser.parse_args()
    folder = args.output.resolve()
    if args.server:
        return server(folder,args.server)
    # Refuse existing data; never touch production storage or previous results.
    folder.mkdir(parents=True,exist_ok=False)
    offline(folder)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    keep = {'SYSTEMROOT','WINDIR','PATH','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','COMSPEC','PATHEXT'}
    env = {k:v for k,v in os.environ.items() if k.upper() in keep}
    env.update(PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1')
    with (folder/'server.log').open('w',encoding='utf-8') as log:
        proc = subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--output',str(folder),'--server',str(port)],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
        (folder/'server-process.json').write_text(json.dumps({'pid':proc.pid,'port':port}),encoding='utf-8')
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            for _ in range(100):
                if proc.poll() is not None:raise RuntimeError('server exited; see server.log')
                try:
                    if opener.open(f'http://127.0.0.1:{port}/healthz',timeout=1).status==200:break
                except OSError:time.sleep(.2)
            else:raise RuntimeError('server startup timed out')
            asyncio.run(measure(folder,port))
        finally:
            (folder/'hold').unlink(missing_ok=True)
            if os.name=='nt':
                # The venv redirector may own a Python child on Windows.
                subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True)
            else:
                proc.terminate()
            try:proc.wait(15)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()


if __name__=='__main__':
    main()
