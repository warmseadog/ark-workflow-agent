"""Select a source segment without changing the user's original video."""
import json
import math
import subprocess
from pathlib import Path
from .person_video import probe, validate_file


def normalize_clip(value):
    if value is None:
        return None
    if not isinstance(value, dict) or not {'start', 'duration'} <= set(value) or set(value) - {'start', 'duration', 'retime'}:
        raise ValueError('片段设置需包含起点和时长。')
    start, duration = value['start'], value['duration']
    if type(start) not in (int, float) or not math.isfinite(start) or not 0 <= start <= 86400:
        raise ValueError('片段起点需为有效的非负秒数。')
    if type(duration) not in (int, float) or not math.isfinite(duration) or not 2 <= duration <= 30:
        raise ValueError('片段时长需为 2–30 秒。')
    result = {'start': round(start, 3), 'duration': round(duration, 3)}
    if 'retime' in value:
        if value['retime'] != 'slow':
            raise ValueError('不支持的视频时长调整方式。')
        result['retime'] = 'slow'
    return result


def fingerprint(clip):
    clip = normalize_clip(clip)
    return ':clip-v1:' + json.dumps(clip, sort_keys=True) if clip else ''


def validate_source(path, clip=None, *, max_seconds=30):
    clip = normalize_clip(clip)
    if not clip:
        return validate_file(path, person=False, max_seconds=max_seconds)
    path = Path(path)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError('动作视频不存在或为空，请重新选择。')
    info = probe(path)
    if clip['duration'] > max_seconds:
        raise ValueError(f'当前模型片段不能超过 {max_seconds} 秒。')
    remaining = info['duration'] - clip['start']
    if remaining < 2:
        raise ValueError('片段起点之后需至少保留 2 秒原视频。')
    if clip.get('retime') != 'slow' and clip['duration'] > remaining + 0.02:
        raise ValueError(f"片段范围超出原视频（{info['duration']:.2f} 秒），请调整起点或时长。")
    return {**info, 'source_duration': info['duration'], 'duration': clip['duration']}


def clip_video(source, target, clip, *, max_seconds=30):
    clip = normalize_clip(clip)
    if not clip:
        return Path(source)
    source_info = validate_source(source, clip, max_seconds=max_seconds)
    import imageio_ffmpeg
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + '.partial.mp4')
    remaining = source_info['source_duration'] - clip['start']
    factor = max(1, clip['duration'] / remaining) if clip.get('retime') == 'slow' else 1
    filters = []
    if factor > 1:
        tempo = 1 / factor
        tempos = []
        while tempo < 0.5:
            tempos.append('atempo=0.5')
            tempo /= 0.5
        tempos.append(f'atempo={tempo:.10f}')
        filters = ['-vf', f"setpts={factor:.10f}*(PTS-STARTPTS),fps={min(60, max(24, source_info['fps']))},tpad=stop_mode=clone:stop_duration={clip['duration']}",
                   '-af', ','.join(tempos) + f",apad=whole_dur={clip['duration']}"]
    command = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-y', '-v', 'error',
               '-ss', str(clip['start']), '-i', str(source), *filters, '-t', str(clip['duration']),
               '-map', '0:v:0', '-map', '0:a?', '-c:v', 'libx264', '-preset', 'fast',
               '-threads', '1', '-crf', '18', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(temporary)]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=600)
        info = validate_file(temporary, person=False, max_seconds=max_seconds + 0.1)
        if abs(info['duration'] - clip['duration']) > 0.15:
            raise ValueError('截取后视频时长异常，请重新选择片段。')
        temporary.replace(target)
        return target
    except (subprocess.SubprocessError, OSError) as error:
        raise ValueError('视频片段截取失败，请检查视频或更换素材后重试。') from error
    finally:
        temporary.unlink(missing_ok=True)
