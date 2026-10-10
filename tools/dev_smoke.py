"""Offline end-to-end check using a new database and actual media subprocesses."""
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

from tools.dev import ROOT, install_network_guard, prepare_environment


def run():
    # A separate root makes this repeatable after either profile has been used.
    root = Path(tempfile.mkdtemp(prefix='smoke-', dir=ROOT/'.dev-data'))
    env = prepare_environment(root, 'demo', 8000)
    os.environ.clear()
    os.environ.update(env)
    install_network_guard()
    import imageio_ffmpeg
    from PIL import Image
    from fastapi.testclient import TestClient
    from tools.dev_app import app
    source = root/'source.mp4'
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-v','error','-f','lavfi',
        '-i','color=c=blue:s=320x240:d=3','-c:v','libx264',str(source)],
        check=True, timeout=30, capture_output=True)
    photo = io.BytesIO()
    Image.new('RGB',(320,320),(80,120,160)).save(photo,format='PNG')
    checks = []

    def ok(response, label):
        if response.status_code != 200:
            raise RuntimeError(f'{label}: HTTP {response.status_code}: {response.text[:500]}')
        checks.append(label)
        return response

    # Real lifespan: execute the actual queue, local masking and mock provider.
    with TestClient(app, base_url='http://127.0.0.1:8000') as client:
        for path in ['/__dev','/healthz','/','/admin/settings','/api/production/model-options',
                     '/api/portrait/people','/api/prompt-templates','/api/support/config',
                     '/api/production/drafts','/api/production/runs']:
            ok(client.get(path),path)
        for path in ['/api/production/assets/import','/api/model-settings','/api/portrait/sessions']:
            response = client.request('PUT' if path == '/api/model-settings' else 'POST',path,json={})
            if response.status_code != 409 or 'integration' not in response.text:
                raise RuntimeError('Demo capability isolation failed: '+path)
        checks.append('cloud actions and provider changes blocked in demo')
        assets = {}
        for kind, name, content, mime in [
            ('video','source.mp4',source.read_bytes(),'video/mp4'),
            ('face','person.png',photo.getvalue(),'image/png'),
            ('clothing','clothing.png',photo.getvalue(),'image/png')]:
            assets[kind] = ok(client.post('/api/production/assets',data={'kind':kind},
                files={'file':(name,content,mime)}), 'upload '+kind).json()['id']
        draft = ok(client.post('/api/production/drafts',json={'person_input_policy':'auto_virtual'}),
                   'create draft').json()
        draft = ok(client.put('/api/production/drafts/'+draft['id'],json={
            'revision':draft['revision'],'source_asset_id':assets['video'],
            'face_asset_ids':[assets['face']],'clothing_asset_ids':[assets['clothing']],
            'prompt':'Local development smoke test'}),'save draft').json()
        job = ok(client.post('/api/production/runs',json={
            'draft_id':draft['id'],'revision':draft['revision'],'idempotency_key':'smoke'}),
            'submit mock job').json()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job = client.get('/api/production/runs/'+job['id']).json()
            if job['status'] in {'succeeded','failed','needs_attention','cancelled'}:
                break
            time.sleep(.2)
        if job['status'] != 'succeeded':
            raise RuntimeError('Smoke job: '+json.dumps({k:job.get(k) for k in
                ['status','stage','error','message']},ensure_ascii=False))
        checks.append('worker + real masking + mock provider')
        downloaded = ok(client.get(job['download_url']), 'download output')
        output = root/'downloaded.mp4'
        output.write_bytes(downloaded.content)
        import cv2
        capture = cv2.VideoCapture(str(output))
        try:
            if not capture.isOpened() or not capture.read()[0]:
                raise RuntimeError('Downloaded result is not a decodable video')
        finally:
            capture.release()
        checks.append('decode downloaded video')
    report = {'ok':True,'profile':'demo','cloud_calls':0,'checks':checks,
              'artifacts':str(root)}
    (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0
