"""Local playback derivatives. Never overwrite provider output or call a model."""
from pathlib import Path
import json
import os
import subprocess
import threading
from .production_store import ProductionStore,now

_lock=threading.Lock()

def source_path(settings,ident):
    root=(settings.storage_dir/'outputs').resolve()
    if ident.startswith('legacy-'):
        from .tenancy import legacy_allowed
        if not legacy_allowed(settings):raise LookupError('找不到任务。')
        from .jobs import store as jobs
        job=jobs.get(ident[7:])
        if not job or not job.output_name:raise LookupError('历史视频尚未就绪。')
        path=(root/job.output_name).resolve()
        if not path.is_relative_to(root):raise LookupError('视频文件不可用。')
        return path
    return root/(ident+'.mp4')

def signature(path):
    stat=path.stat()
    return f'{stat.st_size}:{stat.st_mtime_ns}'

def status(settings,ident):
    store=ProductionStore(settings.storage_dir);store.require_visible(ident)
    if ident.startswith('legacy-'):
        from .jobs import store as jobs
        job=jobs.get(ident[7:])
        run={'status':job.status if job else None}
    else:run=store.get_run(ident)
    if run['status']!='succeeded' or not source_path(settings,ident).is_file():
        raise LookupError('视频尚未就绪。')
    base='/api/production/runs/'+ident
    with store.connection() as db:row=db.execute('SELECT * FROM production_playbacks WHERE id=?',(ident,)).fetchone()
    state=row['status'] if row else 'queued'
    ready=state=='ready' and row['signature']==signature(source_path(settings,ident))
    root=settings.storage_dir/'playback'/ident
    ready=ready and all((root/(kind+'.mp4')).is_file() for kind in ('smooth','original'))
    return {'status':'ready' if ready else 'failed' if state in ('failed','ready') else 'processing',
            'smooth_url':base+'/playback/smooth' if ready else None,
            'original_url':base+'/playback/original' if ready else ('/api/jobs/'+ident[7:]+'/download' if ident.startswith('legacy-') else base+'/download'),
            'duration':json.loads(row['metadata']).get('duration') if row and row['metadata'] else None}

def media_path(settings,ident,quality):
    if quality not in ('smooth','original'):raise LookupError('播放版本不存在。')
    if status(settings,ident)['status']!='ready':raise LookupError('播放版本尚未准备好。')
    return settings.storage_dir/'playback'/ident/(quality+'.mp4')

def process_one(settings):
    if not _lock.acquire(blocking=False):return False
    try:
        store=ProductionStore(settings.storage_dir)
        with store.connection() as db:
            row=db.execute("""SELECT r.id FROM production_runs r LEFT JOIN production_playbacks p ON p.id=r.id
                WHERE r.status='succeeded' AND r.id NOT IN (SELECT id FROM production_deleted_runs)
                AND (p.id IS NULL OR p.status='queued') ORDER BY r.created_at DESC LIMIT 1""").fetchone()
            if row:ident=row['id']
            else:
                from .tenancy import legacy_allowed
                if not legacy_allowed(settings):return False
                from .jobs import store as jobs
                excluded={r[0] for r in db.execute("SELECT id FROM production_playbacks WHERE status!='queued' UNION SELECT id FROM production_deleted_runs")}
                candidates=[job for job in jobs.list() if job.status=='succeeded' and job.output_name and 'legacy-'+job.id not in excluded]
                if not candidates:return False
                ident='legacy-'+max(candidates,key=lambda job:job.created_at).id
            db.execute("INSERT OR REPLACE INTO production_playbacks VALUES (?,?,?,?,?,?)",(ident,'processing','',None,'',now()))
        root=settings.storage_dir/'playback'/ident
        temporary=[root/(quality+'.tmp.mp4') for quality in ('original','smooth')]
        try:
            root.mkdir(parents=True,exist_ok=True)
            import imageio_ffmpeg
            from .person_video import probe
            source=source_path(settings,ident);before=signature(source);metadata=probe(source)
            binary=imageio_ffmpeg.get_ffmpeg_exe()
            common=[binary,'-hide_banner','-loglevel','error','-nostdin','-y','-threads','1','-i',str(source),'-map','0:v:0','-map','0:a:0?']
            commands=[common+['-c','copy','-movflags','+faststart',str(temporary[0])],
                common+['-vf','scale=1280:1280:force_original_aspect_ratio=decrease:force_divisible_by=2','-c:v','libx264','-preset','fast','-crf','24','-maxrate','2500k','-bufsize','5000k','-threads','1','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-movflags','+faststart',str(temporary[1])]]
            for command in commands:
                if os.name!='nt':command=['nice','-n','10',*command]
                subprocess.run(command,check=True,capture_output=True,timeout=180)
            if signature(source)!=before:raise ValueError('Source changed during processing')
            for path,quality in zip(temporary,('original','smooth')):
                if not path.is_file() or not path.stat().st_size:raise ValueError('Empty playback output')
                path.replace(root/(quality+'.mp4'))
            with store.connection() as db:
                db.execute('UPDATE production_playbacks SET status=?,signature=?,metadata=?,message=?,updated_at=? WHERE id=?',
                    ('ready',before,json.dumps(metadata),'播放版本已就绪',now(),ident))
        except Exception:
            with store.connection() as db:db.execute('UPDATE production_playbacks SET status=?,message=?,updated_at=? WHERE id=?',('failed','流畅预览暂不可用，可播放或下载原片。',now(),ident))
        finally:
            for path in temporary:
                try:path.unlink(missing_ok=True)
                except OSError:pass
        return True
    finally:_lock.release()
