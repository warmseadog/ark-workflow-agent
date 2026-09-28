from dataclasses import replace
from pathlib import Path
import hashlib,struct,subprocess
import imageio_ffmpeg
from fastapi.testclient import TestClient
import pytest
from app import main,production_worker
from app.production_store import ProductionStore

@pytest.fixture
def setup(tmp_path,monkeypatch):
    settings=replace(main.settings,storage_dir=tmp_path,seedance_mode='mock')
    from app import jobs
    monkeypatch.setattr(jobs,'store',jobs.JobStore())
    monkeypatch.setattr(main,'settings',settings);monkeypatch.setattr(production_worker,'wake',lambda *_:None)
    store=ProductionStore(tmp_path);draft=store.create_draft({});run=store.create_run(draft['id'],1,'preview',{})
    store.update_run(run['id'],status='succeeded')
    root=tmp_path/'outputs';root.mkdir();path=root/(run['id']+'.mp4')
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-y','-f','lavfi','-i','testsrc2=s=640x360:r=24','-t','2','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True,capture_output=True)
    return settings,store,run,path,TestClient(main.app)

def atoms(path):
    result=[]
    with path.open('rb') as f:
        while True:
            data=f.read(8)
            if len(data)<8:return result
            size,kind=struct.unpack('>I4s',data);result.append(kind)
            if size==1:size=struct.unpack('>Q',f.read(8))[0];f.seek(size-16,1)
            elif size>0:f.seek(size-8,1)
            else:return result

def test_real_preview_faststart_original_preserved_and_range_served(setup):
    from app.playback import process_one
    settings,store,run,path,client=setup;before=hashlib.sha256(path.read_bytes()).hexdigest()
    process_one(settings)
    response=client.get('/api/production/runs/'+run['id']+'/playback')
    assert response.status_code==200,response.text
    data=response.json();assert data['status']=='ready'
    for quality in ('smooth','original'):
        media=client.get(data[quality+'_url'],headers={'Range':'bytes=0-1023'})
        assert media.status_code==206 and len(media.content)==1024
        cached=settings.storage_dir/'playback'/run['id']/(quality+'.mp4')
        sequence=atoms(cached);assert sequence.index(b'moov')<sequence.index(b'mdat')
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    stamp=(settings.storage_dir/'playback'/run['id']/'smooth.mp4').stat().st_mtime_ns
    process_one(settings)
    assert (settings.storage_dir/'playback'/run['id']/'smooth.mp4').stat().st_mtime_ns==stamp
    store.delete_run(run['id'])
    assert client.get(data['smooth_url']).status_code==404

def test_failed_preview_does_not_fail_generation_or_replace_original(setup):
    from app.playback import process_one
    settings,store,run,path,client=setup;path.write_bytes(b'invalid video')
    process_one(settings)
    response=client.get('/api/production/runs/'+run['id']+'/playback')
    assert response.json()['status']=='failed'
    assert response.json()['original_url'].endswith('/download')
    assert store.get_run(run['id'])['status']=='succeeded' and path.read_bytes()==b'invalid video'

def test_named_download_keeps_bytes(setup):
    _,store,run,path,client=setup
    store.rename_run(run['id'],'春季穿搭')
    response=client.get('/api/production/runs/'+run['id']+'/download')
    assert response.content==path.read_bytes()
    assert 'content-disposition' in response.headers


def test_cache_directory_failure_becomes_failed_not_stuck_processing(setup,monkeypatch):
    from app.playback import process_one
    settings,store,run,path,client=setup
    original=Path.mkdir
    def mkdir(path,*a,**kw):
        if path.parent.name=='playback':raise OSError('fixture disk failure')
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'mkdir',mkdir)
    process_one(settings)
    assert client.get('/api/production/runs/'+run['id']+'/playback').json()['status']=='failed'

def test_legacy_completed_video_gets_playback_cache(setup,monkeypatch):
    from app.playback import process_one
    from app import jobs
    settings,store,run,path,client=setup
    legacy=jobs.JobStore();monkeypatch.setattr(jobs,'store',legacy)
    job=legacy.create();legacy.update(job.id,status='succeeded',output_name=path.name)
    process_one(settings);process_one(settings)
    ident='legacy-'+job.id
    result=client.get('/api/production/runs/'+ident+'/playback')
    assert result.status_code==200,result.text
    assert result.json()['status']=='ready'
    assert client.get(result.json()['smooth_url'],headers={'Range':'bytes=0-10'}).status_code==206
    client.delete('/api/production/runs/'+ident)
    assert client.get(result.json()['smooth_url']).status_code==404
