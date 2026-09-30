"""Owner playback is independent of portrait eligibility; shared grants still apply."""
import hashlib
from pathlib import Path
import subprocess
import threading
import uuid

from fastapi import HTTPException
from fastapi.responses import FileResponse

from .production_store import ProductionStore

_slots = threading.BoundedSemaphore(2)


def preview_asset(settings, ident):
    from .shared_portraits import authorize_asset
    store = ProductionStore(settings.storage_dir)
    asset = store.get_asset(ident, private=True)  # Always resolve within the caller's tenant.
    with store.connection() as db:
        exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='shared_portrait_provenance'").fetchone()
        mirrored = exists and db.execute('SELECT 1 FROM shared_portrait_provenance WHERE asset_id=?', (ident,)).fetchone()
    if mirrored:
        authorize_asset(settings, ident)  # Revoking a shared source also revokes its mirror.
    path = Path(asset['path']).resolve()
    if not path.is_relative_to((settings.storage_dir/'assets').resolve()) or not path.is_file():
        raise LookupError('素材文件不存在，请重新上传。')
    return asset


def reference_status(settings, ident):
    from .shared_portraits import authorize_asset
    from .person_video import probe
    asset = preview_asset(settings, ident)
    result = {'can_preview': True, 'can_use': True, 'state': 'unchecked',
              'message': '可预览 · 尚未入库检查，生成前会自动检查。',
              'person_id': None, 'person_type': None}
    if asset['kind'] in {'video', 'person_video'}:
        result['duration'] = probe(asset['path'])['duration']
    try:
        source = authorize_asset(settings, ident)
    except (LookupError, ValueError, PermissionError) as error:
        return {**result, 'state': 'blocked', 'can_use': False,
                'message': '可预览，但不可用于生成：' + str(error) + ' 请从人物库选择可用素材或更换视频。'}
    if source:
        lib, photo = source
        person = lib.person(photo['person_id'])
        result.update(person_id=person['id'], person_type=person['person_type'])
        if photo['status'] == 'active':
            result.update(state='verified', message='可预览 · 已通过素材检查，提交时会再次核实授权。')
        else:
            result.update(state='blocked', can_use=False,
                          message='可预览，但不可用于生成：' + (photo.get('message') or '人物素材尚未通过检查。') + ' 请在人物库处理或更换视频。')
    return result


def preview_response(settings, ident):
    asset = preview_asset(settings, ident)
    if asset['kind'] != 'person_video':
        raise ValueError('请选择人物视频素材。')
    source = Path(asset['path'])
    stat = source.stat()
    key = hashlib.sha256((asset['sha256'] + f':{stat.st_size}:{stat.st_mtime_ns}:h264-v1').encode()).hexdigest()
    root = settings.storage_dir/'asset-previews'
    target = root/(key+'.mp4')
    if not target.is_file():
        if not _slots.acquire(timeout=2):
            raise HTTPException(503, '正在准备其他视频预览，请稍后重试。', headers={'Retry-After': '3'})
        temporary = root/(key+'.'+uuid.uuid4().hex+'.mp4')
        try:
            if not target.is_file():
                import imageio_ffmpeg
                root.mkdir(parents=True, exist_ok=True)
                subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y',
                                '-protocol_whitelist', 'file,pipe', '-threads', '1', '-i', str(source),
                                '-map', '0:v:0', '-map', '0:a:0?', '-t', '30',
                                '-vf', 'scale=1280:1280:force_original_aspect_ratio=decrease:force_divisible_by=2',
                                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-threads', '1',
                                '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '96k', '-movflags', '+faststart', str(temporary)],
                               check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
                if not temporary.is_file() or not temporary.stat().st_size:
                    raise ValueError('Empty preview')
                temporary.replace(target)
        except (OSError, ValueError, subprocess.SubprocessError):
            raise HTTPException(422, '兼容预览生成失败，请更换有效的视频文件。') from None
        finally:
            temporary.unlink(missing_ok=True)
            _slots.release()
    return FileResponse(target, media_type='video/mp4', headers={'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
