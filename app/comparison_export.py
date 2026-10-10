"""Task-scoped side-by-side MP4 export using the comparison player's time mapping."""
import hashlib
import json
from pathlib import Path
import subprocess
import threading
import uuid

from fastapi import HTTPException

from .asset_preview import preview_asset
from .person_video import probe
from .preprocessing_limits import cache_writer

_slots = threading.BoundedSemaphore(2)


def render_comparison(source, result, target, clip=None, audio='result'):
    import imageio_ffmpeg
    left, right = probe(source), probe(result)
    clip = clip or {}
    start = min(max(0, float(clip.get('start', 0))), left['duration'])
    remaining = left['duration'] - start
    seconds = float(clip.get('duration') or remaining)
    if remaining <= 0 or seconds <= 0:
        raise ValueError('原片片段不可用，请重新选择任务。')
    scale = min(1, remaining / seconds) if clip.get('retime') == 'slow' else 1
    length = min(seconds * scale, remaining)
    duration = right['duration']
    width = max(2, int(min(720, max(left['width'], right['width']))) // 2 * 2)
    height = max(2, int(min(1080, max(left['height'], right['height']))) // 2 * 2)
    fit = (f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
           f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1')
    filters = (f'[0:v:0]setpts=(PTS-STARTPTS)/{scale:.10f},fps=30,{fit},'
               f'tpad=stop_mode=clone:stop_duration={duration}[left];'
               f'[1:v:0]setpts=PTS-STARTPTS,fps=30,{fit}[right];'
               '[left][right]hstack=inputs=2:shortest=1[out]')
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y',
               '-protocol_whitelist', 'file,pipe', '-threads', '1', '-ss', str(start), '-t', str(length), '-i', str(source),
               '-protocol_whitelist', 'file,pipe', '-threads', '1', '-i', str(result),
               '-filter_complex_threads', '1', '-filter_complex', filters, '-map', '[out]']
    if audio != 'mute':
        command += ['-map', '0:a:0?' if audio == 'source' else '1:a:0?']
        audio_filters = ['asetpts=PTS-STARTPTS']
        if audio == 'source':
            tempo = scale
            while tempo < .5:
                audio_filters.append('atempo=0.5')
                tempo /= .5
            audio_filters.append(f'atempo={tempo:.10f}')
        command += ['-af', ','.join(audio_filters + ['apad']), '-c:a', 'aac', '-b:a', '128k']
    else:
        command += ['-an']
    command += ['-t', str(duration), '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                '-threads', '1', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(target)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=300)
        if abs(probe(target)['duration'] - duration) > .15:
            raise ValueError('对比视频时长异常，请重试。')
    except (OSError, subprocess.SubprocessError):
        raise ValueError('对比视频导出失败，请稍后重试。') from None


def comparison_path(settings, store, run, audio='result'):
    if audio not in {'result', 'source', 'mute'}:
        raise ValueError('对比视频声音选项无效。')
    store.require_visible(run['id'])
    if run['status'] != 'succeeded' or run.get('legacy'):
        raise LookupError('对比视频尚未就绪。')
    asset = preview_asset(settings, run['snapshot'].get('source_asset_id'))
    if asset['kind'] != 'video':
        raise LookupError('原始动作视频不可用。')
    source = Path(asset['path'])
    result = settings.storage_dir/'outputs'/(run['id']+'.mp4')
    if not result.is_file() or not result.resolve().is_relative_to((settings.storage_dir/'outputs').resolve()):
        raise LookupError('生成视频文件不存在。')
    clip = run['snapshot'].get('source_clip')
    stamps = [(str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns) for p in (source, result)]
    key = hashlib.sha256(json.dumps([1, stamps, clip, audio], sort_keys=True).encode()).hexdigest()
    target = settings.storage_dir/'cache'/'comparisons'/(key+'.mp4')
    if not target.is_file():
        if not _slots.acquire(timeout=2):
            raise HTTPException(503, '正在导出其他对比视频，请稍后重试。', headers={'Retry-After': '3'})
        temporary = target.with_name(key+'.'+uuid.uuid4().hex+'.mp4')
        try:
            with cache_writer(target):
                if not target.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    render_comparison(source, result, temporary, clip, audio)
                    temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
            _slots.release()
    # Recheck visibility and source authorization after a potentially long encode.
    store.require_visible(run['id'])
    preview_asset(settings, asset['id'])
    return target
