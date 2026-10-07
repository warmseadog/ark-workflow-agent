"""Non-destructive input adaptation for Seedance reference media."""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from .input_limits import IMAGE_MAX_BYTES, PERSON_VIDEO_MIN_PIXELS, PERSON_VIDEO_MAX_BYTES
from .preprocessing_limits import cache_writer, local_lock


def _hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def _notice(kind,before,after):
    return {'kind':kind,'before':[int(x) for x in before],'after':[int(x) for x in after]}


def adapt_image(source,directory):
    from PIL import Image,ImageOps
    source=Path(source);directory=Path(directory)
    try:
        with Image.open(source) as original:
            orientation=original.getexif().get(274,1)
            im=ImageOps.exif_transpose(original)
            w,h=im.size
            if .4<=w/h<=2.5 and orientation==1:
                if source.stat().st_size>IMAGE_MAX_BYTES:raise ValueError('image too large')
                return source,None
            width=max(w,math.ceil(h*.4));height=max(h,math.ceil(w/2.5))
            if width*height>40_000_000 or max(width,height)>16384:
                raise ValueError('padded image exceeds decoding limits')
            directory.mkdir(parents=True,exist_ok=True)
            target=directory/(_hash(source)+'-image-v1.png')
            with cache_writer(target),local_lock:
                if target.exists():
                    try:
                        with Image.open(target) as cached:
                            if cached.size!=(width,height) or target.stat().st_size>IMAGE_MAX_BYTES:raise ValueError('invalid cached image')
                            cached.verify()
                        return target,_notice('image',(w,h),(width,height))
                    except (OSError,ValueError):target.unlink(missing_ok=True)
                canvas=Image.new('RGB',(width,height),(240,240,240))
                if im.mode in ('RGBA','LA') or 'transparency' in im.info:
                    rgba=im.convert('RGBA');canvas.paste(rgba,((width-w)//2,(height-h)//2),rgba)
                else:canvas.paste(im.convert('RGB'),((width-w)//2,(height-h)//2))
                fd,name=tempfile.mkstemp(dir=directory,suffix='.png');os.close(fd)
                temporary=Path(name)
                try:
                    canvas.save(temporary,format='PNG')
                    if temporary.stat().st_size>IMAGE_MAX_BYTES:raise ValueError('image too large')
                    with Image.open(temporary) as check:
                        check.load()
                        if not .4<=check.width/check.height<=2.5:raise ValueError('invalid ratio')
                    temporary.replace(target)
                finally:temporary.unlink(missing_ok=True)
            return target,_notice('image',(w,h),(width,height))
    except (OSError,ValueError,Image.DecompressionBombError):
        raise ValueError('参考图片比例适配失败，请更换尺寸合适的参考图；尚未提交视频生成。') from None


def adapt_video(source,directory,*,max_seconds):
    import imageio_ffmpeg
    from .person_video import validate_file
    source=Path(source);directory=Path(directory)
    info=validate_file(source,person=False,max_seconds=max_seconds)
    w,h=int(info['width']),int(info['height'])
    if w*h>=PERSON_VIDEO_MIN_PIXELS:return source,None
    factor=math.sqrt(PERSON_VIDEO_MIN_PIXELS/(w*h))
    width=math.ceil(w*factor/2)*2;height=math.ceil(h*factor/2)*2
    if not .4<=width/height<=2.5 or max(width,height)>6000:
        raise ValueError('动作参考视频比例不符合模型要求，请更换素材。')
    directory.mkdir(parents=True,exist_ok=True)
    target=directory/(_hash(source)+f'-video-v1-{width}x{height}.mp4')
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    def check(path):
        result=validate_file(path,person=False,max_seconds=max_seconds)
        if ((int(result['width']),int(result['height']))!=(width,height)
                or abs(result['duration']-info['duration'])>.1
                or abs(result['fps']-info['fps'])>.1
                or path.stat().st_size>PERSON_VIDEO_MAX_BYTES):raise ValueError('invalid adapted video')
        subprocess.run([ffmpeg,'-nostdin','-v','error','-xerror','-i',str(path),'-map','0:v:0','-f','null','-'],
                       check=True,capture_output=True,timeout=120)
    try:
        with cache_writer(target),local_lock:
            if target.exists():
                try:
                    check(target)
                    return target,_notice('video',(w,h),(width,height))
                except (OSError,ValueError,subprocess.SubprocessError):target.unlink(missing_ok=True)
            fd,name=tempfile.mkstemp(dir=directory,suffix='.mp4');os.close(fd)
            temporary=Path(name)
            try:
                subprocess.run([ffmpeg,'-nostdin','-y','-v','error','-xerror','-i',str(source),
                    '-map','0:v:0','-map','0:a?','-vf',f'scale={width}:{height}:flags=lanczos,setsar=1',
                    '-c:v','libx264','-preset','fast','-threads','1','-crf','18','-pix_fmt','yuv420p',
                    '-c:a','copy','-movflags','+faststart',str(temporary)],check=True,capture_output=True,timeout=600)
                check(temporary);temporary.replace(target)
            finally:temporary.unlink(missing_ok=True)
        return target,_notice('video',(w,h),(width,height))
    except (OSError,ValueError,subprocess.SubprocessError):
        raise ValueError('动作参考视频尺寸适配失败，请更换更清晰的原始视频；尚未提交视频生成。') from None


def record_adaptation(work,notice):
    """Only dimensions and public material roles belong in this display record."""
    path=Path(work)/'input-adaptations.json'
    with cache_writer(path):
        values=json.loads(path.read_text(encoding='utf-8')) if path.exists() else []
        if notice not in values:values.append(notice)
        fd,name=tempfile.mkstemp(dir=path.parent,suffix='.json');os.close(fd)
        temporary=Path(name)
        try:
            temporary.write_text(json.dumps(values,ensure_ascii=False),encoding='utf-8')
            temporary.replace(path)
        finally:temporary.unlink(missing_ok=True)
