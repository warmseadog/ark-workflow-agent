"""Frame an editing input without cropping people or modifying the original."""
import math
from pathlib import Path
import subprocess

RATIOS = ('adaptive', '16:9', '9:16', '1:1', '4:3', '3:4', '21:9')


def validate_ratio(value):
    if not isinstance(value, str) or value not in RATIOS:
        raise ValueError('请选择有效的画面比例。')
    return value


def reframe_video(source, target, ratio):
    validate_ratio(ratio)
    source = Path(source)
    if ratio == 'adaptive':
        return source
    from .person_video import probe
    import imageio_ffmpeg
    info = probe(source)
    a, b = map(int, ratio.split(':'))
    divisor = math.gcd(a, b); a //= divisor; b //= divisor
    if abs(info['width'] / info['height'] - a / b) < 0.001:
        return source
    # Even, exact-ratio dimensions; bound padding and encoding cost on large inputs.
    unit = min(math.ceil(max(info['width']/a, info['height']/b)/2)*2,
               math.floor(1920/max(a, b)/2)*2)
    width, height = a*unit, b*unit
    target = Path(target); target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.stem + '.partial.mp4')
    filters = f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black'
    try:
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-y', '-v', 'error',
            '-i', str(source), '-map', '0:v:0', '-map', '0:a:0?', '-vf', filters,
            '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-threads', '1',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart', str(temporary)],
            check=True, capture_output=True, timeout=600)
        output = probe(temporary)
        if abs(output['duration']-info['duration']) > .15 or abs(output['width']/output['height']-a/b) > .001:
            raise ValueError('画面比例转换结果异常，请更换参考视频后重试。')
        temporary.replace(target)
        return target
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError('画面比例转换失败，请更换参考视频后重试。') from error
    finally:
        temporary.unlink(missing_ok=True)
