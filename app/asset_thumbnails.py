"""Private cached stills; video decoding is bounded and never sent to the browser."""
from pathlib import Path
import hashlib
import uuid
import subprocess
import threading
import time
from fastapi.responses import FileResponse, Response

_VIDEO = b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="180" viewBox="0 0 320 180"><rect width="320" height="180" fill="#e8edf4"/><path d="M140 60v60l50-30z" fill="#66748a"/></svg>'
_VIDEO_SLOTS = threading.BoundedSemaphore(2)


def video_thumbnail(settings, asset, headers, *, allowed_root=None, size=320, wait_seconds=0):
    source = Path(asset['path']).resolve()
    if not source.is_relative_to((allowed_root or settings.storage_dir/'assets').resolve()) or not source.is_file():
        return Response(_VIDEO, media_type='image/svg+xml', headers=headers)
    key = hashlib.sha256((asset['sha256']+f':video:{size}:v1').encode()).hexdigest()
    root = settings.storage_dir/'asset-thumbs'; root.mkdir(parents=True, exist_ok=True)
    target = root/(key+'.jpg'); failure = root/(key+'.failed')
    if target.is_file():
        return FileResponse(target, media_type='image/jpeg', headers=headers)
    if (failure.exists() and time.time()-failure.stat().st_mtime < 300) or not _VIDEO_SLOTS.acquire(timeout=wait_seconds):
        return Response(_VIDEO, media_type='image/svg+xml', headers=headers)
    temporary = root/(key+'.'+uuid.uuid4().hex+'.jpg')
    try:
        if target.is_file():
            return FileResponse(target, media_type='image/jpeg', headers=headers)
        import imageio_ffmpeg
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y',
                        '-protocol_whitelist', 'file,pipe', '-threads', '1', '-i', str(source),
                        '-frames:v', '1', '-an', '-vf', f'scale={size}:{size}:force_original_aspect_ratio=decrease',
                        '-threads', '1', '-f', 'image2', str(temporary)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ValueError('No video frame')
        temporary.replace(target)
        return FileResponse(target, media_type='image/jpeg', headers=headers)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        failure.touch()
        return Response(_VIDEO, media_type='image/svg+xml', headers=headers)
    finally:
        temporary.unlink(missing_ok=True)
        _VIDEO_SLOTS.release()


def thumbnail_response(settings, asset):
    headers={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}
    if asset['kind'] in {'video','person_video'} or asset['mime'].startswith('video/'):
        return video_thumbnail(settings,asset,headers)
    from PIL import Image, ImageOps
    source=Path(asset['path']).resolve()
    if not source.is_relative_to((settings.storage_dir/'assets').resolve()) or not source.is_file():
        raise LookupError('素材文件不可用。')
    key=hashlib.sha256((asset['sha256']+':320:v1').encode()).hexdigest()
    root=settings.storage_dir/'asset-thumbs'; root.mkdir(parents=True,exist_ok=True)
    target=root/(key+'.jpg')
    if not target.is_file():
        temporary=root/(key+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with Image.open(source) as image:
                image.draft('RGB',(320,320))
                image.thumbnail((320,320))
                image=ImageOps.exif_transpose(image).convert('RGB')
                image.save(temporary,format='JPEG',quality=76,optimize=False)
            temporary.replace(target)
        except (OSError,ValueError,Image.DecompressionBombError):
            raise ValueError('素材缩略图暂不可用。') from None
        finally: temporary.unlink(missing_ok=True)
    return FileResponse(target,media_type='image/jpeg',headers=headers)
